"""Project pricing adjustments through the existing scoped HTTP API."""
from copy import deepcopy
from uuid import UUID

from api.routes.construction_project_pricing_adjustments import (
    ProjectPricingAdjustmentReadData,
    ProjectPricingAdjustmentListEnvelope,
)
from api.routes.construction_project_pricing_adjustment_writes import (
    ProjectPricingAdjustmentCreateRequest,
    ProjectPricingAdjustmentPatchRequest,
    ProjectPricingAdjustmentWriteData,
)
from services.construction.construction_language_bot_api import (
    ConstructionLanguageBotApi, problem,
)


class ProjectAdjustmentBotApi(ConstructionLanguageBotApi):
    @staticmethod
    def path(project_id, adjustment_id=None):
        if not isinstance(project_id, UUID):
            raise ValueError("Project UUID is required.")
        value = "construction/projects/" + str(project_id) + "/pricing-adjustments"
        if adjustment_id is not None:
            if not isinstance(adjustment_id, UUID):
                raise ValueError("Adjustment UUID is required.")
            value += "/" + str(adjustment_id)
        return value

    async def context(
        self, *, telegram_user_id, tenant_id, project_id,
        expected_user_id, expected_client_id=None,
    ):
        session, home = await self._session(
            telegram_user_id=telegram_user_id, tenant_id=tenant_id,
            expected_user_id=expected_user_id,
        )
        if not isinstance(project_id, UUID):
            raise ValueError("Project UUID is required.")
        result = await self.entry.api.construction.project_details(
            session=session, project_id=project_id,
        )
        item, meta = result.data, result.meta
        try:
            caps = meta["capabilities"]
            if (
                item["id"] != str(project_id)
                or item["tenant_id"] != str(session.tenant_id)
                or not isinstance(home.get("language"), str)
                or not home["language"].strip()
                or any(type(caps.get(name)) is not bool for name in (
                    "can_read_pricing_adjustments",
                    "can_edit_pricing_adjustments",
                ))
            ):
                raise ValueError("Project pricing context differs.")
            if expected_client_id is not None and (
                not isinstance(expected_client_id, UUID)
                or item["client_id"] != str(expected_client_id)
            ):
                raise problem(409, "SOURCE_CONTEXT_CHANGED")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise problem() from None
        if caps["can_read_pricing_adjustments"] is not True:
            raise problem(403, "permission_required")
        return session, {
            "actor_user_id": str(session.user_id),
            "project_name": item["name"], "language": home["language"],
            "can_manage": caps["can_edit_pricing_adjustments"],
        }

    async def list_adjustments(self, *, cursor=None, **binding):
        session, context = await self.context(**binding)
        if cursor is not None and (
            not isinstance(cursor, str) or not 1 <= len(cursor) <= 1024
        ):
            raise ValueError("Invalid adjustment cursor.")
        params = {"limit": 10}
        if cursor is not None:
            params["cursor"] = cursor
        result = await self.entry.api.construction._request(
            "GET", self.path(binding["project_id"]),
            session=session, params=params,
        )
        try:
            envelope = ProjectPricingAdjustmentListEnvelope.model_validate({
                "data": result.data, "meta": result.meta,
                "request_id": result.request_id,
            })
            items = envelope.data
            ids = [item.id for item in items]
            next_cursor = envelope.meta.pagination.next_cursor
            if (
                len(items) > 10 or ids != sorted(set(ids))
                or next_cursor is not None and len(items) != 10
                or any(
                    item.tenant_id != session.tenant_id
                    or item.project_id != binding["project_id"]
                    for item in items
                )
            ):
                raise ValueError("Adjustment page context differs.")
        except (ValueError, TypeError, AttributeError):
            raise problem() from None
        return {
            **context, "items": [item.model_dump(mode="json") for item in items],
            "next_cursor": next_cursor,
        }

    async def get_adjustment(self, *, adjustment_id, **binding):
        path = self.path(binding["project_id"], adjustment_id)
        session, context = await self.context(**binding)
        result = await self.entry.api.construction._request(
            "GET", path, session=session,
        )
        try:
            item = ProjectPricingAdjustmentReadData.model_validate(result.data)
            if (
                result.meta != {} or item.tenant_id != session.tenant_id
                or item.project_id != binding["project_id"] or item.id != adjustment_id
            ):
                raise ValueError("Adjustment context differs.")
        except (ValueError, TypeError, AttributeError):
            raise problem() from None
        return {**context, "item": item.model_dump(mode="json")}

    async def write_adjustment(self, *, request, **binding):
        if not isinstance(request, dict) or set(request) != {
            "operation", "project_id", "adjustment_id", "body", "key",
        } or request["project_id"] != str(binding["project_id"]):
            raise ValueError("Invalid adjustment operation.")
        key, adjustment_id = request["key"], request["adjustment_id"]
        if (
            not isinstance(key, str) or not 1 <= len(key) <= 255
            or any(not 33 <= ord(char) <= 126 for char in key)
        ):
            raise ValueError("Invalid operation key.")
        if request["operation"] == "create" and adjustment_id is None:
            model = ProjectPricingAdjustmentCreateRequest.model_validate(request["body"])
            body = model.model_dump(mode="json")
            method, path = "POST", self.path(binding["project_id"])
        elif request["operation"] == "patch" and isinstance(adjustment_id, str):
            adjustment_id = UUID(adjustment_id)
            model = ProjectPricingAdjustmentPatchRequest.model_validate(request["body"])
            body = model.model_dump(mode="json", exclude_unset=True)
            method, path = "PATCH", self.path(binding["project_id"], adjustment_id)
        else:
            raise ValueError("Unsupported adjustment operation.")
        fields = model.domain_fields()
        if not fields:
            raise ValueError("An edited adjustment field is required.")
        session, context = await self.context(**binding)
        if context["can_manage"] is not True:
            raise problem(403, "permission_required")
        result = await self.entry.api.construction._request(
            method, path, session=session,
            json_body=deepcopy(body), idempotency_key=key,
        )
        try:
            item = ProjectPricingAdjustmentWriteData.model_validate(result.data)
            if (
                result.meta != {} or item.tenant_id != session.tenant_id
                or item.project_id != binding["project_id"]
                or any(getattr(item, name) != value for name, value in fields.items())
            ):
                raise ValueError("Adjustment write response differs.")
            if request["operation"] == "create":
                if (
                    item.created_by != session.user_id
                    or item.row_version != 1 or item.changed is not True
                ):
                    raise ValueError("Adjustment create result differs.")
            elif (
                item.id != adjustment_id
                or item.row_version != body["expected_row_version"] + int(item.changed)
            ):
                raise ValueError("Adjustment update version differs.")
        except (ValueError, TypeError, AttributeError):
            raise problem() from None
        return {**context, "item": item.model_dump(mode="json")}
