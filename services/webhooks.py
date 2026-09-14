from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID
import asyncio
import hashlib
import hmac
import json
import ipaddress
import secrets
import socket
from urllib.parse import urlsplit

import aiohttp

from cryptography.fernet import (
    Fernet,
    InvalidToken,
)
from services.api_idempotency import (
    ApiIdempotencyKeyReusedError,
)


@dataclass(frozen=True)
class WebhookAccessContext:
    tenant_id: UUID
    principal_type: str
    principal_id: UUID
    api_client_id: UUID | None
    grants: frozenset[str]


@dataclass(frozen=True)
class WebhookSecretMaterial:
    secret: str
    secret_ciphertext: bytes


class WebhookSecretCodec:
    def __init__(
        self,
        *,
        encryption_key: str,
    ) -> None:
        if not isinstance(encryption_key, str):
            raise ValueError(
                "Webhook encryption key is invalid."
            )

        normalized_key = encryption_key.strip()
        if not normalized_key:
            raise ValueError(
                "Webhook encryption key is missing."
            )

        try:
            self._fernet = Fernet(
                normalized_key.encode("ascii")
            )
        except (
            UnicodeEncodeError,
            ValueError,
        ) as exc:
            raise ValueError(
                "Webhook encryption key is invalid."
            ) from exc

    def issue_secret(
        self,
    ) -> WebhookSecretMaterial:
        secret = secrets.token_urlsafe(32)

        return WebhookSecretMaterial(
            secret=secret,
            secret_ciphertext=(
                self._fernet.encrypt(
                    secret.encode("utf-8")
                )
            ),
        )

    def decrypt(
        self,
        secret_ciphertext: bytes,
    ) -> str:
        try:
            plaintext = self._fernet.decrypt(
                secret_ciphertext
            )
            return plaintext.decode("utf-8")
        except (
            InvalidToken,
            UnicodeDecodeError,
            TypeError,
        ) as exc:
            raise ValueError(
                "Webhook secret could not be decrypted."
            ) from exc



WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS = 300
WEBHOOK_MAX_RETRIES = 5
WEBHOOK_MAX_TOTAL_ATTEMPTS = 6
WEBHOOK_RETRY_DELAYS_SECONDS = (
    60,
    5 * 60,
    30 * 60,
    2 * 60 * 60,
    12 * 60 * 60,
)
WEBHOOK_HTTP_TIMEOUT_SECONDS = 10


class WebhookSigner:
    @staticmethod
    def sign(
        *,
        secret: str,
        timestamp: int,
        raw_body: bytes,
    ) -> str:
        if not isinstance(raw_body, bytes):
            raise TypeError(
                "Webhook raw body must be bytes."
            )

        signing_payload = (
            str(timestamp).encode("ascii")
            + b"."
            + raw_body
        )
        digest = hmac.new(
            secret.encode("utf-8"),
            signing_payload,
            hashlib.sha256,
        ).hexdigest()

        return f"v1={digest}"

    def build_headers(
        self,
        *,
        webhook_id: str,
        secret: str,
        timestamp: int,
        raw_body: bytes,
    ) -> dict[str, str]:
        return {
            "X-SGHR-Webhook-Id": str(
                webhook_id
            ),
            "X-SGHR-Webhook-Timestamp": str(
                timestamp
            ),
            "X-SGHR-Webhook-Signature": (
                self.sign(
                    secret=secret,
                    timestamp=timestamp,
                    raw_body=raw_body,
                )
            ),
        }



class WebhookCallbackUrlError(ValueError):
    pass


@dataclass(frozen=True)
class WebhookResolvedTarget:
    callback_url: str
    hostname: str
    port: int
    addresses: tuple[str, ...]


@dataclass(frozen=True)
class WebhookPinnedHttpResponse:
    status_code: int


class _WebhookPinnedResolver(
    aiohttp.abc.AbstractResolver
):
    def __init__(
        self,
        target: WebhookResolvedTarget,
    ) -> None:
        self.target = target

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: int = socket.AF_INET,
    ) -> list[dict[str, object]]:
        normalized_host = host.rstrip(".").lower()

        if (
            normalized_host
            != self.target.hostname
            or int(port) != self.target.port
        ):
            raise OSError(
                "Webhook DNS target changed."
            )

        results = []
        for value in self.target.addresses:
            address = ipaddress.ip_address(value)
            address_family = (
                socket.AF_INET6
                if address.version == 6
                else socket.AF_INET
            )
            results.append(
                {
                    "hostname": self.target.hostname,
                    "host": str(address),
                    "port": self.target.port,
                    "family": address_family,
                    "proto": socket.IPPROTO_TCP,
                    "flags": 0,
                }
            )

        if not results:
            raise OSError(
                "Webhook target has no pinned addresses."
            )

        return results

    async def close(self) -> None:
        return None


