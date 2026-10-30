"""Construction API transport. Credentials come from an existing Platform session.

This client neither authenticates Telegram users nor queries repositories. API
authorization, validation, scope and pagination remain on the server.
"""
from dataclasses import dataclass, field
from ipaddress import ip_address
import json
import re
from uuid import UUID

import httpx
from pydantic import SecretStr


MAX_RESPONSE_BYTES = 1024 * 1024


class ConstructionApiClientError(Exception):
    def __init__(self, *, status_code, code, request_id=None):
        # No server message/body, Authorization header or token is retained.
        super().__init__(f'Construction API request failed ({status_code}, {code}).')
        self.status_code = status_code
        self.code = code
        self.request_id = request_id


@dataclass(frozen=True)
class PlatformApiClientSession:
    # Metadata binds the UI to its session; the server still verifies the JWT.
    user_id: UUID
    tenant_id: UUID
    access_token: SecretStr = field(repr=False)

    def __post_init__(self):
        if not isinstance(self.user_id, UUID) or not isinstance(self.tenant_id, UUID):
            raise ValueError('Platform session UUIDs are required.')
        if not isinstance(self.access_token, SecretStr):
            raise ValueError('Platform access token must be a secret value.')
        token = self.access_token.get_secret_value()
        if not token or len(token) > 16384 or any(char.isspace() for char in token):
            raise ValueError('Platform access token is invalid.')


@dataclass(frozen=True)
class ConstructionApiResult:
    data: dict | list | None
    meta: dict
    request_id: str


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate response field.')
        result[key] = value
    return result


def _response_error(status=502, code='invalid_api_response', request_id=None):
    return ConstructionApiClientError(status_code=status, code=code, request_id=request_id)


