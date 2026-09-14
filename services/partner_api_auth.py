from dataclasses import dataclass
from datetime import datetime
from ipaddress import (
    ip_address,
    ip_network,
)
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from api.security import ApiKeyCodec
from api.settings import ApiPartnerSettings
from database.repositories.partner_api import (
    PartnerApiRepository,
)
from services.partner_api import (
    PartnerApiScopeError,
    validate_api_key_scopes,
)


class PartnerApiAuthenticationError(
    PermissionError
):
    pass


@dataclass(frozen=True)
class PartnerApiPrincipal:
    tenant_id: UUID
    api_client_id: UUID
    api_key_id: UUID
    scopes: frozenset[str]


class PartnerApiAuthenticationService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: PartnerApiRepository,
        codec: ApiKeyCodec,
        environment: str,
    ) -> None:
        if environment not in {
            "sandbox",
            "production",
        }:
            raise ValueError(
                "Partner API environment is invalid."
            )

        self.session = session
        self.repository = repository
        self.codec = codec
        self.environment = environment

    @staticmethod
    def _ip_is_allowed(
        *,
        remote_ip: str | None,
        allowlist,
    ) -> bool:
        if not allowlist:
            return True

        if not remote_ip:
            return False

        try:
            address = ip_address(remote_ip)
            return any(
                address
                in ip_network(
                    str(network),
                    strict=False,
                )
                for network in allowlist
            )
        except ValueError:
            return False

    async def authenticate(
        self,
        *,
        api_key: str,
        remote_ip: str | None,
        now: datetime,
    ) -> PartnerApiPrincipal:
        if (
            now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise PartnerApiAuthenticationError(
                "API Key authentication failed."
            )

        try:
            key_prefix = (
                self.codec.extract_key_prefix(
                    api_key
                )
            )

            stored_key = await (
                self.repository
                .get_active_api_key(
                    key_prefix=key_prefix,
                    environment=self.environment,
                    now=now,
                )
            )

            if stored_key is None:
                raise PartnerApiAuthenticationError(
                    "API Key authentication failed."
                )

            if not self.codec.verify_api_key(
                api_key,
                stored_key.key_hash,
            ):
                raise PartnerApiAuthenticationError(
                    "API Key authentication failed."
                )

            if not self._ip_is_allowed(
                remote_ip=remote_ip,
                allowlist=(
                    stored_key.ip_allowlist
                ),
            ):
                raise PartnerApiAuthenticationError(
                    "API Key authentication failed."
                )

            stored_scopes = await (
                self.repository
                .list_api_key_scopes(
                    tenant_id=stored_key.tenant_id,
                    api_key_id=stored_key.id,
                )
            )
            validated_scopes = (
                validate_api_key_scopes(
                    stored_scopes
                )
            )

            await (
                self.repository.mark_api_key_used(
                    api_key=stored_key,
                    now=now,
                )
            )
            await self.session.commit()

            return PartnerApiPrincipal(
                tenant_id=stored_key.tenant_id,
                api_client_id=(
                    stored_key.api_client_id
                ),
                api_key_id=stored_key.id,
                scopes=frozenset(
                    validated_scopes
                ),
            )

        except PartnerApiAuthenticationError:
            await self.session.rollback()
            raise
        except (
            PartnerApiScopeError,
            ValueError,
        ) as exc:
            await self.session.rollback()
            raise PartnerApiAuthenticationError(
                "API Key authentication failed."
            ) from exc
        except Exception as exc:
            await self.session.rollback()
            raise PartnerApiAuthenticationError(
                "API Key authentication failed."
            ) from exc



def build_partner_api_authentication_service(
    session: AsyncSession,
) -> PartnerApiAuthenticationService:
    settings = ApiPartnerSettings.from_env()

    return PartnerApiAuthenticationService(
        session=session,
        repository=PartnerApiRepository(
            session
        ),
        codec=ApiKeyCodec(),
        environment=settings.environment,
    )
