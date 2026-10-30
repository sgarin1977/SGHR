"""Private organization logo upload, preview and profile attachment."""
from copy import deepcopy
from io import BytesIO
import logging
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction.construction_lead_create import lock_for, private
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from services.construction.construction_company_logo_api import (
    MAX_LOGO_BYTES, SAFE_TYPES, validate_image, problem,
)
from ui.construction_company_logo import tr
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_company_logo")
logger = logging.getLogger(__name__)
VIEW = "construction_company_logo_view"
PARENT = "construction_company_profile_view"
CONTEXT = "construction_suite_context"
KEYS = (VIEW, CONTEXT, "last_menu_message_id")


class LogoInputError(ValueError):
    pass


class LimitedBuffer(BytesIO):
    def __init__(self, limit):
        super().__init__()
        self.limit = limit

    def write(self, chunk):
        if self.tell() + len(chunk) > self.limit:
            raise LogoInputError("Image exceeds the size limit.")
        return super().write(chunk)


def button(text, callback):
    return InlineKeyboardButton(text=text, callback_data=callback)


def cb(view, action):
    return "CLG:" + action + ":" + view["nonce"]


def same(data, snapshot):
    return all(data.get(key) == snapshot.get(key) for key in KEYS)


def bound(data, view, sender):
    context = data.get(CONTEXT)
    return (
        isinstance(view, dict) and view.get("telegram_user_id") == sender
        and isinstance(context, dict) and context.get("telegram_user_id") == sender
        and context.get("tenant_id") == view.get("tenant_id")
        and view.get("menu_id") is not None
        and view.get("menu_id") == data.get("last_menu_message_id")
    )


async def suspend_logo_view(state):
    view = (await state.get_data()).get(VIEW)
    if isinstance(view, dict) and view:
        if view.get("attempted") or view.get("new_file_id"):
            view = {key: value for key, value in view.items() if key != "legal_name"}
            view["menu_id"] = None
        else:
            view = {}
        await state.update_data(**{VIEW: view})


async def logo_navigation_middleware(handler, event, data):
    state = data.get("state")
    if state is not None and private(event) and not (event.data or "").startswith("CLG:"):
        await suspend_logo_view(state)
    return await handler(event, data)


async def show(event, state, view, text, rows):
    data = await state.get_data()
    if data.get(VIEW) != view or not bound(data, view, event.from_user.id):
        return
    snapshot = {key: deepcopy(data.get(key)) for key in KEYS}
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    if getattr(event, "message", None) is not None:
        sent = await edit_or_replace_menu_message(
            callback=event, text=text, reply_markup=markup,
        )
    else:
        sent = await event.answer(text, reply_markup=markup)
    if not same(await state.get_data(), snapshot):
        return
    await state.update_data(**{
        VIEW: {**view, "menu_id": sent.message_id},
        "last_menu_message_id": sent.message_id,
    })


def back_rows(view):
    return [
        [button(tr("refresh", view["language"]), cb(view, "refresh"))],
        [button(tr("open_profile", view["language"]), "CP:open")],
    ]


async def fetch(state, view, bot, event_update):
    data = await state.get_data()
    if data.get(VIEW) != view or not bound(data, view, view["telegram_user_id"]):
        raise ValueError("Logo context is no longer active.")
    snapshot = {key: deepcopy(data.get(key)) for key in KEYS}
    async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
        result = await service.company_logo_context(
            telegram_user_id=view["telegram_user_id"],
            tenant_id=UUID(view["tenant_id"]),
            expected_user_id=UUID(view["actor_user_id"]),
        )
    if not same(await state.get_data(), snapshot) or result["actor_user_id"] != view["actor_user_id"]:
        raise ValueError("Logo context changed.")
    return result


def context_view(view, result):
    result_view = {
        key: value for key, value in view.items()
        if key not in ("legal_name", "new_file_id", "mime_type", "size_bytes", "notice")
    }
    result_view.update(
        profile_id=result["profile_id"], expected_row_version=result["row_version"],
        logo_file_id=result["logo_file_id"],
        max_bytes=result["max_bytes"], mime_types=list(result["mime_types"]),
        phase="menu", attempted=False, nonce=uuid4().hex,
        language=result["language"] or view["language"],
    )
    return result_view