class WebhookPinnedHttpClient:
    async def post_pinned(
        self,
        *,
        target: WebhookResolvedTarget,
        content: bytes,
        headers: dict[str, str],
        timeout: int,
        follow_redirects: bool,
    ) -> WebhookPinnedHttpResponse:
        if follow_redirects:
            raise ValueError(
                "Webhook redirects are forbidden."
            )

        resolver = _WebhookPinnedResolver(
            target
        )
        connector = aiohttp.TCPConnector(
            resolver=resolver,
            use_dns_cache=False,
        )
        request_timeout = aiohttp.ClientTimeout(
            total=timeout
        )

        async with aiohttp.ClientSession(
            connector=connector,
            trust_env=False,
        ) as session:
            async with session.post(
                target.callback_url,
                data=content,
                headers=headers,
                timeout=request_timeout,
                allow_redirects=False,
            ) as response:
                status_code = int(
                    response.status
                )

        return WebhookPinnedHttpResponse(
            status_code=status_code
        )


class WebhookCallbackUrlValidator:
    def __init__(
        self,
        *,
        resolver=None,
    ) -> None:
        self._resolver = (
            resolver
            if resolver is not None
            else self._resolve_hostname
        )

    @staticmethod
    async def _resolve_hostname(
        hostname: str,
    ) -> tuple[str, ...]:
        loop = asyncio.get_running_loop()

        try:
            records = await loop.getaddrinfo(
                hostname,
                None,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise WebhookCallbackUrlError(
                "Webhook hostname could not "
                "be resolved."
            ) from exc

        return tuple(
            sorted(
                {
                    record[4][0]
                    for record in records
                }
            )
        )

    @staticmethod
    def _require_public_ip(
        value: str,
    ) -> None:
        try:
            address = ipaddress.ip_address(
                value
            )
        except ValueError as exc:
            raise WebhookCallbackUrlError(
                "Webhook hostname resolved "
                "to an invalid address."
            ) from exc

        if not address.is_global:
            raise WebhookCallbackUrlError(
                "Webhook callback target "
                "is not public."
            )

    async def resolve_target(
        self,
        callback_url: str,
        *,
        environment: str,
    ) -> WebhookResolvedTarget:
        if not isinstance(callback_url, str):
            raise WebhookCallbackUrlError(
                "Webhook callback URL is invalid."
            )

        normalized_url = callback_url.strip()
        if not normalized_url:
            raise WebhookCallbackUrlError(
                "Webhook callback URL is missing."
            )

        try:
            parsed = urlsplit(normalized_url)
            parsed.port
        except ValueError as exc:
            raise WebhookCallbackUrlError(
                "Webhook callback URL is invalid."
            ) from exc

        scheme = parsed.scheme.lower()
        if environment == "production":
            if scheme != "https":
                raise WebhookCallbackUrlError(
                    "Production Webhook callback "
                    "must use HTTPS."
                )
        elif scheme not in {"http", "https"}:
            raise WebhookCallbackUrlError(
                "Webhook callback URL scheme "
                "is invalid."
            )

        if (
            parsed.username is not None
            or parsed.password is not None
        ):
            raise WebhookCallbackUrlError(
                "Webhook callback URL must not "
                "contain credentials."
            )

        hostname = parsed.hostname
        if hostname is None:
            raise WebhookCallbackUrlError(
                "Webhook callback hostname "
                "is missing."
            )

        hostname = hostname.rstrip(".").lower()
        if (
            hostname == "localhost"
            or hostname.endswith(".localhost")
        ):
            raise WebhookCallbackUrlError(
                "Localhost Webhook targets "
                "are forbidden."
            )

        try:
            literal_address = (
                ipaddress.ip_address(hostname)
            )
        except ValueError:
            addresses = await self._resolver(
                hostname
            )
        else:
            addresses = (
                str(literal_address),
            )

        if not addresses:
            raise WebhookCallbackUrlError(
                "Webhook hostname resolved "
                "to no addresses."
            )

        for address in addresses:
            self._require_public_ip(address)

        port = parsed.port
        if port is None:
            port = 443 if scheme == "https" else 80

        return WebhookResolvedTarget(
            callback_url=normalized_url,
            hostname=hostname,
            port=port,
            addresses=tuple(
                str(address)
                for address in addresses
            ),
        )

    async def validate(
        self,
        callback_url: str,
        *,
        environment: str,
    ) -> str:
        target = await self.resolve_target(
            callback_url,
            environment=environment,
        )
        return target.callback_url



ALLOWED_WEBHOOK_EVENT_TYPES = frozenset(
    {
        "contact_request.created",
        "contact_request.updated",
        "service_order.created",
        "service_order.confirmed",
        "service_order.completed",
        "service_order.cancelled",
        "review.created",
        "review.published",
        "professional_cabinet.updated",
        (
            "professional_cabinet."
            "availability_changed"
        ),
    }
)


class WebhookEventTypeError(ValueError):
    pass


def validate_webhook_event_types(
    event_types,
) -> tuple[str, ...]:
    normalized = []
    seen = set()

    for value in event_types:
        if not isinstance(value, str):
            raise WebhookEventTypeError(
                "Webhook event type is invalid."
            )

        event_type = value.strip()
        if (
            event_type
            not in ALLOWED_WEBHOOK_EVENT_TYPES
        ):
            raise WebhookEventTypeError(
                "Webhook event type is not allowed."
            )

        if event_type not in seen:
            seen.add(event_type)
            normalized.append(event_type)

    if not normalized:
        raise WebhookEventTypeError(
            "At least one Webhook event "
            "type is required."
        )

    return tuple(normalized)



class WebhookOperationError(Exception):
    pass


class WebhookEndpointStatusError(ValueError):
    pass


class WebhookEndpointNotFoundError(LookupError):
    pass


class WebhookDeliveryNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class WebhookEndpointIssuedView:
    id: UUID
    api_client_id: UUID
    callback_url: str
    status: str
    event_types: tuple[str, ...]
    created_at: datetime
    secret: str


@dataclass(frozen=True)
class WebhookEndpointView:
    id: UUID
    api_client_id: UUID
    callback_url: str
    status: str
    event_types: tuple[str, ...]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class WebhookEndpointPage:
    items: tuple[WebhookEndpointView, ...]
    page: int
    has_next: bool


@dataclass(frozen=True)
class WebhookDeliveryView:
    id: UUID
    webhook_event_id: UUID
    webhook_endpoint_id: UUID
    status: str
    attempt_count: int
    next_attempt_at: datetime | None
    response_status: int | None
    error_category: str | None
    delivered_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class WebhookDeliveryPage:
    items: tuple[WebhookDeliveryView, ...]
    page: int
    has_next: bool


WEBHOOK_MAX_PAYLOAD_BYTES = 256 * 1024


class WebhookPayloadError(ValueError):
    pass


class WebhookPayloadTooLargeError(
    WebhookPayloadError
):
    pass


class WebhookEventPublisher:
    def __init__(
        self,
        *,
        repository,
    ) -> None:
        self.repository = repository

    @staticmethod
    def _validate_payload(
        payload,
    ) -> dict:
        if not isinstance(payload, dict):
            raise WebhookPayloadError(
                "Webhook payload must be "
                "an object."
            )

        normalized_payload = dict(payload)

        try:
            serialized = json.dumps(
                normalized_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise WebhookPayloadError(
                "Webhook payload is not "
                "valid JSON."
            ) from exc

        if (
            len(serialized)
            > WEBHOOK_MAX_PAYLOAD_BYTES
        ):
            raise WebhookPayloadTooLargeError(
                "Webhook payload exceeds "
                "256 KB."
            )

        return normalized_payload

    async def publish(
        self,
        *,
        tenant_id: UUID,
        event_type: str,
        payload: dict,
    ):
        validated_event_type = (
            validate_webhook_event_types(
                (event_type,)
            )[0]
        )
        validated_payload = (
            self._validate_payload(payload)
        )

        event = await (
            self.repository.create_event(
                tenant_id=tenant_id,
                event_type=(
                    validated_event_type
                ),
                payload=validated_payload,
            )
        )

        endpoints = await (
            self.repository
            .list_active_subscribed_endpoints(
                tenant_id=tenant_id,
                event_type=(
                    validated_event_type
                ),
            )
        )

        await self.repository.create_deliveries(
            tenant_id=tenant_id,
            webhook_event_id=event.id,
            webhook_endpoint_ids=tuple(
                endpoint.id
                for endpoint in endpoints
            ),
            queued_at=datetime.now(UTC),
        )

        return event


def build_webhook_event_body(
    *,
    event_id: UUID,
    event_type: str,
    created_at: datetime,
    data: dict,
) -> bytes:
    validated_event_type = (
        validate_webhook_event_types(
            (event_type,)
        )[0]
    )

    if (
        created_at.tzinfo is None
        or created_at.utcoffset() is None
    ):
        raise WebhookPayloadError(
            "Webhook event timestamp must "
            "be timezone-aware."
        )

    normalized_data = (
        WebhookEventPublisher
        ._validate_payload(data)
    )
    created_at_utc = (
        created_at.astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z")
    )

    raw_body = json.dumps(
        {
            "id": str(event_id),
            "type": validated_event_type,
            "created_at": created_at_utc,
            "data": normalized_data,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

    if len(raw_body) > WEBHOOK_MAX_PAYLOAD_BYTES:
        raise WebhookPayloadTooLargeError(
            "Webhook payload exceeds 256 KB."
        )

    return raw_body


@dataclass(frozen=True)
class WebhookDeliveryAttemptResult:
    succeeded: bool
    status_code: int | None
    error_category: str | None


class WebhookDeliverySender:
    def __init__(
        self,
        *,
        http_client,
        callback_validator,
        secret_codec,
        signer,
        environment: str,
        clock=None,
    ) -> None:
        if environment not in {
            "sandbox",
            "production",
        }:
            raise ValueError(
                "Webhook environment is invalid."
            )

        if (
            environment == "production"
            and isinstance(
                callback_validator,
                WebhookCallbackUrlValidator,
            )
            and not callable(
                getattr(
                    http_client,
                    "post_pinned",
                    None,
                )
            )
        ):
            raise ValueError(
                "Production Webhook delivery requires "
                "a DNS-pinned HTTP transport."
            )

        self.http_client = http_client
        self.callback_validator = (
            callback_validator
        )
        self.secret_codec = secret_codec
        self.signer = signer
        self.environment = environment
        self.clock = (
            clock
            if clock is not None
            else lambda: datetime.now(UTC)
        )

    async def send(
        self,
        *,
        event,
        endpoint,
    ) -> WebhookDeliveryAttemptResult:
        try:
            pinned_target = None
            resolve_target = getattr(
                self.callback_validator,
                "resolve_target",
                None,
            )

            if (
                callable(resolve_target)
                and hasattr(
                    self.http_client,
                    "post_pinned",
                )
            ):
                pinned_target = await resolve_target(
                    endpoint.callback_url,
                    environment=self.environment,
                )
                callback_url = (
                    pinned_target.callback_url
                )
            else:
                callback_url = await (
                    self.callback_validator.validate(
                        endpoint.callback_url,
                        environment=self.environment,
                    )
                )

            secret = self.secret_codec.decrypt(
                endpoint.secret_ciphertext
            )
            raw_body = build_webhook_event_body(
                event_id=event.id,
                event_type=event.event_type,
                created_at=event.created_at,
                data=event.payload,
            )
            timestamp = int(
                self.clock().timestamp()
            )

            headers = {
                "Content-Type": (
                    "application/json"
                ),
            }
            headers.update(
                self.signer.build_headers(
                    webhook_id=str(event.id),
                    secret=secret,
                    timestamp=timestamp,
                    raw_body=raw_body,
                )
            )

            if pinned_target is not None:
                response = await (
                    self.http_client.post_pinned(
                        target=pinned_target,
                        content=raw_body,
                        headers=headers,
                        timeout=(
                            WEBHOOK_HTTP_TIMEOUT_SECONDS
                        ),
                        follow_redirects=False,
                    )
                )
            else:
                response = await (
                    self.http_client.post(
                        callback_url,
                        content=raw_body,
                        headers=headers,
                        timeout=(
                            WEBHOOK_HTTP_TIMEOUT_SECONDS
                        ),
                        follow_redirects=False,
                    )
                )
        except WebhookCallbackUrlError:
            return WebhookDeliveryAttemptResult(
                succeeded=False,
                status_code=None,
                error_category=(
                    "invalid_callback"
                ),
            )
        except Exception:
            return WebhookDeliveryAttemptResult(
                succeeded=False,
                status_code=None,
                error_category=(
                    "transport_error"
                ),
            )

        status_code = int(
            response.status_code
        )
        succeeded = (
            200 <= status_code < 300
        )

        return WebhookDeliveryAttemptResult(
            succeeded=succeeded,
            status_code=status_code,
            error_category=(
                None
                if succeeded
                else "http_error"
            ),
        )


def build_webhook_delivery_sender(
    *,
    secret_encryption_key: str,
    environment: str,
    clock=None,
) -> WebhookDeliverySender:
    return WebhookDeliverySender(
        http_client=WebhookPinnedHttpClient(),
        callback_validator=(
            WebhookCallbackUrlValidator()
        ),
        secret_codec=WebhookSecretCodec(
            encryption_key=(
                secret_encryption_key
            ),
        ),
        signer=WebhookSigner(),
        environment=environment,
        clock=clock,
    )


class WebhookDeliveryWorker:
    def __init__(
        self,
        *,
        session,
        repository,
        sender,
        clock=None,
    ) -> None:
        self.session = session
        self.repository = repository
        self.sender = sender
        self.clock = (
            clock
            if clock is not None
            else lambda: datetime.now(UTC)
        )

    async def run_once(self) -> bool:
        now = self.clock()

        try:
            context = await (
                self.repository
                .get_due_delivery_context_for_update(
                    now=now,
                )
            )

            if context is None:
                await self.session.rollback()
                return False

            (
                delivery,
                event,
                endpoint,
            ) = context

            attempt = await self.sender.send(
                event=event,
                endpoint=endpoint,
            )

            if not attempt.succeeded:
                completed_attempt_count = (
                    int(delivery.attempt_count) + 1
                )
                retry_at = None

                if (
                    completed_attempt_count
                    < WEBHOOK_MAX_TOTAL_ATTEMPTS
                ):
                    retry_index = (
                        completed_attempt_count - 1
                    )
                    retry_at = (
                        now
                        + timedelta(
                            seconds=(
                                WEBHOOK_RETRY_DELAYS_SECONDS[
                                    retry_index
                                ]
                            ),
                        )
                    )

                await (
                    self.repository
                    .mark_delivery_failed(
                        delivery=delivery,
                        endpoint=endpoint,
                        response_status=(
                            attempt.status_code
                        ),
                        error_category=(
                            attempt.error_category
                            or "delivery_failed"
                        ),
                        retry_at=retry_at,
                    )
                )
                await self.session.commit()
                return True

            await (
                self.repository
                .mark_delivery_succeeded(
                    delivery=delivery,
                    endpoint=endpoint,
                    response_status=(
                        attempt.status_code
                    ),
                    delivered_at=now,
                )
            )
            await self.session.commit()

            return True

        except Exception:
            await self.session.rollback()
            raise


class WebhookRetentionService:
    def __init__(
        self,
        *,
        session,
        repository,
        clock=None,
    ) -> None:
        self.session = session
        self.repository = repository
        self.clock = (
            clock
            if clock is not None
            else lambda: datetime.now(UTC)
        )

    async def purge_expired_events(
        self,
    ) -> int:
        try:
            deleted = await (
                self.repository
                .delete_expired_events(
                    cutoff=self.clock(),
                )
            )
            await self.session.commit()
            return deleted
        except Exception:
            await self.session.rollback()
            raise


class WebhookService:
    def __init__(
        self,
        *,
        session,
        repository,
        secret_codec,
        callback_validator,
        environment: str,
        idempotency,
    ) -> None:
        if environment not in {
            "sandbox",
            "production",
        }:
            raise ValueError(
                "Webhook environment is invalid."
            )

        self.session = session
        self.repository = repository
        self.secret_codec = secret_codec
        self.callback_validator = (
            callback_validator
        )
        self.environment = environment
        self.idempotency = idempotency

    async def create_endpoint(
        self,
        *,
        tenant_id: UUID,
        principal_type: str,
        principal_id: UUID,
        api_client_id: UUID,
        callback_url: str,
        event_types: tuple[str, ...],
        idempotency_key: str,
    ) -> WebhookEndpointIssuedView:
        if principal_type not in {
            "user",
            "api_key",
        }:
            raise ValueError(
                "Webhook principal type is invalid."
            )

        validated_event_types = (
            validate_webhook_event_types(
                event_types
            )
        )
        validated_callback_url = await (
            self.callback_validator.validate(
                callback_url,
                environment=self.environment,
            )
        )

        try:
            reservation = await (
                self.idempotency.reserve(
                    tenant_id=tenant_id,
                    principal_type=principal_type,
                    principal_id=principal_id,
                    operation=(
                        "webhook_endpoint.create"
                    ),
                    idempotency_key=(
                        idempotency_key
                    ),
                    payload={
                        "api_client_id": str(
                            api_client_id
                        ),
                        "callback_url": (
                            validated_callback_url
                        ),
                        "event_types": list(
                            validated_event_types
                        ),
                    },
                )
            )

            if reservation.is_replay:
                replay = (
                    reservation.response_payload
                )
                if not isinstance(replay, dict):
                    raise ValueError(
                        "Stored Webhook endpoint "
                        "response is invalid."
                    )

                return WebhookEndpointIssuedView(
                    id=UUID(str(replay["id"])),
                    api_client_id=UUID(
                        str(
                            replay[
                                "api_client_id"
                            ]
                        )
                    ),
                    callback_url=str(
                        replay["callback_url"]
                    ),
                    status=str(
                        replay["status"]
                    ),
                    event_types=tuple(
                        str(value)
                        for value in replay[
                            "event_types"
                        ]
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
                    secret=str(
                        replay["secret"]
                    ),
                )

            api_client = await (
                self.repository
                .get_active_api_client(
                    tenant_id=tenant_id,
                    api_client_id=api_client_id,
                )
            )
            if api_client is None:
                raise WebhookEndpointNotFoundError(
                    "Active API client "
                    "was not found."
                )

            secret_material = (
                self.secret_codec.issue_secret()
            )

            endpoint = await (
                self.repository.create_endpoint(
                    tenant_id=tenant_id,
                    api_client_id=api_client_id,
                    callback_url=(
                        validated_callback_url
                    ),
                    secret_ciphertext=(
                        secret_material
                        .secret_ciphertext
                    ),
                )
            )

            await (
                self.repository
                .create_subscriptions(
                    tenant_id=tenant_id,
                    webhook_endpoint_id=(
                        endpoint.id
                    ),
                    event_types=(
                        validated_event_types
                    ),
                )
            )

            result = WebhookEndpointIssuedView(
                id=endpoint.id,
                api_client_id=(
                    endpoint.api_client_id
                ),
                callback_url=(
                    endpoint.callback_url
                ),
                status=endpoint.status,
                event_types=(
                    validated_event_types
                ),
                created_at=endpoint.created_at,
                secret=secret_material.secret,
            )

            await self.idempotency.complete(
                reservation=reservation,
                response_status=201,
                response_payload={
                    "id": str(result.id),
                    "api_client_id": str(
                        result.api_client_id
                    ),
                    "callback_url": (
                        result.callback_url
                    ),
                    "status": result.status,
                    "event_types": list(
                        result.event_types
                    ),
                    "created_at": (
                        result.created_at
                        .isoformat()
                    ),
                    "secret": result.secret,
                },
            )

            await self.session.commit()
            return result

        except ApiIdempotencyKeyReusedError:
            await self.session.rollback()
            raise
        except WebhookEndpointNotFoundError:
            await self.session.rollback()
            raise
        except Exception as exc:
            await self.session.rollback()
            raise WebhookOperationError(
                "Webhook endpoint creation failed."
            ) from exc

    async def list_endpoints(
        self,
        *,
        tenant_id: UUID,
        api_client_id: UUID | None,
        page: int,
        page_size: int,
    ) -> WebhookEndpointPage:
        normalized_page = max(0, int(page))
        normalized_page_size = max(
            1,
            min(int(page_size), 100),
        )

        endpoint_rows = await (
            self.repository.list_endpoints(
                tenant_id=tenant_id,
                api_client_id=api_client_id,
                limit=normalized_page_size + 1,
                offset=(
                    normalized_page
                    * normalized_page_size
                ),
            )
        )

        has_next = (
            len(endpoint_rows)
            > normalized_page_size
        )
        visible_rows = tuple(
            endpoint_rows[
                :normalized_page_size
            ]
        )
        endpoint_ids = tuple(
            row.id
            for row in visible_rows
        )

        subscription_rows = await (
            self.repository
            .list_subscriptions_for_endpoints(
                tenant_id=tenant_id,
                endpoint_ids=endpoint_ids,
            )
        )

        events_by_endpoint: dict[
            UUID,
            list[str],
        ] = {
            endpoint_id: []
            for endpoint_id in endpoint_ids
        }

        for subscription in subscription_rows:
            event_types = events_by_endpoint.get(
                subscription.webhook_endpoint_id
            )
            if event_types is not None:
                event_types.append(
                    subscription.event_type
                )

        items = tuple(
            WebhookEndpointView(
                id=row.id,
                api_client_id=row.api_client_id,
                callback_url=row.callback_url,
                status=row.status,
                event_types=tuple(
                    events_by_endpoint[row.id]
                ),
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in visible_rows
        )

        return WebhookEndpointPage(
            items=items,
            page=normalized_page,
            has_next=has_next,
        )

    async def get_endpoint(
        self,
        *,
        tenant_id: UUID,
        endpoint_id: UUID,
        api_client_id: UUID | None,
    ) -> WebhookEndpointView:
        endpoint = await (
            self.repository.get_endpoint(
                tenant_id=tenant_id,
                endpoint_id=endpoint_id,
                api_client_id=api_client_id,
            )
        )

        if endpoint is None:
            raise WebhookEndpointNotFoundError(
                "Webhook endpoint was not found."
            )

        subscriptions = await (
            self.repository
            .list_subscriptions_for_endpoints(
                tenant_id=tenant_id,
                endpoint_ids=(
                    endpoint.id,
                ),
            )
        )

        return WebhookEndpointView(
            id=endpoint.id,
            api_client_id=(
                endpoint.api_client_id
            ),
            callback_url=(
                endpoint.callback_url
            ),
            status=endpoint.status,
            event_types=tuple(
                subscription.event_type
                for subscription in subscriptions
                if (
                    subscription
                    .webhook_endpoint_id
                    == endpoint.id
                )
            ),
            created_at=endpoint.created_at,
            updated_at=endpoint.updated_at,
        )

    async def update_endpoint(
        self,
        *,
        tenant_id: UUID,
        endpoint_id: UUID,
        api_client_id: UUID | None,
        callback_url: str | None,
        event_types: tuple[str, ...] | None,
        status: str | None,
    ) -> WebhookEndpointView:
        try:
            endpoint = await (
                self.repository
                .get_endpoint_for_update(
                    tenant_id=tenant_id,
                    endpoint_id=endpoint_id,
                    api_client_id=api_client_id,
                )
            )

            if endpoint is None:
                raise (
                    WebhookEndpointNotFoundError(
                        "Webhook endpoint "
                        "was not found."
                    )
                )

            resolved_callback_url = (
                endpoint.callback_url
            )
            if callback_url is not None:
                resolved_callback_url = await (
                    self.callback_validator.validate(
                        callback_url,
                        environment=self.environment,
                    )
                )

            resolved_status = endpoint.status
            if status is not None:
                resolved_status = (
                    status.strip().lower()
                    if isinstance(status, str)
                    else ""
                )
                if resolved_status not in {
                    "active",
                    "suspended",
                    "disabled",
                }:
                    raise (
                        WebhookEndpointStatusError(
                            "Webhook endpoint "
                            "status is invalid."
                        )
                    )

            if event_types is None:
                subscriptions = await (
                    self.repository
                    .list_subscriptions_for_endpoints(
                        tenant_id=tenant_id,
                        endpoint_ids=(
                            endpoint.id,
                        ),
                    )
                )
                resolved_event_types = tuple(
                    subscription.event_type
                    for subscription
                    in subscriptions
                    if (
                        subscription
                        .webhook_endpoint_id
                        == endpoint.id
                    )
                )
            else:
                resolved_event_types = (
                    validate_webhook_event_types(
                        event_types
                    )
                )

            endpoint = await (
                self.repository.update_endpoint(
                    endpoint=endpoint,
                    callback_url=(
                        resolved_callback_url
                    ),
                    status=resolved_status,
                )
            )

            if event_types is not None:
                await (
                    self.repository
                    .replace_subscriptions(
                        tenant_id=tenant_id,
                        webhook_endpoint_id=(
                            endpoint.id
                        ),
                        event_types=(
                            resolved_event_types
                        ),
                    )
                )

            await self.session.commit()

            return WebhookEndpointView(
                id=endpoint.id,
                api_client_id=(
                    endpoint.api_client_id
                ),
                callback_url=(
                    endpoint.callback_url
                ),
                status=endpoint.status,
                event_types=(
                    resolved_event_types
                ),
                created_at=endpoint.created_at,
                updated_at=endpoint.updated_at,
            )

        except (
            WebhookCallbackUrlError,
            WebhookEndpointNotFoundError,
            WebhookEndpointStatusError,
            WebhookEventTypeError,
        ):
            await self.session.rollback()
            raise
        except Exception as exc:
            await self.session.rollback()
            raise WebhookOperationError(
                "Webhook endpoint update failed."
            ) from exc

    async def list_deliveries(
        self,
        *,
        tenant_id: UUID,
        api_client_id: UUID | None,
        page: int,
        page_size: int,
    ) -> WebhookDeliveryPage:
        normalized_page = max(0, int(page))
        normalized_page_size = max(
            1,
            min(int(page_size), 100),
        )

        rows = await (
            self.repository.list_deliveries(
                tenant_id=tenant_id,
                api_client_id=api_client_id,
                limit=normalized_page_size + 1,
                offset=(
                    normalized_page
                    * normalized_page_size
                ),
            )
        )

        has_next = (
            len(rows) > normalized_page_size
        )
        visible_rows = rows[
            :normalized_page_size
        ]

        items = tuple(
            WebhookDeliveryView(
                id=row.id,
                webhook_event_id=(
                    row.webhook_event_id
                ),
                webhook_endpoint_id=(
                    row.webhook_endpoint_id
                ),
                status=row.status,
                attempt_count=row.attempt_count,
                next_attempt_at=(
                    row.next_attempt_at
                ),
                response_status=(
                    row.response_status
                ),
                error_category=(
                    row.error_category
                ),
                delivered_at=row.delivered_at,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in visible_rows
        )

        return WebhookDeliveryPage(
            items=items,
            page=normalized_page,
            has_next=has_next,
        )

    async def redeliver_delivery(
        self,
        *,
        tenant_id: UUID,
        principal_type: str,
        principal_id: UUID,
        api_client_id: UUID | None,
        delivery_id: UUID,
        idempotency_key: str,
    ) -> WebhookDeliveryView:
        if principal_type not in {
            "user",
            "api_key",
        }:
            raise ValueError(
                "Webhook principal type is invalid."
            )

        try:
            reservation = await (
                self.idempotency.reserve(
                    tenant_id=tenant_id,
                    principal_type=principal_type,
                    principal_id=principal_id,
                    operation=(
                        "webhook_delivery.redeliver"
                    ),
                    idempotency_key=idempotency_key,
                    payload={
                        "delivery_id": str(
                            delivery_id
                        ),
                    },
                )
            )

            if reservation.is_replay:
                payload = (
                    reservation.response_payload
                )
                if not isinstance(payload, dict):
                    raise ValueError(
                        "Stored Webhook redelivery "
                        "response is invalid."
                    )

                def parse_optional_datetime(
                    value,
                ):
                    if value is None:
                        return None
                    return datetime.fromisoformat(
                        str(value)
                    )

                return WebhookDeliveryView(
                    id=UUID(str(payload["id"])),
                    webhook_event_id=UUID(
                        str(
                            payload[
                                "webhook_event_id"
                            ]
                        )
                    ),
                    webhook_endpoint_id=UUID(
                        str(
                            payload[
                                "webhook_endpoint_id"
                            ]
                        )
                    ),
                    status=str(payload["status"]),
                    attempt_count=int(
                        payload["attempt_count"]
                    ),
                    next_attempt_at=(
                        parse_optional_datetime(
                            payload[
                                "next_attempt_at"
                            ]
                        )
                    ),
                    response_status=(
                        int(
                            payload[
                                "response_status"
                            ]
                        )
                        if payload[
                            "response_status"
                        ] is not None
                        else None
                    ),
                    error_category=(
                        str(
                            payload[
                                "error_category"
                            ]
                        )
                        if payload[
                            "error_category"
                        ] is not None
                        else None
                    ),
                    delivered_at=(
                        parse_optional_datetime(
                            payload[
                                "delivered_at"
                            ]
                        )
                    ),
                    created_at=datetime.fromisoformat(
                        str(payload["created_at"])
                    ),
                    updated_at=datetime.fromisoformat(
                        str(payload["updated_at"])
                    ),
                )

            delivery = await (
                self.repository
                .get_delivery_for_update(
                    tenant_id=tenant_id,
                    delivery_id=delivery_id,
                    api_client_id=api_client_id,
                )
            )

            if delivery is None:
                raise WebhookDeliveryNotFoundError(
                    "Webhook delivery was not found."
                )

            delivery = await (
                self.repository.requeue_delivery(
                    delivery=delivery,
                    queued_at=datetime.now(UTC),
                )
            )

            result = WebhookDeliveryView(
                id=delivery.id,
                webhook_event_id=(
                    delivery.webhook_event_id
                ),
                webhook_endpoint_id=(
                    delivery.webhook_endpoint_id
                ),
                status=delivery.status,
                attempt_count=(
                    delivery.attempt_count
                ),
                next_attempt_at=(
                    delivery.next_attempt_at
                ),
                response_status=(
                    delivery.response_status
                ),
                error_category=(
                    delivery.error_category
                ),
                delivered_at=(
                    delivery.delivered_at
                ),
                created_at=delivery.created_at,
                updated_at=delivery.updated_at,
            )

            response_payload = {
                "id": str(result.id),
                "webhook_event_id": str(
                    result.webhook_event_id
                ),
                "webhook_endpoint_id": str(
                    result.webhook_endpoint_id
                ),
                "status": result.status,
                "attempt_count": (
                    result.attempt_count
                ),
                "next_attempt_at": (
                    result.next_attempt_at
                    .isoformat()
                    if (
                        result.next_attempt_at
                        is not None
                    )
                    else None
                ),
                "response_status": (
                    result.response_status
                ),
                "error_category": (
                    result.error_category
                ),
                "delivered_at": (
                    result.delivered_at.isoformat()
                    if result.delivered_at
                    is not None
                    else None
                ),
                "created_at": (
                    result.created_at.isoformat()
                ),
                "updated_at": (
                    result.updated_at.isoformat()
                ),
            }

            await self.idempotency.complete(
                reservation=reservation,
                response_status=200,
                response_payload=(
                    response_payload
                ),
            )
            await self.session.commit()

            return result

        except (
            ApiIdempotencyKeyReusedError,
            WebhookDeliveryNotFoundError,
        ):
            await self.session.rollback()
            raise
        except Exception as exc:
            await self.session.rollback()
            raise WebhookOperationError(
                "Webhook redelivery failed."
            ) from exc

