"""Read-only canonical account projection for the authenticated owner."""
from dataclasses import dataclass
from uuid import UUID
from sqlalchemy import select
from database.models import User, UserAccount

@dataclass(frozen=True)
class TelegramAccountStateRecord:
    account_id: UUID
    user_id: UUID
    telegram_user_id: str
    status: str
    row_version: int

class PlatformTelegramAccountStateRepository:
    def __init__(self, session):
        self.session = session

    async def list_owned_states(self, *, user_id, limit, after=None):
        if (not isinstance(user_id, UUID) or type(limit) is not int or not 1 <= limit <= 100
                or (after is not None and not isinstance(after, UUID))):
            raise ValueError('Account page context is invalid.')
        statement = select(UserAccount.id, UserAccount.user_id, UserAccount.platform_user_id,
            UserAccount.status, UserAccount.row_version).join(User, User.id == UserAccount.user_id).where(
                User.id == user_id, User.status == 'active', UserAccount.user_id == user_id,
                UserAccount.platform == 'telegram', UserAccount.status.in_(('active', 'unlinked')))
        if after is not None:
            statement = statement.where(UserAccount.id > after)
        statement = statement.order_by(UserAccount.id).limit(limit+1).execution_options(autoflush=False)
        with self.session.no_autoflush:
            rows = (await self.session.execute(statement)).all()
        return tuple(TelegramAccountStateRecord(*row) for row in rows)
