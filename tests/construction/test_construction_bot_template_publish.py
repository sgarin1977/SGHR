"""Reviewed publication keeps original receipt identity and uncertain retry intent."""
from copy import deepcopy
from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4
import json

import httpx
import pytest
from pydantic import SecretStr

from test_construction_bot_template_drafts import (
    screen, templates_screen, draft_screen, click, draft_data,
)
from test_construction_bot_template_draft_check import check_screen, checked, open_draft
from test_construction_bot_organization_templates import version
from handlers.construction import construction_organization_templates as cards
from handlers.construction import construction_template_publish as handler
from services.construction.construction_template_publish_bot_api import (
    TemplatePublishBotApi, Summary,
)
from services.construction.construction_template_draft_header_bot_api import digest
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from api.routes.construction_template_versions import TemplatePublicationData
from ui.construction_template_publish import LABELS


def selector(raw):
    return {
        name: raw[name] for name in (
            "template_id", "version_id", "version", "version_row_version", "template_row_version",
        )
    } | {"definition_hash": digest(raw["definition"])}


def publication(raw):
    result = {
        name: deepcopy(raw[name])
        for name in TemplatePublicationData.model_fields if name in raw
    }
    result.update(
        version_row_version=raw["version_row_version"] + 1,
        content_hash=digest(raw["definition"]), published_at=raw["updated_at"],
        section_count=2, question_count=3,
    )
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "ok", "inactive", "stale", "foreign", "hash", "counter", "permission",
])
async def test_existing_publication_contract_and_replay_without_draft_reread(case):
    tenant, user = uuid4(), uuid4()
    raw = draft_data(version(tenant), uuid4())
    selected = selector(raw)
    if case == "inactive":
        raw["active"] = False
    receipt = publication(raw)
    if case == "stale":
        raw["version_row_version"] = 9
    elif case == "foreign":
        receipt["tenant_id"] = str(uuid4())
    elif case == "hash":
        receipt["content_hash"] = "0" * 64
    elif case == "counter":
        receipt["version_row_version"] = True
    calls = []
    allowed = {"value": True}

    def transport(request):
        calls.append(request)
        if request.method == "GET":
            data = raw
        elif request.url.path.endswith("/validate"):
            data = checked(selected, tenant, active=raw["active"])
        else:
            assert request.url.path.endswith("/publish")
            data = receipt
        return httpx.Response(200, json={"data": data, "meta": {}, "request_id": "local"})

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        client = ConstructionApiClient(http=http)
        client.company_profile_capabilities = AsyncMock(side_effect=lambda **kwargs: NS(data={
            "tenant_id": str(tenant), "can_manage_settings": allowed["value"],
        }))
        entry = NS(
            _presentation=AsyncMock(return_value=(
                PlatformApiClientSession(user, tenant, SecretStr("local-only")),
                {"name": "Org", "language": "uk"},
            )), api=NS(construction=client),
        )
        api = TemplatePublishBotApi(entry)
        binding = dict(telegram_user_id=123, tenant_id=tenant, expected_user_id=user)
        if case == "stale":
            with pytest.raises(ConstructionApiClientError) as caught:
                await api.prepare_publish(selector=selected, **binding)
            assert caught.value.status_code == 409
            assert len(calls) == 1
            return
        reviewed = await api.prepare_publish(selector=selected, **binding)
        operation = {**reviewed["request"], "key": "same-publication-key"}
        assert len(calls) == 2
        assert calls[0].method == "GET" and calls[1].url.path.endswith("/validate")
        assert "idempotency-key" not in calls[1].headers
        if case == "permission":
            allowed["value"] = False
        if case in ("foreign", "hash", "counter", "permission"):
            with pytest.raises(ConstructionApiClientError) as caught:
                await api.publish(request=operation, **binding)
            assert caught.value.status_code == (403 if case == "permission" else 502)
        else:
            first = await api.publish(request=operation, **binding)
            second = await api.publish(request=deepcopy(operation), **binding)
            assert first["item"] == second["item"]
            assert first["item"]["version_row_version"] == 9
            assert first["item"]["created_by"] == raw["created_by"]
            assert first["item"]["active"] is (case != "inactive")
        posts = calls[2:]
        if case == "permission":
            assert posts == []
        else:
            assert posts
            for request in posts:
                assert request.method == "POST" and request.url.path.endswith("/publish")
                assert not request.url.query
                assert request.headers["idempotency-key"] == "same-publication-key"
                assert json.loads(request.content) == {"expected_row_version": 8}


@pytest.fixture
def publication_screen(check_screen, monkeypatch):
    c = check_screen
    raw = draft_data(version(c.tenant), c.draft_id)
    c.publish_request = {
        "selector": deepcopy(c.current) | {},
        "summary": {},
        "body": {"expected_row_version": 8},
    }
    c.publish_request["selector"] = {
        name: c.current[name] for name in (
            "template_id", "version_id", "version", "version_row_version",
            "template_row_version", "definition_hash",
        )
    }
    raw.update(section_count=2, question_count=3)
    c.publish_request["summary"] = {name: raw[name] for name in Summary.model_fields}
    c.published = publication(raw)
    c.published["content_hash"] = c.current["definition_hash"]
    c.publish_api = NS(
        prepare_publish=AsyncMock(side_effect=lambda **kwargs: {
            **c.common, "request": deepcopy(c.publish_request),
        }),
        publish=AsyncMock(side_effect=lambda **kwargs: {
            **c.common, "item": deepcopy(c.published),
        }),
        read_draft=c.draft_api.read_draft,
        get_template=c.templates_api.get_template,
    )
    monkeypatch.setattr(handler, "TemplatePublishBotApi", lambda service: c.publish_api)

    @asynccontextmanager
    async def entry(**kwargs):
        yield NS()

    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    return c


