"""Canonical user_accounts adapter for the future token-verified binding service.

The service must validate the Telegram proof and consume its binding challenge
in the same transaction as account creation and mandatory audit. This adapter
is not an authentication entry point and never registers a new Platform user.
"""
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import UserAccount
from database.repositories.platform_telegram_binding_tokens import PlatformTelegramBindingTokenRepository
from database.repositories.user import PlatformTelegramAccountUnlinkedError
from database.models import User
from sqlalchemy import update


class PlatformTelegramAccountAlreadyLinkedError(Exception):
    status_code = 409
    code = 'TELEGRAM_ACCOUNT_ALREADY_LINKED'

    def __init__(self):
        super().__init__('Telegram account is already linked.')


class PlatformTelegramIdentityUnavailableError(Exception):
    def __init__(self):
        super().__init__('Telegram identity is not available.')


def _user(value):
    if not isinstance(value, UUID):
        raise ValueError('Platform user identifier is invalid.')
    return value


def _telegram_id(value):
    # Use the integer from a verified Telegram identity, not a username or
    # freely formatted string that could create alternate textual identities.
    if type(value) is not int or value <= 0:
        raise ValueError('Telegram user identifier is invalid.')
    try:
        return str(value)
    except ValueError:
        raise ValueError('Telegram user identifier is invalid.') from None


class PlatformTelegramAccountRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_account_for_update(self, *, telegram_user_id: int) -> UserAccount | None:
        identifier = _telegram_id(telegram_user_id)
        statement = select(UserAccount).where(
            UserAccount.platform == 'telegram', UserAccount.platform_user_id == identifier,
        ).execution_options(populate_existing=True).with_for_update()
        with self.session.no_autoflush:
            result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def list_accounts(self, *, user_id: UUID) -> tuple[UserAccount, ...]:
        statement = select(UserAccount).where(
            UserAccount.user_id == _user(user_id), UserAccount.platform == 'telegram', UserAccount.status == 'active',
        ).order_by(UserAccount.id).execution_options(populate_existing=True)
        with self.session.no_autoflush:
            result = await self.session.execute(statement)
        return tuple(result.scalars().all())

    async def create_or_get_account(self, *, user_id: UUID, telegram_user_id: int) -> tuple[UserAccount, bool]:
        """Create a canonical identity or return its existing same-user link.

        No existing account fields are updated. ON CONFLICT DO NOTHING uses
        the existing native UNIQUE(platform, platform_user_id), also protecting
        races against the current Telegram registration path. The caller owns
        rollback on errors and must not expose this as a standalone link API.
        """
        user_id, identifier = _user(user_id), _telegram_id(telegram_user_id)
        if await PlatformTelegramBindingTokenRepository(self.session).lock_active_user(user_id=user_id) != user_id:
            raise PlatformTelegramIdentityUnavailableError()
        statement = insert(UserAccount).values(
            id=uuid4(), user_id=user_id, platform='telegram', platform_user_id=identifier,
            raw_profile={}, created_at=func.now(), updated_at=func.now(),
        ).on_conflict_do_nothing(
            index_elements=[UserAccount.platform, UserAccount.platform_user_id],
        ).returning(UserAccount).execution_options(populate_existing=True)
        with self.session.no_autoflush:
            result = await self.session.execute(statement)
        record = result.scalar_one_or_none()
        if record is not None:
            return self._require_owner(record, user_id=user_id, identifier=identifier), True
        # A concurrent unlink can remove the conflicting row before this read.
        # Fail closed; caller retries the complete audited business operation.
        record = await self.get_account_for_update(telegram_user_id=telegram_user_id)
        return self._require_owner(record, user_id=user_id, identifier=identifier), False

    @staticmethod
    def _require_owner(record, *, user_id, identifier):
        if (record is None or not isinstance(record.id, UUID)
            or record.platform != 'telegram' or record.platform_user_id != identifier
            or not isinstance(record.user_id, UUID)):
            raise PlatformTelegramIdentityUnavailableError()
        if record.user_id != user_id:
            raise PlatformTelegramAccountAlreadyLinkedError()
        # Explicit proof-based reactivation follows in the relink operation.
        # Until then an inactive row must not be reported as an active binding.
        if getattr(record, 'status', None) != 'active':
            raise PlatformTelegramAccountUnlinkedError()
        return record

    async def _lock_active_user(self, *, user_id: UUID) -> User | None:
        """Shared lock order for authentication and unlink: user, then account.

        Refresh the full user, including tenant/status, before issuing credentials.
        The caller owns this transaction and must keep it open through commit.
        """
        user_id = _user(user_id)
        statement = select(User).where(
            User.id == user_id, User.status == 'active',
        ).execution_options(populate_existing=True).with_for_update()
        with self.session.no_autoflush:
            result = await self.session.execute(statement)
        record = result.scalar_one_or_none()
        if record is not None and (record.id != user_id or record.status != 'active'):
            raise PlatformTelegramIdentityUnavailableError()
        return record

    async def lock_account_for_authentication(self, *, user_id: UUID, telegram_user_id: int) -> UserAccount:
        """Hold the exact active canonical identity until authentication commits."""
        user_id, identifier = _user(user_id), _telegram_id(telegram_user_id)
        if await self._lock_active_user(user_id=user_id) is None:
            raise PlatformTelegramIdentityUnavailableError()
        record = await self.get_account_for_update(telegram_user_id=telegram_user_id)
        record = self._require_owner(record, user_id=user_id, identifier=identifier)
        if type(record.row_version) is not int or not 1 <= record.row_version <= 2**63-1:
            raise PlatformTelegramIdentityUnavailableError()
        return record

    async def get_owned_account_for_update(self, *, account_id: UUID, user_id: UUID) -> UserAccount | None:
        """Read active or unlinked state for a future versioned domain operation."""
        account_id, user_id = _user(account_id), _user(user_id)
        if await self._lock_active_user(user_id=user_id) is None:
            raise PlatformTelegramIdentityUnavailableError()
        statement = select(UserAccount).where(
            UserAccount.id == account_id, UserAccount.user_id == user_id,
            UserAccount.platform == 'telegram',
        ).execution_options(populate_existing=True).with_for_update()
        with self.session.no_autoflush:
            result = await self.session.execute(statement)
        record = result.scalar_one_or_none()
        if record is not None and (
            record.id != account_id or record.user_id != user_id or record.platform != 'telegram'
            or record.status not in ('active', 'unlinked')
            or type(record.row_version) is not int or not 1 <= record.row_version <= 2**63-1
        ):
            raise PlatformTelegramIdentityUnavailableError()
        return record

    async def unlink_account(self, *, account_id: UUID, user_id: UUID, expected_row_version: int) -> UserAccount | None:
        """Conditional storage primitive; caller must add audit and commit atomically.

        None means no matching active version. No sessions are revoked here.
        Keeping the row preserves identity ownership and prevents onboarding reuse.
        """
        account_id, user_id = _user(account_id), _user(user_id)
        if type(expected_row_version) is not int or not 1 <= expected_row_version < 2**63-1:
            raise ValueError('Account row version is invalid.')
        if await self._lock_active_user(user_id=user_id) is None:
            raise PlatformTelegramIdentityUnavailableError()
        statement = update(UserAccount).where(
            UserAccount.id == account_id, UserAccount.user_id == user_id,
            UserAccount.platform == 'telegram', UserAccount.status == 'active',
            UserAccount.row_version == expected_row_version,
        ).values(
            status='unlinked', row_version=UserAccount.row_version+1, updated_at=func.now(),
        ).returning(UserAccount).execution_options(populate_existing=True)
        with self.session.no_autoflush:
            result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def reactivate_account(self, *, account_id: UUID, user_id: UUID,
        telegram_user_id: int, expected_row_version: int) -> UserAccount | None:
        """Storage primitive for the token/proof-verified consume operation only.

        Preserve the canonical identity and its profile. The caller owns mandatory
        audit, binding-token consumption, idempotency and the transaction commit.
        """
        account_id, user_id, identifier = _user(account_id), _user(user_id), _telegram_id(telegram_user_id)
        if type(expected_row_version) is not int or not 1 <= expected_row_version < 2**63-1:
            raise ValueError('Account expected row version is invalid.')
        if await PlatformTelegramBindingTokenRepository(self.session).lock_active_user(user_id=user_id) != user_id:
            raise PlatformTelegramIdentityUnavailableError()
        statement = update(UserAccount).where(
            UserAccount.id == account_id, UserAccount.user_id == user_id,
            UserAccount.platform == 'telegram', UserAccount.platform_user_id == identifier,
            UserAccount.status == 'unlinked', UserAccount.row_version == expected_row_version,
        ).values(status='active', row_version=UserAccount.row_version+1, updated_at=func.now()
        ).returning(UserAccount).execution_options(populate_existing=True)
        with self.session.no_autoflush:
            result = await self.session.execute(statement)
        return result.scalar_one_or_none()
