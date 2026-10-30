"""Authorized CON-C015 suggestions; no geocoding, writes or automatic links."""
from datetime import datetime, timezone
from uuid import UUID

from database.repositories.construction.construction_address_sources import ConstructionAddressSource
from services.construction.construction_address_matching import (
    ConstructionAddressCandidate, MAX_ADDRESS_SUGGESTIONS,
    normalize_construction_address, select_construction_address_matches,
)
from services.construction.construction_grants import ConstructionAccessContextResolver
from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext,
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
    require_construction_access, require_construction_role_scope,
)


class ConstructionAddressSuggestionOperationError(Exception):
    pass


class ConstructionAddressSuggestionValidationError(ValueError):
    pass


class ConstructionAddressSuggestionsService:
    def __init__(self, *, session, repository, access_repository, now_provider=None):
        if (session is None or not callable(getattr(session, 'in_transaction', None))
                or not hasattr(session, 'no_autoflush')
                or any(getattr(item, 'session', None) is not session for item in (repository, access_repository))
                or not callable(getattr(repository, 'iter_address_sources', None))
                or any(not callable(getattr(access_repository, name, None))
                    for name in ('has_active_platform_membership', 'list_active_grants'))
                or (now_provider is not None and not callable(now_provider))):
            raise ValueError('Address suggestions require shared-session read repositories.')
        self.session, self.repository, self.access_repository = session, repository, access_repository
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.resolver = ConstructionAccessContextResolver(repository=access_repository, now_provider=self._now)

    def _now(self):
        value = self.now_provider()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConstructionAddressSuggestionOperationError('Address suggestion clock must be aware.')
        return value.astimezone(timezone.utc)

    @staticmethod
    def _scope(actor, context):
        if (getattr(actor, 'status', None) != 'active'
                or not isinstance(getattr(actor, 'user_id', None), UUID)
                or not isinstance(getattr(actor, 'tenant_id', None), UUID)
                or not isinstance(context, ConstructionAccessContext)
                or context.user_id != actor.user_id or context.tenant_id != actor.tenant_id):
            raise ConstructionPermissionDeniedError('Address suggestion access is unavailable.')
        projects = set()
        tenant_wide = False
        for grant in context.grants:
            if not isinstance(grant, ConstructionGrantContext) or not isinstance(grant.scope_id, UUID):
                raise ConstructionScopeDeniedError('Address suggestion scope is invalid.')
            require_construction_role_scope(role=grant.role, scope_type=grant.scope_type)
            if grant.scope_type == 'tenant' and grant.scope_id == actor.tenant_id:
                try:
                    require_construction_access(actor=actor, access_context=context,
                        permission_code='construction.projects.read')
                except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
                    continue
                tenant_wide = True
            elif grant.scope_type == 'project':
                try:
                    require_construction_access(actor=actor, access_context=context,
                        permission_code='construction.projects.read', project_id=grant.scope_id)
                except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
                    continue
                projects.add(grant.scope_id)
        if tenant_wide:
            return None
        if projects:
            return tuple(sorted(projects, key=lambda value: value.int))
        raise ConstructionPermissionDeniedError('Address suggestion project read access is unavailable.')

    async def _current_scope(self, actor):
        if await self.access_repository.has_active_platform_membership(
                tenant_id=actor.tenant_id, user_id=actor.user_id) is not True:
            raise ConstructionPermissionDeniedError('Address suggestion membership is unavailable.')
        return self._scope(actor, await self.resolver.resolve(actor=actor))

    @staticmethod
    def _candidates(row, actor, allowed):
        if (not isinstance(row, ConstructionAddressSource) or row.tenant_id != actor.tenant_id
                or not isinstance(row.project_id, UUID)
                or not all(isinstance(value, str) for value in (row.project_name, row.project_status, row.address_raw))
                or (row.address_formatted is not None and not isinstance(row.address_formatted, str))
                or ((row.client_id is None) != (row.client_name is None))
                or (row.client_id is not None and (not isinstance(row.client_id, UUID) or not isinstance(row.client_name, str)))):
            raise ConstructionAddressSuggestionOperationError('Address source projection is invalid.')
        if allowed is not None and row.project_id not in allowed:
            raise ConstructionScopeDeniedError('Address source is outside the authorized projects.')
        values = [ConstructionAddressCandidate('project', row.project_id, actor.tenant_id,
            row.project_name, row.address_raw, row.address_formatted)]
        # Existing client-read services require tenant-wide CRM read access.
        # Project read permission alone must not expose client card names.
        if allowed is None and row.client_id is not None:
            values.append(ConstructionAddressCandidate('client', row.client_id, actor.tenant_id,
                row.client_name, row.address_raw, row.address_formatted))
        return tuple(values)

    async def suggest_addresses(self, *, actor, access_context, query, limit=MAX_ADDRESS_SUGGESTIONS):
        try:
            self._scope(actor, access_context)
            if type(limit) is not int or not 1 <= limit <= MAX_ADDRESS_SUGGESTIONS:
                raise ConstructionAddressSuggestionValidationError('Address suggestion limit must be between 1 and 10.')
            try:
                normalized = normalize_construction_address(query)
            except ValueError:
                raise ConstructionAddressSuggestionValidationError('Address query must be text.') from None
            if not self.session.in_transaction():
                raise ConstructionAddressSuggestionOperationError('Address suggestions require an existing transaction.')
            with self.session.no_autoflush:
                allowed = await self._current_scope(actor)
                matches = ()
                if normalized:
                    async for source in self.repository.iter_address_sources(
                            tenant_id=actor.tenant_id, allowed_project_ids=allowed):
                        candidates = tuple(candidate for candidate, _ in matches) + self._candidates(source, actor, allowed)
                        # Each step retains the best unique entities, together
                        # with their normalized-address tie-break information.
                        # At most limit+2 candidates are held during ranking.
                        matches = select_construction_address_matches(tenant_id=actor.tenant_id,
                            query=normalized, candidates=candidates, limit=limit)
                current = await self._current_scope(actor)
                if current != allowed:
                    raise ConstructionScopeDeniedError('Address suggestion scope changed during the read.')
                if not self.session.in_transaction():
                    raise ConstructionAddressSuggestionOperationError('Address suggestion transaction ended during the read.')
                return tuple(suggestion for _, suggestion in matches)
        except (ConstructionAddressSuggestionValidationError, ConstructionAddressSuggestionOperationError,
                ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
            raise
        except Exception:
            raise ConstructionAddressSuggestionOperationError('Address suggestion read failed.') from None


def build_construction_address_suggestions_service(*, session, now_provider=None):
    from database.repositories.construction.construction_address_sources import ConstructionAddressSourcesRepository
    from database.repositories.construction import ConstructionAccessRepository
    return ConstructionAddressSuggestionsService(session=session, repository=ConstructionAddressSourcesRepository(session),
        access_repository=ConstructionAccessRepository(session), now_provider=now_provider)
