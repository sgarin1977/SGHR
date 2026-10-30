"""Optional existing-record comparisons during lead address entry."""
from copy import deepcopy
import logging
from uuid import UUID, uuid4

from handlers.construction import construction_lead_create as parent
from handlers.construction.construction_project_address_suggestions import (
    LANGUAGES, tr as address_tr, validate_items,
)

logger = logging.getLogger(__name__)
ADDRESS_INDEX = parent.FIELDS.index("address_raw")
NEXT_INDEX = ADDRESS_INDEX + 1
LABELS = {
    "continue": (
        "Продовжити звернення", "Продолжить обращение",
        "Continue the lead", "Continuar o contacto",
        "Kontynuuj zgłoszenie", "Anfrage fortsetzen", "Aanvraag vervolgen",
    ),
    "note": (
        "Це можливі наявні клієнти й об’єкти. Звернення буде збережене лише після остаточного підтвердження.",
        "Это возможные существующие клиенты и объекты. Обращение сохранится только после итогового подтверждения.",
        "These may be existing clients and projects. The lead is saved only after final confirmation.",
        "Estes podem ser clientes e projetos existentes. O contacto só é guardado após confirmação final.",
        "To mogą być istniejący klienci i obiekty. Zgłoszenie zostanie zapisane dopiero po końcowym potwierdzeniu.",
        "Dies können vorhandene Kunden und Objekte sein. Die Anfrage wird erst nach abschließender Bestätigung gespeichert.",
        "Dit kunnen bestaande klanten en projecten zijn. De aanvraag wordt pas na de laatste bevestiging opgeslagen.",
    ),
}


def tr(key, language):
    language = str(language or "ru").lower().replace("_", "-").split("-", 1)[0]
    index = LANGUAGES.index(language) if language in LANGUAGES else LANGUAGES.index("ru")
    return LABELS[key][index]


def snapshot(data):
    return deepcopy({
        key: data.get(key)
        for key in (parent.FORM, parent.CONTEXT, "last_menu_message_id")
    })


def same(data, saved):
    return all(data.get(key) == value for key, value in saved.items())


def bound(data, form, event):
    context = data.get(parent.CONTEXT)
    source = getattr(event, "message", None)
    return (
        isinstance(form, dict)
        and data.get(parent.FORM) == form
        and isinstance(context, dict)
        and form.get("telegram_user_id") == event.from_user.id
        and context.get("telegram_user_id") == event.from_user.id
        and context.get("tenant_id") == form.get("tenant_id")
        and form.get("attempted_at") is None
        and form.get("step") == NEXT_INDEX
        and form.get("menu_id") is not None
        and form["menu_id"] == data.get("last_menu_message_id")
        and (source is None or source.message_id == form["menu_id"])
    )


async def render(event, state, form, *, items=None, notice=None, expected_snapshot=None):
    if await state.get_state() is not None:
        return
    data = await state.get_data()
    saved = expected_snapshot if expected_snapshot is not None else snapshot(data)
    if not same(data, saved):
        return
    language = form["language"]
    status = form.get("address_suggestion_state")
    if status == "empty":
        headline = address_tr("empty", language)
    elif status == "matched" and items is not None:
        headline = address_tr("found", language)
    elif status == "matched":
        headline = address_tr("summary", language).format(
            count=form["address_suggestion_count"],
        )
    else:
        headline = address_tr("unavailable", language)
    lines = [
        parent.tr("title", language),
        str(form["organization"])[:100],
        form["payload"]["address_raw"][:160],
        headline,
        tr("note", language),
    ]
    if items is not None:
        for index, item in enumerate(items):
            lines.append(
                f'{index + 1}. {address_tr(item["entity_type"], language)}: '
                + item["label"][:30] + "\n" + item["address_raw"][:50]
            )
    if notice:
        lines.insert(0, notice)
    rows = [
        [parent.btn(tr("continue", language), parent.cb(form, "address_continue"))],
        [parent.btn(address_tr("change", language), parent.cb(form, "address_change"))],
        [parent.btn(address_tr("retry", language), parent.cb(form, "address_retry"))],
        [parent.btn(parent.tr("cancel", language), parent.cb(form, "cancel"))],
    ]
    from ui.texts import t
    rows.append([parent.btn(t("construction_bot_leads_btn", language), "CS_LEADS")])
    await parent.show(
        event, state, form, "\n\n".join(lines)[:1750], rows,
        expected_snapshot=saved,
    )


async def lookup(event, state, form, *, bot, event_update):
    if await state.get_state() is not None:
        return
    data = await state.get_data()
    if not bound(data, form, event):
        return
    form = {
        **deepcopy(form),
        "id": uuid4().hex, "phase": "address_suggestions",
        "address_suggestion_state": "checking",
        "address_suggestion_count": 0,
    }
    await state.update_data(**{parent.FORM: form})
    saved = snapshot(await state.get_data())
    items = None
    try:
        query = form["payload"]["address_raw"]
        if not isinstance(query, str) or not query.strip() or len(query) > 4096:
            raise ValueError("Address suggestion query unavailable.")
        async with parent.construction_bot_api_entry(bot=bot, event_update=event_update) as service:
            result = await service.lead_address_suggestions(
                telegram_user_id=event.from_user.id,
                tenant_id=UUID(form["tenant_id"]),
                expected_user_id=UUID(form["actor_user_id"]),
                query=query,
            )
        if (
            result["actor_user_id"] != form["actor_user_id"]
            or result["tenant_id"] != form["tenant_id"]
        ):
            raise ValueError("Address suggestion identity changed.")
        validate_items(result["items"])
        items = result["items"]
        form = {
            **form,
            "address_suggestion_state": "matched" if items else "empty",
            "address_suggestion_count": len(items),
        }
    except Exception as error:
        logger.warning(
            "construction_lead_address_suggestions_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        items = None
        form = {
            **form,
            "address_suggestion_state": "unavailable",
            "address_suggestion_count": 0,
        }
    if await state.get_state() is not None or not same(await state.get_data(), saved):
        return
    await render(event, state, form, items=items, expected_snapshot=saved)


async def dispatch(event, state, form, action, *, bot, event_update):
    if not bound(await state.get_data(), form, event):
        return True
    if action == "cancel":
        return False
    if action == "address_retry":
        await lookup(event, state, form, bot=bot, event_update=event_update)
        return True
    if action not in ("address_continue", "address_change"):
        return True
    form = deepcopy(form)
    form.update(id=uuid4().hex, phase="input")
    form.pop("address_suggestion_state", None)
    form.pop("address_suggestion_count", None)
    if action == "address_change":
        form["step"] = ADDRESS_INDEX
        form["payload"] = {
            key: value for key, value in form["payload"].items()
            if key not in ("address_raw", "comment")
        }
    else:
        form["step"] = NEXT_INDEX
    await state.update_data(**{parent.FORM: form})
    await parent.display(event, state, form)
    return True
