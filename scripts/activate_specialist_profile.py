import asyncio
import os
import sys
import uuid
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from database.models import Specialist
from database.session import async_session


def resolve_specialist_id() -> uuid.UUID:
    raw_value = None

    if len(sys.argv) > 1:
        raw_value = sys.argv[1]

    if not raw_value:
        raw_value = os.getenv("SPECIALIST_ID")

    if not raw_value:
        raise SystemExit(
            "FAIL: provide specialist id via SPECIALIST_ID env or first argument"
        )

    try:
        return uuid.UUID(raw_value)
    except ValueError as exc:
        raise SystemExit(f"FAIL: invalid specialist id: {raw_value}") from exc


async def main():
    specialist_id = resolve_specialist_id()

    async with async_session() as session:
        specialist = await session.get(Specialist, specialist_id)

        if not specialist:
            raise SystemExit(f"FAIL: specialist profile not found: {specialist_id}")

        specialist.status = "active"
        await session.commit()

        print("OK: specialist profile activated for beta 0.5 smoke")
        print(f"specialist_id={specialist.id}")
        print(f"status={specialist.status}")


if __name__ == "__main__":
    asyncio.run(main())