"""Token-verified Platform binding operation; public idempotency is a workflow concern.

The shared operation accepts signed Telegram initData or an opaque proof from
its trusted Bot API update adapter. Public HTTP continues to require initData.
The caller starts the shared transaction and commits after workflow completion.
Every failure rolls back that transaction, including the mandatory audit write.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from api.telegram_auth import TelegramIdentity, TelegramInitDataVerifier
from services.platform_telegram_binding_bot_proof import TelegramBotBindingUpdateVerifier
from database.repositories.event import EventRepository
from database.repositories.platform_telegram_accounts import PlatformTelegramAccountRepository
from database.repositories.platform_telegram_binding_tokens import PlatformTelegramBindingTokenRepository
from services.platform_telegram_binding_tokens import (
    PlatformTelegramBindingTokenDeniedError,
    hash_platform_telegram_binding_token,
    require_live_platform_telegram_binding_token,
)
from copy import deepcopy
from database.repositories.user import PlatformTelegramAccountUnlinkedError
from database.repositories.platform_telegram_accounts import PlatformTelegramAccountAlreadyLinkedError


class PlatformTelegramBindingOperationError(Exception):
    pass


class PlatformTelegramBindingVersionConflictError(Exception):
    status_code = 409
    code = 'ROW_VERSION_CONFLICT'

    def __init__(self):
        super().__init__('Telegram binding version conflict.')


@dataclass(frozen=True)
class PlatformTelegramBindingResult:
    binding_id: UUID
    account_id: UUID
    user_id: UUID
    consumed_at: datetime
    row_version: int
    account_created: bool
    tenant_id: UUID


def _denied():
    return PlatformTelegramBindingTokenDeniedError('Telegram binding token is not available.')


class PlatformTelegramBindingConsumeService:
    def __init__(self, *, session, token_repository, account_repository, event_repository,
        telegram_verifier, now_provider=None):
        if not isinstance(telegram_verifier, (TelegramInitDataVerifier, TelegramBotBindingUpdateVerifier)):
            raise ValueError('Configured Telegram signature verifier or trusted Bot API context is required.')
        if any(getattr(repository, 'session', None) is not session
            for repository in (token_repository, account_repository, event_repository)):
            raise ValueError('Binding repositories and audit must share one session.')
        self.session, self.tokens, self.accounts, self.events = session, token_repository, account_repository, event_repository
        self.verifier = telegram_verifier
        self.now_provider = now_provider if now_provider is not None else lambda: datetime.now(timezone.utc)

    def _now(self):
        value = self.now_provider()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise PlatformTelegramBindingOperationError('Binding clock must be timezone-aware.')
        return value.astimezone(timezone.utc)

    def _identity(self, proof, now):
        if isinstance(self.verifier, TelegramInitDataVerifier) and (not isinstance(proof, str) or not proof):
            raise _denied()
        identity = self.verifier.verify(proof, now=now)
        if (not isinstance(identity, TelegramIdentity) or type(identity.platform_user_id) is not int
            or identity.platform_user_id <= 0):
            raise _denied()
        return identity

    async def consume_token(self, *, token: str, telegram_init_data: object, trace_id: str | None = None):
        if self.session.in_transaction() is not True:
            raise PlatformTelegramBindingOperationError('Binding requires an active caller transaction.')
        try:
            if trace_id is not None and (not isinstance(trace_id, str) or not trace_id.strip() or len(trace_id) > 128):
                raise ValueError('Binding trace identifier is invalid.')
            # Reject forged/expired Telegram proof before database access.
            identity = self._identity(telegram_init_data, self._now())
            try:
                digest = hash_platform_telegram_binding_token(token)
            except ValueError:
                raise _denied() from None
            with self.session.no_autoflush:
                locator = await self.tokens.get_token_locator(token_hash=digest)
                if (not isinstance(locator, tuple) or len(locator) != 3
                    or not all(isinstance(identifier, UUID) for identifier in locator)):
                    raise _denied()
                binding_id, owner_id, tenant_id = locator
                if await self.tokens.lock_active_user(user_id=owner_id) != owner_id:
                    raise _denied()
                row = await self.tokens.get_token_for_update(token_id=binding_id, user_id=owner_id, tenant_id=tenant_id)
                if (row is None or getattr(row, 'id', None) != binding_id or getattr(row, 'user_id', None) != owner_id
                    or getattr(row, 'token_hash', None) != digest
                    or getattr(row, 'tenant_id', None) != tenant_id
                    or not isinstance(getattr(row, 'initiated_by_user_id', None), UUID)):
                    raise _denied()
                initiated_by_user_id = row.initiated_by_user_id
                version = getattr(row, 'row_version', None)
                if type(version) is not int or not 1 <= version < 9223372036854775807:
                    raise PlatformTelegramBindingOperationError('Stored binding version is invalid.')
                # Blocking locks can outlast either TTL; revalidate both proofs
                # against a fresh server timestamp after acquiring the locks.
                now = self._now()
                current_identity = self._identity(telegram_init_data, now)
                if current_identity.platform_user_id != identity.platform_user_id:
                    raise _denied()
                require_live_platform_telegram_binding_token(token=token, expected_hash=row.token_hash,
                    issued_at=getattr(row, 'issued_at', None), expires_at=getattr(row, 'expires_at', None),
                    consumed_at=getattr(row, 'consumed_at', 'missing'), revoked_at=getattr(row, 'revoked_at', 'missing'),
                    as_of=now)
                unused_binding_state = dict(expected_hash=row.token_hash, issued_at=row.issued_at,
                    expires_at=row.expires_at, consumed_at=row.consumed_at, revoked_at=row.revoked_at)
                changed = await self.tokens.consume_token(token_id=binding_id, user_id=owner_id, tenant_id=tenant_id, token_hash=digest,
                    expected_row_version=version, as_of=now)
                if changed is None:
                    raise PlatformTelegramBindingVersionConflictError()
                if (changed.id != binding_id or changed.user_id != owner_id or changed.token_hash != digest
                    or changed.row_version != version+1 or changed.consumed_at != now or changed.revoked_at is not None
                    or getattr(changed, 'tenant_id', None) != tenant_id
                    or getattr(changed, 'initiated_by_user_id', None) != initiated_by_user_id):
                    raise PlatformTelegramBindingOperationError('Binding write receipt is invalid.')
                relink_previous_version = None
                try:
                    account, created = await self.accounts.create_or_get_account(
                        user_id=owner_id, telegram_user_id=identity.platform_user_id)
                except PlatformTelegramAccountUnlinkedError:
                    account, relink_previous_version = await self._relink_account(owner_id=owner_id,
                        telegram_user_id=identity.platform_user_id, proof=telegram_init_data, token=token,
                        unused_binding_state=unused_binding_state)
                    created = False
                if (type(created) is not bool or not isinstance(getattr(account, 'id', None), UUID)
                    or getattr(account, 'user_id', None) != owner_id or getattr(account, 'platform', None) != 'telegram'
                    or getattr(account, 'platform_user_id', None) != str(identity.platform_user_id)
                    or getattr(account, 'status', None) != 'active'
                    or type(getattr(account, 'row_version', None)) is not int
                    or not 1 <= account.row_version <= 2**63-1):
                    raise PlatformTelegramBindingOperationError('Canonical account receipt is invalid.')
                payload = {'operation': 'platform.telegram.binding.consume', 'account_id': str(account.id),
                    'account_created': created, 'changes': [
                        {'field': 'consumed_at', 'old_value': None, 'new_value': now.isoformat()},
                        {'field': 'row_version', 'old_value': version, 'new_value': version+1},
                    ]}
                if relink_previous_version is not None:
                    payload['account_reactivated'] = True
                    payload['changes'].extend([
                        {'field': 'account.status', 'old_value': 'unlinked', 'new_value': 'active'},
                        {'field': 'account.row_version', 'old_value': relink_previous_version, 'new_value': relink_previous_version+1},
                    ])
                audit = await self.events.create_event(event_type='platform_telegram_binding_consumed',
                    tenant_id=tenant_id, user_id=owner_id, entity_type='platform_telegram_binding_token',
                    entity_id=binding_id, payload=deepcopy(payload),
                    platform='telegram' if isinstance(self.verifier, TelegramBotBindingUpdateVerifier) else 'api', trace_id=trace_id)
                if (not isinstance(getattr(audit, 'id', None), UUID)
                    or getattr(audit, 'user_id', None) != owner_id or getattr(audit, 'tenant_id', 'missing') != tenant_id
                    or getattr(audit, 'event_type', None) != 'platform_telegram_binding_consumed'
                    or getattr(audit, 'entity_type', None) != 'platform_telegram_binding_token'
                    or getattr(audit, 'entity_id', None) != binding_id or getattr(audit, 'payload', None) != payload):
                    raise PlatformTelegramBindingOperationError('Mandatory binding audit receipt is invalid.')
                return PlatformTelegramBindingResult(binding_id, account.id, owner_id, now, version+1, created, tenant_id)
        except BaseException:
            await self.session.rollback()
            raise

    async def _relink_account(self, *, owner_id, telegram_user_id, proof, token, unused_binding_state):
        """Restore only the same owner after a live one-time proof-verified consume.

        Called solely after create_or_get rejected an unlinked account. That
        repository path already locks the user and canonical account; normal
        authentication keeps rejecting inactive rows and cannot call this path.
        """
        row = await self.accounts.get_account_for_update(telegram_user_id=telegram_user_id)
        if (row is None or not isinstance(getattr(row, 'id', None), UUID)
            or not isinstance(getattr(row, 'user_id', None), UUID)
            or getattr(row, 'platform', None) != 'telegram'
            or getattr(row, 'platform_user_id', None) != str(telegram_user_id)):
            raise PlatformTelegramBindingOperationError('Canonical relink state is invalid.')
        if row.user_id != owner_id: raise PlatformTelegramAccountAlreadyLinkedError()
        version, identifier = getattr(row, 'row_version', None), row.id
        if (row.status != 'unlinked' or type(version) is not int or not 1 <= version < 2**63-1):
            raise PlatformTelegramBindingOperationError('Canonical relink version is invalid.')
        # Waiting for the canonical-account lock can outlast either TTL. The
        # snapshot was read under the binding lock while it was still unused;
        # only our own pending consume has changed that locked row since then.
        checked_at = self._now()
        if self._identity(proof, checked_at).platform_user_id != telegram_user_id: raise _denied()
        require_live_platform_telegram_binding_token(token=token, as_of=checked_at, **unused_binding_state)
        changed = await self.accounts.reactivate_account(account_id=identifier, user_id=owner_id,
            telegram_user_id=telegram_user_id, expected_row_version=version)
        if changed is None: raise PlatformTelegramBindingVersionConflictError()
        if (getattr(changed, 'id', None) != identifier or getattr(changed, 'user_id', None) != owner_id
            or getattr(changed, 'platform', None) != 'telegram'
            or getattr(changed, 'platform_user_id', None) != str(telegram_user_id)
            or getattr(changed, 'status', None) != 'active'
            or type(getattr(changed, 'row_version', None)) is not int or changed.row_version != version+1):
            raise PlatformTelegramBindingOperationError('Canonical relink receipt is invalid.')
        return changed, version


def build_platform_telegram_binding_consume_service(*, session, telegram_verifier,
    now_provider=None):
    """Consume derives its trusted user and tenant from the persisted binding record."""
    return PlatformTelegramBindingConsumeService(session=session,
        token_repository=PlatformTelegramBindingTokenRepository(session),
        account_repository=PlatformTelegramAccountRepository(session), event_repository=EventRepository(session),
        telegram_verifier=telegram_verifier, now_provider=now_provider)
