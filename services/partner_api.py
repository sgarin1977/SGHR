from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from api.security import ApiKeyCodec
from api.settings import ApiPartnerSettings

from database.models import ApiClient
from database.repositories.partner_api import (
    PartnerApiRepository,
)



ALLOWED_API_KEY_SCOPES = frozenset(
    {
        "specialists.read",
        "specialists.search",
        "professional_cabinets.read",
        "services.read",
        "contact_requests.read",
        "contact_requests.write",
        "service_orders.read",
        "service_orders.write",
        "reviews.read",
        "files.read",
        "webhooks.read",
        "webhooks.write",
    }
)


class PartnerApiScopeError(ValueError):
    pass


def validate_api_key_scopes(
    scopes,
) -> tuple[str, ...]:
    validated = []
    seen = set()

    for value in scopes:
        if not isinstance(value, str):
            raise PartnerApiScopeError(
                "API Key scope is not allowed."
            )

        scope = value.strip()
        if scope not in ALLOWED_API_KEY_SCOPES:
            raise PartnerApiScopeError(
                "API Key scope is not allowed."
            )

        if scope not in seen:
            seen.add(scope)
            validated.append(scope)

    if not validated:
        raise PartnerApiScopeError(
            "At least one API Key scope is required."
        )

    return tuple(validated)


class PartnerApiOperationError(Exception):
    pass



class PartnerApiNotFoundError(LookupError):
    pass

@dataclass(frozen=True)
class ApiClientView:
    id: UUID
    owner_type: str
    owner_id: UUID | None
    name: str
    status: str
    metadata: dict[str, object]
    created_at: datetime
    updated_at: datetime



@dataclass(frozen=True)
class ApiClientPage:
    items: tuple[ApiClientView, ...]
    page: int
    has_next: bool



@dataclass(frozen=True)
class ApiKeyView:
    id: UUID
    api_client_id: UUID
    name: str
    key_prefix: str
    environment: str
    status: str
    expires_at: datetime | None
    last_used_at: datetime | None
    ip_allowlist: tuple[str, ...]
    created_at: datetime
    scopes: tuple[str, ...]


@dataclass(frozen=True)
class ApiKeyIssuedView(ApiKeyView):
    key: str


@dataclass(frozen=True)
class ApiKeyPage:
    items: tuple[ApiKeyView, ...]
    page: int
    has_next: bool


