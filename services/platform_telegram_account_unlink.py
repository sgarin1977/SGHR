"""Authorized canonical Telegram unlink, including mandatory Platform audit.

The caller opens the transaction and later completes required idempotency and
commits. This service never revokes existing auth sessions. Those sessions do
not carry a source account identity in the approved current Platform model.
"""
from copy import deepcopy
from dataclasses import dataclass
from uuid import UUID

from database.repositories.event import EventRepository
from database.repositories.platform_telegram_accounts import (
    PlatformTelegramAccountRepository,
    PlatformTelegramIdentityUnavailableError,
)
from services.api_identity import ApiActorContext, ApiIdentityAccessError, ApiIdentityService
from services.platform_telegram_binding_consume import (
    PlatformTelegramBindingOperationError,
    PlatformTelegramBindingVersionConflictError,
)


UNLINK_OPERATION = 'platform.telegram.account.unlink'
UNLINK_EVENT = 'platform_telegram_account_unlinked'


@dataclass(frozen=True)
class PlatformTelegramAccountUnlinkResult:
    account_id: UUID
    user_id: UUID
    tenant_id: UUID
    status: str
    row_version: int
    changed: bool


def _error():
    return PlatformTelegramBindingOperationError('Telegram account unlink state is invalid.')


class PlatformTelegramAccountUnlinkService:
    def __init__(self, *, session, account_repository, event_repository, identity_service):
        if (session is None or not callable(getattr(session, 'in_transaction', None))
            or any(getattr(service, 'session', None) is not session
                for service in (account_repository, event_repository, identity_service))
            or not callable(getattr(identity_service, 'require_actor', None))
            or not callable(getattr(account_repository, 'get_owned_account_for_update', None))
            or not callable(getattr(account_repository, 'unlink_account', None))
            or not callable(getattr(event_repository, 'create_event', None))):
            raise ValueError('Account unlink requires shared-session Platform services.')
        self.session, self.accounts, self.events, self.identity = (
            session, account_repository, event_repository, identity_service,
        )

    async def _actor(self, actor):
        if (not isinstance(actor, ApiActorContext) or actor.status != 'active'
            or not isinstance(actor.user_id, UUID) or not isinstance(actor.tenant_id, UUID)):
            raise ApiIdentityAccessError('API actor is not available.')
        current = await self.identity.require_actor(user_id=actor.user_id, tenant_id=actor.tenant_id)
        if (not isinstance(current, ApiActorContext) or current.status != 'active'
            or current.user_id != actor.user_id or current.tenant_id != actor.tenant_id):
            raise ApiIdentityAccessError('API actor is not available.')
        return current

    @staticmethod
    def _account(row, *, account_id, user_id):
        if (row is None or getattr(row, 'id', None) != account_id
            or getattr(row, 'user_id', None) != user_id or getattr(row, 'platform', None) != 'telegram'
            or not isinstance(getattr(row, 'platform_user_id', None), str) or not row.platform_user_id
            or getattr(row, 'status', None) not in ('active', 'unlinked')
            or type(getattr(row, 'row_version', None)) is not int
            or not 1 <= row.row_version <= 2**63-1):
            raise _error()
        return row

    async def unlink_account(self, *, actor, account_id, expected_row_version, trace_id=None):
        """Own account only; no-op requires the current optimistic version.

        A retry under the same Idempotency-Key belongs to the enclosing workflow,
        which can return its earlier result even after this version has changed.
        """
        try:
            if self.session.in_transaction() is not True:
                raise PlatformTelegramBindingOperationError('Account unlink requires an active caller transaction.')
            if not isinstance(account_id, UUID): raise ValueError('Account identifier is invalid.')
            if type(expected_row_version) is not int or not 1 <= expected_row_version < 2**63-1:
                raise ValueError('Account expected row version is invalid.')
            if trace_id is not None and (not isinstance(trace_id, str) or not trace_id.strip() or len(trace_id) > 128):
                raise ValueError('Account trace identifier is invalid.')
            with self.session.no_autoflush:
                actor = await self._actor(actor)
                # Repository refreshes/locks the user, then the owned Telegram
                # row, in the same order as the final Telegram authentication gate.
                try:
                    row = await self.accounts.get_owned_account_for_update(account_id=account_id, user_id=actor.user_id)
                except PlatformTelegramIdentityUnavailableError:
                    raise ApiIdentityAccessError('API actor is not available.') from None
                if row is None:
                    # Missing, email and another user's account share this denial.
                    raise ApiIdentityAccessError('Telegram account is not available.')
                row = self._account(row, account_id=account_id, user_id=actor.user_id)
                actor = await self._actor(actor)
                if row.row_version != expected_row_version:
                    raise PlatformTelegramBindingVersionConflictError()
                if row.status == 'unlinked':
                    return PlatformTelegramAccountUnlinkResult(account_id, actor.user_id, actor.tenant_id,
                        'unlinked', row.row_version, False)

                identifier = row.platform_user_id
                try:
                    changed = await self.accounts.unlink_account(account_id=account_id, user_id=actor.user_id,
                        expected_row_version=expected_row_version)
                except PlatformTelegramIdentityUnavailableError:
                    raise ApiIdentityAccessError('API actor is not available.') from None
                if changed is None: raise PlatformTelegramBindingVersionConflictError()
                changed = self._account(changed, account_id=account_id, user_id=actor.user_id)
                if (changed.platform_user_id != identifier or changed.status != 'unlinked'
                    or changed.row_version != expected_row_version+1): raise _error()
                payload = {'operation': UNLINK_OPERATION, 'account_id': str(account_id), 'changes': [
                    {'field': 'status', 'old_value': 'active', 'new_value': 'unlinked'},
                    {'field': 'row_version', 'old_value': expected_row_version, 'new_value': expected_row_version+1},
                ]}
                audit = await self.events.create_event(event_type=UNLINK_EVENT, tenant_id=actor.tenant_id,
                    user_id=actor.user_id, entity_type='user_account', entity_id=account_id,
                    payload=deepcopy(payload), platform='api', trace_id=trace_id)
                if (not isinstance(getattr(audit, 'id', None), UUID)
                    or getattr(audit, 'tenant_id', None) != actor.tenant_id
                    or getattr(audit, 'user_id', None) != actor.user_id
                    or getattr(audit, 'event_type', None) != UNLINK_EVENT
                    or getattr(audit, 'entity_type', None) != 'user_account'
                    or getattr(audit, 'entity_id', None) != account_id
                    or getattr(audit, 'platform', None) != 'api'
                    or getattr(audit, 'payload', None) != payload
                    or (trace_id is not None and getattr(audit, 'trace_id', None) != trace_id)): raise _error()
                await self._actor(actor)
                return PlatformTelegramAccountUnlinkResult(account_id, actor.user_id, actor.tenant_id,
                    'unlinked', expected_row_version+1, True)
        except BaseException:
            await self.session.rollback()
            raise


def build_platform_telegram_account_unlink_service(*, session):
    return PlatformTelegramAccountUnlinkService(session=session,
        account_repository=PlatformTelegramAccountRepository(session), event_repository=EventRepository(session),
        identity_service=ApiIdentityService(session))
