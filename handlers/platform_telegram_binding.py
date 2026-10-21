"""Private Bot API /start binding transport; registered before normal onboarding."""
import logging

from aiogram import Bot, Router
from aiogram.filters import CommandObject, CommandStart, Filter
from aiogram.types import Message, Update

from api.dependencies import get_api_request_rate_limit_service
from api.settings import ApiIdempotencySettings
from api.telegram_auth import TelegramAuthenticationError
from database.repositories.platform_telegram_accounts import PlatformTelegramAccountAlreadyLinkedError
from database.session import get_session
from services.api_rate_limits import ApiRequestRateLimitExceededError
from services.platform_telegram_binding_bot import consume_platform_telegram_binding_bot_start
from services.platform_telegram_binding_bot_proof import TelegramBotBindingUpdateVerifier
from services.platform_telegram_binding_presentation import (
    PlatformTelegramBindingPresentationError, read_platform_telegram_binding_start_parameter,
)
from services.platform_telegram_binding_tokens import PlatformTelegramBindingTokenDeniedError
from ui.texts import t
from utils.telegram_cleanup import delete_telegram_messages


logger = logging.getLogger(__name__)


class BindingStartArgument(Filter):
    async def __call__(self, message: Message, command: CommandObject):
        # Reserve malformed/case-altered binding arguments too, so they cannot
        # fall through to normal registration. The token itself is not changed.
        return isinstance(command.args, str) and command.args.lstrip().lower().startswith('bind_')


def _same_dispatch_context(message, bot, update):
    if not isinstance(bot, Bot) or not isinstance(message, Message) or not isinstance(update, Update):
        return False
    try:
        return update.bot is bot and update.message is message and message.bot is bot
    except RuntimeError:
        return False


def get_binding_encryption_key():
    return ApiIdempotencySettings.from_env().encryption_key


async def binding_start(message: Message, bot: Bot, command: CommandObject, event_update: Update | None = None):
    if not _same_dispatch_context(message, bot, event_update):
        # No response or database work for detached or mismatched input.
        return
    language = (getattr(message.from_user, 'language_code', None) or 'ru').strip().lower()
    try:
        await delete_telegram_messages(bot=bot, chat_id=message.chat.id, message_ids=[message.message_id])
    except Exception:
        # Cleanup is best-effort; never log a message body or exception detail.
        logger.warning('platform_telegram_binding_source_cleanup_failed')
    if message.chat.type != 'private':
        await message.answer(t('platform_telegram_binding_private', language))
        return
    response = 'platform_telegram_binding_unavailable'
    try:
        TelegramBotBindingUpdateVerifier(bot=bot, update=event_update)
        rates = get_api_request_rate_limit_service()
        await rates.ensure_telegram_binding_allowed(bot_id=bot.id, telegram_user_id=message.from_user.id)
        read_platform_telegram_binding_start_parameter(command.args)
        encryption_key = get_binding_encryption_key()
        async with get_session() as session:
            if not session.in_transaction():
                await session.begin()
            await consume_platform_telegram_binding_bot_start(session=session, bot=bot, update=event_update,
                encryption_key=encryption_key)
        response = 'platform_telegram_binding_success'
    except ApiRequestRateLimitExceededError:
        response = 'platform_telegram_binding_rate_limited'
    except PlatformTelegramAccountAlreadyLinkedError:
        response = 'platform_telegram_binding_conflict'
    except (TelegramAuthenticationError, PlatformTelegramBindingTokenDeniedError, PlatformTelegramBindingPresentationError):
        response = 'platform_telegram_binding_invalid'
    except Exception as error:
        # DB/transport exception strings can contain SQL parameters or secrets.
        logger.warning('platform_telegram_binding_failed error_type=%s', type(error).__name__)
    await message.answer(t(response, language))


def build_platform_telegram_binding_router():
    router = Router(name='platform_telegram_binding')
    router.message.register(binding_start, CommandStart(ignore_case=True), BindingStartArgument())
    return router


platform_telegram_binding_router = build_platform_telegram_binding_router()