class PartnerApiService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: PartnerApiRepository,
        api_key_codec: object | None = None,
        environment: str | None = None,
        idempotency: object | None = None,
    ) -> None:
        self.session = session
        self.repository = repository
        self.api_key_codec = api_key_codec
        self.idempotency = idempotency

        if (
            environment is not None
            and environment not in {
                "sandbox",
                "production",
            }
        ):
            raise ValueError(
                "Partner API environment "
                "is invalid."
            )

        self.environment = environment

    @staticmethod
    def _to_api_client_view(
        api_client: ApiClient,
    ) -> ApiClientView:
        return ApiClientView(
            id=api_client.id,
            owner_type=api_client.owner_type,
            owner_id=api_client.owner_id,
            name=api_client.name,
            status=api_client.status,
            metadata=dict(
                api_client.extra_metadata
            ),
            created_at=api_client.created_at,
            updated_at=api_client.updated_at,
        )

    async def create_api_client(
        self,
        *,
        tenant_id: UUID,
        owner_type: str,
        owner_id: UUID | None,
        name: str,
        metadata: dict[str, object],
    ) -> ApiClientView:
        try:
            api_client = (
                await self.repository
                .create_api_client(
                    tenant_id=tenant_id,
                    owner_type=owner_type,
                    owner_id=owner_id,
                    name=name,
                    metadata=metadata,
                )
            )
            await self.session.commit()
        except Exception as exc:
            await self.session.rollback()
            raise PartnerApiOperationError(
                "API client operation failed."
            ) from exc

        return self._to_api_client_view(
            api_client
        )

    async def list_api_clients(
        self,
        *,
        tenant_id: UUID,
        page: int = 0,
        page_size: int = 20,
    ) -> ApiClientPage:
        normalized_page = max(
            0,
            int(page),
        )
        normalized_size = max(
            1,
            min(int(page_size), 100),
        )

        rows = await (
            self.repository.list_api_clients(
                tenant_id=tenant_id,
                limit=normalized_size + 1,
                offset=(
                    normalized_page
                    * normalized_size
                ),
            )
        )

        return ApiClientPage(
            items=tuple(
                self._to_api_client_view(row)
                for row in rows[
                    :normalized_size
                ]
            ),
            page=normalized_page,
            has_next=(
                len(rows) > normalized_size
            ),
        )

    async def get_api_client(
        self,
        *,
        tenant_id: UUID,
        api_client_id: UUID,
    ) -> ApiClientView:
        api_client = await (
            self.repository.get_api_client(
                tenant_id=tenant_id,
                api_client_id=api_client_id,
            )
        )

        if api_client is None:
            raise PartnerApiNotFoundError(
                "API client not found."
            )

        return self._to_api_client_view(
            api_client
        )

    @staticmethod
    def _to_api_key_view(
        api_key,
        scopes: tuple[str, ...] = (),
    ) -> ApiKeyView:
        return ApiKeyView(
            id=api_key.id,
            api_client_id=api_key.api_client_id,
            name=api_key.name,
            key_prefix=api_key.key_prefix,
            environment=api_key.environment,
            status=api_key.status,
            expires_at=api_key.expires_at,
            last_used_at=api_key.last_used_at,
            ip_allowlist=tuple(
                str(value)
                for value in (
                    api_key.ip_allowlist or ()
                )
            ),
            created_at=api_key.created_at,
            scopes=scopes,
        )

    async def update_api_client(
        self,
        *,
        tenant_id: UUID,
        api_client_id: UUID,
        name: str | None,
        status: str | None,
        metadata: dict[str, object] | None,
    ) -> ApiClientView:
        try:
            api_client = await (
                self.repository
                .get_api_client_for_update(
                    tenant_id=tenant_id,
                    api_client_id=api_client_id,
                )
            )

            if api_client is None:
                raise PartnerApiNotFoundError(
                    "API client not found."
                )

            updated = await (
                self.repository.update_api_client(
                    api_client=api_client,
                    name=(
                        name
                        if name is not None
                        else api_client.name
                    ),
                    status=(
                        status
                        if status is not None
                        else api_client.status
                    ),
                    metadata=(
                        metadata
                        if metadata is not None
                        else dict(
                            api_client.extra_metadata
                        )
                    ),
                )
            )

            await self.session.commit()
        except PartnerApiNotFoundError:
            await self.session.rollback()
            raise
        except Exception as exc:
            await self.session.rollback()
            raise PartnerApiOperationError(
                "API client update failed."
            ) from exc

        return self._to_api_client_view(updated)


    async def create_api_key(
        self,
        *,
        tenant_id: UUID,
        api_client_id: UUID,
        name: str,
        expires_at: datetime | None,
        ip_allowlist: list[str] | None,
        scopes: tuple[str, ...],
        actor_user_id: UUID | None = None,
        idempotency_key: str | None = None,
    ) -> ApiKeyIssuedView:
        if self.idempotency is not None:
            if (
                actor_user_id is None
                or idempotency_key is None
            ):
                raise ValueError(
                    "API key creation requires "
                    "an idempotency principal."
                )

            reservation = await (
                self.idempotency.reserve(
                    tenant_id=tenant_id,
                    principal_type="user",
                    principal_id=actor_user_id,
                    operation="api_key.create",
                    idempotency_key=(
                        idempotency_key
                    ),
                    payload={
                        "api_client_id": str(
                            api_client_id
                        ),
                        "name": name,
                        "expires_at": (
                            expires_at.isoformat()
                            if expires_at is not None
                            else None
                        ),
                        "ip_allowlist": (
                            list(ip_allowlist)
                            if ip_allowlist is not None
                            else None
                        ),
                        "scopes": list(scopes),
                    },
                )
            )

            if reservation.is_replay:
                replay = (
                    reservation.response_payload
                )
                if not isinstance(replay, dict):
                    raise ValueError(
                        "Stored API key response "
                        "is invalid."
                    )

                replay_expires_at = replay.get(
                    "expires_at"
                )
                replay_last_used_at = replay.get(
                    "last_used_at"
                )

                return ApiKeyIssuedView(
                    id=UUID(str(replay["id"])),
                    api_client_id=UUID(
                        str(
                            replay[
                                "api_client_id"
                            ]
                        )
                    ),
                    name=str(replay["name"]),
                    key_prefix=str(
                        replay["key_prefix"]
                    ),
                    environment=str(
                        replay["environment"]
                    ),
                    status=str(replay["status"]),
                    expires_at=(
                        datetime.fromisoformat(
                            str(replay_expires_at)
                        )
                        if replay_expires_at
                        else None
                    ),
                    last_used_at=(
                        datetime.fromisoformat(
                            str(replay_last_used_at)
                        )
                        if replay_last_used_at
                        else None
                    ),
                    ip_allowlist=tuple(
                        str(value)
                        for value in replay.get(
                            "ip_allowlist",
                            (),
                        )
                    ),
                    created_at=(
                        datetime.fromisoformat(
                            str(
                                replay[
                                    "created_at"
                                ]
                            )
                        )
                    ),
                    scopes=tuple(
                        str(value)
                        for value in replay[
                            "scopes"
                        ]
                    ),
                    key=str(replay["key"]),
                )

        try:
            if self.environment is None:
                raise RuntimeError(
                    "Partner API environment "
                    "is not configured."
                )

            now = datetime.now(UTC)
            effective_expires_at = (
                expires_at
                if expires_at is not None
                else now + timedelta(days=90)
            )

            if (
                effective_expires_at.tzinfo
                is None
                or effective_expires_at.utcoffset()
                is None
            ):
                raise ValueError(
                    "API key expiration must be "
                    "timezone-aware."
                )

            if effective_expires_at <= now:
                raise ValueError(
                    "API key expiration must be "
                    "in the future."
                )

            if (
                effective_expires_at
                > now + timedelta(days=365)
            ):
                raise ValueError(
                    "API key expiration cannot exceed "
                    "365 days."
                )

            validated_scopes = (
                validate_api_key_scopes(
                    scopes
                )
            )
            api_client = await (
                self.repository.get_api_client(
                    tenant_id=tenant_id,
                    api_client_id=api_client_id,
                )
            )

            if api_client is None:
                raise PartnerApiNotFoundError(
                    "API client not found."
                )

            if self.api_key_codec is None:
                raise RuntimeError(
                    "API key codec is not configured."
                )

            material = (
                self.api_key_codec.issue_api_key()
            )

            api_key = await (
                self.repository.create_api_key(
                    tenant_id=tenant_id,
                    api_client_id=api_client_id,
                    name=name,
                    key_prefix=material.key_prefix,
                    key_hash=material.key_hash,
                    environment=self.environment,
                    expires_at=(
                        effective_expires_at
                    ),
                    ip_allowlist=ip_allowlist,
                )
            )

            await (
                self.repository
                .create_api_key_scopes(
                    tenant_id=tenant_id,
                    api_key_id=api_key.id,
                    scopes=validated_scopes,
                )
            )

            if self.idempotency is not None:
                response_view = (
                    self._to_api_key_view(
                        api_key,
                        scopes=validated_scopes,
                    )
                )

                await self.idempotency.complete(
                    reservation=reservation,
                    response_status=201,
                    response_payload={
                        "id": str(response_view.id),
                        "api_client_id": str(
                            response_view.api_client_id
                        ),
                        "name": response_view.name,
                        "key_prefix": (
                            response_view.key_prefix
                        ),
                        "environment": (
                            response_view.environment
                        ),
                        "status": (
                            response_view.status
                        ),
                        "expires_at": (
                            response_view
                            .expires_at
                            .isoformat()
                            if (
                                response_view.expires_at
                                is not None
                            )
                            else None
                        ),
                        "last_used_at": (
                            response_view
                            .last_used_at
                            .isoformat()
                            if (
                                response_view.last_used_at
                                is not None
                            )
                            else None
                        ),
                        "ip_allowlist": list(
                            response_view.ip_allowlist
                        ),
                        "created_at": (
                            response_view
                            .created_at
                            .isoformat()
                        ),
                        "scopes": list(
                            response_view.scopes
                        ),
                        "key": material.key,
                    },
                )

            await self.session.commit()
        except PartnerApiNotFoundError:
            await self.session.rollback()
            raise
        except Exception as exc:
            await self.session.rollback()
            raise PartnerApiOperationError(
                "API key operation failed."
            ) from exc

        view = self._to_api_key_view(
            api_key,
            scopes=validated_scopes,
        )

        return ApiKeyIssuedView(
            id=view.id,
            api_client_id=view.api_client_id,
            name=view.name,
            key_prefix=view.key_prefix,
            environment=view.environment,
            status=view.status,
            expires_at=view.expires_at,
            last_used_at=view.last_used_at,
            ip_allowlist=view.ip_allowlist,
            created_at=view.created_at,
            scopes=view.scopes,
            key=material.key,
        )

    async def list_api_keys(
        self,
        *,
        tenant_id: UUID,
        api_client_id: UUID,
        page: int = 0,
        page_size: int = 20,
    ) -> ApiKeyPage:
        api_client = await (
            self.repository.get_api_client(
                tenant_id=tenant_id,
                api_client_id=api_client_id,
            )
        )

        if api_client is None:
            raise PartnerApiNotFoundError(
                "API client not found."
            )

        normalized_page = max(
            0,
            int(page),
        )
        normalized_size = max(
            1,
            min(int(page_size), 100),
        )

        rows = await (
            self.repository.list_api_keys(
                tenant_id=tenant_id,
                api_client_id=api_client_id,
                limit=normalized_size + 1,
                offset=(
                    normalized_page
                    * normalized_size
                ),
            )
        )

        items = []

        for row in rows[:normalized_size]:
            stored_scopes = await (
                self.repository
                .list_api_key_scopes(
                    tenant_id=tenant_id,
                    api_key_id=row.id,
                )
            )
            validated_scopes = (
                validate_api_key_scopes(
                    stored_scopes
                )
            )

            items.append(
                self._to_api_key_view(
                    row,
                    scopes=validated_scopes,
                )
            )

        return ApiKeyPage(
            items=tuple(items),
            page=normalized_page,
            has_next=(
                len(rows) > normalized_size
            ),
        )

    async def revoke_api_key(
        self,
        *,
        tenant_id: UUID,
        api_key_id: UUID,
    ) -> ApiKeyView:
        try:
            api_key = await (
                self.repository
                .get_api_key_for_update(
                    tenant_id=tenant_id,
                    api_key_id=api_key_id,
                )
            )

            if api_key is None:
                raise PartnerApiNotFoundError(
                    "API key not found."
                )

            api_key = await (
                self.repository.revoke_api_key(
                    api_key=api_key,
                )
            )
            await self.session.commit()
        except PartnerApiNotFoundError:
            await self.session.rollback()
            raise
        except Exception as exc:
            await self.session.rollback()
            raise PartnerApiOperationError(
                "API key operation failed."
            ) from exc

        return self._to_api_key_view(
            api_key
        )




def build_partner_api_service(
    session: AsyncSession,
    *,
    idempotency: object | None = None,
) -> PartnerApiService:
    settings = ApiPartnerSettings.from_env()

    return PartnerApiService(
        session=session,
        repository=PartnerApiRepository(
            session
        ),
        api_key_codec=ApiKeyCodec(),
        environment=settings.environment,
        idempotency=idempotency,
    )
