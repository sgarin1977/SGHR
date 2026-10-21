"""Own canonical Telegram identities; grants and tenant membership confer no authority here."""
from dataclasses import dataclass
from uuid import UUID
from services.api_identity import ApiIdentityAccessError
from services.api_platform_principal import ApiPlatformPrincipal, build_api_platform_principal_service
from database.repositories.platform_telegram_account_state import (
    PlatformTelegramAccountStateRepository, TelegramAccountStateRecord,
)

class PlatformTelegramAccountStateError(Exception):
    pass

@dataclass(frozen=True)
class TelegramAccountStatePage:
    items: tuple[TelegramAccountStateRecord, ...]
    next_cursor: UUID | None

class PlatformTelegramAccountStateService:
    def __init__(self, *, repository, identity):
        if (getattr(repository, 'session', None) is None
                or getattr(identity, 'session', None) is not repository.session):
            raise ValueError('Account reads require a shared Platform session.')
        self.repository, self.identity = repository, identity

    async def list_accounts(self, *, principal, limit=20, after=None):
        if not isinstance(principal, ApiPlatformPrincipal):
            raise ApiIdentityAccessError('Platform identity is not available.')
        if (type(limit) is not int or not 1 <= limit <= 100
                or (after is not None and not isinstance(after, UUID))):
            raise ValueError('Account page context is invalid.')
        try:
            fresh = await self.identity.require_principal(user_id=principal.user_id)
            if fresh != principal:
                raise ApiIdentityAccessError('Platform identity is not available.')
            rows = await self.repository.list_owned_states(user_id=principal.user_id, limit=limit, after=after)
            if not isinstance(rows, tuple) or len(rows) > limit+1:
                raise PlatformTelegramAccountStateError('Account projection is invalid.')
            previous = after
            for row in rows:
                if (not isinstance(row, TelegramAccountStateRecord) or not isinstance(row.account_id, UUID)
                        or row.user_id != principal.user_id or row.status not in ('active', 'unlinked')
                        or type(row.row_version) is not int or not 1 <= row.row_version <= 2**63-1
                        or not isinstance(row.telegram_user_id, str) or not row.telegram_user_id.isascii()
                        or not row.telegram_user_id.isdecimal() or row.telegram_user_id.startswith('0')
                        or (previous is not None and row.account_id <= previous)):
                    raise PlatformTelegramAccountStateError('Account projection is invalid.')
                previous = row.account_id
            if await self.identity.require_principal(user_id=principal.user_id) != principal:
                raise ApiIdentityAccessError('Platform identity is not available.')
            return TelegramAccountStatePage(rows[:limit], rows[limit-1].account_id if len(rows) > limit else None)
        except (ApiIdentityAccessError, PlatformTelegramAccountStateError):
            raise
        except Exception:
            raise PlatformTelegramAccountStateError('Telegram account read failed.') from None

def build_platform_telegram_account_state_service(*, session):
    return PlatformTelegramAccountStateService(repository=PlatformTelegramAccountStateRepository(session),
        identity=build_api_platform_principal_service(session=session))
