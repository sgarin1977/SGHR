import asyncio

from sqlalchemy import delete, select

from database.models import UserAccount, UserConsent
from database.session import get_session


TELEGRAM_ID = "755072549"


async def main():
    async with get_session() as session:
        account_result = await session.execute(
            select(UserAccount).where(
                UserAccount.platform == "telegram",
                UserAccount.platform_user_id == TELEGRAM_ID,
            )
        )
        account = account_result.scalar_one_or_none()

        if not account:
            print("UserAccount не знайдено")
            return

        result = await session.execute(
            delete(UserConsent)
            .where(UserConsent.user_id == account.user_id)
            .returning(UserConsent.id)
        )
        deleted_ids = result.scalars().all()

        await session.commit()

        print("user_id:", account.user_id)
        print("deleted_consents:", len(deleted_ids))


if __name__ == "__main__":
    asyncio.run(main())