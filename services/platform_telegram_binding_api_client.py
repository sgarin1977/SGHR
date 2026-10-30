"""Own Platform Telegram state and binding transport; no domain/DB calls."""
from datetime import timedelta
import re
from urllib.parse import urlsplit
from uuid import UUID

from api.routes.platform_telegram_account_state import (
    TelegramAccountStateResponse, TelegramAccountStateMeta,
)
from api.routes.platform_telegram_binding import (
    BindingIssuedResponse, BindingResentResponse, BindingRevokedResponse,
    BindingVersionRequest,
)
from services.construction.construction_api_client import ConstructionApiResult, _response_error
from services.platform_telegram_binding_presentation import read_platform_telegram_binding_code

PREFIX = "platform/integrations/telegram"


async def accounts(client, *, session, cursor=None):
    if cursor is not None and not isinstance(cursor, UUID):
        raise ValueError("Account cursor must be a UUID.")
    params = {"limit": 5}
    if cursor is not None:
        params["cursor"] = str(cursor)
    result = await client._get(PREFIX + "/accounts", session=session, params=params)
    try:
        if not isinstance(result.data, list) or len(result.data) > 5:
            raise ValueError("Account page differs.")
        items = [TelegramAccountStateResponse.model_validate(item) for item in result.data]
        meta = TelegramAccountStateMeta.model_validate(result.meta)
        previous = cursor
        for item in items:
            if previous is not None and item.account_id <= previous:
                raise ValueError("Account ordering differs.")
            previous = item.account_id
        following = meta.pagination.next_cursor
        if following is not None and (
            len(items) != 5 or following != items[-1].account_id
        ):
            raise ValueError("Account continuation differs.")
    except (ValueError, TypeError, KeyError, AttributeError):
        raise _response_error() from None
    return ConstructionApiResult(
        [item.model_dump(mode="json") for item in items],
        meta.model_dump(mode="json"), result.request_id,
    )


async def binding(client, *, session, operation, idempotency_key,
                  binding_id=None, expected_row_version=None):
    if (
        not isinstance(idempotency_key, str)
        or not 1 <= len(idempotency_key) <= 255
        or any(ord(char) < 33 or ord(char) > 126 for char in idempotency_key)
    ):
        raise ValueError("A valid binding idempotency key is required.")
    if operation == "initiate":
        if binding_id is not None or expected_row_version is not None:
            raise ValueError("Binding initiation has no target selector.")
        path, payload, model = PREFIX + "/bindings", {}, BindingIssuedResponse
    elif operation in ("resend", "revoke"):
        if not isinstance(binding_id, UUID):
            raise ValueError("Binding UUID is required.")
        body = BindingVersionRequest.model_validate({
            "expected_row_version": expected_row_version,
        })
        path = PREFIX + "/bindings/" + str(binding_id) + "/" + operation
        payload = body.model_dump(mode="json")
        model = BindingResentResponse if operation == "resend" else BindingRevokedResponse
    else:
        raise ValueError("Unknown binding operation.")

    result = await client._request(
        "POST", path, session=session, json_body=payload,
        idempotency_key=idempotency_key,
    )
    try:
        if result.meta != {}:
            raise ValueError("Binding response metadata differs.")
        item = model.model_validate(result.data)
        if operation == "revoke":
            if item.binding_id != binding_id or item.row_version != expected_row_version + 1:
                raise ValueError("Binding revocation receipt differs.")
        else:
            code = read_platform_telegram_binding_code(item.code)
            link = urlsplit(item.deep_link)
            if (
                item.row_version != 1
                or item.expires_at - item.issued_at != timedelta(minutes=15)
                or link.scheme != "https" or link.netloc != "t.me"
                or re.fullmatch(r"/[A-Za-z0-9_]{5,32}", link.path) is None
                or link.query != "start=bind_" + code or link.fragment
            ):
                raise ValueError("Binding presentation differs.")
            if operation == "resend" and (
                item.source_binding_id != binding_id
                or item.source_row_version != expected_row_version + 1
                or item.binding_id == binding_id
            ):
                raise ValueError("Binding replacement receipt differs.")
    except (ValueError, TypeError, KeyError, AttributeError):
        raise _response_error() from None
    # These response models hide code/deep_link in repr.
    return item


async def unlink_account(client, *, session, account_id,
                         expected_row_version, idempotency_key):
    from api.routes.platform_telegram_binding import AccountUnlinkedResponse

    if not isinstance(account_id, UUID):
        raise ValueError("Telegram account UUID is required.")
    if (
        not isinstance(idempotency_key, str)
        or not 1 <= len(idempotency_key) <= 255
        or any(ord(char) < 33 or ord(char) > 126 for char in idempotency_key)
    ):
        raise ValueError("A valid unlink idempotency key is required.")
    body = BindingVersionRequest.model_validate({
        "expected_row_version": expected_row_version,
    })
    result = await client._request(
        "POST", PREFIX + "/accounts/" + str(account_id) + "/unlink",
        session=session, json_body=body.model_dump(mode="json"),
        idempotency_key=idempotency_key,
    )
    try:
        item = AccountUnlinkedResponse.model_validate(result.data)
        if (
            result.meta != {} or item.account_id != account_id
            or item.row_version != expected_row_version + int(item.changed)
        ):
            raise ValueError("Telegram unlink receipt differs.")
    except (ValueError, TypeError, KeyError, AttributeError):
        raise _response_error() from None
    return item
