import asyncio
import sys
from pathlib import Path

from sqlalchemy import select, func

sys.path.append(str(Path(__file__).resolve().parent.parent))

from database.session import async_session
from database.models import UserAccount


TELEGRAM_ID = "483721727"


async def main():
    async with async_session() as session:
        result = await session.execute(
            select(func.count()).select_from(UserAccount).where(
                UserAccount.platform == "telegram",
                UserAccount.platform_user_id == TELEGRAM_ID,
            )
        )
        count = result.scalar_one()

        print(f"telegram_id={TELEGRAM_ID}")
        print(f"user_accounts_count={count}")

        if count == 1:
            print("OK: no duplicate user_accounts")
            return

        if count == 0:
            print("FAIL: user account not found")
            raise SystemExit(1)

        print("FAIL: duplicate user_accounts found")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())