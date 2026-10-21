"""Internal Platform proof adapter for aiogram's trusted Bot API polling path.

Only call this from a server-dispatched Update received by the configured bot.
This class does not authenticate arbitrary JSON or create a Platform session.
It supplies Telegram identity to the existing token-verified binding operation.
"""
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.types import Message, Update

from api.telegram_auth import (
    DEFAULT_FUTURE_SKEW_SECONDS, TelegramAuthenticationError, TelegramIdentity,
)


def _denied():
    return TelegramAuthenticationError('Trusted Telegram binding update is unavailable.')


class TelegramBotBindingUpdateVerifier:
    """Snapshot one private, unedited Bot API message and issue an opaque proof.

    The Bot/Update objects must be the actual aiogram dispatch context. A web
    request body, user ID, forwarded message or detached Update is not a proof.
    The identity is snapshotted; later changes to the Pydantic objects are not
    used by the workflow. Each verifier accepts only its own proof object.
    """
    def __init__(self, *, bot, update):
        if (not isinstance(bot, Bot) or type(bot.id) is not int or bot.id <= 0 or not isinstance(update, Update)
            or type(update.update_id) is not int or update.update_id < 0):
            raise _denied()
        try:
            if update.bot is not bot:
                raise _denied()
            message = update.message
            if not isinstance(message, Message) or message.bot is not bot:
                raise _denied()
        except RuntimeError:
            raise _denied() from None
        if any(getattr(update, name) is not None for name in type(update).model_fields
            if name not in ('update_id', 'message')):
            raise _denied()
        sender = message.from_user
        if (sender is None or sender.is_bot or type(sender.id) is not int or sender.id <= 0
            or message.chat.type != 'private' or message.chat.id != sender.id
            or type(message.message_id) is not int or message.message_id <= 0
            or message.sender_chat is not None or message.forward_origin is not None
            or message.via_bot is not None or message.edit_date is not None
            or message.business_connection_id is not None
            or not isinstance(message.text, str) or not message.text or len(message.text) > 128
            or not isinstance(message.date, datetime) or message.date.tzinfo is None
            or message.date.utcoffset() is None):
            raise _denied()
        self._proof = object()
        self._identity = TelegramIdentity(platform_user_id=sender.id,
            username=sender.username, first_name=sender.first_name, last_name=sender.last_name,
            language_code=sender.language_code, auth_date=message.date.astimezone(timezone.utc))
        self._bot_id = bot.id
        self._update_id = update.update_id
        self._text = message.text

    @property
    def proof(self):
        return self._proof

    @property
    def command_text(self):
        return self._text

    @property
    def idempotency_key(self):
        # A retry of the same delivered Update uses the same key after restart.
        # The shared workflow additionally binds the key to token hash and TG ID.
        return f'telegram-bot-binding:{self._bot_id}:{self._update_id}'

    def verify(self, proof, *, now=None):
        if proof is not self._proof:
            raise _denied()
        instant = now if now is not None else datetime.now(timezone.utc)
        if (not isinstance(instant, datetime) or instant.tzinfo is None
            or instant.utcoffset() is None):
            raise _denied()
        age = instant.astimezone(timezone.utc) - self._identity.auth_date
        # A delayed redelivery remains authenticated by the Bot API transport.
        # The shared operation enforces token TTL on first consume; its existing
        # idempotency retention governs replay of an earlier successful update.
        if age < -timedelta(seconds=DEFAULT_FUTURE_SKEW_SECONDS):
            raise _denied()
        return self._identity
