"""Bot API transport adapter over the shared Platform binding workflow.

No handler is registered here. The caller provides the trusted aiogram dispatch
context and an active Platform transaction. The existing workflow owns commit,
rollback, token/identity checks, idempotency and mandatory audit.
"""
import re
from uuid import uuid4

from api.telegram_auth import TelegramAuthenticationError
from services.platform_telegram_binding_bot_proof import TelegramBotBindingUpdateVerifier
from services.platform_telegram_binding_presentation import read_platform_telegram_binding_start_parameter
from services.platform_telegram_binding_workflow import build_platform_telegram_binding_workflow


async def consume_platform_telegram_binding_bot_start(*, session, bot, update,
    encryption_key, now_provider=None):
    try:
        verifier = TelegramBotBindingUpdateVerifier(bot=bot, update=update)
        command = re.fullmatch(r'/start(?:@[A-Za-z0-9_]{5,32})? ([A-Za-z0-9_-]+)', verifier.command_text)
        if command is None:
            raise TelegramAuthenticationError('Trusted Telegram binding command is unavailable.')
        token = read_platform_telegram_binding_start_parameter(command.group(1))
        workflow = build_platform_telegram_binding_workflow(session=session, encryption_key=encryption_key,
            telegram_verifier=verifier, now_provider=now_provider)
        # The existing internal proof parameter also carries this opaque Bot
        # proof. Public HTTP still accepts signed initData strings exclusively.
        return await workflow.consume_binding(token=token, telegram_init_data=verifier.proof,
            idempotency_key=verifier.idempotency_key, trace_id='telegram-binding:' + uuid4().hex)
    except BaseException:
        if session.in_transaction():
            await session.rollback()
        raise
