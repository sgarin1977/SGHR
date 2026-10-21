"""Client identity unlink; caller owns trusted routing, RLS and final commit.

An already authenticated Platform user may remove only their own existing
client link without membership. The editor path uses the existing fresh CRM
edit policy. This does not unlink Telegram, revoke sessions, remove membership
or revoke Construction grants. No public route or idempotency policy is added.
"""
from dataclasses import dataclass
from datetime import datetime,timezone
from types import SimpleNamespace
from uuid import UUID

from database.repositories.construction_client_user_links import (
    ConstructionClientUserLinkRepository,ConstructionClientUserLinkState,ConstructionClientUserLinkMutation,MAX_CLIENT_ROW_VERSION,
)
from services.api_identity import ApiActorContext,ApiIdentityAccessError
from services.construction_audit import ConstructionAuditLogger,ConstructionAuditChange
from services.construction_client_link_confirmation import PlatformClientLinkConfirmationIdentityReader
from services.construction_clients import ConstructionClientValidationError,ConstructionClientNotFoundError,ConstructionClientOperationError
from services.construction_foundation import ConstructionRowVersionConflictError,require_expected_row_version
from services.construction_grants import ConstructionAccessContextResolver
from services.construction_permissions import (
    ConstructionAccessContext,ConstructionPermissionDeniedError,ConstructionScopeDeniedError,require_construction_access,
)


class ConstructionClientUnlinkDeniedError(ConstructionPermissionDeniedError):
    def __init__(self):super().__init__('Own client link is not available.')


@dataclass(frozen=True)
class ConstructionClientUnlinkResult:
    client_id: UUID
    tenant_id: UUID
    previous_linked_user_id: UUID | None
    linked_user_id: UUID | None
    row_version: int
    changed: bool
    audit_id: UUID | None


