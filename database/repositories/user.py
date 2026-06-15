import os
import uuid
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    ConversationParticipant,
    Profession,
    Specialist,
    SpecialistProfession,
    Tenant,
    User,
    UserAccount,
    UserRoleMapping,
)


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

    async def list_active_roles(
        self,
        user_id: uuid.UUID,
    ) -> list[str]:
        stmt = (
            select(UserRoleMapping.role)
            .where(
                UserRoleMapping.user_id == user_id,
                UserRoleMapping.status == "active",
            )
            .distinct()
            .order_by(UserRoleMapping.role)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def ensure_active_role(
        self,
        *,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        role: str,
    ) -> bool:
        result = await self.session.execute(
            select(UserRoleMapping).where(
                UserRoleMapping.user_id == user_id,
                UserRoleMapping.role == role,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            if existing.status != "active":
                existing.status = "active"
                await self.session.flush()
                return True

            return False

        self.session.add(
            UserRoleMapping(
                user_id=user_id,
                tenant_id=tenant_id,
                role=role,
                status="active",
            )
        )
        await self.session.flush()
        return True

    async def get_primary_specialist_profession_name(
        self,
        user_id: uuid.UUID,
        language: str = "ru",
    ) -> Optional[str]:
        localized_name = {
            "ru": Profession.name_ru,
            "en": Profession.name_en,
            "pt": Profession.name_pt,
        }.get(language, Profession.name_ru)

        stmt = (
            select(
                func.coalesce(
                    localized_name,
                    Profession.name_ru,
                    Profession.name_en,
                    Profession.name_pt,
                    Profession.name,
                )
            )
            .select_from(Specialist)
            .join(
                SpecialistProfession,
                SpecialistProfession.specialist_id == Specialist.id,
            )
            .join(
                Profession,
                Profession.id == SpecialistProfession.profession_id,
            )
            .where(
                Specialist.user_id == user_id,
                SpecialistProfession.status == "active",
            )
            .order_by(
                SpecialistProfession.is_primary.desc(),
                SpecialistProfession.created_at.asc(),
            )
            .limit(1)
        )

        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_role_unread_counts(
        self,
        user_id: uuid.UUID,
    ) -> dict[str, int]:
        stmt = (
            select(
                ConversationParticipant.participant_role,
                func.coalesce(func.sum(ConversationParticipant.unread_count), 0),
            )
            .where(
                ConversationParticipant.user_id == user_id,
                ConversationParticipant.is_archived.is_(False),
                ConversationParticipant.is_hidden.is_(False),
            )
            .group_by(ConversationParticipant.participant_role)
        )

        result = await self.session.execute(stmt)

        counts: dict[str, int] = {}
        for participant_role, unread_count in result.all():
            if participant_role in {"client", "specialist"}:
                counts[participant_role] = int(unread_count or 0)

        return counts

    async def update_language_code(
        self,
        *,
        user_id: uuid.UUID,
        language_code: str,
    ) -> User:
        user = await self.session.get(User, user_id)
        if not user:
            raise ValueError("User not found.")

        user.language_code = language_code[:10] if language_code else "ru"
        await self.session.flush()
        return user

    async def set_active_role(
        self,
        user_id: uuid.UUID,
        role: str,
    ) -> User:
        role_result = await self.session.execute(
            select(UserRoleMapping.id)
            .where(
                UserRoleMapping.user_id == user_id,
                UserRoleMapping.role == role,
                UserRoleMapping.status == "active",
            )
            .limit(1)
        )

        if role_result.scalar_one_or_none() is None:
            raise ValueError("Role is not active for this user.")

        user = await self.session.get(User, user_id)
        if not user:
            raise ValueError("User not found.")

        user.active_role = role
        await self.session.flush()
        return user

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

        roles_to_create = ["client"]
        if role != "client":
            roles_to_create.append(role)

        for role_name in roles_to_create:
            self.session.add(
                UserRoleMapping(
                    user_id=new_user.id,
                    tenant_id=tenant_id,
                    role=role_name,
                    status="active",
                )
            )

        try:
            await self.session.commit()
            return new_user.id
        except IntegrityError:
            await self.session.rollback()

            existing_account = await self.get_by_platform_account("telegram", platform_user_id)
            if existing_account:
                return existing_account.user_id

            raise