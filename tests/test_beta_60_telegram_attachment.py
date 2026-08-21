from types import SimpleNamespace

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
)

from utils.telegram_cleanup import (
    send_telegram_attachment,
)


class FakeBot:
    def __init__(
        self,
        *,
        fail=False,
    ):
        self.fail = fail
        self.calls = []

    async def send_photo(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("photo", kwargs)
        )

        if self.fail:
            raise TelegramBadRequest(
                method=SimpleNamespace(),
                message=(
                    "wrong file identifier/"
                    "HTTP URL specified"
                ),
            )

        return SimpleNamespace(
            message_id=101
        )

    async def send_document(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("document", kwargs)
        )

        if self.fail:
            raise TelegramBadRequest(
                method=SimpleNamespace(),
                message=(
                    "wrong file identifier/"
                    "HTTP URL specified"
                ),
            )

        return SimpleNamespace(
            message_id=102
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attachment_type",
    [
        "photo",
        "document",
    ],
)
async def test_invalid_telegram_file_is_skipped(
    attachment_type,
):
    bot = FakeBot(fail=True)

    result = await send_telegram_attachment(
        bot=bot,
        chat_id=123,
        attachment={
            "type": attachment_type,
            "file_id": "invalid-file-id",
            "file_unique_id": "unique",
        },
        caption="Message attachment",
    )

    assert result is None
    assert len(bot.calls) == 1


@pytest.mark.asyncio
async def test_valid_telegram_file_is_sent():
    bot = FakeBot()

    result = await send_telegram_attachment(
        bot=bot,
        chat_id=456,
        attachment={
            "type": "photo",
            "file_id": "valid-file-id",
        },
        caption="Photo",
    )

    assert result.message_id == 101
    assert bot.calls == [
        (
            "photo",
            {
                "chat_id": 456,
                "photo": "valid-file-id",
                "caption": "Photo",
                "reply_markup": None,
            },
        )
    ]


@pytest.mark.asyncio
async def test_missing_attachment_file_is_skipped():
    result = await send_telegram_attachment(
        bot=FakeBot(),
        chat_id=789,
        attachment={
            "type": "photo",
            "file_id": None,
        },
    )

    assert result is None



class FakeDialogBot:
    def __init__(self):
        self.messages = []

    async def send_message(
        self,
        **kwargs,
    ):
        self.messages.append(kwargs)
        return SimpleNamespace(
            message_id=303
        )


@pytest.mark.asyncio
async def test_dialog_attachment_fallback_keeps_keyboard(
    monkeypatch,
):
    from handlers import user_dialogs

    async def unavailable_attachment(
        **kwargs,
    ):
        return None

    monkeypatch.setattr(
        user_dialogs,
        "send_telegram_attachment",
        unavailable_attachment,
    )

    bot = FakeDialogBot()
    keyboard = SimpleNamespace()

    result = await (
        user_dialogs
        .send_dialog_attachment_or_fallback(
            bot=bot,
            chat_id=123,
            attachment={
                "type": "photo",
                "file_id": "invalid",
            },
            language="en",
            caption="Sender · 12:00",
            reply_markup=keyboard,
        )
    )

    assert result.message_id == 303
    assert len(bot.messages) == 1
    assert (
        bot.messages[0]["chat_id"]
        == 123
    )
    assert (
        "Sender · 12:00"
        in bot.messages[0]["text"]
    )
    assert (
        "Could not send the file"
        in bot.messages[0]["text"]
    )
    assert (
        bot.messages[0]["reply_markup"]
        is keyboard
    )


@pytest.mark.asyncio
async def test_dialog_attachment_fallback_is_not_used_for_valid_file(
    monkeypatch,
):
    from handlers import user_dialogs

    sent_attachment = SimpleNamespace(
        message_id=404
    )

    async def available_attachment(
        **kwargs,
    ):
        return sent_attachment

    monkeypatch.setattr(
        user_dialogs,
        "send_telegram_attachment",
        available_attachment,
    )

    bot = FakeDialogBot()

    result = await (
        user_dialogs
        .send_dialog_attachment_or_fallback(
            bot=bot,
            chat_id=456,
            attachment={
                "type": "document",
                "file_id": "valid",
            },
            language="en",
            caption="Document",
        )
    )

    assert result is sent_attachment
    assert bot.messages == []



@pytest.mark.asyncio
async def test_contact_notification_falls_back_to_text(
    monkeypatch,
):
    from handlers import search

    async def unavailable_attachment(
        **kwargs,
    ):
        return None

    monkeypatch.setattr(
        search,
        "send_telegram_attachment",
        unavailable_attachment,
    )

    bot = FakeDialogBot()
    keyboard = SimpleNamespace()

    delivered = await (
        search.send_contact_notification(
            bot=bot,
            chat_id=123,
            text="Original notification",
            language="en",
            reply_markup=keyboard,
            attachment={
                "type": "photo",
                "file_id": "stale",
            },
        )
    )

    assert delivered is True
    assert len(bot.messages) == 1
    assert (
        "Original notification"
        in bot.messages[0]["text"]
    )
    assert (
        bot.messages[0]["reply_markup"]
        is keyboard
    )


@pytest.mark.asyncio
async def test_contact_notification_uses_valid_attachment(
    monkeypatch,
):
    from handlers import search

    sent_attachment = SimpleNamespace(
        message_id=505
    )
    calls = []

    async def available_attachment(
        **kwargs,
    ):
        calls.append(kwargs)
        return sent_attachment

    monkeypatch.setattr(
        search,
        "send_telegram_attachment",
        available_attachment,
    )

    bot = FakeDialogBot()
    keyboard = SimpleNamespace()

    delivered = await (
        search.send_contact_notification(
            bot=bot,
            chat_id=456,
            text="Photo notification",
            language="en",
            reply_markup=keyboard,
            attachment={
                "type": "photo",
                "file_id": "valid",
            },
        )
    )

    assert delivered is True
    assert len(calls) == 1
    assert bot.messages == []
