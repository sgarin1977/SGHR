"""Platform Telegram challenge storage; caller owns authorization and transaction."""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import PlatformTelegramBindingToken, User
from services.platform_telegram_binding_tokens import PLATFORM_TELEGRAM_BINDING_TOKEN_TTL


def _identifier(value):
    if not isinstance(value, UUID):
        raise ValueError('Telegram binding identifier is invalid.')
    return value


def _hash(value):
    if (not isinstance(value, str) or len(value) != 64
        or any(character not in '0123456789abcdef' for character in value)):
        raise ValueError('Telegram binding hash is invalid.')
    return value


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Telegram binding requires an aware server timestamp.')
    return value.astimezone(timezone.utc)


def _version(value):
    if type(value) is not int or not 1 <= value <= 9223372036854775807:
        raise ValueError('Telegram binding version is invalid.')
    return value


class PlatformTelegramBindingTokenRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def lock_active_user(self, *, user_id: UUID) -> UUID | None:
        """An existing user row is a serialization anchor for later workflows."""
        statement = select(User.id).where(User.id == _identifier(user_id), User.status == 'active').with_for_update()
        with self.session.no_autoflush:
            result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def create_token(self, *, token_id: UUID, user_id: UUID, tenant_id: UUID,
        initiated_by_user_id: UUID, token_hash: str,
        issued_at: datetime, expires_at: datetime) -> PlatformTelegramBindingToken:
        token_id, user_id, token_hash = _identifier(token_id), _identifier(user_id), _hash(token_hash)
        tenant_id, initiated_by_user_id = _identifier(tenant_id), _identifier(initiated_by_user_id)
        issued_at, expires_at = _utc(issued_at), _utc(expires_at)
        try:
            valid_expiry = expires_at == issued_at + PLATFORM_TELEGRAM_BINDING_TOKEN_TTL
        except OverflowError:
            valid_expiry = False
        if not valid_expiry:
            raise ValueError('Telegram binding lifetime is invalid.')
        record = PlatformTelegramBindingToken(id=token_id, user_id=user_id, tenant_id=tenant_id,
            initiated_by_user_id=initiated_by_user_id, token_hash=token_hash,
            issued_at=issued_at, expires_at=expires_at, consumed_at=None, revoked_at=None,
            updated_at=issued_at, row_version=1)
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_token_for_update(self, *, token_id: UUID, user_id: UUID, tenant_id: UUID) -> PlatformTelegramBindingToken | None:
        """Load all states for the authenticated owner's service-level checks."""
        statement = select(PlatformTelegramBindingToken).where(
            PlatformTelegramBindingToken.id == _identifier(token_id),
            PlatformTelegramBindingToken.user_id == _identifier(user_id),
            PlatformTelegramBindingToken.tenant_id == _identifier(tenant_id),
        ).execution_options(populate_existing=True).with_for_update()
        with self.session.no_autoflush:
            result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_token_by_hash_for_update(self, *, token_hash: str) -> PlatformTelegramBindingToken | None:
        """Internal lookup after code/link hashing; this grants no user access.

        The service must verify token state and the Telegram identity proof.
        It must not expose this record, its owner or hash to an untrusted caller.
        """
        statement = select(PlatformTelegramBindingToken).where(
            PlatformTelegramBindingToken.token_hash == _hash(token_hash),
        ).execution_options(populate_existing=True).with_for_update()
        with self.session.no_autoflush:
            result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    def _live_statement(self, *, token_id, user_id, tenant_id, expected_row_version, as_of):
        token_id, user_id, tenant_id = _identifier(token_id), _identifier(user_id), _identifier(tenant_id)
        expected_row_version, as_of = _version(expected_row_version), _utc(as_of)
        return update(PlatformTelegramBindingToken).where(
            PlatformTelegramBindingToken.id == token_id,
            PlatformTelegramBindingToken.user_id == user_id,
            PlatformTelegramBindingToken.tenant_id == tenant_id,
            PlatformTelegramBindingToken.row_version == expected_row_version,
            PlatformTelegramBindingToken.issued_at <= as_of,
            PlatformTelegramBindingToken.expires_at > as_of,
            PlatformTelegramBindingToken.consumed_at.is_(None),
            PlatformTelegramBindingToken.revoked_at.is_(None),
        ), as_of

    async def consume_token(self, *, token_id: UUID, user_id: UUID, tenant_id: UUID, token_hash: str,
        expected_row_version: int, as_of: datetime) -> PlatformTelegramBindingToken | None:
        token_hash = _hash(token_hash)
        statement, as_of = self._live_statement(token_id=token_id, user_id=user_id, tenant_id=tenant_id,
            expected_row_version=expected_row_version, as_of=as_of)
        statement = statement.where(PlatformTelegramBindingToken.token_hash == token_hash).values(
            consumed_at=as_of, updated_at=as_of,
            row_version=PlatformTelegramBindingToken.row_version + 1,
        )
        return await self._return_updated(statement)

    async def revoke_token(self, *, token_id: UUID, user_id: UUID, tenant_id: UUID,
        expected_row_version: int, as_of: datetime) -> PlatformTelegramBindingToken | None:
        """Conditionally revoke a live, unused token. No resend policy is implied."""
        statement, as_of = self._live_statement(token_id=token_id, user_id=user_id, tenant_id=tenant_id,
            expected_row_version=expected_row_version, as_of=as_of)
        statement = statement.values(revoked_at=as_of, updated_at=as_of,
            row_version=PlatformTelegramBindingToken.row_version + 1)
        return await self._return_updated(statement)

    async def _return_updated(self, statement):
        # Bulk UPDATE does not apply SQLAlchemy mapper versioning automatically;
        # the version predicate/increment above provide the conditional write.
        statement = statement.returning(PlatformTelegramBindingToken).execution_options(
            synchronize_session=False, populate_existing=True,
        )
        with self.session.no_autoflush:
            result = await self.session.execute(statement)
        return result.scalar_one_or_none()


    async def get_token_locator(self, *, token_hash: str) -> tuple[UUID, UUID, UUID] | None:
        """Read stored routing context; caller locks user before reloading the token.

        This snapshot is not authorization and does not establish token state.
        Its purpose is a consistent user -> token -> account lock order.
        """
        statement = select(PlatformTelegramBindingToken.id, PlatformTelegramBindingToken.user_id,
            PlatformTelegramBindingToken.tenant_id).where(
            PlatformTelegramBindingToken.token_hash == _hash(token_hash),
        )
        with self.session.no_autoflush:
            result = await self.session.execute(statement)
        row = result.one_or_none()
        return tuple(row) if row is not None else None
