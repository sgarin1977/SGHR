import os
import uuid
from typing import Optional

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.role_policy import (
    ADMINISTRATIVE_ROLES,
)
from database.models import (
    City,
    Country,
    ConversationParticipant,
    Profession,
    ProfessionalCabinet,
    RoleScope,
    Specialist,
    Tenant,
    User,
    UserAccount,
    UserRoleMapping,
)


class PlatformTelegramAccountUnlinkedError(PermissionError):
    """A disconnected canonical identity must never enter automatic onboarding."""

    def __init__(self):
        super().__init__("Telegram account is not available.")


class PlatformInvitationMembershipConflictError(Exception):
    """An invitation cannot restore blocked Platform membership."""

    status_code = 409
    code = "PLATFORM_MEMBERSHIP_CONFLICT"

    def __init__(self):
        super().__init__("Platform membership requires an authorized Platform operation.")


class PlatformInvitationMembershipUnavailableError(PermissionError):
    def __init__(self):
        super().__init__("Platform invitation membership is not available.")


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def has_verified_email(self, *, user_id: uuid.UUID, email: str) -> bool:
        from sqlalchemy import or_
        from database.models import ApiEmailAuthChallenge, EventLog

        if not isinstance(user_id, uuid.UUID) or not isinstance(email, str):
            return False
        normalized_email = email.strip().casefold()
        if not normalized_email or len(normalized_email) > 320:
            return False

        matching_proof = (
            select(UserAccount.id)
            .join(User, User.id == UserAccount.user_id)
            .join(EventLog, EventLog.user_id == UserAccount.user_id)
            .join(ApiEmailAuthChallenge,
                  ApiEmailAuthChallenge.id == EventLog.entity_id)
            .where(
                UserAccount.user_id == user_id,
                User.status == "active",
                UserAccount.platform == "email",
                UserAccount.email == normalized_email,
                UserAccount.platform_user_id == normalized_email,
                EventLog.event_type == "api_email_auth_succeeded",
                EventLog.entity_type == "api_email_auth_challenge",
                EventLog.platform == "api",
                ApiEmailAuthChallenge.email == normalized_email,
                ApiEmailAuthChallenge.status == "used",
                ApiEmailAuthChallenge.used_at.is_not(None),
                ApiEmailAuthChallenge.challenge_type.in_(("otp", "magic_link")),
                or_(
                    ApiEmailAuthChallenge.requested_user_id.is_(None),
                    ApiEmailAuthChallenge.requested_user_id == user_id,
                ),
            )
        )
        result = await self.session.execute(select(matching_proof.exists()))
        return result.scalar_one() is True

    async def list_team_identity_accounts(
        self, *, tenant_id: uuid.UUID, user_ids,
    ):
        from sqlalchemy import case, or_
        from database.models import ApiEmailAuthChallenge, EventLog

        ids = tuple(dict.fromkeys(user_ids))
        if not isinstance(tenant_id, uuid.UUID):
            raise ValueError("Invalid tenant identity scope.")
        if any(not isinstance(item, uuid.UUID) for item in ids):
            raise ValueError("Invalid user identity scope.")
        if not ids:
            return []

        membership = (
            select(UserRoleMapping.id)
            .where(
                UserRoleMapping.user_id == User.id,
                UserRoleMapping.tenant_id == tenant_id,
                UserRoleMapping.status == "active",
            )
            .correlate(User)
            .exists()
        )

        verified_email = (
            select(EventLog.id)
            .join(
                ApiEmailAuthChallenge,
                EventLog.entity_id == ApiEmailAuthChallenge.id,
            )
            .where(
                EventLog.user_id == UserAccount.user_id,
                EventLog.event_type == "api_email_auth_succeeded",
                EventLog.entity_type == "api_email_auth_challenge",
                EventLog.platform == "api",
                ApiEmailAuthChallenge.status == "used",
                ApiEmailAuthChallenge.used_at.is_not(None),
                ApiEmailAuthChallenge.challenge_type.in_(
                    ("otp", "magic_link")
                ),
                ApiEmailAuthChallenge.email == UserAccount.email,
                ApiEmailAuthChallenge.email
                == UserAccount.platform_user_id,
                UserAccount.platform == "email",
                or_(
                    ApiEmailAuthChallenge.requested_user_id.is_(None),
                    ApiEmailAuthChallenge.requested_user_id
                    == UserAccount.user_id,
                ),
            )
            .correlate(UserAccount)
            .exists()
        )

        statement = (
            select(
                UserAccount.user_id,
                UserAccount.display_name,
                case(
                    (verified_email, UserAccount.email),
                    else_=None,
                ).label("verified_email"),
            )
            .join(User, User.id == UserAccount.user_id)
            .where(
                UserAccount.user_id.in_(ids),
                User.status == "active",
                or_(User.tenant_id == tenant_id, membership),
            )
            .order_by(UserAccount.user_id, UserAccount.id)
        )
        result = await self.session.execute(statement)
        return [dict(row) for row in result.mappings().all()]


    async def get_by_platform_account(
        self,
        platform: str,
        platform_user_id: str,
    ) -> Optional[UserAccount]:
        stmt = select(UserAccount).where(
            UserAccount.platform == platform,
            UserAccount.platform_user_id == platform_user_id,
        )
        # Do not hide an unlinked account as "missing": onboarding would
        # otherwise create a second Platform user for the same Telegram identity.
        stmt = stmt.execution_options(populate_existing=True)
        with self.session.no_autoflush:
            result = await self.session.execute(stmt)
        account = result.scalar_one_or_none()
        if platform == "telegram" and account is not None and account.status != "active":
            raise PlatformTelegramAccountUnlinkedError()
        return account


    async def get_email_account_for_update(
        self,
        email: str,
    ) -> Optional[UserAccount]:
        normalized_email = (
            email.strip().casefold()
            if isinstance(email, str)
            else ""
        )

        if (
            not normalized_email
            or len(normalized_email) > 320
        ):
            raise ValueError(
                "Email address is invalid."
            )

        statement = (
            select(UserAccount)
            .where(
                UserAccount.platform
                == "email",
                UserAccount.platform_user_id
                == normalized_email,
            )
            .with_for_update()
        )

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()


    async def get_by_id(
        self,
        user_id: uuid.UUID,
    ) -> Optional[User]:
        return await self.session.get(
            User,
            user_id,
        )

    async def get_telegram_account_by_user_id(
        self,
        user_id: uuid.UUID,
    ) -> Optional[UserAccount]:
        stmt = select(UserAccount).where(
            UserAccount.user_id == user_id,
            UserAccount.platform == "telegram",
            UserAccount.status == "active",
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_language_code(
        self,
        user_id: uuid.UUID,
    ) -> str | None:
        result = await self.session.execute(
            select(User.language_code).where(
                User.id == user_id
            )
        )
        return result.scalar_one_or_none()

    async def get_public_platform_stats(self) -> dict[str, int]:
        countries_result = await self.session.execute(
            select(func.count(Country.id)).where(Country.is_active.is_(True))
        )
        cities_result = await self.session.execute(
            select(func.count(City.id)).where(City.is_active.is_(True))
        )
        users_result = await self.session.execute(
            select(func.count(User.id)).where(User.status == "active")
        )
        specialists_result = await self.session.execute(
            select(func.count(Specialist.id)).where(
                Specialist.status.in_(
                    [
                        "approved",
                        "pending_moderation",
                        "draft",
                    ]
                )
            )
        )

        return {
            "countries": int(countries_result.scalar_one() or 0),
            "cities": int(cities_result.scalar_one() or 0),
            "users": int(users_result.scalar_one() or 0),
            "specialists": int(specialists_result.scalar_one() or 0),
        }

    async def has_active_tenant_membership(
        self,
        *,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> bool:
        statement = (
            select(UserRoleMapping.id)
            .where(
                UserRoleMapping.user_id
                == user_id,
                UserRoleMapping.tenant_id
                == tenant_id,
                UserRoleMapping.status
                == "active",
            )
            .limit(1)
        )
        result = await self.session.execute(
            statement
        )
        return (
            result.scalar_one_or_none()
            is not None
        )

    async def list_active_roles(
        self,
        user_id: uuid.UUID,
        *,
        tenant_id: uuid.UUID | None = None,
    ) -> list[str]:
        conditions = [
            UserRoleMapping.user_id == user_id,
            UserRoleMapping.status == "active",
        ]

        if tenant_id is not None:
            conditions.append(
                UserRoleMapping.tenant_id
                == tenant_id
            )

        stmt = (
            select(UserRoleMapping.role)
            .where(*conditions)
            .distinct()
            .order_by(UserRoleMapping.role)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_active_role_scopes(
        self,
        *,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        roles: tuple[str, ...],
    ) -> list[RoleScope]:
        active_roles = tuple(
            sorted(set(roles))
        )

        if not active_roles:
            return []

        stmt = (
            select(RoleScope)
            .join(
                UserRoleMapping,
                RoleScope.user_role_id
                == UserRoleMapping.id,
            )
            .where(
                RoleScope.tenant_id == tenant_id,
                RoleScope.user_id == user_id,
                RoleScope.status == "active",
                RoleScope.role.in_(active_roles),
                UserRoleMapping.tenant_id
                == tenant_id,
                UserRoleMapping.user_id == user_id,
                UserRoleMapping.status == "active",
                UserRoleMapping.role
                == RoleScope.role,
            )
            .order_by(
                RoleScope.role,
                RoleScope.scope_type,
                RoleScope.id,
            )
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_active_permissions(
        self,
        *,
        roles: tuple[str, ...],
    ) -> list[str]:
        active_roles = tuple(
            sorted(set(roles))
        )

        if not active_roles:
            return []

        stmt = text(
            """
            SELECT DISTINCT
                rp.permission_code
            FROM role_permissions AS rp
            INNER JOIN permissions AS p
                ON p.code = rp.permission_code
            WHERE rp.role IN :roles
            ORDER BY rp.permission_code
            """
        ).bindparams(
            bindparam(
                "roles",
                expanding=True,
            )
        )
        result = await self.session.execute(
            stmt,
            {
                "roles": active_roles,
            },
        )
        return list(result.scalars().all())

    async def get_client_profile_row(
        self,
        user_id: uuid.UUID,
        language: str = "ru",
        *,
        telegram_platform_user_id: str | None = None,
    ):
        localized_city_name = {
            "ru": City.name_ru,
            "en": City.name_en,
            "pt": City.name_pt,
            "uk": City.name_uk,
            "pl": City.name_pl,
            "de": City.name_de,
            "nl": City.name_nl,
        }.get(
            language,
            City.name_ru,
        )

        result = await self.session.execute(
            select(
                User,
                func.coalesce(
                    localized_city_name,
                    City.name_ru,
                    City.name_en,
                    City.name_pt,
                    City.name,
                ).label("city_name"),
            )
            .outerjoin(City, City.id == User.city_id)
            .where(User.id == user_id)
            .execution_options(autoflush=False, populate_existing=True)
        )
        profile = result.one_or_none()
        if profile is None:
            return None

        # A Platform user may own several Telegram identities. Keep the profile
        # row independent of account cardinality; use an explicit identity when
        # supplied by the incoming Telegram context. User-only reads have no
        # primary-account heuristic and expose no arbitrary account's fields.
        statement = select(UserAccount).where(
            UserAccount.user_id == user_id,
            UserAccount.platform == "telegram",
            UserAccount.status == "active",
        )
        if telegram_platform_user_id is not None:
            statement = statement.where(
                UserAccount.platform_user_id == telegram_platform_user_id,
            )
        accounts = await self.session.execute(
            statement.limit(2).execution_options(
                autoflush=False, populate_existing=True,
            )
        )
        candidates = accounts.scalars().all()
        account = candidates[0] if len(candidates) == 1 else None
        user, city_name = profile
        return user, account, city_name

    async def ensure_invitation_tenant_membership(
        self, *, user_id: uuid.UUID, tenant_id: uuid.UUID,
    ) -> bool:
        """Return True only when a new client membership is inserted.

        The caller must first validate invitation, token and verified email,
        bind trusted recipient/tenant context for RLS, and use the same
        transaction for membership, grants, invitation and audit.
        This method never commits, rolls back or restores any role.
        """
        from sqlalchemy.dialects.postgresql import insert

        if not isinstance(user_id, uuid.UUID) or not isinstance(tenant_id, uuid.UUID):
            raise ValueError("Verified Platform user and tenant UUIDs are required.")
        if not self.session.in_transaction():
            raise ValueError("Platform invitation membership requires an active transaction.")

        # Lock the existing global user first, including the missing-role case.
        result = await self.session.execute(
            select(User.id, User.tenant_id, User.status)
            .where(User.id == user_id)
            .with_for_update()
        )
        user = result.mappings().one_or_none()
        if user is None or user["status"] != "active":
            raise PlatformInvitationMembershipUnavailableError()

        role_statement = (
            select(UserRoleMapping.id, UserRoleMapping.role, UserRoleMapping.status)
            .where(
                UserRoleMapping.user_id == user_id,
                UserRoleMapping.tenant_id == tenant_id,
            )
            .order_by(UserRoleMapping.id)
            .with_for_update()
        )
        result = await self.session.execute(role_statement)
        roles = result.mappings().all()
        if any(row["role"] == "client" and row["status"] != "active" for row in roles):
            raise PlatformInvitationMembershipConflictError()
        if user["tenant_id"] == tenant_id or any(row["status"] == "active" for row in roles):
            return False

        table = UserRoleMapping.__table__
        result = await self.session.execute(
            insert(table).values(
                id=uuid.uuid4(), user_id=user_id, tenant_id=tenant_id,
                role="client", status="active", granted_by=None,
                granted_at=func.now(),
            )
            .on_conflict_do_nothing(
                index_elements=[table.c.user_id, table.c.tenant_id, table.c.role],
            )
            .returning(table.c.id)
        )
        if result.scalar_one_or_none() is not None:
            return True

        # The physical unique key may have won a concurrent insert.
        # Re-read its status instead of updating or assuming it is active.
        result = await self.session.execute(role_statement)
        roles = result.mappings().all()
        if any(row["role"] == "client" and row["status"] != "active" for row in roles):
            raise PlatformInvitationMembershipConflictError()
        if any(row["role"] == "client" and row["status"] == "active" for row in roles):
            return False
        raise PlatformInvitationMembershipConflictError()

    async def ensure_active_role(
        self,
        *,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        role: str,
    ) -> bool:
        normalized_role = (
            role or ""
        ).strip().lower()

        if normalized_role in ADMINISTRATIVE_ROLES:
            raise PermissionError(
                "Administrative roles can only "
                "be changed through Root CLI."
            )

        result = await self.session.execute(
            select(UserRoleMapping).where(
                UserRoleMapping.user_id == user_id,
                UserRoleMapping.tenant_id == tenant_id,
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
            "uk": Profession.name_uk,
            "pl": Profession.name_pl,
            "de": Profession.name_de,
            "nl": Profession.name_nl,
        }.get(
            language,
            Profession.name_ru,
        )

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
                ProfessionalCabinet,
                ProfessionalCabinet.id
                == Specialist.active_professional_cabinet_id,
            )
            .join(
                Profession,
                Profession.id
                == ProfessionalCabinet.profession_id,
            )
            .where(
                Specialist.user_id == user_id,
                Specialist.status != "deleted",
                ProfessionalCabinet.tenant_id
                == Specialist.tenant_id,
                ProfessionalCabinet.specialist_id
                == Specialist.id,
                ProfessionalCabinet.is_active.is_(True),
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


    async def create_email_account(
        self,
        *,
        user_id: uuid.UUID,
        email: str,
        source: str,
    ) -> UserAccount:
        normalized_email = (
            email.strip().casefold()
            if isinstance(email, str)
            else ""
        )

        if (
            not normalized_email
            or len(normalized_email) > 320
        ):
            raise ValueError(
                "Email address is invalid."
            )

        user = await self.session.get(
            User,
            user_id,
        )

        if (
            user is None
            or user.id != user_id
            or user.tenant_id is None
            or user.status != "active"
        ):
            raise ValueError(
                "Email identity owner is "
                "not available."
            )

        account = UserAccount(
            user_id=user.id,
            platform="email",
            platform_user_id=(
                normalized_email
            ),
            email=normalized_email,
            source=source,
        )

        self.session.add(account)
        await self.session.flush()

        return account



    async def create_email_user_core(
        self,
        *,
        tenant_id: uuid.UUID,
        email: str,
        language_code: str,
        source: str,
    ) -> User:
        if not isinstance(
            tenant_id,
            uuid.UUID,
        ):
            raise ValueError(
                "Tenant ID is invalid."
            )

        normalized_email = (
            email.strip().casefold()
            if isinstance(email, str)
            else ""
        )

        if (
            not normalized_email
            or len(normalized_email) > 320
        ):
            raise ValueError(
                "Email address is invalid."
            )

        normalized_language = (
            language_code.strip().lower()
            if isinstance(
                language_code,
                str,
            )
            else ""
        ) or "ru"

        new_user = User(
            tenant_id=tenant_id,
            active_role=None,
            language_code=(
                normalized_language[:10]
            ),
            status="active",
        )

        self.session.add(new_user)
        await self.session.flush()

        account = UserAccount(
            user_id=new_user.id,
            platform="email",
            platform_user_id=(
                normalized_email
            ),
            email=normalized_email,
            source=source,
        )
        role = UserRoleMapping(
            user_id=new_user.id,
            tenant_id=tenant_id,
            role="client",
            status="active",
        )

        self.session.add(account)
        self.session.add(role)
        await self.session.flush()

        return new_user


    async def create_telegram_user_core(
        self,
        platform_user_id: str,
        username: Optional[str],
        first_name: Optional[str],
        last_name: Optional[str],
        language_code: str,
        role: str,
    ) -> uuid.UUID:
        normalized_role = (
            role or ""
        ).strip().lower()

        if normalized_role in ADMINISTRATIVE_ROLES:
            raise PermissionError(
                "Administrative roles can only "
                "be changed through Root CLI."
            )

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