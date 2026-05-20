import pytest_asyncio
from database.session import async_session


@pytest_asyncio.fixture
async def db_session():
    async with async_session() as session:
        yield session