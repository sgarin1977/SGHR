import asyncio

from sqlalchemy import text

from database.session import async_session


async def main():
    async with async_session() as session:
        result = await session.execute(
            text("select version_num from alembic_version")
        )
        print(result.scalar_one_or_none())


if __name__ == "__main__":
    asyncio.run(main())