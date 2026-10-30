"""Construction branding UI using only existing authenticated APIs."""
from copy import deepcopy
import logging
import re
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from handlers.construction.construction_lead_create import lock_for, private
from handlers.construction.construction_company_logo import telegram_image, LogoInputError
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from services.construction.construction_company_settings_api import FIELDS, LOGO_FIELD, problem
from ui.construction_company_settings import tr, settings_text
from ui.texts import t
from utils.telegram_cleanup import edit_or_replace_menu_message

router = Router(name="construction_company_settings")
logger = logging.getLogger(__name__)
VIEW = "construction_company_settings_view"
PARENT = "construction_company_profile_view"
CONTEXT = "construction_suite_context"
KEYS = (VIEW, CONTEXT, "last_menu_message_id")
LIMITS = {
    "display_name_override": 255, "phone_override": 50,
    "email_override": 320, "website_override": 2048,
    "document_header_text": 4096, "document_footer_text": 4096,
    "accent_color": 7,
}


def button(text, callback):
    return InlineKeyboardButton(text=text, callback_data=callback)


def cb(view, action, index=None):
    value = "CST:" + action + ":" + view["nonce"]
    return value if index is None else value + ":" + str(index)


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


async def suspend_settings_view(state):
    view = (await state.get_data()).get(VIEW)
    if isinstance(view, dict) and view:
        if view.get("attempted") or view.get("uploaded_file_id"):
            view = {key: value for key, value in view.items() if key != "changes"}
            view["menu_id"] = None
        else:
            view = {}
        await state.update_data(**{VIEW: view})


async def settings_navigation_middleware(handler, event, data):
    state = data.get("state")
    if state is not None and private(event) and not (event.data or "").startswith("CST:"):
        await suspend_settings_view(state)
    return await handler(event, data)


async def show(event, state, view, text, rows):
    data = await state.get_data()
    if data.get(VIEW) != view or not bound(data, view, event.from_user.id):
        return
    snapshot = {key: deepcopy(data.get(key)) for key in KEYS}
    chunks = [text[index:index + 3000] for index in range(0, len(text), 3000)] or [text]
    sent = None
    for index, chunk in enumerate(chunks):
        if not same(await state.get_data(), snapshot):
            return
        markup = InlineKeyboardMarkup(inline_keyboard=rows) if index == len(chunks) - 1 else None
        message = getattr(event, "message", None)
        if message is not None and len(chunks) == 1:
            sent = await edit_or_replace_menu_message(
                callback=event, text=chunk, reply_markup=markup,
            )
        else:
            sent = await (message or event).answer(chunk, reply_markup=markup)
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
        raise ValueError("Settings context is no longer active.")
    snapshot = {key: deepcopy(data.get(key)) for key in KEYS}
    async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
        result = await service.construction_company_settings(
            telegram_user_id=view["telegram_user_id"],
            tenant_id=UUID(view["tenant_id"]),
            expected_user_id=UUID(view["actor_user_id"]),
        )
    if not same(await state.get_data(), snapshot) or result["actor_user_id"] != view["actor_user_id"]:
        raise ValueError("Settings context changed.")
    return result


def context_view(view, result):
    view = {
        key: value for key, value in view.items()
        if key not in ("changes", "field", "uploaded_file_id", "notice", "changed")
    }
    item = result["item"]
    view.update(
        expected_row_version=item["row_version"] if item else None,
        logo_file_id=item[LOGO_FIELD] if item else None,
        max_bytes=result["max_bytes"], mime_types=list(result["mime_types"]),
        phase="menu", attempted=False, nonce=uuid4().hex,
        language=result["language"] or view["language"],
    )
    return view


async def menu(event, state, view, result):
    view = context_view(view, result)
    language = view["language"]
    rows = []
    if result["can_manage"] is True:
        rows.extend([
            [button(tr(field, language), cb(view, "field", index))]
            for index, field in enumerate(FIELDS)
        ])
    if view["logo_file_id"] and result["can_preview_logo"]:
        rows.append([button(tr("preview", language), cb(view, "preview"))])
    rows.extend(back_rows(view))
    await state.update_data(**{VIEW: view})
    await show(event, state, view, settings_text(result, language), rows)


