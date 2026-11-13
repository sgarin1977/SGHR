"""Telegram tax profiles using the existing authenticated HTTP contracts."""
from copy import deepcopy
from uuid import UUID

from api.routes.construction_tax_profiles import TaxProfileReadData
from api.routes.construction_tax_profile_list import TaxProfileListEnvelope
from api.routes.construction_tax_profile_writes import (
    TaxProfileCreateRequest, TaxProfilePatchRequest, TaxProfileWriteData,
)
from services.construction.construction_language_bot_api import (
    ConstructionLanguageBotApi, problem,
)


class TaxProfileBotApi(ConstructionLanguageBotApi):
    async def context(
        self, *, telegram_user_id, tenant_id, expected_user_id,
    ):
        session, home = await self._session(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
            expected_user_id=expected_user_id,
        )
        if (
            type(home.get("can_edit_tax_profiles")) is not bool
            or not isinstance(home.get("language"), str)
            or not home["language"].strip()
        ):
            raise problem()
        return session, {
            "actor_user_id": str(session.user_id),
            "organization": home["name"],
            "language": home["language"],
            "can_manage": home["can_edit_tax_profiles"],
        }

    async def list_profiles(self, *, cursor=None, **binding):
        session, context = await self.context(**binding)
        if cursor is not None and (
            not isinstance(cursor, str) or not 1 <= len(cursor) <= 512
        ):
            raise ValueError("Invalid profile cursor.")
        params = {"limit": 10}
        if cursor is not None:
            params["cursor"] = cursor
        result = await self.entry.api.construction._request(
            "GET", "construction/settings/tax-profiles",
            session=session, params=params,
        )
        try:
            envelope = TaxProfileListEnvelope.model_validate({
                "data": result.data, "meta": result.meta,
                "request_id": result.request_id,
            })
            items = envelope.data
            ids = [item.id for item in items]
            next_cursor = envelope.meta.pagination.next_cursor
            if (
                len(items) > 10
                or ids != sorted(set(ids))
                or any(item.tenant_id != session.tenant_id for item in items)
                or next_cursor is not None and len(items) != 10
            ):
                raise ValueError("Profile page context differs.")
        except (ValueError, TypeError, AttributeError):
            raise problem() from None
        return {
            **context,
            "items": [item.model_dump(mode="json") for item in items],
            "next_cursor": next_cursor,
        }

    async def get_profile(self, *, profile_id, **binding):
        if not isinstance(profile_id, UUID):
            raise ValueError("Profile UUID is required.")
        session, context = await self.context(**binding)
        result = await self.entry.api.construction._request(
            "GET", "construction/settings/tax-profiles/" + str(profile_id),
            session=session,
        )
        try:
            item = TaxProfileReadData.model_validate(result.data)
            if (
                result.meta != {}
                or item.tenant_id != session.tenant_id
                or item.id != profile_id
            ):
                raise ValueError("Profile context differs.")
        except (ValueError, TypeError, AttributeError):
            raise problem() from None
        return {**context, "item": item.model_dump(mode="json")}

    async def write_profile(self, *, request, **binding):
        if not isinstance(request, dict) or set(request) != {
            "operation", "profile_id", "body", "key",
        }:
            raise ValueError("Invalid profile operation.")
        operation, profile_id = request["operation"], request["profile_id"]
        key = request["key"]
        if (
            not isinstance(key, str) or not 1 <= len(key) <= 255
            or any(not 33 <= ord(char) <= 126 for char in key)
        ):
            raise ValueError("Invalid operation key.")
        if operation == "create" and profile_id is None:
            model = TaxProfileCreateRequest.model_validate(request["body"])
            body = model.model_dump(mode="json")
            fields = body
            method, path = "POST", "construction/settings/tax-profiles"
        elif operation == "patch" and isinstance(profile_id, str):
            profile_id = UUID(profile_id)
            model = TaxProfilePatchRequest.model_validate(request["body"])
            body = model.model_dump(mode="json", exclude_unset=True)
            fields = model.patch_fields()
            if not fields:
                raise ValueError("An edited field is required.")
            method = "PATCH"
            path = "construction/settings/tax-profiles/" + str(profile_id)
        else:
            raise ValueError("Unsupported profile operation.")

        session, context = await self.context(**binding)
        if context["can_manage"] is not True:
            raise problem(403, "permission_required")
        result = await self.entry.api.construction._request(
            method, path, session=session,
            json_body=deepcopy(body), idempotency_key=key,
        )
        try:
            item = TaxProfileWriteData.model_validate(result.data)
            if (
                result.meta != {}
                or item.tenant_id != session.tenant_id
                or any(getattr(item, name) != value for name, value in fields.items())
            ):
                raise ValueError("Profile write result differs.")
            if operation == "create":
                if (
                    item.created_by != session.user_id
                    or item.row_version != 1 or item.changed is not True
                ):
                    raise ValueError("Profile create result differs.")
            elif (
                item.id != profile_id
                or item.row_version != body["expected_row_version"] + int(item.changed)
            ):
                raise ValueError("Profile update version differs.")
        except (ValueError, TypeError, AttributeError):
            raise problem() from None
        return {**context, "item": item.model_dump(mode="json")}
