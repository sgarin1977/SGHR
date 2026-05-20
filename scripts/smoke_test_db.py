import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from database.session import get_session
from database.models import User, UserAccount, UserRoleMapping

async def run_smoke_test():
    print("⏳ Запуск Smoke-тесту шару бази даних для Етапу 0.2...")
    
    async with get_session() as session:
        try:
            # 1. Тестуємо читання з таблиць
            print("👀 Перевірка підключення та читання таблиці користувачів...")
            user_stmt = select(User).limit(1)
            user_res = await session.execute(user_stmt)
            user_res.scalar_one_or_none()
            
            print("👀 Перевірка читання таблиці акаунтів...")
            account_stmt = select(UserAccount).limit(1)
            account_res = await session.execute(account_stmt)
            account_res.scalar_one_or_none()

            print("👀 Перевірка читання таблиці ролей...")
            role_stmt = select(UserRoleMapping).limit(1)
            role_res = await session.execute(role_stmt)
            role_res.scalar_one_or_none()
            
            print("✅ Структрура моделей повністю синхронізована з Supabase!")
            print("🎉 SMOKE TEST PASSED: OK")
            
        except Exception as e:
            print(f"❌ SMOKE TEST FAILED: {e}")
            sys.exit(1)

if __name__ == "__main__":
    asyncio.run(run_smoke_test())