async def draft(event, state, view, notice=None):
    language, phase = view["language"], view["phase"]
    rows = []
    if phase == "input":
        text = tr(view["field"], language) + "\n\n" + tr("enter_value", language)
        rows.append([button(tr("reset", language), cb(view, "reset"))])
    elif phase == "logo":
        text = (
            tr(LOGO_FIELD, language) + "\n\n" + tr("send_image", language)
            + "\n" + ", ".join(view["mime_types"])
            + "\n" + tr("maximum", language) + ": " + str(view["max_bytes"])
        )
        rows.append([button(tr("reset", language), cb(view, "reset"))])
    elif phase == "logo_reset_only":
        text = tr("storage_unavailable", language)
        rows.append([button(tr("reset", language), cb(view, "reset"))])
    elif phase == "review":
        value = view["changes"][view["field"]]
        if value is None:
            text_value = tr("inherit", language)
        elif view["field"] == LOGO_FIELD:
            text_value = tr("configured", language)
        else:
            text_value = value
        text = tr("review", language) + "\n\n" + tr(view["field"], language) + ":\n" + text_value
        rows.append([button(tr("save", language), cb(view, "save"))])
    elif phase == "done":
        text = tr("saved" if view.get("changed", True) else "unchanged", language)
    elif phase == "upload_uncertain":
        text = tr("uncertain_upload", language)
    elif phase == "uncertain":
        text = tr("uncertain_write", language)
    elif phase == "blocked":
        text = tr(view.get("notice", "invalid_write"), language)
    else:
        raise ValueError("Unknown settings phase.")
    if phase in ("input", "logo", "logo_reset_only", "review"):
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
        raise ValueError("Stale Construction settings source.")
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
    if (
        view["phase"] != "review" or view.get("attempted")
        or view.get("field") not in FIELDS
        or not isinstance(view.get("changes"), dict)
        or set(view["changes"]) != {view["field"]}
    ):
        raise ValueError("Settings change is not ready.")
    changes = deepcopy(view["changes"])
    view = {key: value for key, value in view.items() if key != "changes"}
    view.update(phase="saving", attempted=True, nonce=uuid4().hex)
    await state.update_data(**{VIEW: view})
    data = await state.get_data()
    snapshot = {key: deepcopy(data.get(key)) for key in KEYS}
    try:
        async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
            result = await service.save_construction_company_settings(
                telegram_user_id=view["telegram_user_id"],
                tenant_id=UUID(view["tenant_id"]),
                expected_user_id=UUID(view["actor_user_id"]),
                changes=changes, expected_row_version=view["expected_row_version"],
            )
        item = result["item"]
        if (
            result["actor_user_id"] != view["actor_user_id"]
            or item is not None and item["tenant_id"] != view["tenant_id"]
        ):
            raise ValueError("Settings save context differs.")
        if not same(await state.get_data(), snapshot):
            return
        view.update(phase="done", changed=result["changed"])
    except Exception as error:
        logger.warning(
            "construction_bot_settings_save_failed error_type=%s status=%s code=%s",
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


@router.callback_query(F.data.startswith("CST:"))
async def settings_callback(callback, state, bot=None, event_update=None):
    language = callback.from_user.language_code or "ru"
    if not private(callback):
        await callback.answer(t("construction_bot_private", language), show_alert=True)
        return
    await callback.answer()
    async with lock_for(bot, callback.from_user.id):
        try:
            if await state.get_state() is not None:
                raise ValueError("Another form is active.")
            if callback.data.startswith("CST:open:"):
                await open_screen(callback, state, bot, event_update)
                return
            data = await state.get_data()
            view = deepcopy(data.get(VIEW))
            parts = callback.data.split(":")
            if (
                len(parts) not in (3, 4)
                or not bound(data, view, callback.from_user.id)
                or view.get("nonce") != parts[2]
                or view.get("menu_id") != callback.message.message_id
            ):
                raise ValueError("Stale settings selection.")
            language, action = view["language"], parts[1]
            if action == "save" and len(parts) == 3:
                await save(callback, state, view, bot, event_update)
                return
            if action == "reset" and len(parts) == 3:
                if view["phase"] not in ("input", "logo", "logo_reset_only") or view.get("field") not in FIELDS:
                    raise ValueError("Settings reset is unavailable.")
                view.update(
                    phase="review", changes={view["field"]: None}, nonce=uuid4().hex,
                )
                await state.update_data(**{VIEW: view})
                await draft(callback, state, view)
                return
            if action == "preview" and len(parts) == 3:
                if view["phase"] != "menu" or not view.get("logo_file_id"):
                    raise ValueError("Override logo is unavailable.")
                snapshot = {key: deepcopy(data.get(key)) for key in KEYS}
                async with construction_bot_api_entry(bot=bot, event_update=event_update) as service:
                    result = await service.download_construction_company_settings_logo(
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
                    caption=tr(LOGO_FIELD, language),
                )
                return
            if action == "refresh" and len(parts) == 3:
                result = await fetch(state, view, bot, event_update)
                await menu(callback, state, view, result)
                return
            if (
                action != "field" or len(parts) != 4 or view["phase"] != "menu"
                or parts[3] not in {str(index) for index in range(len(FIELDS))}
            ):
                raise ValueError("Invalid settings field.")
            result = await fetch(state, view, bot, event_update)
            if result["can_manage"] is not True:
                raise problem(403, "permission_required")
            view = context_view(view, result)
            field = FIELDS[int(parts[3])]
            view.update(field=field, changes={}, nonce=uuid4().hex)
            if field == LOGO_FIELD:
                view["phase"] = "logo" if result["can_upload_logo"] else "logo_reset_only"
            else:
                view["phase"] = "input"
            await state.update_data(**{VIEW: view})
            await draft(callback, state, view)
        except Exception as error:
            logger.warning(
                "construction_bot_settings_failed error_type=%s status=%s code=%s",
                type(error).__name__, getattr(error, "status_code", None),
                getattr(error, "code", None),
            )
            await callback.message.answer(t("construction_lead_form_stale", language))


class SettingsInput(BaseFilter):
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
            and view.get("phase") in ("input", "logo")
            and not view.get("attempted")
        )


