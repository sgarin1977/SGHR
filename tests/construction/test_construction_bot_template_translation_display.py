"""Local translation metadata, rendering and exact-version Telegram cards."""
from copy import deepcopy
from types import SimpleNamespace as NS
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr

from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_organization_templates_bot_api import (
    OrganizationTemplatesBotApi,
)
from services.construction.construction_starter_templates_bot_api import (
    StarterTemplatesBotApi,
)
from ui.construction_template_translation import (
    LANGUAGES, LABELS, template_heading, tr,
)

from test_construction_bot_tax_profiles import screen
from test_construction_bot_organization_templates import (
    templates_screen, open_templates, click as organization_click,
)
from test_construction_bot_starter_templates import (
    starter_screen, wizard, enter, click as starter_click,
)
from handlers.construction import construction_organization_templates as organization_handler
from handlers.construction import construction_starter_templates as starter_handler


def example(language="uk", origin="CACHE", status="AUTO_TRANSLATED"):
    source_language = language if origin == "ORIGINAL" else (
        "en" if language != "en" else "ru"
    )
    item = {
        "template_id": str(uuid4()), "version_id": str(uuid4()),
        "version": 3, "source_language": source_language,
        "title": "Original <title>", "description": "Original & description",
        "display_title": "Displayed <title>",
        "display_description": "Displayed & description",
    }
    if origin == "ORIGINAL":
        item["display_title"], item["display_description"] = (
            item["title"], item["description"],
        )
    item["text_presentations"] = presentations(
        item, uuid4(), uuid4(), language, origin, status,
    )
    return item


def presentations(item, tenant, user, language="uk", origin="CACHE", status="AUTO_TRANSLATED"):
    if origin in ("ORIGINAL", "PUBLISHED_LABEL"):
        status = None
    rows = {}
    for field in ("title", "description"):
        absent = item[field] is None
        rows[field] = {
            "tenant_id": str(tenant), "user_id": str(user),
            "template_id": item["template_id"], "version_id": item["version_id"],
            "field_name": field, "target_language": language,
            "source_language": None if absent else item["source_language"],
            "source_version": None if absent else item["version"],
            "original_text": item[field],
            "display_text": None if absent else item["display_" + field],
            "translation_status": None if absent else status,
            "origin": "ORIGINAL" if absent else origin,
            "cache_row_version": (
                2 if not absent and origin in ("CACHE", "GENERATED") else None
            ),
            "cache_written": not absent and origin == "GENERATED",
        }
    return rows


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("origin,status", [
    ("ORIGINAL", None),
    ("PUBLISHED_LABEL", None),
    ("CACHE", "VERIFIED"),
    ("CACHE", "AUTO_TRANSLATED"),
    ("GENERATED", "AUTO_TRANSLATED"),
])
def test_states_and_originals_are_displayed_without_rewriting(language, origin, status):
    item = example(language, origin, status)
    before = deepcopy(item)
    text = template_heading(item, language)
    assert item == before
    assert "Original &lt;title&gt;" in text
    assert "Original &amp; description" in text
    assert "<title>" not in text
    if origin == "ORIGINAL":
        assert tr("AUTO_TRANSLATED", language) not in text
        assert tr("VERIFIED", language) not in text
    else:
        assert "Displayed &lt;title&gt;" in text
        assert tr("original", language) in text
        assert tr("PUBLISHED_LABEL" if origin == "PUBLISHED_LABEL" else status, language) in text
    if origin == "PUBLISHED_LABEL":
        assert tr("VERIFIED", language) not in text


@pytest.mark.parametrize("failure", [
    "version", "language", "original", "display", "template", "stale",
])
def test_mismatched_or_stale_metadata_is_not_displayed_as_current(failure):
    item = example()
    row = item["text_presentations"]["title"]
    if failure == "version":
        row["source_version"] += 1
    elif failure == "language":
        row["target_language"] = "pl"
    elif failure == "original":
        row["original_text"] = "Different source"
    elif failure == "display":
        row["display_text"] = "Different display"
    elif failure == "template":
        row["template_id"] = str(uuid4())
    else:
        row["translation_status"] = "STALE"
    with pytest.raises(ValueError):
        template_heading(item, "uk")


