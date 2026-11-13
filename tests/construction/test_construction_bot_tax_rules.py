"""Exact rates, existing HTTP boundary and shared tax-rule Telegram workflow."""
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from handlers.construction import construction_tax_profiles as tax
from handlers.construction import construction_tax_rules as rules
from services.construction.construction_tax_rule_bot_api import TaxRuleBotApi
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from test_construction_bot_tax_profiles import screen, open_screen, click
from ui.construction_tax_rules import LABELS


@pytest.mark.parametrize("text,expected", [
    ("0", "0"), ("100", "100"), ("20,125", "20.125"),
    (" 0.000000000000001 ", "0.000000000000001"), (".5", ".5"),
])
def test_rate_input_preserves_exact_decimal_text(text, expected):
    assert rules.parse_rate(text) == expected


@pytest.mark.parametrize("text", [
    "", "-1", "100.0001", "NaN", "Infinity", "1e2", "20%", "1,2.3", 20.0,
])
def test_invalid_rate_is_not_rounded_or_defaulted(text):
    with pytest.raises(ValueError):
        rules.parse_rate(text)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["create", "patch", "float_response", "foreign", "conflict", "denied"])
async def test_transport_uses_exact_string_and_existing_profile_rule_route(case):
    tenant, user, profile_id, rule_id = uuid4(), uuid4(), uuid4(), uuid4()
    calls = []
    now = "2026-11-11T12:00:00Z"
    def transport(request):
        calls.append(request)
        if case == "conflict":
            return httpx.Response(409, json={"error": {
                "code": "TAX_RULE_CATEGORY_EXISTS", "request_id": "local",
            }})
        return httpx.Response(200, json={
            "data": {
                "id": str(rule_id), "tenant_id": str(tenant),
                "tax_profile_id": str(uuid4()) if case == "foreign" else str(profile_id),
                "category": "WORK",
                "rate_percent": 20.125 if case == "float_response" else "20.125",
                "row_version": 8 if case == "patch" else 1,
                "created_by": str(user), "created_at": now, "updated_at": now,
                "changed": True,
            }, "meta": {}, "request_id": "local",
        })
    session = PlatformApiClientSession(user, tenant, SecretStr("local"))
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        entry = NS(
            _presentation=AsyncMock(return_value=(session, {
                "language": "uk", "name": "Org",
                "can_edit_tax_profiles": case != "denied",
            })),
            api=NS(construction=ConstructionApiClient(http=http)),
        )
        body = {"category": "WORK", "rate_percent": "20.125"}
        if case == "patch":
            body = {"rate_percent": "20.125", "expected_row_version": 7}
        operation = {
            "kind": "rule", "operation": "patch" if case == "patch" else "create",
            "profile_id": str(profile_id),
            "rule_id": str(rule_id) if case == "patch" else None,
            "body": body, "key": "stable-rule-key",
        }
        call = TaxRuleBotApi(entry).write_rule(
            request=operation, telegram_user_id=123,
            tenant_id=tenant, expected_user_id=user,
        )
        if case in ("create", "patch"):
            await call
        else:
            with pytest.raises(ConstructionApiClientError) as caught:
                await call
            assert caught.value.status_code == (
                403 if case == "denied" else 409 if case == "conflict" else 502
            )
    if case == "denied":
        assert calls == []
    else:
        import json
        assert len(calls) == 1
        assert calls[0].url.path == (
            "/api/v1/construction/settings/tax-profiles/" + str(profile_id)
            + "/rules" + ("/" + str(rule_id) if case == "patch" else "")
        )
        assert calls[0].method == ("PATCH" if case == "patch" else "POST")
        assert json.loads(calls[0].content) == body
        assert calls[0].headers["idempotency-key"] == "stable-rule-key"


@pytest.fixture
def rule_screen(screen, monkeypatch):
    c = screen
    c.api.list_profiles.return_value["items"] = [c.profile]
    c.rule = {
        "id": str(uuid4()), "tenant_id": str(c.tenant),
        "tax_profile_id": c.profile["id"], "category": "WORK",
        "rate_percent": "12.50", "row_version": 7,
        "created_by": str(c.user),
        "created_at": c.profile["created_at"], "updated_at": c.profile["updated_at"],
    }
    c.rule_page = {**c.common, "items": []}
    async def write_rule(**kwargs):
        operation = kwargs["request"]
        item = deepcopy(c.rule)
        item.update({
            key: value for key, value in operation["body"].items()
            if key != "expected_row_version"
        })
        item["row_version"] = (
            1 if operation["operation"] == "create"
            else operation["body"]["expected_row_version"] + 1
        )
        return {**c.common, "item": {**item, "changed": True}}
    c.rule_api = NS(
        context=c.api.context,
        list_rules=AsyncMock(return_value=c.rule_page),
        get_rule=AsyncMock(side_effect=lambda **kwargs: {
            **c.common, "item": deepcopy(c.rule),
        }),
        get_profile=c.api.get_profile,
        write_rule=AsyncMock(side_effect=write_rule),
    )
    c.successful_rule_write = write_rule
    monkeypatch.setattr(rules, "TaxRuleBotApi", lambda service: c.rule_api)
    monkeypatch.setattr(rules, "construction_bot_api_entry", tax.construction_bot_api_entry)
    return c


