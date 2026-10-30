from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from services.construction.construction_audit import ConstructionAuditChange, ConstructionAuditLogger
from services.construction.construction_grants import ConstructionGrantOperationError
from services.construction.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS, ConstructionAccessContext, ConstructionGrantContext,
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
    require_construction_access_manager, require_construction_role_scope,
    require_delegable_construction_role, require_delegable_construction_scope,
)


@dataclass(frozen=True)
class ConstructionTeamBlockResult:
    user_id: UUID
    revoked_grant_ids: tuple[UUID, ...]


class ConstructionTeamBlockService:
    """Prepare tenant-local revocations and audit; the workflow owns commit."""

    def __init__(self, *, session, repository, event_repository, now_provider=None):
        if (
            session is None
            or any(getattr(item, "session", None) is not session for item in (repository, event_repository))
            or any(not callable(getattr(repository, name, None)) for name in (
                "list_active_grants", "get_grant_for_update", "revoke_effective_user_grants",
            ))
            or not callable(getattr(event_repository, "create_event", None))
            or any(not callable(getattr(session, name, None)) for name in ("in_transaction", "rollback"))
            or (now_provider is not None and not callable(now_provider))
        ):
            raise ValueError("Team block requires valid shared-session wiring.")
        self.session = session
        self.repository = repository
        self.audit_logger = ConstructionAuditLogger(event_repository=event_repository)
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def _now(self):
        value = self.now_provider()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConstructionGrantOperationError("Team block server clock is invalid.")
        return value.astimezone(timezone.utc)

    def _expiry(self, value):
        if value is not None and (
            not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None
        ):
            raise ConstructionGrantOperationError("Team block grant metadata is invalid.")
        return value

    def _authority_matches(self, grant, actor):
        return (
            grant is not None
            and isinstance(getattr(grant, "id", None), UUID)
            and getattr(grant, "tenant_id", None) == actor.tenant_id
            and getattr(grant, "user_id", None) == actor.user_id
            and getattr(grant, "role", None) in ("OWNER", "ADMIN")
            and getattr(grant, "scope_type", None) == "tenant"
            and getattr(grant, "scope_id", None) == actor.tenant_id
            and getattr(grant, "status", None) == "active"
            and getattr(grant, "revoked_at", None) is None
            and getattr(grant, "revoked_by_user_id", None) is None
        )

    async def _locked_authority(self, actor):
        candidates = await self.repository.list_active_grants(
            tenant_id=actor.tenant_id, user_id=actor.user_id, as_of=self._now(),
            scope_type="tenant", scope_id=actor.tenant_id,
        )
        if not isinstance(candidates, (list, tuple)):
            raise ConstructionGrantOperationError("Team block authority response is invalid.")
        candidates = sorted(
            (grant for grant in candidates if self._authority_matches(grant, actor)),
            key=lambda grant: grant.id.int,
        )
        eligible = []
        for candidate in candidates:
            deadline = self._expiry(candidate.expires_at)
            if deadline is not None and deadline <= self._now():
                continue
            locked = await self.repository.get_grant_for_update(
                tenant_id=actor.tenant_id, grant_id=candidate.id,
            )
            if not self._authority_matches(locked, actor) or locked.id != candidate.id:
                continue
            deadline = self._expiry(locked.expires_at)
            if deadline is not None and deadline <= self._now():
                continue
            eligible.append(locked)
        if not eligible:
            raise ConstructionPermissionDeniedError("Tenant-wide Construction access management is required.")
        # Lock candidates in canonical UUID order. Choose the strongest valid
        # tenant authority afterwards, so an additional ADMIN grant never masks OWNER.
        chosen = min(eligible, key=lambda grant: (grant.role != "OWNER", grant.id.int))
        context = ConstructionAccessContext(
            actor.user_id, actor.tenant_id, (chosen.role,),
            tuple(sorted(CONSTRUCTION_ROLE_PERMISSIONS[chosen.role])),
            (ConstructionGrantContext(chosen.role, "tenant", actor.tenant_id),),
        )
        require_construction_access_manager(actor=actor, access_context=context)
        return self._expiry(chosen.expires_at), context

    async def _locked_targets(self, *, actor, user_id, access, deadline):
        candidates = await self.repository.list_active_grants(
            tenant_id=actor.tenant_id, user_id=user_id, as_of=self._check_deadline(deadline))
        if not isinstance(candidates, (list, tuple)):
            raise ConstructionGrantOperationError("Team block target response is invalid.")
        ids = set()
        for candidate in candidates:
            identifier = getattr(candidate, "id", None)
            if not isinstance(identifier, UUID) or identifier in ids:
                raise ConstructionGrantOperationError("Team block target response is invalid.")
            ids.add(identifier)
        targets = []
        for identifier in sorted(ids, key=lambda value: value.int):
            grant = await self.repository.get_grant_for_update(tenant_id=actor.tenant_id, grant_id=identifier)
            if (grant is None or grant.id != identifier or grant.tenant_id != actor.tenant_id
                    or grant.user_id != user_id or not isinstance(grant.scope_id, UUID)
                    or (grant.scope_type == "tenant" and grant.scope_id != actor.tenant_id)):
                raise ConstructionGrantOperationError("Team block locked target is invalid.")
            if grant.status == "revoked":
                continue
            if (grant.status != "active" or grant.revoked_at is not None
                    or grant.revoked_by_user_id is not None):
                raise ConstructionGrantOperationError("Team block locked target lifecycle is invalid.")
            instant = self._check_deadline(deadline)
            expiry = self._expiry(grant.expires_at)
            if expiry is not None and expiry <= instant:
                continue
            require_construction_role_scope(role=grant.role, scope_type=grant.scope_type)
            require_delegable_construction_scope(actor=actor, access_context=access,
                scope_type=grant.scope_type, scope_id=grant.scope_id)
            require_delegable_construction_role(actor=actor, access_context=access,
                target_role=grant.role, scope_type=grant.scope_type, scope_id=grant.scope_id)
            targets.append(grant)
        return targets

    def _check_deadline(self, deadline):
        instant = self._now()
        if deadline is not None and deadline <= instant:
            raise ConstructionPermissionDeniedError("Construction access management has expired.")
        return instant

    def _validate_revocations(self, rows, *, actor, user_id, instant):
        if not isinstance(rows, (list, tuple)):
            raise ConstructionGrantOperationError("Team block revocation response is invalid.")
        ids = set()
        for grant in rows:
            if (
                not isinstance(getattr(grant, "id", None), UUID) or grant.id in ids
                or grant.tenant_id != actor.tenant_id or grant.user_id != user_id
                or grant.status != "revoked" or grant.revoked_by_user_id != actor.user_id
                or grant.revoked_at != instant
                or not isinstance(grant.scope_id, UUID)
                or (grant.scope_type == "tenant" and grant.scope_id != actor.tenant_id)
            ):
                raise ConstructionGrantOperationError("Team block revocation response is invalid.")
            try:
                require_construction_role_scope(role=grant.role, scope_type=grant.scope_type)
            except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError) as error:
                raise ConstructionGrantOperationError("Team block revocation scope is invalid.") from error
            expiry = self._expiry(grant.expires_at)
            if expiry is not None and expiry <= instant:
                raise ConstructionGrantOperationError("Team block returned an expired grant.")
            ids.add(grant.id)
        return sorted(rows, key=lambda grant: grant.id.int)

    async def block_access(self, *, actor, user_id, trace_id=None):
        if not isinstance(user_id, UUID):
            await self.session.rollback()
            raise ValueError("Team block target must be a UUID.")
        try:
            if (
                getattr(actor, "status", None) != "active"
                or not all(isinstance(value, UUID) for value in (
                    getattr(actor, "tenant_id", None), getattr(actor, "user_id", None),
                ))
                or not self.session.in_transaction()
            ):
                raise ConstructionPermissionDeniedError("Construction team operation is not available.")
            deadline, access = await self._locked_authority(actor)
            targets = await self._locked_targets(actor=actor, user_id=user_id, access=access, deadline=deadline)
            instant = self._check_deadline(deadline)
            expected = {grant.id for grant in targets if grant.expires_at is None or grant.expires_at > instant}
            rows = await self.repository.revoke_effective_user_grants(
                tenant_id=actor.tenant_id, user_id=user_id,
                revoked_by_user_id=actor.user_id, as_of=instant,
            )
            self._check_deadline(deadline)
            rows = self._validate_revocations(rows, actor=actor, user_id=user_id, instant=instant)
            if {grant.id for grant in rows} != expected:
                raise ConstructionGrantOperationError("Team block changed unreviewed grants.")
            for grant in rows:
                require_delegable_construction_role(actor=actor, access_context=access,
                    target_role=grant.role, scope_type=grant.scope_type, scope_id=grant.scope_id)
            for grant in rows:
                await self.audit_logger.record_operation(
                    actor=actor, event_type="construction_access_grant_revoked",
                    entity_type="construction_access_grant", entity_id=grant.id, operation="revoke",
                    changes=(
                        ConstructionAuditChange("status", "active", "revoked"),
                        ConstructionAuditChange("revoked_by_user_id", None, str(actor.user_id)),
                        ConstructionAuditChange("revoked_at", None, instant.isoformat()),
                    ),
                    details={
                        "target_user_id": str(user_id), "role": grant.role,
                        "scope_type": grant.scope_type, "scope_id": str(grant.scope_id),
                        "team_action": "block",
                    },
                    trace_id=trace_id,
                )
                self._check_deadline(deadline)
            from services.construction.construction_owner_boundary import require_owner_survival_for_grants
            await require_owner_survival_for_grants(
                session=self.session, tenant_id=actor.tenant_id,
                grants=tuple(rows), now_provider=self._now,
            )
            return ConstructionTeamBlockResult(user_id, tuple(grant.id for grant in rows))
        except Exception as error:
            await self.session.rollback()
            if isinstance(error, (ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
                raise
            raise ConstructionGrantOperationError("Construction team block failed.") from error


def build_construction_team_block_service(session, *, now_provider=None):
    from database.repositories.construction.construction_team_access_mutations import ConstructionTeamAccessMutationRepository
    from database.repositories.event import EventRepository
    from services.construction.construction_membership_writers import SerializedConstructionTeamBlockService
    return SerializedConstructionTeamBlockService(
        session=session, repository=ConstructionTeamAccessMutationRepository(session),
        event_repository=EventRepository(session), now_provider=now_provider,
    )