async def menu(event, state, view, result):
    view = context_view(view, result)
    language = view["language"]
    text = tr("button", language) + "\n\n" + tr(
        "configured" if view["logo_file_id"] else "not_configured", language,
    )
    rows = []
    if result["can_transfer"] and view["logo_file_id"]:
        rows.append([button(tr("preview", language), cb(view, "preview"))])
    if result["can_manage"]:
        if result["can_transfer"]:
            rows.append([button(tr("upload", language), cb(view, "upload"))])
        else:
            text += "\n\n" + tr("storage_unavailable", language)
        if view["logo_file_id"]:
            rows.append([button(tr("remove", language), cb(view, "remove"))])
    rows.extend(back_rows(view))
    await state.update_data(**{VIEW: view})
    await show(event, state, view, text, rows)


async def draft(event, state, view, notice=None):
    language, phase = view["language"], view["phase"]
    rows = []
    if phase == "image":
        text = (
            tr("send_image", language) + "\n\n"
            + ", ".join(view["mime_types"]) + "\n"
            + tr("maximum", language) + ": " + str(view["max_bytes"])
        )
    elif phase == "legal":
        text = tr("legal_name_needed", language)
    elif phase == "review":
        text = tr("review_logo", language)
        if view.get("legal_name"):
            text += "\n\n" + tr("legal_name", language) + ":\n" + view["legal_name"]
        rows.append([button(tr("save", language), cb(view, "save"))])
    elif phase == "remove":
        text = tr("confirm_remove", language)
        rows.append([button(tr("remove", language), cb(view, "save"))])
    elif phase == "done":
        text = tr("saved", language)
    elif phase == "upload_uncertain":
        text = tr("uncertain_upload", language)
    elif phase == "uncertain":
        text = tr("uncertain_write", language)
    elif phase == "blocked":
        text = tr(view.get("notice", "invalid_write"), language)
    else:
        raise ValueError("Unknown logo phase.")
    if phase in ("image", "legal", "review", "remove"):
        rows.append([button(tr("cancel", language), cb(view, "refresh"))])
    else:
        rows.extend(back_rows(view))
    if notice:
        text = notice + "\n\n" + text
    await show(event, state, view, text, rows)


async def open_screen(callback, state, bot, event_update):
    data = await state.get_data()
    source, context = data.get(PARENT), data.get(CONTEXT)
    parts = callback.data.split(":")
    if (
        len(parts) != 3 or parts[1] != "open"
        or not isinstance(source, dict) or not isinstance(context, dict)
        or source.get("nonce") != parts[2]
        or source.get("telegram_user_id") != callback.from_user.id
        or context.get("telegram_user_id") != callback.from_user.id
        or source.get("tenant_id") != context.get("tenant_id")
        or source.get("menu_id") != callback.message.message_id
        or data.get("last_menu_message_id") != callback.message.message_id
    ):
        raise ValueError("Stale company logo source.")
    from handlers.construction.construction_team import suspend_forms
    from handlers.construction.construction_area_create import suspend_area_form
    from handlers.construction.construction_company_edit import suspend_company_form
    await suspend_forms(state)
    await suspend_area_form(state)
    await suspend_company_form(state)
    view = {
        "nonce": uuid4().hex, "telegram_user_id": callback.from_user.id,
        "tenant_id": str(UUID(source["tenant_id"])),
        "actor_user_id": str(UUID(source["actor_user_id"])),
        "menu_id": callback.message.message_id,
        "language": callback.from_user.language_code or "ru",
        "phase": "menu", "attempted": False,
    }
    await state.update_data(**{VIEW: view, PARENT: {}})
    result = await fetch(state, view, bot, event_update)
    await menu(callback, state, view, result)


