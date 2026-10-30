"""API transport for the trusted in-process Telegram dispatcher."""
from dataclasses import dataclass, field
import json
import re
from uuid import UUID

import httpx
from pydantic import SecretStr

from api.platform_bot_context import bind_platform_bot_api_context
from services.construction.construction_api_client import (
    ConstructionApiClient,
    ConstructionApiClientError,
    ConstructionApiResult,
    MAX_RESPONSE_BYTES,
    PlatformApiClientSession,
)


def failure(status=502, code="invalid_api_response", request_id=None):
    return ConstructionApiClientError(
        status_code=status, code=code, request_id=request_id
    )


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate API response field.")
        result[key] = value
    return result


@dataclass(frozen=True)
class PlatformBotApiTokens:
    session: PlatformApiClientSession
    refresh_token: SecretStr = field(repr=False)
    expires_in: int


class PlatformBotApiClient:
    """Uses registered API routes; never calls repositories or domain services."""

    def __init__(self, *, application, context):
        bound = bind_platform_bot_api_context(application, context=context)
        self._http = httpx.AsyncClient(
            base_url="http://127.0.0.1",
            transport=httpx.ASGITransport(
                app=bound, raise_app_exceptions=False
            ),
            follow_redirects=False,
            timeout=10.0,
        )
        self.construction = ConstructionApiClient(http=self._http)

    async def __aenter__(self):
        await self._http.__aenter__()
        return self

    async def __aexit__(self, *args):
        await self._http.__aexit__(*args)

    async def _bootstrap(self, method, suffix, **kwargs):
        return await self._request(
            method, "/api/v1/platform/bot/" + suffix, **kwargs
        )

    async def _request(self, method, path, *, idempotency_key=None, **kwargs):
        headers = {"Accept": "application/json"}
        if idempotency_key is not None:
            if (
                not isinstance(idempotency_key, str)
                or not idempotency_key.strip() or len(idempotency_key) > 255
            ):
                raise ValueError("Idempotency key is invalid.")
            headers["Idempotency-Key"] = idempotency_key
        try:
            async with self._http.stream(
                method,
                path,
                headers=headers,
                follow_redirects=False,
                **kwargs,
            ) as response:
                if 300 <= response.status_code < 400:
                    raise failure(code="api_redirect_denied")

                parts, size = [], 0
                async for part in response.aiter_bytes():
                    size += len(part)
                    if size > MAX_RESPONSE_BYTES:
                        raise failure(code="api_response_too_large")
                    parts.append(part)

                try:
                    body = json.loads(
                        b"".join(parts), object_pairs_hook=unique_fields
                    )
                except (ValueError, UnicodeError):
                    raise failure() from None

                if not isinstance(body, dict):
                    raise failure()

                if not 200 <= response.status_code < 300:
                    error = body.get("error")
                    if not isinstance(error, dict):
                        raise failure()
                    code = error.get("code")
                    if (
                        not isinstance(code, str)
                        or not re.fullmatch(
                            r"[A-Za-z][A-Za-z0-9_.-]{0,95}", code
                        )
                    ):
                        raise failure()
                    request_id = error.get("request_id")
                    if (
                        not isinstance(request_id, str)
                        or not 1 <= len(request_id) <= 128
                    ):
                        request_id = None
                    raise failure(response.status_code, code, request_id)

                if (
                    set(body) != {"data", "meta", "request_id"}
                    or not isinstance(body["data"], (dict, list))
                    or not isinstance(body["meta"], dict)
                    or not isinstance(body["request_id"], str)
                    or not 1 <= len(body["request_id"]) <= 128
                ):
                    raise failure()

                return ConstructionApiResult(
                    body["data"], body["meta"], body["request_id"]
                )
        except httpx.TimeoutException:
            raise failure(504, "api_timeout") from None
        except httpx.HTTPError:
            raise failure(503, "api_unavailable") from None

    async def own_client_link_state(self, *, client_id, expected_user_id):
        from api.routes.construction_client_own_link_state import ClientOwnLinkStateResponse

        if not all(isinstance(value, UUID) for value in (client_id, expected_user_id)):
            raise ValueError("Own-link identifiers must be UUIDs.")
        result = await self._bootstrap(
            "GET", "client-links/" + str(client_id) + "/self",
            params={"expected_user_id": str(expected_user_id)},
        )
        try:
            item = ClientOwnLinkStateResponse.model_validate(result.data)
            if (
                result.meta != {"actor_user_id": str(expected_user_id)}
                or item.client_id != client_id
                or item.linked_user_id != expected_user_id
            ):
                raise ValueError("Own-link response context differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise failure() from None
        return ConstructionApiResult(
            item.model_dump(mode="json"), result.meta, result.request_id,
        )

    async def unlink_own_client(
        self, *, client_id, expected_user_id, expected_row_version,
    ):
        from api.routes.platform_bot_client_links import BotOwnerUnlinkRequest
        from api.routes.construction_client_unlink import ClientUnlinkResponse

        if not all(isinstance(value, UUID) for value in (client_id, expected_user_id)):
            raise ValueError("Own-link identifiers must be UUIDs.")
        body = BotOwnerUnlinkRequest(
            expected_user_id=expected_user_id,
            expected_row_version=expected_row_version,
        )
        result = await self._bootstrap(
            "POST", "client-links/" + str(client_id) + "/unlink",
            json=body.model_dump(mode="json"),
        )
        try:
            item = ClientUnlinkResponse.model_validate(result.data)
            if (
                result.meta != {"actor_user_id": str(expected_user_id)}
                or item.client_id != client_id or item.changed is not True
                or item.row_version != expected_row_version + 1
            ):
                raise ValueError("Own-unlink response context differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise failure() from None
        return ConstructionApiResult(
            item.model_dump(mode="json"), result.meta, result.request_id,
        )


    async def organizations(self, *, after=None, limit=10):
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("Organization limit must be between 1 and 20.")
        if after is not None and not isinstance(after, UUID):
            raise ValueError("Organization cursor must be a UUID.")

        params = {"limit": limit}
        if after is not None:
            params["cursor"] = str(after)
        result = await self._bootstrap(
            "GET", "organizations", params=params
        )

        try:
            if not isinstance(result.data, list) or len(result.data) > limit:
                raise ValueError()
            previous = after
            for row in result.data:
                if (
                    not isinstance(row, dict)
                    or set(row) != {"id", "name"}
                    or not isinstance(row["id"], str)
                    or not isinstance(row["name"], str)
                    or not row["name"]
                ):
                    raise ValueError()
                current = UUID(row["id"])
                if previous is not None and current <= previous:
                    raise ValueError()
                previous = current

            pagination = result.meta["pagination"]
            if (
                not isinstance(pagination, dict)
                or set(pagination) != {"next_cursor"}
            ):
                raise ValueError()
            cursor = pagination["next_cursor"]
            if cursor is not None:
                if (
                    not isinstance(cursor, str)
                    or len(result.data) != limit
                    or cursor != result.data[-1]["id"]
                ):
                    raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise failure() from None

        return result

    async def organization_creation_context(self):
        from api.routes.platform_organization_create import OrganizationCreationContext

        result = await self._bootstrap("GET", "organization-creation-context")
        try:
            context = OrganizationCreationContext.model_validate(result.data)
            if len(set(context.languages)) != len(context.languages):
                raise ValueError("Duplicate language.")
            if any(
                re.fullmatch(r"[A-Za-z0-9-]{2,10}", code) is None
                for code in context.languages
            ):
                raise ValueError("Invalid language.")
        except (ValueError, TypeError, KeyError):
            raise failure() from None
        return ConstructionApiResult(
            context.model_dump(mode="json"), result.meta, result.request_id,
        )

    async def create_organization(
        self, *, payload, expected_user_id, idempotency_key,
    ):
        from api.routes.platform_organization_create import (
            OrganizationCreateRequest, OrganizationCreateResponse,
        )
        if (
            not isinstance(expected_user_id, UUID)
            or not isinstance(payload, dict)
            or set(payload) != {"name", "language", "currency"}
        ):
            raise ValueError("Organization creation context is invalid.")
        body = OrganizationCreateRequest.model_validate(payload)
        result = await self._bootstrap(
            "POST", "organizations",
            idempotency_key=idempotency_key,
            json={
                **body.model_dump(mode="json"),
                "expected_creator_user_id": str(expected_user_id),
            },
        )
        try:
            item = OrganizationCreateResponse.model_validate(result.data)
            if (
                item.creator_user_id != expected_user_id
                or item.name != body.name.strip()
                or item.default_language != body.language
                or item.default_currency != body.currency
                or item.slug != "org-" + item.id.hex
            ):
                raise ValueError("Organization receipt context differs.")
        except (ValueError, TypeError, KeyError):
            raise failure() from None
        return ConstructionApiResult(
            item.model_dump(mode="json"), result.meta, result.request_id,
        )

    async def authenticate(self, *, tenant_id):
        if not isinstance(tenant_id, UUID):
            raise ValueError("Selected organization must be a UUID.")

        result = await self._bootstrap(
            "POST", "session", json={"tenant_id": str(tenant_id)}
        )
        data = result.data
        try:
            if (
                not isinstance(data, dict)
                or set(data) != {
                    "user_id", "tenant_id", "access_token",
                    "refresh_token", "token_type", "expires_in",
                }
                or data["tenant_id"] != str(tenant_id)
                or data["token_type"] != "bearer"
                or type(data["expires_in"]) is not int
                or data["expires_in"] <= 0
            ):
                raise ValueError()

            for name in ("access_token", "refresh_token"):
                token = data[name]
                if (
                    not isinstance(token, str)
                    or not 1 <= len(token) <= 16384
                    or any(char.isspace() for char in token)
                ):
                    raise ValueError()

            session = PlatformApiClientSession(
                user_id=UUID(data["user_id"]),
                tenant_id=tenant_id,
                access_token=SecretStr(data["access_token"]),
            )
            return PlatformBotApiTokens(
                session=session,
                refresh_token=SecretStr(data["refresh_token"]),
                expires_in=data["expires_in"],
            )
        except (KeyError, TypeError, ValueError):
            raise failure() from None

    async def refresh(self, *, tokens):
        if not isinstance(tokens, PlatformBotApiTokens):
            raise ValueError("Existing Platform credentials are required.")
        result = await self._request(
            "POST", "/api/v1/auth/refresh",
            json={"refresh_token": tokens.refresh_token.get_secret_value()},
        )
        data = result.data
        try:
            if (
                not isinstance(data, dict)
                or set(data) != {
                    "access_token", "refresh_token", "token_type", "expires_in"
                }
                or data["token_type"] != "bearer"
                or type(data["expires_in"]) is not int
                or data["expires_in"] <= 0
            ):
                raise ValueError()
            for name in ("access_token", "refresh_token"):
                value = data[name]
                if (
                    not isinstance(value, str)
                    or not 1 <= len(value) <= 16384
                    or any(char.isspace() for char in value)
                ):
                    raise ValueError()
            return PlatformBotApiTokens(
                session=PlatformApiClientSession(
                    user_id=tokens.session.user_id,
                    tenant_id=tokens.session.tenant_id,
                    access_token=SecretStr(data["access_token"]),
                ),
                refresh_token=SecretStr(data["refresh_token"]),
                expires_in=data["expires_in"],
            )
        except (KeyError, TypeError, ValueError):
            raise failure() from None