async def open_review(c):
    await open_draft(c)
    await click(c, "draftcheck")
    await click(c, "pubreview")


@pytest.mark.asyncio
async def test_publication_requires_review_and_duplicate_confirmation_cannot_write(publication_screen):
    c = publication_screen
    await open_review(c)
    assert c.data[cards.VIEW]["phase"] == "pub_review"
    assert "key" not in c.data[cards.VIEW]["publication_request"]
    c.publish_api.publish.assert_not_awaited()
    reads = c.draft_api.read_draft.await_count
    old = cards.cb(c.data[cards.VIEW], "pubconfirm")
    await click(c, "pubconfirm")
    operation = c.publish_api.publish.await_args.kwargs["request"]
    assert operation["body"] == {"expected_row_version": 8}
    assert len(operation["key"]) == 32
    assert c.draft_api.read_draft.await_count == reads
    assert c.data[cards.VIEW]["phase"] == "pub_saved"
    assert c.data[cards.PUBLISH_PENDING] == {}
    slot = c.parent["tenant_id"] + ":" + c.parent["actor_user_id"]
    assert c.published["template_id"] not in c.data[cards.DRAFTS].get(slot, {})
    c.callback.data = old
    await cards.templates_callback(c.callback, c.state)
    c.publish_api.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_publishes_nothing(publication_screen):
    c = publication_screen
    await open_review(c)
    await click(c, "pubcancel")
    assert c.data[cards.VIEW]["phase"] == "draft"
    c.publish_api.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_result_survives_navigation_and_reuses_exact_request(publication_screen):
    c = publication_screen
    await open_review(c)
    original_publish = c.publish_api.publish.side_effect
    c.publish_api.publish.side_effect = ConstructionApiClientError(status_code=504, code="api_timeout")
    await click(c, "pubconfirm")
    original = deepcopy(c.publish_api.publish.await_args.kwargs["request"])
    assert c.data[cards.VIEW]["phase"] == "pub_pending"
    await click(c, "list")
    c.publish_api.publish.side_effect = original_publish
    await click(c, "pubretry")
    assert c.publish_api.publish.await_args.kwargs["request"] == original
    c.publish_api.prepare_publish.assert_awaited_once()
    assert c.data[cards.PUBLISH_PENDING] == {}


@pytest.mark.asyncio
async def test_conflict_clears_failed_intent_and_requires_new_review(publication_screen):
    c = publication_screen
    await open_review(c)
    c.publish_api.publish.side_effect = ConstructionApiClientError(
        status_code=409, code="ROW_VERSION_CONFLICT",
    )
    await click(c, "pubconfirm")
    assert c.data[cards.VIEW]["phase"] == "pub_blocked"
    assert c.data[cards.PUBLISH_PENDING] == {}
    await click(c, "pubconfirm")
    c.publish_api.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_permission_failure_retains_original_intent(publication_screen):
    c = publication_screen
    await open_review(c)
    c.publish_api.publish.side_effect = ConstructionApiClientError(status_code=403, code="forbidden")
    await click(c, "pubconfirm")
    assert c.data[cards.VIEW]["phase"] == "pub_pending"
    assert c.data[cards.PUBLISH_PENDING]


@pytest.mark.asyncio
async def test_late_success_preserves_recovery_without_restoring_other_tenant(publication_screen):
    c = publication_screen
    await open_review(c)
    original = c.publish_api.publish.side_effect

    async def late(**kwargs):
        result = original(**kwargs)
        c.data[cards.CONTEXT]["tenant_id"] = str(uuid4())
        return result

    c.publish_api.publish.side_effect = late
    c.sent.reset_mock()
    await click(c, "pubconfirm")
    c.sent.assert_not_awaited()
    assert c.data[cards.PUBLISH_PENDING]


@pytest.mark.asyncio
async def test_unchecked_or_group_confirmation_cannot_publish(publication_screen):
    c = publication_screen
    await open_draft(c)
    await click(c, "pubconfirm")
    c.publish_api.publish.assert_not_awaited()
    await click(c, "draftcheck")
    await click(c, "pubreview")
    c.callback.message.chat.type = "group"
    await click(c, "pubconfirm")
    c.publish_api.publish.assert_not_awaited()


def test_labels_and_callback_bounds():
    assert all(len(values) == 7 for values in LABELS.values())
    for action in ("pubreview", "pubconfirm", "pubretry", "pubcancel", "pubopen"):
        assert len(cards.cb({"nonce": "a" * 32}, action).encode("utf-8")) <= 64