async def save(callback, state, view, bot, event_update):
    if view["phase"] not in ("review", "remove") or view.get("attempted"):
        raise ValueError("Logo save is not ready.")
    removing = view["phase"] == "remove"
    file_id = None if removing else UUID(view["new_file_id"])
    legal_name = view.get("legal_name") if view["profile_id"] is None else None
    if removing and view["profile_id"] is None:
        raise ValueError("Absent profile cannot be cleared.")
    view = {key: value for key, value in view.items() if key != "legal_name"}
    view.update(phase="saving", attempted=True, nonce=uuid4().hex)
    await state.update_data(**{VIEW: view})
    data = await state.get_data()
    snapshot = {key: deepcopy(data.get(key)) for key in KEYS}
    try:
        async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
            result = await service.save_company_logo(
                telegram_user_id=view["telegram_user_id"],
                tenant_id=UUID(view["tenant_id"]),
                expected_user_id=UUID(view["actor_user_id"]),
                profile_id=UUID(view["profile_id"]) if view["profile_id"] else None,
                expected_row_version=view["expected_row_version"],
                logo_file_id=file_id, legal_name=legal_name,
            )
        item = result["item"]
        if (
            result["actor_user_id"] != view["actor_user_id"]
            or item["tenant_id"] != view["tenant_id"]
            or (view["profile_id"] is not None and item["id"] != view["profile_id"])
        ):
            raise ValueError("Logo save context differs.")
        if not same(await state.get_data(), snapshot):
            return
        view["phase"] = "done"
    except Exception as error:
        logger.warning(
            "construction_bot_logo_save_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        if not same(await state.get_data(), snapshot):
            return
        status = getattr(error, "status_code", None)
        if status in (400, 401, 403, 404, 409, 422, 429):
            view.update(
                phase="blocked",
                notice="conflict_write" if status == 409 else "invalid_write",
            )
        else:
            view["phase"] = "uncertain"
    await state.update_data(**{VIEW: view})
    await draft(callback, state, view)


@router.callback_query(F.data.startswith("CLG:"))
async def logo_callback(callback, state, bot=None, event_update=None):
    language = callback.from_user.language_code or "ru"
    if not private(callback):
        await callback.answer(t("construction_bot_private", language), show_alert=True)
        return
    await callback.answer()
    async with lock_for(bot, callback.from_user.id):
        try:
            if await state.get_state() is not None:
                raise ValueError("Another form is active.")
            if callback.data.startswith("CLG:open:"):
                await open_screen(callback, state, bot, event_update)
                return
            data = await state.get_data()
            view = deepcopy(data.get(VIEW))
            parts = callback.data.split(":")
            if (
                len(parts) != 3 or not bound(data, view, callback.from_user.id)
                or view.get("nonce") != parts[2]
                or view.get("menu_id") != callback.message.message_id
            ):
                raise ValueError("Stale logo selection.")
            action, language = parts[1], view["language"]
            if action == "save":
                await save(callback, state, view, bot, event_update)
                return
            if action == "preview":
                if view["phase"] != "menu" or not view.get("logo_file_id"):
                    raise ValueError("Logo preview is unavailable.")
                snapshot = {key: deepcopy(data.get(key)) for key in KEYS}
                async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                    result = await service.download_company_logo(
                        telegram_user_id=view["telegram_user_id"],
                        tenant_id=UUID(view["tenant_id"]),
                        expected_user_id=UUID(view["actor_user_id"]),
                        expected_file_id=UUID(view["logo_file_id"]),
                    )
                if (
                    not same(await state.get_data(), snapshot)
                    or result["actor_user_id"] != view["actor_user_id"]
                    or result["id"] != view["logo_file_id"]
                ):
                    return
                await callback.message.answer_document(
                    BufferedInputFile(result["content"], filename=result["filename"]),
                    caption=tr("button", language),
                )
                return
            if action not in ("refresh", "upload", "remove"):
                raise ValueError("Unknown logo action.")
            if action != "refresh" and view["phase"] != "menu":
                raise ValueError("Logo menu is no longer active.")
            result = await fetch(state, view, bot, event_update)
            if action == "refresh":
                await menu(callback, state, view, result)
                return
            if result["can_manage"] is not True:
                raise problem(403, "permission_required")
            view = context_view(view, result)
            if action == "upload":
                if result["can_transfer"] is not True:
                    raise problem(503, "files_unavailable")
                view["phase"] = "image"
            else:
                if not view["profile_id"] or not view["logo_file_id"]:
                    raise ValueError("There is no profile logo to remove.")
                view["phase"] = "remove"
            await state.update_data(**{VIEW: view})
            await draft(callback, state, view)
        except Exception as error:
            logger.warning(
                "construction_bot_logo_failed error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            await callback.message.answer(t("construction_lead_form_stale", language))


def media(message):
    photos = getattr(message, "photo", None)
    document = getattr(message, "document", None)
    if photos:
        return photos[-1], "image/jpeg"
    if document is not None:
        mime = getattr(document, "mime_type", None)
        if mime is None:
            extension = PurePosixPath(getattr(document, "file_name", "") or "").suffix.lower()
            mime = next((key for key, values in SAFE_TYPES.items() if extension in values), None)
        return document, mime
    raise LogoInputError("An image is required.")


async def telegram_image(bot, message, *, maximum, mimes):
    selected, mime = media(message)
    if mime not in mimes:
        raise LogoInputError("Unsupported image type.")
    size = getattr(selected, "file_size", None)
    if size is not None and (type(size) is not int or not 0 < size <= maximum):
        raise LogoInputError("Invalid declared image size.")
    telegram_file = await bot.get_file(selected.file_id)
    actual_size = getattr(telegram_file, "file_size", None)
    if actual_size is not None and (
        type(actual_size) is not int or not 0 < actual_size <= maximum
    ):
        raise LogoInputError("Invalid Telegram image size.")
    destination = LimitedBuffer(maximum)
    try:
        await bot.download_file(telegram_file.file_path, destination=destination)
        content = destination.getvalue()
    finally:
        destination.close()
    if (
        size is not None and size != len(content)
        or actual_size is not None and actual_size != len(content)
    ):
        raise LogoInputError("Image size differs.")
    try:
        validate_image(content, mime, maximum)
    except Exception:
        raise LogoInputError("Invalid image.") from None
    return content, mime


class LogoInput(BaseFilter):
    async def __call__(self, message, state):
        if (
            not private(message) or (getattr(message, "text", None) or "").startswith("/")
            or await state.get_state() is not None
        ):
            return False
        data = await state.get_data()
        view = data.get(VIEW)
        return (
            bound(data, view, message.from_user.id)
            and view.get("phase") in ("image", "legal")
            and not view.get("attempted")
        )


@router.message(LogoInput())
async def logo_message(message, state, bot=None, event_update=None):
    async with lock_for(bot, message.from_user.id):
        if not await LogoInput()(message, state):
            return
        view = deepcopy((await state.get_data())[VIEW])
        if view["phase"] == "legal":
            value = getattr(message, "text", None)
            if (
                not isinstance(value, str) or not value.strip() or len(value.strip()) > 255
                or any(ord(char) < 32 for char in value)
            ):
                await draft(message, state, view, tr("invalid_input", view["language"]))
                return
            view.update(legal_name=value.strip(), phase="review", nonce=uuid4().hex)
            await state.update_data(**{VIEW: view})
            await draft(message, state, view)
            return
        snapshot = None
        try:
            result = await fetch(state, view, bot, event_update)
            if result["can_manage"] is not True:
                raise problem(403, "permission_required")
            if result["can_transfer"] is not True:
                raise problem(503, "files_unavailable")
            if (
                result["profile_id"] != view["profile_id"]
                or result["row_version"] != view["expected_row_version"]
            ):
                raise problem(409, "ROW_VERSION_CONFLICT")
            view.update(phase="uploading", attempted=True, nonce=uuid4().hex)
            await state.update_data(**{VIEW: view})
            data = await state.get_data()
            snapshot = {key: deepcopy(data.get(key)) for key in KEYS}
            content, mime = await telegram_image(
                bot, message, maximum=min(result["max_bytes"], MAX_LOGO_BYTES),
                mimes=result["mime_types"],
            )
            if not same(await state.get_data(), snapshot):
                return
            async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                uploaded = await service.upload_company_logo(
                    telegram_user_id=view["telegram_user_id"],
                    tenant_id=UUID(view["tenant_id"]),
                    expected_user_id=UUID(view["actor_user_id"]),
                    content=content, mime=mime,
                )
            if not same(await state.get_data(), snapshot):
                return
            if uploaded["actor_user_id"] != view["actor_user_id"]:
                raise ValueError("Upload actor differs.")
            view.update(
                new_file_id=str(UUID(uploaded["item"]["id"])),
                mime_type=uploaded["item"]["mime_type"],
                size_bytes=uploaded["item"]["size_bytes"],
                phase="legal" if view["profile_id"] is None else "review",
                attempted=False, nonce=uuid4().hex,
            )
        except LogoInputError:
            if snapshot is None or not same(await state.get_data(), snapshot):
                return
            view.update(phase="image", attempted=False, nonce=uuid4().hex)
            await state.update_data(**{VIEW: view})
            await draft(message, state, view, tr("invalid_image", view["language"]))
            return
        except Exception as error:
            logger.warning(
                "construction_bot_logo_upload_failed error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            data = await state.get_data()
            if snapshot is not None and not same(data, snapshot):
                return
            if snapshot is None and (
                data.get(VIEW) != view or not bound(data, view, message.from_user.id)
            ):
                return
            status = getattr(error, "status_code", None)
            view.update(
                phase="blocked" if status in (401, 403, 404, 409, 422, 429) else "upload_uncertain",
                notice="conflict_write" if status == 409 else "invalid_write",
                attempted=True, nonce=uuid4().hex,
            )
        await state.update_data(**{VIEW: view})
        await draft(message, state, view)
