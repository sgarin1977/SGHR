import os
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import User, UserAccount, UserRoleMapping, Tenant


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_platform_account(
        self,
        platform: str,
        platform_user_id: str,
    ) -> Optional[UserAccount]:
        stmt = select(UserAccount).where(
            UserAccount.platform == platform,
            UserAccount.platform_user_id == platform_user_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_telegram_account_by_user_id(
        self,
        user_id: uuid.UUID,
    ) -> Optional[UserAccount]:
        stmt = select(UserAccount).where(
            UserAccount.user_id == user_id,
            UserAccount.platform == "telegram",
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_telegram_user_core(
        self,
        platform_user_id: str,
        username: Optional[str],
        first_name: Optional[str],
        last_name: Optional[str],
        language_code: str,
        role: str,
    ) -> uuid.UUID:
        existing_account = await self.get_by_platform_account("telegram", platform_user_id)
        if existing_account:
            return existing_account.user_id

        tenant_id_str = os.getenv("DEFAULT_TENANT_ID")
        if tenant_id_str and tenant_id_str.strip():
            tenant_id = uuid.UUID(tenant_id_str.strip())
        else:
            tenant_res = await self.session.execute(select(Tenant.id).limit(1))
            tenant_id = tenant_res.scalar_one_or_none()

            if not tenant_id:
                raise Exception(
                    "Критична помилка: У базі немає жодного запису в таблиці tenants. "
                    "Запустіть seed_beta_data.py згідно з ТЗ!"
                )

        new_user = User(
            tenant_id=tenant_id,
            active_role=role if role in ["super_admin", "admin"] else None,
            language_code=language_code[:10] if language_code else "ru",
            status="active",
        )
        self.session.add(new_user)
        await self.session.flush()

        new_account = UserAccount(
            user_id=new_user.id,
            platform="telegram",
            platform_user_id=platform_user_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
        )
        self.session.add(new_account)

        new_role = UserRoleMapping(
            user_id=new_user.id,
            tenant_id=tenant_id,
            role=role,
            status="active",
        )
        self.session.add(new_role)

        try:
            await self.session.commit()
            return new_user.id
        except IntegrityError:
            await self.session.rollback()

            existing_account = await self.get_by_platform_account("telegram", platform_user_id)
            if existing_account:
                return existing_account.user_id

            raise