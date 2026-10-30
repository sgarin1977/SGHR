"""Internal Platform proof for an aiogram-dispatched private message/callback.

This is a server-side transport adapter, not an authenticator for posted JSON.
It must receive the actual Bot and Update from the polling dispatch context.
"""
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.types import CallbackQuery, Message, Update

from api.telegram_auth import TelegramAuthenticationError, TelegramIdentity


PROOF_MAX_AGE_SECONDS = 60


def _denied():
    return TelegramAuthenticationError('Trusted Platform bot update is unavailable.')


class PlatformBotUpdateVerifier:
    def __init__(self, *, bot, update, now_provider=None):
        if not isinstance(bot, Bot) or not isinstance(update, Update):
            raise _denied()
        try:
            if update.bot is not bot or type(update.update_id) is not int or update.update_id < 0:
                raise _denied()
            present = [name for name in type(update).model_fields
                       if name != 'update_id' and getattr(update, name) is not None]
            if present == ['message']:
                message = update.message
                if not isinstance(message, Message) or message.bot is not bot:
                    raise _denied()
                sender = message.from_user
                if message.edit_date is not None:
                    raise _denied()
            elif present == ['callback_query']:
                callback = update.callback_query
                if not isinstance(callback, CallbackQuery) or callback.bot is not bot:
                    raise _denied()
                message = callback.message
                if (not isinstance(message, Message) or message.bot is not bot
                        or callback.inline_message_id is not None):
                    raise _denied()
                sender = callback.from_user
                # The menu being clicked must have been sent by this same bot.
                if (message.from_user is None or not message.from_user.is_bot
                        or message.from_user.id != bot.id):
                    raise _denied()
            else:
                raise _denied()
        except RuntimeError:
            raise _denied() from None
        if (sender is None or sender.is_bot or type(sender.id) is not int or sender.id <= 0
                or message.chat.type != 'private' or message.chat.id != sender.id
                or message.sender_chat is not None or message.forward_origin is not None
                or message.via_bot is not None or message.business_connection_id is not None):
            raise _denied()
        clock = now_provider or (lambda: datetime.now(timezone.utc))
        received_at = clock()
        if (not isinstance(received_at, datetime) or received_at.tzinfo is None
                or received_at.utcoffset() is None):
            raise _denied()
        self._received_at = received_at.astimezone(timezone.utc)
        self._identity = TelegramIdentity(platform_user_id=sender.id, username=sender.username,
            first_name=sender.first_name, last_name=sender.last_name,
            language_code=sender.language_code, auth_date=self._received_at)
        self._proof = object()
        self.bot_id = bot.id
        self.update_id = update.update_id

    @property
    def proof(self):
        return self._proof

    def verify(self, proof, *, now=None):
        instant = now if now is not None else datetime.now(timezone.utc)
        if (proof is not self._proof or not isinstance(instant, datetime)
                or instant.tzinfo is None or instant.utcoffset() is None):
            raise _denied()
        age = instant.astimezone(timezone.utc) - self._received_at
        if age < timedelta(0) or age >= timedelta(seconds=PROOF_MAX_AGE_SECONDS):
            raise _denied()
        return self._identity
