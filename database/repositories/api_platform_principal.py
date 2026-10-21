"""Fresh canonical Platform identity projection; no organization authorization."""
from uuid import UUID

from sqlalchemy import select

from database.models import User


class ApiPlatformPrincipalRepository:
    def __init__(self, session):
        if session is None or not callable(getattr(session, 'execute', None)) or not hasattr(session, 'no_autoflush'):
            raise ValueError('Platform principal reads require the Platform session.')
        self.session = session

    async def get_active_user_id(self, *, user_id):
        if not isinstance(user_id, UUID):
            raise ValueError('Canonical user UUID is required.')
        statement = select(User.id, User.status).where(User.id == user_id, User.status == 'active').execution_options(autoflush=False)
        with self.session.no_autoflush:
            row = (await self.session.execute(statement)).one_or_none()
        if row is None:
            return None
        if tuple(row) != (user_id, 'active') or not isinstance(row[0], UUID):
            raise ValueError('Canonical Platform user projection is invalid.')
        return user_id