class ConstructionApiClient:
    def __init__(self, *, http: httpx.AsyncClient):
        # The caller owns this client and its connection lifetime. No endpoint
        # origin, redirect destination or tenant is accepted from Telegram input.
        url = http.base_url
        try:
            loopback = url.host == 'localhost' or ip_address(url.host).is_loopback
        except ValueError:
            loopback = False
        if (url.scheme not in ('http', 'https') or not url.host
                or (url.scheme == 'http' and not loopback)
                or url.username or url.password or url.query or url.fragment or url.path != '/'):
            raise ValueError('API origin requires HTTPS, or HTTP on loopback, without credentials or a path.')
        self.http = http

    async def _get(self, path, *, session, params=None):
        return await self._request("GET", path, session=session, params=params)

    async def _request(self, method, path, *, session, params=None,
                       json_body=None, idempotency_key=None, allow_null=False):
        if type(allow_null) is not bool or (
            allow_null and (method, path) not in {
                ("GET", "organization/company-profile"),
                ("GET", "construction/settings/company"),
            }
        ):
            raise ValueError("Nullable response is not supported for this operation.")
        if not isinstance(session, PlatformApiClientSession):
            raise ValueError('A Platform API session is required.')
        headers = {
            'Authorization': 'Bearer ' + session.access_token.get_secret_value(),
            'Accept': 'application/json',
        }
        if idempotency_key is not None:
            headers['Idempotency-Key'] = idempotency_key
        try:
            async with self.http.stream(method, '/api/v1/' + path, params=params,
                    json=json_body, headers=headers,
                    follow_redirects=False, timeout=10.0) as response:
                # Credentials are never forwarded to a redirect target.
                if 300 <= response.status_code < 400:
                    raise _response_error(code='api_redirect_denied')
                parts, size = [], 0
                async for part in response.aiter_bytes():
                    size += len(part)
                    if size > MAX_RESPONSE_BYTES:
                        raise _response_error(code='api_response_too_large')
                    parts.append(part)
                try:
                    body = json.loads(b''.join(parts), object_pairs_hook=_object)
                except (ValueError, UnicodeError):
                    raise _response_error() from None
                if not isinstance(body, dict):
                    raise _response_error()
                if not 200 <= response.status_code < 300:
                    error = body.get('error')
                    if not isinstance(error, dict):
                        raise _response_error()
                    code = error.get('code')
                    request_id = error.get('request_id')
                    if not isinstance(code, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,95}', code):
                        raise _response_error()
                    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
                        request_id = None
                    raise _response_error(response.status_code, code, request_id)
                if (set(body) != {'data', 'meta', 'request_id'}
                        or (not isinstance(body['data'], (dict, list))
                            and not (allow_null and body['data'] is None))
                        or not isinstance(body['meta'], dict)
                        or not isinstance(body['request_id'], str) or not 1 <= len(body['request_id']) <= 128):
                    raise _response_error()
                return ConstructionApiResult(body['data'], body['meta'], body['request_id'])
        except httpx.TimeoutException:
            raise _response_error(504, 'api_timeout') from None
        except httpx.HTTPError:
            raise _response_error(503, 'api_unavailable') from None

    @staticmethod
    def _entity(result, *, session, entity_id=None):
        data = result.data
        if (not isinstance(data, dict) or data.get('tenant_id') != str(session.tenant_id)
                or (entity_id is not None and data.get('id') != str(entity_id))):
            raise _response_error()
        return result

    @staticmethod
    def _company_identifier_response(result, *, many):
        from api.schemas import (
            CompanyIdentifierListResponse, CompanyIdentifierResponse,
        )
        try:
            schema = CompanyIdentifierListResponse if many else CompanyIdentifierResponse
            response = schema.model_validate({
                "data": result.data, "meta": result.meta,
                "request_id": result.request_id,
            })
            items = response.data if many else [response.data]
            raw_items = result.data if many else [result.data]
            if many and not isinstance(result.data, list):
                raise ValueError("Identifier list differs.")
            seen = set()
            for item, raw in zip(items, raw_items):
                if (
                    item.id in seen
                    or type(raw["is_primary"]) is not bool
                    or len(item.country_code) != 2
                    or not item.country_code.isascii()
                    or not item.country_code.isalpha()
                    or item.country_code != item.country_code.upper()
                    or not item.identifier_type.strip()
                    or len(item.identifier_type) > 50
                    or not item.identifier_value.strip()
                    or len(item.identifier_value) > 100
                    or item.created_at.tzinfo is None
                    or item.created_at.utcoffset() is None
                    or item.updated_at.tzinfo is None
                    or item.updated_at.utcoffset() is None
                ):
                    raise ValueError("Identifier response fields differ.")
                seen.add(item.id)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return ConstructionApiResult(
            response.model_dump(mode="json")["data"],
            response.meta.model_dump(mode="json"), result.request_id,
        )

    @staticmethod
    def _company_bank_response(result, *, many, account_id=None):
        from api.schemas import (
            CompanyBankAccountListResponse, CompanyBankAccountResponse,
        )
        try:
            schema = CompanyBankAccountListResponse if many else CompanyBankAccountResponse
            response = schema.model_validate({
                "data": result.data, "meta": result.meta,
                "request_id": result.request_id,
            })
            if many and not isinstance(result.data, list):
                raise ValueError("Bank account list differs.")
            items = response.data if many else [response.data]
            raw_items = result.data if many else [result.data]
            seen = set()
            for item, raw in zip(items, raw_items):
                if (
                    item.id in seen
                    or (account_id is not None and item.id != account_id)
                    or type(raw["row_version"]) is not int
                    or item.row_version <= 0
                    or any(type(raw[key]) is not bool for key in (
                        "is_primary", "is_active", "show_in_documents",
                    ))
                    or not item.account_holder_name.strip()
                    or len(item.account_holder_name) > 255
                    or not (item.iban or item.account_number)
                    or len(item.currency) != 3
                    or not item.currency.isascii()
                    or not item.currency.isalpha()
                    or item.currency != item.currency.upper()
                    or item.created_at.tzinfo is None
                    or item.created_at.utcoffset() is None
                    or item.updated_at.tzinfo is None
                    or item.updated_at.utcoffset() is None
                ):
                    raise ValueError("Bank account response fields differ.")
                seen.add(item.id)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return ConstructionApiResult(
            response.model_dump(mode="json")["data"],
            response.meta.model_dump(mode="json"), result.request_id,
        )

    async def company_bank_accounts(self, *, session):
        result = await self._get(
            "organization/company-profile/bank-accounts", session=session,
        )
        return self._company_bank_response(result, many=True)

    async def mutate_company_bank_account(
        self, *, session, operation, payload, account_id=None,
    ):
        from api.schemas import (
            CompanyBankAccountCreateRequest,
            CompanyBankAccountUpdateRequest,
            CompanyBankAccountDeactivateRequest,
        )
        text_fields = {
            "label", "account_holder_name", "bank_name", "country_code",
            "iban", "bic_swift", "account_number", "currency",
        }
        flags = {"is_primary", "show_in_documents"}
        fields = text_fields | flags
        if not isinstance(payload, dict):
            raise ValueError("Bank account payload must be an object.")
        if any(
            key in text_fields and value is not None and not isinstance(value, str)
            for key, value in payload.items()
        ) or any(
            key in flags and type(value) is not bool
            for key, value in payload.items()
        ):
            raise ValueError("Bank account field types differ.")
        for key in ("account_holder_name", "currency"):
            if key in payload and (
                not isinstance(payload[key], str) or not payload[key].strip()
            ):
                raise ValueError("Required bank account field is empty.")

        path = "organization/company-profile/bank-accounts"
        if operation == "create":
            if (
                account_id is not None or not set(payload) <= fields
                or not {"account_holder_name", "currency"} <= set(payload)
                or not (
                    isinstance(payload.get("iban"), str) and payload["iban"].strip()
                    or isinstance(payload.get("account_number"), str)
                    and payload["account_number"].strip()
                )
            ):
                raise ValueError("Bank account creation payload differs.")
            request = CompanyBankAccountCreateRequest.model_validate(payload)
            method = "POST"
        elif operation in ("update", "deactivate"):
            if (
                not isinstance(account_id, UUID)
                or type(payload.get("expected_row_version")) is not int
                or payload["expected_row_version"] <= 0
            ):
                raise ValueError("Bank account target/version differs.")
            path += "/" + str(account_id)
            if operation == "update":
                if (
                    not set(payload) <= fields | {"expected_row_version"}
                    or not set(payload) & fields
                ):
                    raise ValueError("Bank account update fields differ.")
                request = CompanyBankAccountUpdateRequest.model_validate(payload)
                method = "PATCH"
            else:
                if set(payload) != {"expected_row_version"}:
                    raise ValueError("Bank deactivation payload differs.")
                request = CompanyBankAccountDeactivateRequest.model_validate(payload)
                path += "/deactivate"
                method = "POST"
        else:
            raise ValueError("Unknown bank account operation.")

        result = await self._request(
            method, path, session=session,
            json_body=request.model_dump(mode="json", exclude_unset=True),
        )
        result = self._company_bank_response(
            result, many=False, account_id=account_id,
        )
        if (
            operation == "deactivate" and result.data["is_active"] is not False
            or operation == "create" and result.data["is_active"] is not True
        ):
            raise _response_error()
        return result

    async def company_identifiers(self, *, session):
        result = await self._get(
            "organization/company-profile/identifiers", session=session,
        )
        return self._company_identifier_response(result, many=True)

    async def mutate_company_identifier(
        self, *, session, operation, identifier_id=None, payload=None,
    ):
        from api.schemas import (
            CompanyIdentifierCreateRequest, CompanyIdentifierDeleteResponse,
        )

        path = "organization/company-profile/identifiers"
        if operation == "create":
            if identifier_id is not None:
                raise ValueError("Creation must not have a target identifier.")
            method = "POST"
        elif operation in ("replace", "delete"):
            if not isinstance(identifier_id, UUID):
                raise ValueError("Identifier UUID is required.")
            path += "/" + str(identifier_id)
            method = "DELETE" if operation == "delete" else "POST"
            if operation == "replace":
                path += "/replace"
        else:
            raise ValueError("Unknown identifier operation.")

        body = None
        if operation == "delete":
            if payload is not None:
                raise ValueError("Identifier deletion has no body.")
        else:
            fields = {"country_code", "identifier_type", "identifier_value", "is_primary"}
            if (
                not isinstance(payload, dict) or set(payload) != fields
                or any(
                    not isinstance(payload[key], str) or not payload[key].strip()
                    for key in ("country_code", "identifier_type", "identifier_value")
                )
                or type(payload["is_primary"]) is not bool
            ):
                raise ValueError("Identifier payload differs.")
            body = CompanyIdentifierCreateRequest.model_validate(
                payload,
            ).model_dump(mode="json")

        result = await self._request(
            method, path, session=session, json_body=body,
        )
        if operation != "delete":
            return self._company_identifier_response(result, many=False)

        try:
            response = CompanyIdentifierDeleteResponse.model_validate({
                "data": result.data, "meta": result.meta,
                "request_id": result.request_id,
            })
            if (
                response.data.id != identifier_id
                or type(result.data["deleted"]) is not bool
                or response.data.deleted is not True
            ):
                raise ValueError("Identifier deletion receipt differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return ConstructionApiResult(
            response.data.model_dump(mode="json"),
            response.meta.model_dump(mode="json"), result.request_id,
        )

    async def company_profile_capabilities(self, *, session):
        result = await self._get(
            "construction/ui/company-capabilities", session=session,
        )
        data = result.data
        if (
            not isinstance(data, dict)
            or data.get("tenant_id") != str(session.tenant_id)
            or type(data.get("can_manage_profile")) is not bool
        ):
            raise _response_error()
        return result

    async def save_company_profile(
        self, *, session, operation, payload, expected_profile_id=None,
    ):
        from api.schemas import (
            CompanyProfileCreateRequest,
            CompanyProfileUpdateRequest,
            CompanyProfileResponse,
        )

        fields = {
            "legal_name", "commercial_name",
            "registered_address_raw", "operational_address_raw",
            "phone", "email", "website",
            "signatory_name", "signatory_position",
        }
        if not isinstance(payload, dict):
            raise ValueError("Company profile payload must be an object.")
        if any(
            key in fields and value is not None and not isinstance(value, str)
            for key, value in payload.items()
        ):
            raise ValueError("Company field values must be text or null.")

        if operation == "create":
            if (
                not set(payload) <= fields or "legal_name" not in payload
                or not isinstance(payload["legal_name"], str)
                or not payload["legal_name"].strip()
                or expected_profile_id is not None
            ):
                raise ValueError("Company profile creation payload differs.")
            request = CompanyProfileCreateRequest.model_validate(payload)
            method = "POST"
        elif operation == "update":
            if (
                not set(payload) <= fields | {"expected_row_version"}
                or not (set(payload) & fields)
                or type(payload.get("expected_row_version")) is not int
                or payload["expected_row_version"] <= 0
                or not isinstance(expected_profile_id, UUID)
                or ("legal_name" in payload and (
                    not isinstance(payload["legal_name"], str)
                    or not payload["legal_name"].strip()
                ))
            ):
                raise ValueError("Company profile update payload differs.")
            request = CompanyProfileUpdateRequest.model_validate(payload)
            method = "PATCH"
        else:
            raise ValueError("Unknown company profile operation.")

        result = await self._request(
            method, "organization/company-profile", session=session,
            json_body=request.model_dump(mode="json", exclude_unset=True),
        )
        try:
            response = CompanyProfileResponse.model_validate({
                "data": result.data, "meta": result.meta,
                "request_id": result.request_id,
            })
            item = response.data
            if (
                item.tenant_id != session.tenant_id
                or type(result.data["row_version"]) is not int
                or item.row_version <= 0 or not item.legal_name.strip()
                or item.created_at.tzinfo is None
                or item.created_at.utcoffset() is None
                or item.updated_at.tzinfo is None
                or item.updated_at.utcoffset() is None
                or (
                    expected_profile_id is not None
                    and item.id != expected_profile_id
                )
            ):
                raise ValueError("Company profile response context differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return ConstructionApiResult(
            item.model_dump(mode="json"),
            response.meta.model_dump(mode="json"), result.request_id,
        )

    async def company_profile(self, *, session):
        from api.schemas import CompanyProfileReadResponse

        result = await self._request(
            "GET", "organization/company-profile", session=session, allow_null=True,
        )
        try:
            response = CompanyProfileReadResponse.model_validate({
                "data": result.data, "meta": result.meta,
                "request_id": result.request_id,
            })
            item = response.data
            if item is not None and (
                item.tenant_id != session.tenant_id
                or type(result.data["row_version"]) is not int
                or item.row_version <= 0 or not item.legal_name.strip()
                or item.created_at.tzinfo is None or item.created_at.utcoffset() is None
                or item.updated_at.tzinfo is None or item.updated_at.utcoffset() is None
            ):
                raise ValueError("Company profile context or fields differ.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return ConstructionApiResult(
            item.model_dump(mode="json") if item is not None else None,
            response.meta.model_dump(mode="json"), result.request_id,
        )

    async def me(self, *, session):
        result = await self._get('me', session=session)
        return self._entity(result, session=session, entity_id=session.user_id)

    async def leads(self, *, session, limit=20, cursor=None, filters=None):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Lead page size must be between 1 and 100.')
        if cursor is not None and (not isinstance(cursor, str) or not cursor):
            raise ValueError('API cursor must be a non-empty string.')
        allowed = {'status', 'source', 'responsible_user_id', 'converted_client_id', 'converted_project_id'}
        if filters is not None and (not isinstance(filters, dict) or set(filters) - allowed):
            raise ValueError('Unknown lead filter.')
        params = {'limit': limit, **{k: str(v) for k, v in (filters or {}).items() if v is not None}}
        if cursor is not None:
            params['cursor'] = cursor
        result = await self._get('construction/crm/leads', session=session, params=params)
        pagination = result.meta.get('pagination')
        if (not isinstance(result.data, list) or len(result.data) > limit
                or not isinstance(pagination, dict) or set(pagination) != {'next_cursor'}
                or (pagination['next_cursor'] is not None and not isinstance(pagination['next_cursor'], str))):
            raise _response_error()
        for item in result.data:
            self._entity(ConstructionApiResult(item, {}, result.request_id), session=session)
        return result

    async def lead(self, *, session, lead_id):
        return await self._read_entity('construction/crm/leads', session, lead_id)

    async def client_catalog(self, *, session, limit=5, cursor=None):
        from api.routes.construction_client_read import ClientReadResponse
        from api.routes.construction_client_catalog import ClientCatalogMeta

        if (
            type(limit) is not int or not 1 <= limit <= 100
            or (cursor is not None and (
                not isinstance(cursor, str) or not 1 <= len(cursor) <= 1024
            ))
        ):
            raise ValueError("Invalid client catalog request.")
        params = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        result = await self._get(
            "construction/clients", session=session, params=params,
        )
        try:
            if not isinstance(result.data, list) or len(result.data) > limit:
                raise ValueError("Invalid client catalog array.")
            meta = ClientCatalogMeta.model_validate(result.meta)
            next_cursor = meta.pagination.next_cursor
            if next_cursor is not None and (
                not 1 <= len(next_cursor) <= 1024
                or len(result.data) != limit or next_cursor == cursor
            ):
                raise ValueError("Invalid client catalog continuation.")
            public, seen, previous = [], set(), None
            for value in result.data:
                item = ClientReadResponse.model_validate(value)
                key = item.updated_at, item.id
                if (
                    item.tenant_id != session.tenant_id or item.id in seen
                    or (previous is not None and key >= previous)
                ):
                    raise ValueError("Client catalog context or order differs.")
                seen.add(item.id)
                previous = key
                public.append(item.model_dump(mode="json"))
        except (ValueError, TypeError, AttributeError, KeyError):
            raise _response_error() from None
        return ConstructionApiResult(
            public, meta.model_dump(mode="json"), result.request_id,
        )

    async def own_client_link_state(self, *, session, client_id):
        from api.routes.construction_client_own_link_state import ClientOwnLinkStateResponse
        if not isinstance(client_id, UUID):
            raise ValueError("Client UUID is required.")
        result = await self._get(
            "construction/clients/" + str(client_id) + "/user-link/self",
            session=session,
        )
        try:
            item = ClientOwnLinkStateResponse.model_validate(result.data)
            if (
                result.meta != {} or item.client_id != client_id
                or item.linked_user_id != session.user_id
            ):
                raise ValueError("Own client link receipt differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return ConstructionApiResult(
            item.model_dump(mode="json"), result.meta, result.request_id,
        )

    async def unlink_client(self, *, session, client_id, expected_row_version):
        from api.routes.construction_client_unlink import ClientUnlinkRequest, ClientUnlinkResponse
        if not isinstance(client_id, UUID):
            raise ValueError("Client UUID is required.")
        body = ClientUnlinkRequest(expected_row_version=expected_row_version)
        result = await self._request(
            "POST", "construction/clients/" + str(client_id) + "/user-link/unlink",
            session=session, json_body=body.model_dump(mode="json"),
        )
        try:
            item = ClientUnlinkResponse.model_validate(result.data)
            if (
                result.meta != {} or item.client_id != client_id
                or item.linked_user_id is not None
                or item.row_version != expected_row_version + int(item.changed)
            ):
                raise ValueError("Client unlink receipt differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return ConstructionApiResult(
            item.model_dump(mode="json"), result.meta, result.request_id,
        )

    async def request_client_user_link(
        self, *, session, client_id, target_email, expected_row_version,
        idempotency_key, previous_user_id=None,
    ):
        from services.construction.construction_client_link_api_client import ConstructionClientLinkApiClient
        return await ConstructionClientLinkApiClient(self).issue(
            session=session, client_id=client_id, target_email=target_email,
            expected_row_version=expected_row_version,
            idempotency_key=idempotency_key, previous_user_id=previous_user_id,
        )

    async def confirm_client_user_link(self, *, session, material, idempotency_key):
        from services.construction.construction_client_link_api_client import ConstructionClientLinkApiClient
        return await ConstructionClientLinkApiClient(self).confirm(
            session=session, material=material, idempotency_key=idempotency_key,
        )

    async def self_link_client(
        self, *, session, client_id, expected_row_version, previous_user_id=None,
    ):
        from api.routes.construction_client_self_link import (
            ClientSelfLinkRequest, ClientSelfLinkResponse,
        )
        if (
            not isinstance(client_id, UUID)
            or (previous_user_id is not None and not isinstance(previous_user_id, UUID))
        ):
            raise ValueError("Client identity UUIDs are required.")
        payload = {"expected_row_version": expected_row_version}
        if previous_user_id is not None:
            payload["previous_user_id"] = str(previous_user_id)
        body = ClientSelfLinkRequest.model_validate(payload)
        result = await self._request(
            "POST", "construction/clients/" + str(client_id) + "/user-link/self",
            session=session, json_body=body.model_dump(mode="json", exclude_unset=True),
        )
        try:
            item = ClientSelfLinkResponse.model_validate(result.data)
            if (
                result.meta != {}
                or item.client_id != client_id or item.tenant_id != session.tenant_id
                or item.linked_user_id != session.user_id
                or item.row_version != expected_row_version + int(item.changed)
                or (previous_user_id is not None and not item.changed)
            ):
                raise ValueError("Client self-link receipt differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return ConstructionApiResult(
            item.model_dump(mode="json"), result.meta, result.request_id,
        )

    async def patch_client(self, *, session, client_id, payload):
        from api.routes.construction_client_patch import ClientPatchRequest
        from api.routes.construction_client_read import ClientReadResponse

        if (
            not isinstance(client_id, UUID)
            or not isinstance(payload, dict)
            or "expected_row_version" not in payload
            or not set(payload) - {"expected_row_version"}
        ):
            raise ValueError("A versioned client edit is required.")
        body = ClientPatchRequest.model_validate(payload)
        fields = body.patch_fields()
        result = await self._request(
            "PATCH", "construction/clients/" + str(client_id),
            session=session,
            json_body=body.model_dump(mode="json", exclude_unset=True),
        )
        try:
            item = ClientReadResponse.model_validate(result.data)
            if (
                result.meta != {}
                or item.id != client_id
                or item.tenant_id != session.tenant_id
                or item.row_version not in (
                    body.expected_row_version, body.expected_row_version + 1,
                )
                or any(getattr(item, name) != value for name, value in fields.items())
            ):
                raise ValueError("Client edit receipt differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return ConstructionApiResult(
            item.model_dump(mode="json"), result.meta, result.request_id,
        )

    async def archive_client(self, *, session, client_id, expected_row_version):
        from api.routes.construction_client_archive import (
            ClientArchiveRequest, ClientArchiveResponse,
        )
        if not isinstance(client_id, UUID):
            raise ValueError("Client UUID is required.")
        body = ClientArchiveRequest.model_validate({
            "expected_row_version": expected_row_version,
        })
        result = await self._request(
            "POST", "construction/clients/" + str(client_id) + "/archive",
            session=session, json_body=body.model_dump(mode="json"),
        )
        try:
            item = ClientArchiveResponse.model_validate(result.data)
            if (
                result.meta != {} or item.id != client_id
                or item.tenant_id != session.tenant_id
                or item.row_version != expected_row_version + 1
            ):
                raise ValueError("Client archive receipt differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return ConstructionApiResult(
            item.model_dump(mode="json"), result.meta, result.request_id,
        )

    async def client_card(self, *, session, client_id, cursors=None):
        # Reuse the approved public response schemas, not domain services.
        from api.routes.construction_client_read import ClientCardResponse, ClientCardMeta
        from services.construction.construction_client_history_contract import (
            CONSTRUCTION_CLIENT_HISTORY_EVENT_ENTITY,
        )

        sections = ("projects", "leads", "activities", "history")
        if not isinstance(client_id, UUID):
            raise ValueError("Client UUID required.")
        if cursors is not None and (
            not isinstance(cursors, dict) or set(cursors) - set(sections)
        ):
            raise ValueError("Unknown client card section.")
        params = {section + "_limit": 5 for section in sections}
        for section, cursor in (cursors or {}).items():
            if cursor is not None:
                if not isinstance(cursor, str) or not 1 <= len(cursor) <= 1024:
                    raise ValueError("Invalid client card cursor.")
                params[section + "_cursor"] = cursor
        result = await self._get(
            "construction/clients/" + str(client_id),
            session=session, params=params,
        )
        self._entity(result, session=session, entity_id=client_id)
        try:
            card = ClientCardResponse.model_validate(result.data)
            meta = ClientCardMeta.model_validate(result.meta)
            for section in sections:
                items = getattr(card, section)
                cursor = getattr(meta.pagination, section + "_next_cursor")
                if (
                    len(items) > 5
                    or len({item.id for item in items}) != len(items)
                    or any(item.tenant_id != session.tenant_id for item in items)
                    or (
                        cursor is not None
                        and (not 1 <= len(cursor) <= 1024 or len(items) != 5)
                    )
                ):
                    raise ValueError("Client card page context differs.")
            if (
                any(item.client_id != client_id for item in card.projects)
                or any(item.converted_client_id != client_id for item in card.leads)
                or any(
                    CONSTRUCTION_CLIENT_HISTORY_EVENT_ENTITY.get(item.event_type)
                    != item.entity_type
                    for item in card.history
                )
            ):
                raise ValueError("Client card entity context differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return ConstructionApiResult(
            card.model_dump(mode="json"),
            meta.model_dump(mode="json"),
            result.request_id,
        )

    async def create_client(self, *, session, payload, idempotency_key):
        from api.routes.construction_client_read import ClientReadResponse

        if (
            not isinstance(payload, dict)
            or set(payload) != {"display_name", "client_type", "phone", "email", "notes"}
            or not isinstance(payload["display_name"], str)
            or not payload["display_name"].strip()
            or payload["client_type"] not in ("person", "company")
            or any(payload[name] is not None and not isinstance(payload[name], str)
                   for name in ("phone", "email", "notes"))
            or not isinstance(idempotency_key, str)
            or not 1 <= len(idempotency_key) <= 255
            or any(ord(char) < 33 or ord(char) > 126 for char in idempotency_key)
        ):
            raise ValueError("Invalid client creation command.")
        result = await self._request(
            "POST", "construction/clients", session=session,
            json_body=dict(payload), idempotency_key=idempotency_key,
        )
        self._entity(result, session=session)
        try:
            item = ClientReadResponse.model_validate(result.data)
            if (
                item.display_name != payload["display_name"].strip()
                or item.client_type != payload["client_type"]
                or any(getattr(item, field) != payload[field]
                       for field in ("phone", "email", "notes"))
                or item.linked_user_id is not None or item.row_version != 1
            ):
                raise ValueError("Client creation receipt differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return ConstructionApiResult(
            item.model_dump(mode="json"), result.meta, result.request_id,
        )

    async def client(self, *, session, client_id):
        return await self._read_entity('construction/clients', session, client_id)

    async def project_card(self, *, session, project_id, client_id):
        from api.routes.construction_project_read import ProjectReadResponse

        if not isinstance(project_id, UUID) or not isinstance(client_id, UUID):
            raise ValueError("Project and client UUIDs required.")
        result = await self.project(session=session, project_id=project_id)
        try:
            project = ProjectReadResponse.model_validate(result.data)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        # An old client-list selection must not silently open a project
        # that has since been assigned to another client.
        if project.client_id != client_id:
            raise _response_error(404, "not_found")
        return ConstructionApiResult(
            project.model_dump(mode="json"), result.meta, result.request_id,
        )

    async def create_project(self, *, session, payload, idempotency_key):
        from api.routes.construction_project_read import ProjectReadResponse

        if (
            not isinstance(payload, dict)
            or set(payload) != {
                "name", "address_raw", "client_id",
                "responsible_user_id", "comment",
            }
            or any(
                not isinstance(payload[field], str) or not payload[field].strip()
                for field in ("name", "address_raw")
            )
            or (
                payload["comment"] is not None
                and not isinstance(payload["comment"], str)
            )
            or not isinstance(idempotency_key, str)
            or not 1 <= len(idempotency_key) <= 255
            or any(ord(char) < 33 or ord(char) > 126 for char in idempotency_key)
        ):
            raise ValueError("Invalid project creation command.")
        for field in ("client_id", "responsible_user_id"):
            value = payload[field]
            if value is not None:
                try:
                    valid = isinstance(value, str) and str(UUID(value)) == value
                except (ValueError, TypeError, AttributeError):
                    valid = False
                if not valid:
                    raise ValueError("Invalid project reference.")

        result = await self._request(
            "POST", "construction/projects",
            session=session, json_body=dict(payload),
            idempotency_key=idempotency_key,
        )
        self._entity(result, session=session)
        try:
            project = ProjectReadResponse.model_validate(result.data)
            if (
                project.created_by != session.user_id
                or project.name != payload["name"].strip()
                or project.address_raw != payload["address_raw"].strip()
                or (
                    str(project.client_id) if project.client_id is not None else None
                ) != payload["client_id"]
                or (
                    str(project.responsible_user_id)
                    if project.responsible_user_id is not None else None
                ) != payload["responsible_user_id"]
                or project.comment != payload["comment"]
                or project.status != "draft"
                or project.address_verification_status != "pending"
                or project.row_version != 1
            ):
                raise ValueError("Project creation receipt differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return ConstructionApiResult(
            project.model_dump(mode="json"), result.meta, result.request_id,
        )

    async def project_catalog(self, *, session, limit=5, cursor=None):
        from api.routes.construction_project_read import ProjectReadResponse
        from api.routes.construction_project_catalog import ProjectCatalogMeta

        if (
            type(limit) is not int or not 1 <= limit <= 100
            or (cursor is not None and (
                not isinstance(cursor, str) or not 1 <= len(cursor) <= 1024
            ))
        ):
            raise ValueError("Invalid project catalog request.")
        params = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        result = await self._get(
            "construction/projects", session=session, params=params,
        )
        try:
            if not isinstance(result.data, list) or len(result.data) > limit:
                raise ValueError("Invalid project catalog array.")
            meta = ProjectCatalogMeta.model_validate(result.meta)
            next_cursor = meta.pagination.next_cursor
            if next_cursor is not None and (
                not 1 <= len(next_cursor) <= 1024
                or len(result.data) != limit or next_cursor == cursor
            ):
                raise ValueError("Invalid project catalog continuation.")
            public, seen, previous = [], set(), None
            for value in result.data:
                item = ProjectReadResponse.model_validate(value)
                key = item.updated_at, item.id
                if (
                    item.tenant_id != session.tenant_id or item.id in seen
                    or (previous is not None and key >= previous)
                ):
                    raise ValueError("Project catalog binding or order differs.")
                seen.add(item.id)
                previous = key
                public.append(item.model_dump(mode="json"))
        except (ValueError, TypeError, AttributeError, KeyError):
            raise _response_error() from None
        return ConstructionApiResult(
            public, meta.model_dump(mode="json"), result.request_id,
        )

    async def patch_project(self, *, session, project_id, payload):
        from api.routes.construction_project_patch import ProjectPatchRequest
        from api.routes.construction_project_read import ProjectReadResponse

        if (
            not isinstance(project_id, UUID)
            or not isinstance(payload, dict)
            or "expected_row_version" not in payload
            or not set(payload) - {"expected_row_version"}
        ):
            raise ValueError("A versioned project edit is required.")
        body = ProjectPatchRequest.model_validate(payload)
        fields = body.patch_fields()
        result = await self._request(
            "PATCH", "construction/projects/" + str(project_id),
            session=session,
            json_body=body.model_dump(mode="json", exclude_unset=True),
        )
        try:
            item = ProjectReadResponse.model_validate(result.data)
            if (
                result.meta != {} or item.id != project_id
                or item.tenant_id != session.tenant_id
                or item.row_version not in (
                    body.expected_row_version, body.expected_row_version + 1,
                )
                or any(getattr(item, name) != value for name, value in fields.items())
            ):
                raise ValueError("Project edit receipt differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return ConstructionApiResult(
            item.model_dump(mode="json"), result.meta, result.request_id,
        )

    async def project_details(self, *, session, project_id):
        from api.routes.construction_project_read import ProjectReadResponse

        result = await self.project(session=session, project_id=project_id)
        try:
            project = ProjectReadResponse.model_validate(result.data)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return ConstructionApiResult(
            project.model_dump(mode="json"), result.meta, result.request_id,
        )

    async def project(self, *, session, project_id):
        return await self._read_entity('construction/projects', session, project_id)

    async def _read_entity(self, path, session, entity_id):
        if not isinstance(entity_id, UUID):
            raise ValueError('Entity UUID is required.')
        result = await self._get(path + '/' + str(entity_id), session=session)
        return self._entity(result, session=session, entity_id=entity_id)


    async def create_lead(self, *, session, payload, idempotency_key):
        allowed = {
            "source", "name", "work_type_description", "phone", "email",
            "address_raw", "comment", "responsible_user_id",
        }
        if (
            not isinstance(payload, dict)
            or set(payload) - allowed
            or not {"source", "name", "work_type_description"} <= set(payload)
            or not isinstance(idempotency_key, str)
            or not idempotency_key.strip()
            or len(idempotency_key) > 255
            or any(ord(c) < 33 or ord(c) > 126 for c in idempotency_key)
        ):
            raise ValueError("Invalid lead creation command.")
        result = await self._request(
            "POST", "construction/crm/leads", session=session,
            json_body=dict(payload), idempotency_key=idempotency_key,
        )
        self._entity(result, session=session)
        try:
            UUID(result.data["id"])
        except (KeyError, ValueError, TypeError, AttributeError):
            raise _response_error() from None
        if (
            result.data.get("status") != "new"
            or type(result.data.get("row_version")) is not int
            or result.data["row_version"] != 1
        ):
            raise _response_error()
        return result


    async def change_lead_status(self, *, session, lead_id,
                                 next_status, expected_row_version):
        from services.construction.construction_lead_rules import CONSTRUCTION_LEAD_STATUSES

        if (
            not isinstance(lead_id, UUID)
            or not isinstance(next_status, str)
            or next_status not in CONSTRUCTION_LEAD_STATUSES
            or type(expected_row_version) is not int
            or not 0 < expected_row_version < 2**63
        ):
            raise ValueError("Invalid lead status command.")
        result = await self._request(
            "PATCH", "construction/crm/leads/" + str(lead_id) + "/status",
            session=session,
            json_body={
                "status": next_status,
                "expected_row_version": expected_row_version,
            },
        )
        self._entity(result, session=session, entity_id=lead_id)
        data = result.data
        if (
            set(data) != {"id", "tenant_id", "status", "row_version"}
            or data["status"] != next_status
            or type(data["row_version"]) is not int
            or data["row_version"] not in (
                expected_row_version, expected_row_version + 1,
            )
        ):
            raise _response_error()
        return result


    async def create_project_activity(
        self, *, session, project_id, payload, responsible_user_id, idempotency_key,
    ):
        from api.routes.construction_activities import ActivityCreateRequest
        from services.construction.construction_activity_response import ActivityCreatedResponse
        if (
            not isinstance(project_id, UUID)
            or not isinstance(payload, dict)
            or set(payload) != {"type", "due_at", "timezone", "comment"}
            or (responsible_user_id is not None and not isinstance(responsible_user_id, UUID))
            or not isinstance(idempotency_key, str)
            or not 1 <= len(idempotency_key) <= 255
            or any(not 33 <= ord(char) <= 126 for char in idempotency_key)
        ):
            raise ValueError("Invalid project activity command.")
        body = ActivityCreateRequest.model_validate({
            **payload, "project_id": str(project_id), "lead_id": None,
            "client_id": None,
            "responsible_user_id": str(responsible_user_id) if responsible_user_id else None,
        })
        command = body.command()
        result = await self._request(
            "POST", "construction/crm/activities", session=session,
            json_body=body.model_dump(mode="json"), idempotency_key=idempotency_key,
        )
        self._entity(result, session=session)
        try:
            item = ActivityCreatedResponse.model_validate(result.data)
            if (
                result.meta != {} or item.project_id != project_id
                or item.lead_id is not None or item.client_id is not None
                or item.created_by_user_id != session.user_id
                or item.responsible_user_id != responsible_user_id
                or item.type != command.activity_type
                or item.due_at != command.schedule.due_at
                or item.timezone != command.schedule.timezone
                or item.comment != command.comment
                or item.created_at != item.updated_at
            ):
                raise ValueError("Project activity receipt differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return ConstructionApiResult(
            item.model_dump(mode="json"), result.meta, result.request_id,
        )

    async def create_client_activity(
        self, *, session, client_id, payload, responsible_user_id, idempotency_key,
    ):
        from api.routes.construction_activities import ActivityCreateRequest
        from services.construction.construction_activity_response import ActivityCreatedResponse
        if (
            not isinstance(client_id, UUID)
            or not isinstance(payload, dict)
            or set(payload) != {"type", "due_at", "timezone", "comment"}
            or (responsible_user_id is not None and not isinstance(responsible_user_id, UUID))
            or not isinstance(idempotency_key, str)
            or not 1 <= len(idempotency_key) <= 255
            or any(not 33 <= ord(char) <= 126 for char in idempotency_key)
        ):
            raise ValueError("Invalid client activity command.")
        body = ActivityCreateRequest.model_validate({
            **payload, "client_id": str(client_id), "lead_id": None,
            "project_id": None,
            "responsible_user_id": str(responsible_user_id) if responsible_user_id else None,
        })
        command = body.command()
        result = await self._request(
            "POST", "construction/crm/activities", session=session,
            json_body=body.model_dump(mode="json"), idempotency_key=idempotency_key,
        )
        self._entity(result, session=session)
        try:
            item = ActivityCreatedResponse.model_validate(result.data)
            if (
                result.meta != {} or item.client_id != client_id
                or item.lead_id is not None or item.project_id is not None
                or item.created_by_user_id != session.user_id
                or item.responsible_user_id != responsible_user_id
                or item.type != command.activity_type
                or item.due_at != command.schedule.due_at
                or item.timezone != command.schedule.timezone
                or item.comment != command.comment
                or item.created_at != item.updated_at
            ):
                raise ValueError("Client activity receipt differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return ConstructionApiResult(
            item.model_dump(mode="json"), result.meta, result.request_id,
        )

    async def create_lead_activity(self, *, session, lead_id, payload,
                                   responsible_user_id, idempotency_key):
        from datetime import datetime
        from services.construction.construction_activity_response import ActivityCreatedResponse

        if (
            not isinstance(lead_id, UUID)
            or not isinstance(payload, dict)
            or set(payload) != {"type", "due_at", "timezone", "comment"}
            or (
                responsible_user_id is not None
                and not isinstance(responsible_user_id, UUID)
            )
            or not isinstance(idempotency_key, str)
            or not 1 <= len(idempotency_key) <= 255
            or any(ord(c) < 33 or ord(c) > 126 for c in idempotency_key)
        ):
            raise ValueError("Invalid activity creation command.")
        body = {
            **payload,
            "lead_id": str(lead_id),
            "client_id": None,
            "project_id": None,
            "responsible_user_id": (
                str(responsible_user_id) if responsible_user_id else None
            ),
        }
        result = await self._request(
            "POST", "construction/crm/activities", session=session,
            json_body=body, idempotency_key=idempotency_key,
        )
        self._entity(result, session=session)
        try:
            item = ActivityCreatedResponse.model_validate(result.data)
            due = datetime.fromisoformat(payload["due_at"].replace("Z", "+00:00"))
            if (
                str(item.id) != result.data["id"]
                or item.lead_id != lead_id
                or item.client_id is not None
                or item.project_id is not None
                or item.created_by_user_id != session.user_id
                or item.responsible_user_id != responsible_user_id
                or item.type != payload["type"]
                or item.timezone != payload["timezone"]
                or item.due_at != due
                or item.comment != payload["comment"]
            ):
                raise ValueError("Activity response binding differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return result


    async def terminal_client_activity(
        self, *, session, client_id, activity_id, expected_links,
        operation, payload, idempotency_key,
    ):
        if not isinstance(client_id, UUID):
            raise ValueError("Invalid client activity context.")
        return await self._terminal_related_activity(
            session=session, activity_id=activity_id, expected_links=expected_links,
            operation=operation, payload=payload, idempotency_key=idempotency_key,
        )

    async def terminal_project_activity(
        self, *, session, project_id, activity_id, expected_links,
        operation, payload, idempotency_key,
    ):
        if (
            not isinstance(project_id, UUID)
            or not isinstance(expected_links, dict)
            or expected_links.get("project_id") != str(project_id)
        ):
            raise ValueError("Invalid project activity context.")
        return await self._terminal_related_activity(
            session=session, activity_id=activity_id, expected_links=expected_links,
            operation=operation, payload=payload, idempotency_key=idempotency_key,
        )

    async def _terminal_related_activity(
        self, *, session, activity_id, expected_links,
        operation, payload, idempotency_key,
    ):
        from api.routes.construction_activities import ActivityTerminalRequest
        from services.construction.construction_activity_terminal_workflow import ActivityTerminalResponse
        fields = {"lead_id", "client_id", "project_id"}
        if (
            not isinstance(activity_id, UUID)
            or operation not in ("complete", "cancel")
            or not isinstance(expected_links, dict) or set(expected_links) != fields
            or not isinstance(payload, dict)
            or not isinstance(idempotency_key, str)
            or not 1 <= len(idempotency_key) <= 255
            or any(not 33 <= ord(char) <= 126 for char in idempotency_key)
        ):
            raise ValueError("Invalid client activity terminal command.")
        links = {
            name: UUID(str(value)) if value is not None else None
            for name, value in expected_links.items()
        }
        if not any(value is not None for value in links.values()):
            raise ValueError("An original activity relation is required.")
        body = ActivityTerminalRequest.model_validate(payload)
        result = await self._request(
            "POST", "construction/crm/activities/" + str(activity_id) + "/" + operation,
            session=session, json_body=body.model_dump(mode="json", exclude_unset=True),
            idempotency_key=idempotency_key,
        )
        self._entity(result, session=session, entity_id=activity_id)
        try:
            item = ActivityTerminalResponse.model_validate(result.data)
            if (
                result.meta != {}
                or item.status != ("completed" if operation == "complete" else "cancelled")
                or item.row_version != body.expected_row_version + 1
                or any(getattr(item, name) != value for name, value in links.items())
            ):
                raise ValueError("Client activity terminal receipt differs.")
            if "result" in payload:
                supplied = payload["result"]
                expected_result = supplied.strip() or None if supplied is not None else None
                if item.result != expected_result:
                    raise ValueError("Activity result differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return ConstructionApiResult(
            item.model_dump(mode="json"), result.meta, result.request_id,
        )

    async def terminal_lead_activity(self, *, session, lead_id, activity_id,
                                     operation, payload, idempotency_key):
        # Shared response schema; business operations remain behind HTTP.
        from services.construction.construction_activity_terminal_workflow import ActivityTerminalResponse

        if (
            not isinstance(lead_id, UUID)
            or not isinstance(activity_id, UUID)
            or operation not in ("complete", "cancel")
            or not isinstance(payload, dict)
            or set(payload) - {"expected_row_version", "result"}
            or type(payload.get("expected_row_version")) is not int
            or not 0 < payload["expected_row_version"] < 2**63
            or (
                "result" in payload
                and payload["result"] is not None
                and not isinstance(payload["result"], str)
            )
            or not isinstance(idempotency_key, str)
            or not 1 <= len(idempotency_key) <= 255
            or any(ord(c) < 33 or ord(c) > 126 for c in idempotency_key)
        ):
            raise ValueError("Invalid activity terminal command.")

        result = await self._request(
            "POST",
            "construction/crm/activities/" + str(activity_id) + "/" + operation,
            session=session, json_body=dict(payload),
            idempotency_key=idempotency_key,
        )
        self._entity(result, session=session, entity_id=activity_id)
        try:
            item = ActivityTerminalResponse.model_validate(result.data)
            expected_status = "completed" if operation == "complete" else "cancelled"
            if (
                item.lead_id != lead_id
                or item.status != expected_status
                or item.row_version != payload["expected_row_version"] + 1
            ):
                raise ValueError("Activity response binding differs.")
            if "result" in payload:
                supplied = payload["result"]
                expected_result = supplied.strip() or None if supplied is not None else None
                if item.result != expected_result:
                    raise ValueError("Activity result differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return result


    async def convert_lead(self, *, session, lead_id, payload, idempotency_key):
        allowed = {
            "expected_row_version", "client_id", "project_id", "client_type",
            "project_name", "project_address_raw",
        }
        if (
            not isinstance(lead_id, UUID)
            or not isinstance(payload, dict)
            or set(payload) - allowed
            or type(payload.get("expected_row_version")) is not int
            or not 0 < payload["expected_row_version"] < 2**63
            or not isinstance(idempotency_key, str)
            or not 1 <= len(idempotency_key) <= 255
            or any(ord(c) < 33 or ord(c) > 126 for c in idempotency_key)
        ):
            raise ValueError("Invalid lead conversion command.")

        result = await self._request(
            "POST", "construction/crm/leads/" + str(lead_id) + "/convert",
            session=session, json_body=dict(payload),
            idempotency_key=idempotency_key,
        )
        self._entity(result, session=session)
        data = result.data
        try:
            if set(data) != {
                "lead_id", "tenant_id", "client_id", "project_id",
                "status", "row_version",
            }:
                raise ValueError("Unexpected conversion response fields.")
            for field in ("lead_id", "tenant_id", "client_id", "project_id"):
                if not isinstance(data[field], str) or str(UUID(data[field])) != data[field]:
                    raise ValueError("Noncanonical conversion reference.")
            if (
                data["lead_id"] != str(lead_id)
                or data["status"] != "won"
                or type(data["row_version"]) is not int
                or not 0 < data["row_version"] < 2**63
                or (
                    payload.get("client_id") is not None
                    and data["client_id"] != payload["client_id"]
                )
                or (
                    payload.get("project_id") is not None
                    and data["project_id"] != payload["project_id"]
                )
            ):
                raise ValueError("Conversion response binding differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return result


    async def address_suggestions(self, *, session, query):
        if not isinstance(query, str) or not query.strip() or len(query) > 4096:
            raise ValueError("Invalid address query.")
        result = await self._get(
            "construction/address-suggestions",
            session=session, params={"q": query, "limit": 10},
        )
        if not isinstance(result.data, list) or len(result.data) > 10:
            raise _response_error()
        seen = set()
        try:
            for item in result.data:
                if not isinstance(item, dict) or set(item) != {
                    "entity_type", "entity_id", "label", "address_raw",
                    "match_kind", "score",
                }:
                    raise ValueError("Invalid suggestion fields.")
                identity = (item["entity_type"], item["entity_id"])
                if (
                    item["entity_type"] not in ("client", "project")
                    or not isinstance(item["entity_id"], str)
                    or str(UUID(item["entity_id"])) != item["entity_id"]
                    or not isinstance(item["label"], str)
                    or not isinstance(item["address_raw"], str)
                    or item["match_kind"] not in ("exact", "prefix_token", "fuzzy")
                    or type(item["score"]) is not int
                    or not 0 <= item["score"] <= 1000
                    or identity in seen
                ):
                    raise ValueError("Invalid suggestion.")
                seen.add(identity)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        # Preserve API ordering; do not rerank or auto-select on the client.
        return result

    async def client_summary(self, *, session, client_id):
        if not isinstance(client_id, UUID):
            raise ValueError("Client UUID required.")
        result = await self._get(
            "construction/clients/" + str(client_id),
            session=session,
            params={
                "projects_limit": 1, "leads_limit": 1,
                "activities_limit": 1, "history_limit": 1,
            },
        )
        self._entity(result, session=session, entity_id=client_id)
        data = result.data
        if (
            not isinstance(data.get("display_name"), str)
            or not data["display_name"].strip()
            or data.get("client_type") not in ("person", "company")
            or type(data.get("row_version")) is not int
            or not 0 < data["row_version"] < 2**63
            or any(
                data.get(field) is not None and not isinstance(data[field], str)
                for field in ("phone", "email")
            )
        ):
            raise _response_error()
        fields = (
            "id", "tenant_id", "display_name", "client_type",
            "phone", "email", "row_version",
        )
        return ConstructionApiResult(
            {field: data.get(field) for field in fields},
            result.meta, result.request_id,
        )

    @staticmethod
    def _conversion_project_data(data, *, session, client_id):
        try:
            if (
                not isinstance(data, dict)
                or data.get("tenant_id") != str(session.tenant_id)
                or data.get("client_id") != str(client_id)
                or not isinstance(data.get("id"), str)
                or str(UUID(data["id"])) != data["id"]
                or not isinstance(data.get("name"), str)
                or not data["name"].strip()
                or not isinstance(data.get("address_raw"), str)
                or type(data.get("row_version")) is not int
                or not 0 < data["row_version"] < 2**63
            ):
                raise ValueError("Project selection context differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return {
            field: data[field]
            for field in (
                "id", "tenant_id", "client_id",
                "name", "address_raw", "row_version",
            )
        }

    async def conversion_projects(self, *, session, client_id, cursor=None):
        if not isinstance(client_id, UUID):
            raise ValueError("Client UUID required.")
        if cursor is not None and (
            not isinstance(cursor, str) or not 1 <= len(cursor) <= 1024
        ):
            raise ValueError("Invalid client projects cursor.")
        params = {
            "projects_limit": 10, "leads_limit": 1,
            "activities_limit": 1, "history_limit": 1,
        }
        if cursor is not None:
            params["projects_cursor"] = cursor
        result = await self._get(
            "construction/clients/" + str(client_id),
            session=session, params=params,
        )
        self._entity(result, session=session, entity_id=client_id)
        items = result.data.get("projects")
        pagination = result.meta.get("pagination")
        if (
            not isinstance(items, list) or len(items) > 10
            or not isinstance(pagination, dict)
            or "projects_next_cursor" not in pagination
        ):
            raise _response_error()
        next_cursor = pagination["projects_next_cursor"]
        if next_cursor is not None and (
            not isinstance(next_cursor, str)
            or not 1 <= len(next_cursor) <= 1024
        ):
            raise _response_error()
        projects = [
            self._conversion_project_data(
                item, session=session, client_id=client_id,
            )
            for item in items
        ]
        if len({item["id"] for item in projects}) != len(projects):
            raise _response_error()
        return ConstructionApiResult(
            projects, {"pagination": {"next_cursor": next_cursor}},
            result.request_id,
        )

    async def conversion_project(self, *, session, client_id, project_id):
        if not isinstance(client_id, UUID) or not isinstance(project_id, UUID):
            raise ValueError("Client and project UUIDs required.")
        result = await self.project(session=session, project_id=project_id)
        return ConstructionApiResult(
            self._conversion_project_data(
                result.data, session=session, client_id=client_id,
            ),
            result.meta, result.request_id,
        )

    async def replace_project_participant(
        self, *, session, project_id, target_user_id, grant,
        new_role, idempotency_key, expires_at=...,
    ):
        from services.construction.construction_team_transport import Grant
        from api.routes.construction_access_mutations import (
            GrantReplaceRequest, GrantMutationResponse,
        )

        expected = Grant.model_validate(grant)
        if (
            not isinstance(project_id, UUID) or not isinstance(target_user_id, UUID)
            or expected.scope_type != "project" or expected.scope_id != project_id
            # The same role is allowed when its expiry changes.
            or not isinstance(idempotency_key, str)
            or not 1 <= len(idempotency_key) <= 255
            or idempotency_key != idempotency_key.strip()
            or (expected.expires_at is not None and (
                expected.expires_at.tzinfo is None
                or expected.expires_at.utcoffset() is None
            ))
        ):
            raise ValueError("Invalid project role replacement.")
        command = GrantReplaceRequest(
            role=new_role, scope_type="project", scope_id=project_id,
            expires_at=expected.expires_at if expires_at is Ellipsis else expires_at,
        )
        if command.role == expected.role and command.expires_at == expected.expires_at:
            raise ValueError("Project access is unchanged.")
        result = await self._request(
            "POST", f"construction/team/access-grants/{expected.id}/replace",
            session=session, json_body=command.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )
        try:
            item = GrantMutationResponse.model_validate(result.data)
            if (
                item.id == expected.id or item.user_id != target_user_id
                or item.role != command.role or item.scope_type != "project"
                or item.scope_id != project_id or item.status != "active"
                or item.expires_at != command.expires_at
                or (item.expires_at is not None and item.expires_at <= item.granted_at)
            ):
                raise ValueError("Replacement receipt differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return ConstructionApiResult(
            item.model_dump(mode="json"), result.meta, result.request_id,
        )

    async def revoke_project_participant(
        self, *, session, project_id, target_user_id, grant, idempotency_key,
    ):
        from services.construction.construction_team_transport import Grant
        from api.routes.construction_access_mutations import GrantMutationResponse

        expected = Grant.model_validate(grant)
        if (
            not isinstance(project_id, UUID) or not isinstance(target_user_id, UUID)
            or expected.scope_type != "project" or expected.scope_id != project_id
            or not isinstance(idempotency_key, str)
            or not 1 <= len(idempotency_key) <= 255
            or idempotency_key != idempotency_key.strip()
        ):
            raise ValueError("Invalid project role revocation.")
        result = await self._request(
            "POST", f"construction/team/access-grants/{expected.id}/revoke",
            session=session, json_body={}, idempotency_key=idempotency_key,
        )
        try:
            item = GrantMutationResponse.model_validate(result.data)
            if (
                item.id != expected.id or item.user_id != target_user_id
                or item.role != expected.role or item.scope_type != "project"
                or item.scope_id != project_id or item.status != "revoked"
                or item.expires_at != expected.expires_at
                or (item.expires_at is not None and item.expires_at <= item.granted_at)
            ):
                raise ValueError("Revocation receipt differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return ConstructionApiResult(
            item.model_dump(mode="json"), result.meta, result.request_id,
        )

    async def project_role_options(self, *, session, project_id):
        from api.routes.construction_role_options import RoleOptionsResponse

        if not isinstance(project_id, UUID):
            raise ValueError("Project UUID required.")
        result = await self._get(
            "construction/team/role-options", session=session,
            params={"scope_type": "project", "scope_id": str(project_id)},
        )
        try:
            item = RoleOptionsResponse.model_validate(result.data)
            if (
                result.data.get("permissions_read_only") is not True
                or item.scope_type != "project" or item.scope_id != project_id
                or len({value.role for value in item.roles}) != len(item.roles)
                or any(
                    len(value.permissions) != len(set(value.permissions))
                    or any(
                        not code.startswith("construction.") or code != code.strip()
                        for code in value.permissions
                    )
                    for value in item.roles
                )
            ):
                raise ValueError("Role options binding differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return ConstructionApiResult(
            item.model_dump(mode="json"), result.meta, result.request_id,
        )

    async def create_project_participant(
        self, *, session, project_id, target_user_id, role, idempotency_key,
        expires_at=None,
    ):
        from api.routes.construction_access_issuance import GrantCreateRequest
        from api.routes.construction_access_mutations import GrantMutationResponse

        if (
            not isinstance(project_id, UUID) or not isinstance(target_user_id, UUID)
            or not isinstance(idempotency_key, str)
            or not 1 <= len(idempotency_key) <= 255
            or idempotency_key != idempotency_key.strip()
        ):
            raise ValueError("Invalid project participant command.")
        command = GrantCreateRequest(
            role=role, scope_type="project", scope_id=project_id, expires_at=expires_at,
        )
        result = await self._request(
            "POST", f"construction/team/members/{target_user_id}/access-grants",
            session=session, json_body=command.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )
        try:
            item = GrantMutationResponse.model_validate(result.data)
            if (
                item.user_id != target_user_id or item.role != role
                or item.scope_type != "project" or item.scope_id != project_id
                or item.status != "active" or item.expires_at != command.expires_at
                or (item.expires_at is not None and item.expires_at <= item.granted_at)
            ):
                raise ValueError("Project grant receipt binding differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return ConstructionApiResult(
            item.model_dump(mode="json"), result.meta, result.request_id,
        )

    async def remove_organization_member(
        self, *, session, target_user_id, idempotency_key,
    ):
        from api.routes.platform_organization_membership import (
            RemoveOrganizationMemberResponse,
        )
        if (
            not isinstance(target_user_id, UUID)
            or not isinstance(idempotency_key, str)
            or not 1 <= len(idempotency_key) <= 255
            or any(not 33 <= ord(char) <= 126 for char in idempotency_key)
        ):
            raise ValueError("Invalid organization removal command.")
        result = await self._request(
            "POST",
            "platform/organizations/current/members/"
            + str(target_user_id) + "/remove",
            session=session, json_body={},
            idempotency_key=idempotency_key,
        )
        try:
            item = RemoveOrganizationMemberResponse.model_validate(result.data)
            if item.user_id != target_user_id:
                raise ValueError("Removal receipt identity differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return ConstructionApiResult(
            item.model_dump(mode="json"), result.meta, result.request_id,
        )

    async def replace_organization_access(
        self, *, session, target_user_id, original_grant,
        role, expires_at, idempotency_key,
    ):
        from services.construction.construction_organization_access_api import replace
        return await replace(
            self, session=session, target_user_id=target_user_id,
            original_grant=original_grant, role=role, expires_at=expires_at,
            idempotency_key=idempotency_key,
        )

    async def revoke_organization_access(
        self, *, session, target_user_id, original_grant, idempotency_key,
    ):
        from services.construction.construction_organization_access_api import revoke
        return await revoke(
            self, session=session, target_user_id=target_user_id,
            original_grant=original_grant, idempotency_key=idempotency_key,
        )

    async def block_organization_access(self, *, session, target_user_id):
        from services.construction.construction_organization_access_api import block
        return await block(self, session=session, target_user_id=target_user_id)

    async def organization_role_options(self, *, session):
        from services.construction.construction_organization_access_api import role_options
        return await role_options(self, session=session)

    async def create_organization_access(
        self, *, session, target_user_id, role, expires_at, idempotency_key,
    ):
        from services.construction.construction_organization_access_api import create
        return await create(
            self, session=session, target_user_id=target_user_id,
            role=role, expires_at=expires_at, idempotency_key=idempotency_key,
        )

    async def project_planned_activities(self, *, session, project_id, limit=20, cursor=None):
        from services.construction.construction_project_activities_api import read
        return await read(
            self, session=session, project_id=project_id, limit=limit, cursor=cursor,
        )

    async def team_members(self, *, session, project_id=None):
        from services.construction.construction_team_transport import validate_team

        if project_id is not None and not isinstance(project_id, UUID):
            raise ValueError("A project UUID is required.")
        result = await self._get(
            "construction/team/members", session=session,
            params={"project_id": str(project_id)} if project_id is not None else None,
        )
        try:
            data = validate_team(
                result.data, tenant_id=session.tenant_id, project_id=project_id,
            )
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return ConstructionApiResult(data, result.meta, result.request_id)

    async def create_project_area(
        self, *, session, project_id, payload, idempotency_key,
    ):
        from api.routes.construction_project_area_create import ProjectAreaCreateRequest
        from services.construction.construction_area_read import ProjectAreaReadItem

        if (
            not isinstance(project_id, UUID)
            or not isinstance(payload, dict)
            or set(payload) != {"area_type", "name", "sort_order"}
            or not isinstance(idempotency_key, str)
            or not 1 <= len(idempotency_key) <= 255
            or any(not 33 <= ord(char) <= 126 for char in idempotency_key)
        ):
            raise ValueError("Area creation request is invalid.")
        body = ProjectAreaCreateRequest.model_validate(payload)
        result = await self._request(
            "POST", "construction/projects/" + str(project_id) + "/areas",
            session=session, json_body=body.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )
        try:
            item = ProjectAreaReadItem.model_validate(result.data)
            if (
                item.tenant_id != session.tenant_id or item.project_id != project_id
                or item.area_type != body.area_type or item.name != body.name.strip()
                or item.sort_order != body.sort_order
                or item.status != "draft" or item.row_version != 1
            ):
                raise ValueError("Area creation response context differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return ConstructionApiResult(
            item.model_dump(mode="json"), result.meta, result.request_id,
        )

    async def patch_project_area(
        self, *, session, project_id, area_id, payload,
    ):
        from api.routes.construction_area_patch import (
            AreaPatchRequest, AreaPatchResponse,
        )
        if (
            not isinstance(project_id, UUID)
            or not isinstance(area_id, UUID)
            or not isinstance(payload, dict)
            or "expected_row_version" not in payload
            or set(payload) - {"expected_row_version"} not in (
                {"name"}, {"sort_order"},
            )
        ):
            raise ValueError("A single-field versioned area edit is required.")
        body = AreaPatchRequest.model_validate(payload)
        fields = body.patch_fields()
        result = await self._request(
            "PATCH", "construction/areas/" + str(area_id),
            session=session,
            json_body=body.model_dump(mode="json", exclude_unset=True),
        )
        try:
            item = AreaPatchResponse.model_validate(result.data)
            if (
                result.meta != {}
                or item.id != area_id
                or item.tenant_id != session.tenant_id
                or item.project_id != project_id
                or item.row_version not in (
                    body.expected_row_version,
                    body.expected_row_version + 1,
                )
                or any(
                    getattr(item, field) != value
                    for field, value in fields.items()
                )
            ):
                raise ValueError("Area edit receipt differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return ConstructionApiResult(
            item.model_dump(mode="json"), result.meta, result.request_id,
        )

    async def project_areas(self, *, session, project_id):
        from api.routes.construction_project_areas import ProjectAreaReadItem

        if not isinstance(project_id, UUID):
            raise ValueError("A project UUID is required.")
        result = await self._get(
            "construction/projects/" + str(project_id) + "/areas",
            session=session,
        )
        try:
            if not isinstance(result.data, list):
                raise ValueError("An area array is required.")
            capabilities = result.meta.get("capabilities", {})
            if (
                not isinstance(capabilities, dict)
                or (
                    any(
                        key in capabilities and type(capabilities[key]) is not bool
                        for key in ("can_create_area", "can_edit_area")
                    )
                )
            ):
                raise ValueError("Area capabilities are invalid.")
            items = [ProjectAreaReadItem.model_validate(item) for item in result.data]
            if (
                len({item.id for item in items}) != len(items)
                or any(
                    item.tenant_id != session.tenant_id
                    or item.project_id != project_id for item in items
                )
            ):
                raise ValueError("Area response context differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return ConstructionApiResult(
            [item.model_dump(mode="json") for item in items],
            result.meta, result.request_id,
        )

    async def unlink_telegram_account(
        self, *, session, account_id, expected_row_version, idempotency_key,
    ):
        from services.platform_telegram_binding_api_client import unlink_account
        return await unlink_account(
            self, session=session, account_id=account_id,
            expected_row_version=expected_row_version,
            idempotency_key=idempotency_key,
        )

    async def telegram_accounts(self, *, session, cursor=None):
        from services.platform_telegram_binding_api_client import accounts
        return await accounts(self, session=session, cursor=cursor)

    async def telegram_binding(
        self, *, session, operation, idempotency_key,
        binding_id=None, expected_row_version=None,
    ):
        from services.platform_telegram_binding_api_client import binding
        return await binding(
            self, session=session, operation=operation,
            idempotency_key=idempotency_key, binding_id=binding_id,
            expected_row_version=expected_row_version,
        )

    async def navigation(self, *, session):
        result = await self._get(
            "construction/ui/navigation-context", session=session
        )
        self._entity(result, session=session)
        data = result.data
        if (
            not {"tenant_id", "name", "currency", "roles", "can_read_leads"}.issubset(data)
            or bool(set(data) - {
                "tenant_id", "name", "currency", "roles", "can_read_leads",
                "can_create_project", "can_manage_team", "can_read_projects", "can_read_clients",
            })
            or not isinstance(data["name"], str)
            or not data["name"]
            or not isinstance(data["currency"], str)
            or not data["currency"]
            or not isinstance(data["roles"], list)
            or any(not isinstance(role, str) for role in data["roles"])
            or type(data["can_read_leads"]) is not bool
            or (
                "can_read_clients" in data
                and type(data["can_read_clients"]) is not bool
            )
            or (
                "can_read_projects" in data
                and type(data["can_read_projects"]) is not bool
            )
            or (
                "can_create_project" in data
                and type(data["can_create_project"]) is not bool
            )
            or (
                "can_manage_team" in data
                and type(data["can_manage_team"]) is not bool
            )
        ):
            raise _response_error()
        return result

# project_activity_terminal_v1

# universal_area_editor_v1

# client_link_email_http_v1