def test_absent_description_and_legacy_objects_do_not_invent_status():
    item = example()
    item["description"] = item["display_description"] = None
    item["text_presentations"] = presentations(item, uuid4(), uuid4())
    text = template_heading(item, "uk")
    assert "None" not in text
    item.pop("text_presentations")
    text = template_heading(item, "uk")
    assert text == "Original &lt;title&gt;"
    assert tr("VERIFIED", "uk") not in text
    assert tr("AUTO_TRANSLATED", "uk") not in text


def test_escaped_large_text_is_bounded_without_cutting_entities():
    item = example()
    item["title"] = "<&>" * 1000
    item["description"] = "<&>" * 10000
    item["display_title"] = "<&>" * 1000
    item["display_description"] = "<&>" * 10000
    item["text_presentations"] = presentations(item, uuid4(), uuid4())
    text = template_heading(item, "uk")
    assert len(text) <= 2600
    remaining = text.replace("&lt;", "").replace("&gt;", "").replace("&amp;", "")
    assert "&" not in remaining and "<" not in remaining and ">" not in remaining


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", [OrganizationTemplatesBotApi, StarterTemplatesBotApi])
@pytest.mark.parametrize("origin,status", [
    ("ORIGINAL", None), ("PUBLISHED_LABEL", None),
    ("CACHE", "VERIFIED"), ("CACHE", "AUTO_TRANSLATED"),
    ("GENERATED", "AUTO_TRANSLATED"),
])
async def test_http_adapters_preserve_validated_translation_metadata(adapter, origin, status):
    tenant, user = uuid4(), uuid4()
    language = "en" if origin == "ORIGINAL" else "uk"
    item = example(language, origin, status)
    rows = presentations(item, tenant, user, language, origin, status)
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={
            "data": list(rows.values()), "meta": {}, "request_id": "local",
        })

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(respond),
    ) as http:
        client = ConstructionApiClient(http=http)
        service = adapter(NS(api=NS(construction=client)))
        source = NS(
            template_id=UUID(item["template_id"]),
            version_id=UUID(item["version_id"]), version=item["version"],
            source_language=item["source_language"],
            title=item["title"], description=item["description"],
        )
        result = await service._texts(
            source,
            PlatformApiClientSession(user, tenant, SecretStr("local-only")),
            {"language": language},
        )
    assert result["text_presentations"] == rows
    assert result["display_title"] == item["display_title"]
    assert len(calls) == 1 and calls[0].method == "GET"
    assert calls[0].url.path.endswith(
        "/versions/" + item["version_id"] + "/translated-texts",
    )
    assert list(calls[0].url.params.multi_items()) == [
        ("fields", "title"), ("fields", "description"),
    ]


@pytest.mark.asyncio
async def test_organization_card_shows_status_but_stores_only_selectors(templates_screen):
    c = templates_screen
    c.item["text_presentations"] = presentations(c.item, c.tenant, c.user)
    await open_templates(c)
    await organization_click(c, "open", 0)
    text = c.sent.await_args.kwargs["text"]
    assert tr("AUTO_TRANSLATED", "uk") in text
    assert c.item["title"] in text
    assert c.item["display_title"] in text
    assert "text_presentations" not in repr(c.data[organization_handler.VIEW])
    assert c.item["display_title"] not in repr(c.data[organization_handler.VIEW])


@pytest.mark.asyncio
async def test_starter_preview_keeps_copy_explicit_and_metadata_out_of_fsm(starter_screen):
    c = starter_screen
    c.starter["text_presentations"] = presentations(
        c.starter, c.tenant, c.user, origin="PUBLISHED_LABEL", status=None,
    )
    c.starter_api.get_starter.return_value = {
        **c.common, "item": deepcopy(c.starter),
    }
    await enter(c)
    await starter_click(c, "pick", 0)
    text = c.sent.await_args.kwargs["text"]
    assert tr("PUBLISHED_LABEL", "uk") in text
    assert tr("VERIFIED", "uk") not in text
    assert c.starter["title"] in text
    assert "text_presentations" not in repr(c.data[starter_handler.VIEW])
    assert c.starter["display_title"] not in repr(c.data[starter_handler.VIEW])
    c.starter_api.copy.assert_not_awaited()


def test_published_label_has_seven_translations():
    assert all(len(values) == len(LANGUAGES) for values in LABELS.values())