async def open_rules(c):
    await open_screen(c)
    await click(c, "open", 0)
    await click(c, "rules")


async def ready_rule(c, rate="0"):
    await open_rules(c)
    await click(c, "rnew")
    assert c.data[tax.VIEW]["fields"] == {}
    await click(c, "rcat", 0)
    assert c.data[tax.VIEW]["fields"] == {"category": "WORK"}
    c.message.text = rate
    await rules.rate_message(c.message, c.state)
    assert c.data[tax.VIEW]["phase"] == "review"


@pytest.mark.asyncio
async def test_create_requires_category_rate_and_confirmation(rule_screen):
    c = rule_screen
    await ready_rule(c, "0")
    c.rule_api.write_rule.assert_not_awaited()
    await click(c, "save")
    request = c.rule_api.write_rule.await_args.kwargs["request"]
    assert request["profile_id"] == c.profile["id"]
    assert request["body"] == {"category": "WORK", "rate_percent": "0"}
    assert c.data[tax.VIEW]["phase"] == "rcard"
    assert c.data[tax.PENDING] == {}
    c.api.write_profile.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_categories_are_not_offered_for_new_rule(rule_screen):
    c = rule_screen
    c.rule_page["items"] = [c.rule]
    await open_rules(c)
    await click(c, "rnew")
    assert "WORK" not in c.data[tax.VIEW]["category_codes"]
    c.rule_api.write_rule.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_edit_is_sparse_and_versioned(rule_screen):
    c = rule_screen
    c.rule_page["items"] = [c.rule]
    await open_rules(c)
    await click(c, "ropen", 0)
    await click(c, "rfield", 1)
    c.message.text = "20,125"
    await rules.rate_message(c.message, c.state)
    await click(c, "save")
    request = c.rule_api.write_rule.await_args.kwargs["request"]
    assert request["rule_id"] == c.rule["id"]
    assert request["body"] == {
        "rate_percent": "20.125", "expected_row_version": 7,
    }


@pytest.mark.asyncio
async def test_cancel_performs_no_write(rule_screen):
    c = rule_screen
    await ready_rule(c)
    await click(c, "rules")
    c.rule_api.write_rule.assert_not_awaited()
    assert c.data[tax.VIEW]["phase"] == "rlist"


@pytest.mark.asyncio
async def test_uncertain_retry_uses_identical_rule_request(rule_screen):
    c = rule_screen
    await ready_rule(c, "20.125")
    attempts = 0
    async def uncertain(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConstructionApiClientError(status_code=504, code="api_timeout")
        return await c.successful_rule_write(**kwargs)
    c.rule_api.write_rule.side_effect = uncertain
    await click(c, "save")
    original = deepcopy(c.rule_api.write_rule.await_args.kwargs["request"])
    await click(c, "list")
    await click(c, "retry")
    assert c.rule_api.write_rule.await_args.kwargs["request"] == original
    assert c.data[tax.PENDING] == {}


@pytest.mark.asyncio
async def test_duplicate_category_is_not_turned_into_update(rule_screen):
    c = rule_screen
    await ready_rule(c)
    c.rule_api.write_rule.side_effect = ConstructionApiClientError(
        status_code=409, code="TAX_RULE_CATEGORY_EXISTS",
    )
    await click(c, "save")
    assert c.rule_api.write_rule.await_count == 1
    assert c.data[tax.VIEW]["phase"] == "blocked"
    assert c.data[tax.PENDING] == {}
    assert "категор" in c.sent.await_args.kwargs["text"].lower()


@pytest.mark.asyncio
async def test_back_returns_to_exact_parent_profile(rule_screen):
    c = rule_screen
    await open_rules(c)
    await click(c, "rprofile")
    assert c.data[tax.VIEW]["phase"] == "card"
    assert c.data[tax.VIEW]["profile_id"] == c.profile["id"]


@pytest.mark.asyncio
async def test_late_rules_read_cannot_restore_another_context(rule_screen):
    c = rule_screen
    await open_rules(c)
    count = c.sent.await_count
    async def late(**kwargs):
        c.data[tax.CONTEXT] = {}
        return c.rule_page
    c.rule_api.list_rules.side_effect = late
    await click(c, "rules")
    assert c.sent.await_count == count


def test_rule_labels_and_callback_lengths():
    assert all(len(values) == 7 for values in LABELS.values())
    view = {"nonce": "a" * 32}
    for action, index in (("rfield", 1), ("rcat", 4), ("rprofile", None)):
        assert len(tax.cb(view, action, index).encode("utf-8")) <= 64
