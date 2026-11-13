"""Tax rules through the existing authenticated, exact-decimal API."""
from copy import deepcopy
from uuid import UUID

from api.routes.construction_tax_rules import TaxRuleReadData
from api.routes.construction_tax_rule_list import TaxRuleListEnvelope
from api.routes.construction_tax_rule_writes import (
    TaxRuleCreateRequest, TaxRulePatchRequest, TaxRuleWriteData,
)
from services.construction.construction_tax_profile_bot_api import TaxProfileBotApi
from services.construction.construction_language_bot_api import problem


class TaxRuleBotApi(TaxProfileBotApi):
    @staticmethod
    def path(profile_id, rule_id=None):
        if not isinstance(profile_id, UUID):
            raise ValueError("Profile UUID is required.")
        path = "construction/settings/tax-profiles/" + str(profile_id) + "/rules"
        if rule_id is not None:
            if not isinstance(rule_id, UUID):
                raise ValueError("Rule UUID is required.")
            path += "/" + str(rule_id)
        return path

    async def list_rules(self, *, profile_id, **binding):
        path = self.path(profile_id)
        session, context = await self.context(**binding)
        result = await self.entry.api.construction._request(
            "GET", path, session=session, params={"limit": 10},
        )
        try:
            envelope = TaxRuleListEnvelope.model_validate({
                "data": result.data, "meta": result.meta,
                "request_id": result.request_id,
            })
            ids = [item.id for item in envelope.data]
            categories = [item.category for item in envelope.data]
            if (
                ids != sorted(set(ids))
                or len(categories) != len(set(categories))
                or envelope.meta.pagination.next_cursor is not None
                or any(
                    item.tenant_id != session.tenant_id
                    or item.tax_profile_id != profile_id
                    for item in envelope.data
                )
            ):
                raise ValueError("Rule collection context differs.")
        except (ValueError, TypeError, AttributeError):
            raise problem() from None
        return {
            **context,
            "items": [item.model_dump(mode="json") for item in envelope.data],
        }

    async def get_rule(self, *, profile_id, rule_id, **binding):
        path = self.path(profile_id, rule_id)
        session, context = await self.context(**binding)
        result = await self.entry.api.construction._request(
            "GET", path, session=session,
        )
        try:
            item = TaxRuleReadData.model_validate(result.data)
            if (
                result.meta != {}
                or item.tenant_id != session.tenant_id
                or item.tax_profile_id != profile_id or item.id != rule_id
            ):
                raise ValueError("Rule context differs.")
        except (ValueError, TypeError, AttributeError):
            raise problem() from None
        return {**context, "item": item.model_dump(mode="json")}

    async def write_rule(self, *, request, **binding):
        if not isinstance(request, dict) or set(request) != {
            "kind", "operation", "profile_id", "rule_id", "body", "key",
        } or request["kind"] != "rule":
            raise ValueError("Invalid rule operation.")
        profile_id = UUID(request["profile_id"])
        rule_id = request["rule_id"]
        key = request["key"]
        if (
            not isinstance(key, str) or not 1 <= len(key) <= 255
            or any(not 33 <= ord(char) <= 126 for char in key)
        ):
            raise ValueError("Invalid operation key.")
        if request["operation"] == "create" and rule_id is None:
            model = TaxRuleCreateRequest.model_validate(request["body"])
            body = model.model_dump(mode="json")
            method, path = "POST", self.path(profile_id)
        elif request["operation"] == "patch" and isinstance(rule_id, str):
            rule_id = UUID(rule_id)
            model = TaxRulePatchRequest.model_validate(request["body"])
            body = model.model_dump(mode="json", exclude_unset=True)
            method, path = "PATCH", self.path(profile_id, rule_id)
        else:
            raise ValueError("Unsupported rule operation.")
        fields = model.domain_fields()
        if not fields:
            raise ValueError("An edited rule field is required.")

        session, context = await self.context(**binding)
        if context["can_manage"] is not True:
            raise problem(403, "permission_required")
        result = await self.entry.api.construction._request(
            method, path, session=session,
            json_body=deepcopy(body), idempotency_key=key,
        )
        try:
            item = TaxRuleWriteData.model_validate(result.data)
            if (
                result.meta != {}
                or item.tenant_id != session.tenant_id
                or item.tax_profile_id != profile_id
                or any(getattr(item, name) != value for name, value in fields.items())
            ):
                raise ValueError("Rule write response differs.")
            if request["operation"] == "create":
                if (
                    item.created_by != session.user_id
                    or item.row_version != 1 or item.changed is not True
                ):
                    raise ValueError("Rule create result differs.")
            elif (
                item.id != rule_id
                or item.row_version != body["expected_row_version"] + int(item.changed)
            ):
                raise ValueError("Rule update version differs.")
        except (ValueError, TypeError, AttributeError):
            raise problem() from None
        return {**context, "item": item.model_dump(mode="json")}