async def receive_logo(message, state, view, bot, event_update):
    snapshot = None
    try:
        result = await fetch(state, view, bot, event_update)
        if result["can_manage"] is not True:
            raise problem(403, "permission_required")
        if result["can_upload_logo"] is not True:
            raise problem(403, "permission_required")
        current_version = result["item"]["row_version"] if result["item"] else None
        if current_version != view["expected_row_version"]:
            raise problem(409, "ROW_VERSION_CONFLICT")
        view.update(phase="uploading", attempted=True, nonce=uuid4().hex)
        await state.update_data(**{VIEW: view})
        data = await state.get_data()
        snapshot = {key: deepcopy(data.get(key)) for key in KEYS}
        content, mime = await telegram_image(
            bot, message, maximum=result["max_bytes"], mimes=result["mime_types"],
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
            raise ValueError("Settings upload actor differs.")
        file_id = str(UUID(uploaded["item"]["id"]))
        view.update(
            changes={LOGO_FIELD: file_id}, uploaded_file_id=file_id,
            phase="review", attempted=False, nonce=uuid4().hex,
        )
    except LogoInputError:
        if snapshot is None or not same(await state.get_data(), snapshot):
            return
        view.update(phase="logo", attempted=False, nonce=uuid4().hex)
        await state.update_data(**{VIEW: view})
        await draft(message, state, view, tr("invalid_image", view["language"]))
        return
    except Exception as error:
        logger.warning(
            "construction_bot_settings_logo_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        data = await state.get_data()
        if snapshot is not None and not same(data, snapshot):
            return
        if snapshot is None and (data.get(VIEW) != view or not bound(data, view, message.from_user.id)):
            return
        status = getattr(error, "status_code", None)
        view.update(
            phase="blocked" if status in (401, 403, 404, 409, 422, 429) else "upload_uncertain",
            notice="conflict_write" if status == 409 else "invalid_write",
            attempted=True, nonce=uuid4().hex,
        )
    await state.update_data(**{VIEW: view})
    await draft(message, state, view)


@router.message(SettingsInput())
async def settings_message(message, state, bot=None, event_update=None):
    async with lock_for(bot, message.from_user.id):
        if not await SettingsInput()(message, state):
            return
        view = deepcopy((await state.get_data())[VIEW])
        if view["phase"] == "logo":
            await receive_logo(message, state, view, bot, event_update)
            return
        field, value = view["field"], getattr(message, "text", None)
        if isinstance(value, str) and field not in ("document_header_text", "document_footer_text"):
            value = value.strip()
        valid = (
            field in LIMITS and isinstance(value, str) and bool(value.strip())
            and len(value) <= LIMITS[field]
            and not any(ord(char) < 32 and char not in "\n\r\t" for char in value)
        )
        if field == "accent_color":
            valid = valid and re.fullmatch(r"#[0-9A-Fa-f]{6}", value) is not None
        if not valid:
            await draft(message, state, view, tr("invalid_input", view["language"]))
            return
        view.update(
            changes={field: value}, phase="review", nonce=uuid4().hex,
        )
        await state.update_data(**{VIEW: view})
        await draft(message, state, view)