class ConstructionClientUnlinkService:
    def __init__(self, *, session, repository, access_repository, event_repository, identity_reader, now_provider=None):
        methods=((repository,('get_state_for_update','change_linked_user')),
            (access_repository,('has_active_platform_membership','list_active_grants')),(event_repository,('create_event',)))
        if (session is None or not callable(getattr(session,'in_transaction',None))
                or not callable(getattr(session,'rollback',None)) or not hasattr(session,'no_autoflush')
                or any(getattr(repo,'session',None) is not session or any(not callable(getattr(repo,n,None)) for n in names)
                    for repo,names in methods)
                or getattr(identity_reader,'session',None) is None or identity_reader.session is session
                or not callable(getattr(identity_reader,'require_active_user',None))
                or (now_provider is not None and not callable(now_provider))):
            raise ValueError('Client unlink requires shared Construction writes and separate canonical Platform reads.')
        self.session,self.repository,self.access,self.identity=session,repository,access_repository,identity_reader
        self.audit=ConstructionAuditLogger(event_repository=event_repository)
        self.now_provider=now_provider or (lambda:datetime.now(timezone.utc))
        self.resolver=ConstructionAccessContextResolver(repository=access_repository,now_provider=self._now)

    def _now(self):
        now=self.now_provider()
        if not isinstance(now,datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ConstructionClientOperationError('Client unlink clock is invalid.')
        return now.astimezone(timezone.utc)

    async def _active(self,actor):
        if await self.identity.require_active_user(user_id=actor.user_id)!=actor.user_id:
            raise ApiIdentityAccessError('Authenticated Platform actor is not available.')

    @staticmethod
    def _scope(actor,context):
        if not isinstance(actor.tenant_id,UUID) or not isinstance(context,ConstructionAccessContext):
            raise ConstructionPermissionDeniedError('Client edit authorization is not available.')
        require_construction_access(actor=actor,access_context=context,permission_code='construction.projects.edit')

    async def _editor(self,actor):
        await self._active(actor)
        if await self.access.has_active_platform_membership(user_id=actor.user_id,tenant_id=actor.tenant_id) is not True:
            raise ConstructionPermissionDeniedError('Client edit membership is not available.')
        self._scope(actor,await self.resolver.resolve(actor=actor))

    @staticmethod
    def _state(value,tenant_id,client_id):
        if value is None:return None
        if (not isinstance(value,ConstructionClientUserLinkState) or value.tenant_id!=tenant_id or value.client_id!=client_id
                or type(value.row_version) is not int or not 1<=value.row_version<=MAX_CLIENT_ROW_VERSION
                or (value.linked_user_id is not None and not isinstance(value.linked_user_id,UUID))):
            raise ConstructionClientOperationError('Stored client link state is invalid.')
        return value

    async def unlink_self(self, *, actor, tenant_id, client_id, expected_row_version, trace_id=None):
        return await self._unlink(actor=actor,tenant_id=tenant_id,access_context=None,client_id=client_id,
            expected_row_version=expected_row_version,trace_id=trace_id,as_editor=False)

    async def unlink_as_editor(self, *, actor, access_context, client_id, expected_row_version, trace_id=None):
        return await self._unlink(actor=actor,tenant_id=getattr(actor,'tenant_id',None),access_context=access_context,
            client_id=client_id,expected_row_version=expected_row_version,trace_id=trace_id,as_editor=True)

    async def _unlink(self, *, actor, tenant_id, access_context, client_id, expected_row_version, trace_id, as_editor):
        try:
            if not isinstance(actor,ApiActorContext) or actor.status!='active' or not isinstance(actor.user_id,UUID):
                raise ApiIdentityAccessError('Authenticated Platform actor is not available.')
            if as_editor:self._scope(actor,access_context)
            if self.session.in_transaction() is not True:
                raise ConstructionClientOperationError('Client unlink requires an existing caller transaction.')
            if (not isinstance(tenant_id,UUID) or not isinstance(client_id,UUID)
                    or type(expected_row_version) is not int or not 1<=expected_row_version<=MAX_CLIENT_ROW_VERSION
                    or (trace_id is not None and (not isinstance(trace_id,str) or not trace_id.strip() or len(trace_id)>128))):
                raise ConstructionClientValidationError('Client unlink context is invalid.')
            with self.session.no_autoflush:
                if as_editor:await self._editor(actor)
                else:await self._active(actor)
                before=self._state(await self.repository.get_state_for_update(tenant_id=tenant_id,client_id=client_id),tenant_id,client_id)
                if not as_editor:
                    # A null/currently foreign link does not authorize a self
                    # no-op. A future workflow can replay an earlier successful
                    # receipt with its original authenticated recipient proof.
                    if before is None or before.linked_user_id!=actor.user_id:raise ConstructionClientUnlinkDeniedError()
                elif before is None:raise ConstructionClientNotFoundError('Construction client is not available.')
                require_expected_row_version(actual_row_version=before.row_version,expected_row_version=expected_row_version)
                if as_editor:await self._editor(actor)
                else:await self._active(actor)
                if before.linked_user_id is None:
                    return ConstructionClientUnlinkResult(client_id,tenant_id,None,None,before.row_version,False,None)
                if before.row_version==MAX_CLIENT_ROW_VERSION:
                    raise ConstructionClientValidationError('Client link version is exhausted.')
                changed=await self.repository.change_linked_user(tenant_id=tenant_id,client_id=client_id,
                    expected_row_version=before.row_version,expected_linked_user_id=before.linked_user_id,
                    linked_user_id=None,as_of=self._now())
                if changed is None:raise ConstructionRowVersionConflictError()
                if (not isinstance(changed,ConstructionClientUserLinkMutation) or changed.changed is not True
                        or changed.state!=ConstructionClientUserLinkState(client_id,tenant_id,None,before.row_version+1)):
                    raise ConstructionClientOperationError('Client unlink receipt is invalid.')
                if as_editor:await self._editor(actor)
                else:await self._active(actor)
                changes=(ConstructionAuditChange('linked_user_id',str(before.linked_user_id),None),
                    ConstructionAuditChange('row_version',before.row_version,changed.state.row_version))
                details=dict(related_client_id=str(client_id),identity_link_action='editor_unlink' if as_editor else 'self_unlink',
                    authorization_method='construction.projects.edit' if as_editor else 'authenticated_link_owner')
                principal=SimpleNamespace(user_id=actor.user_id,tenant_id=tenant_id)
                event=await self.audit.record_operation(actor=principal,event_type='construction_client_updated',
                    entity_type='construction_client',entity_id=client_id,operation='update',changes=changes,details=details,trace_id=trace_id)
                payload=dict(operation='update',changes=[v.as_payload() for v in changes],reason=None,**details)
                if (not isinstance(getattr(event,'id',None),UUID) or getattr(event,'tenant_id',None)!=tenant_id
                        or getattr(event,'user_id',None)!=actor.user_id or getattr(event,'entity_id',None)!=client_id
                        or getattr(event,'entity_type',None)!='construction_client' or getattr(event,'event_type',None)!='construction_client_updated'
                        or getattr(event,'platform',None)!='api' or getattr(event,'payload',None)!=payload
                        or (trace_id is not None and getattr(event,'trace_id',None)!=trace_id)):
                    raise ConstructionClientOperationError('Mandatory client unlink audit receipt is invalid.')
                if as_editor:await self._editor(actor)
                else:await self._active(actor)
                if self.session.in_transaction() is not True:raise ConstructionClientOperationError('Client unlink transaction ended prematurely.')
                return ConstructionClientUnlinkResult(client_id,tenant_id,before.linked_user_id,None,changed.state.row_version,True,event.id)
        except (ConstructionClientValidationError,ConstructionClientOperationError,ConstructionClientNotFoundError,ApiIdentityAccessError,
                ConstructionPermissionDeniedError,ConstructionScopeDeniedError,ConstructionRowVersionConflictError):
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise ConstructionClientOperationError('Client unlink failed.') from None
        except BaseException:
            await self.session.rollback()
            raise


def build_construction_client_unlink_service(*, session, platform_session, now_provider=None):
    from database.repositories.construction import ConstructionAccessRepository
    from database.repositories.event import EventRepository
    return ConstructionClientUnlinkService(session=session,repository=ConstructionClientUserLinkRepository(session),
        access_repository=ConstructionAccessRepository(session),event_repository=EventRepository(session),
        identity_reader=PlatformClientLinkConfirmationIdentityReader(platform_session),now_provider=now_provider)
