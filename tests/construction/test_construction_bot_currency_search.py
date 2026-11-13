"""Currency search presentation, lifecycle and unchanged write boundary."""
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from handlers.construction import construction_stage2_settings as handler
from services.construction.construction_api_client import ConstructionApiClientError
from test_construction_bot_stage2_settings import language_data, screen, click
from test_construction_bot_stage2_currency import currency_screen, open_currency


@pytest.mark.parametrize("query,expected", [
    (" uAh ", ["UAH"]),
    ("EURO", ["EUR"]),
    ("us dollar", ["USD"]),
    ("dollar", ["USD", "CAD"]),
    ("nothing matches", []),
    ("", ["UAH", "EUR", "USD", "CAD"]),
])
def test_search_matches_catalog_codes_and_names(query, expected):
    options = [
        {"code": "UAH", "name": "Hryvnia"},
        {"code": "EUR", "name": "Euro"},
        {"code": "USD", "name": "US Dollar"},
        {"code": "CAD", "name": "Canadian Dollar"},
    ]
    assert [
        item["code"] for item in handler.filter_currencies(options, query)
    ] == expected


@pytest.mark.parametrize("query", ["", "   ", "x" * 81, "USD\x00", "USD\u202e"])
def test_invalid_query_is_not_used(query):
    with pytest.raises(ValueError):
        handler.normalize_currency_query(query)


@pytest.fixture
def search_screen(currency_screen):
    c = currency_screen
    names = {"UAH": "Hryvnia", "EUR": "Euro", "USD": "US Dollar"}
    for item in c.currency_result["item"]["currencies"]:
        item["name"] = names.get(item["code"], item["name"])
    c.message = NS(
        from_user=c.callback.from_user,
        chat=NS(id=123, type="private"),
        text="usd",
        answer=AsyncMock(return_value=NS(message_id=20)),
    )
    async def edited_menu(*, callback, **kwargs):
        return NS(message_id=callback.message.message_id)
    c.sent.side_effect = edited_menu
    return c


async def begin_search(c):
    await open_currency(c)
    rows = c.sent.await_args.kwargs["reply_markup"].inline_keyboard
    assert any(":search:" in b.callback_data for row in rows for b in row)
    await click(c, "search")
    assert c.data[handler.VIEW]["phase"] == "currency_search"


async def send_query(c, text):
    c.message.text = text
    await handler.currency_search_message(c.message, c.state)
    c.callback.message.message_id = c.data["last_menu_message_id"]


@pytest.mark.asyncio
async def test_search_requires_review_before_existing_versioned_save(search_screen):
    c = search_screen
    await begin_search(c)
    assert await handler.CurrencySearchInput()(c.message, c.state) is True
    await send_query(c, " usd ")
    assert c.data[handler.VIEW]["choice_codes"] == ["USD"]
    assert c.data[handler.VIEW]["currency_query"] == "usd"
    c.currency.write.assert_not_awaited()
    await click(c, "c", 0)
    assert c.data[handler.VIEW]["selected_code"] == "USD"
    c.currency.write.assert_not_awaited()
    await click(c, "save")
    args = c.currency.write.await_args.kwargs
    assert args["currency_code"] == "USD"
    assert args["expected_row_version"] == 7
    assert "query" not in args and "currency_query" not in args


@pytest.mark.asyncio
async def test_no_results_never_selects_currency_and_all_restores_catalog(search_screen):
    c = search_screen
    await begin_search(c)
    await send_query(c, "no such currency")
    assert c.data[handler.VIEW]["choice_codes"] == []
    rows = c.message.answer.await_args.kwargs["reply_markup"].inline_keyboard
    assert not any(":c:" in b.callback_data for row in rows for b in row)
    await click(c, "all")
    assert c.data[handler.VIEW]["choice_codes"] == [
        item["code"] for item in c.currency_result["item"]["currencies"][:8]
    ]
    assert not c.data[handler.VIEW].get("currency_query")
    c.currency.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_filtered_pagination_and_selection_keep_exact_code(search_screen):
    c = search_screen
    for item in c.currency_result["item"]["currencies"]:
        item["name"] = "Test Dollar"
    await begin_search(c)
    await send_query(c, "dollar")
    await click(c, "p", 1)
    assert c.data[handler.VIEW]["currency_query"] == "dollar"
    assert c.data[handler.VIEW]["choice_codes"] == ["AUD", "BRL"]
    c.currency_result["item"]["currencies"].reverse()
    await click(c, "c", 0)
    assert c.data[handler.VIEW]["selected_code"] == "AUD"
    c.currency.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_input_keeps_prompt_without_api_call(search_screen):
    c = search_screen
    await begin_search(c)
    count = c.currency.read.await_count
    await send_query(c, "x" * 81)
    assert c.currency.read.await_count == count
    assert c.data[handler.VIEW]["phase"] == "currency_search"
    c.currency.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_input_discards_search_without_write(search_screen):
    c = search_screen
    await begin_search(c)
    await click(c, "currency")
    assert c.data[handler.VIEW]["phase"] == "catalog"
    assert await handler.CurrencySearchInput()(c.message, c.state) is False
    c.currency.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_late_search_cannot_restore_other_context(search_screen):
    c = search_screen
    await begin_search(c)
    async def late(**kwargs):
        c.data[handler.CONTEXT] = {}
        return c.currency_result
    c.currency.read.side_effect = late
    await send_query(c, "USD")
    c.message.answer.assert_not_awaited()
    c.currency.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoked_authority_blocks_search_results(search_screen):
    c = search_screen
    await begin_search(c)
    c.currency_result["can_manage"] = False
    await send_query(c, "USD")
    assert c.data[handler.VIEW]["phase"] == "blocked"
    assert not c.data[handler.VIEW].get("choice_codes")
    c.currency.write.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["group", "command", "sender", "menu"])
async def test_input_filter_requires_current_private_bound_prompt(search_screen, change):
    c = search_screen
    await begin_search(c)
    if change == "group":
        c.message.chat.type = "group"
    elif change == "command":
        c.message.text = "/start"
    elif change == "sender":
        c.message.from_user = NS(id=999, is_bot=False)
    else:
        c.data["last_menu_message_id"] = 999
    assert await handler.CurrencySearchInput()(c.message, c.state) is False


def test_search_callbacks_are_within_telegram_limit():
    view = {"nonce": "a" * 32}
    for action in ("search", "all"):
        assert len(handler.cb(view, action).encode("utf-8")) <= 64
