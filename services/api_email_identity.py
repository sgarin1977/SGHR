from typing import Protocol
from uuid import UUID


class ApiEmailIdentityError(Exception):
    pass


class ApiEmailIdentityConflictError(
    ApiEmailIdentityError
):
    pass


class EmailIdentityRepository(Protocol):
    async def get_email_account_for_update(
        self,
        email: str,
    ) -> object | None:
        ...

    async def get_by_id(
        self,
        user_id: UUID,
    ) -> object | None:
        ...

    async def create_email_account(
        self,
        **kwargs,
    ) -> object:
        ...

    async def create_email_user_core(
        self,
        **kwargs,
    ) -> object:
        ...


class ApiEmailIdentityService:
    def __init__(
        self,
        *,
        user_repository: EmailIdentityRepository,
        default_tenant_id: UUID,
    ):
        if not isinstance(
            default_tenant_id,
            UUID,
        ):
            raise ValueError(
                "Default tenant ID is invalid."
            )

        self.user_repository = (
            user_repository
        )
        self.default_tenant_id = (
            default_tenant_id
        )

    @staticmethod
    def _require_active_user(
        user: object | None,
        *,
        expected_user_id: UUID,
        expected_tenant_id: UUID | None,
    ) -> object:
        if (
            user is None
            or user.id != expected_user_id
            or user.tenant_id is None
            or user.status != "active"
            or (
                expected_tenant_id
                is not None
                and user.tenant_id
                != expected_tenant_id
            )
        ):
            raise ApiEmailIdentityError(
                "Email identity owner is "
                "not available."
            )

        return user

    async def resolve_verified_email(
        self,
        *,
        email: str,
        requested_user_id: UUID | None,
        language_code: str,
        requested_tenant_id: UUID | None = None,
    ) -> object:
        normalized_email = (
            email.strip().casefold()
            if isinstance(email, str)
            else ""
        )

        if (
            not normalized_email
            or len(normalized_email) > 320
        ):
            raise ApiEmailIdentityError(
                "Email address is invalid."
            )

        if (
            requested_user_id is None
        ) != (
            requested_tenant_id is None
        ):
            raise ApiEmailIdentityError(
                "Email link actor scope is "
                "incomplete."
            )

        account = await (
            self.user_repository
            .get_email_account_for_update(
                normalized_email
            )
        )

        if account is not None:
            if (
                requested_user_id is not None
                and account.user_id
                != requested_user_id
            ):
                raise (
                    ApiEmailIdentityConflictError(
                        "Email identity is already "
                        "linked."
                    )
                )

            user = await (
                self.user_repository.get_by_id(
                    account.user_id
                )
            )

            return self._require_active_user(
                user,
                expected_user_id=(
                    account.user_id
                ),
                expected_tenant_id=(
                    requested_tenant_id
                ),
            )

        if requested_user_id is not None:
            user = await (
                self.user_repository.get_by_id(
                    requested_user_id
                )
            )
            user = self._require_active_user(
                user,
                expected_user_id=(
                    requested_user_id
                ),
                expected_tenant_id=(
                    requested_tenant_id
                ),
            )

            await (
                self.user_repository
                .create_email_account(
                    user_id=requested_user_id,
                    email=normalized_email,
                    source="api_email_link",
                )
            )
            return user

        return await (
            self.user_repository
            .create_email_user_core(
                tenant_id=self.default_tenant_id,
                email=normalized_email,
                language_code=language_code,
                source=(
                    "api_email_registration"
                ),
            )
        )
