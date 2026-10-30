"""Optional address comparisons before the existing versioned project PATCH."""
from copy import deepcopy
import logging
from uuid import UUID, uuid4

from handlers.construction import construction_project_edit as editor
from handlers.construction.construction_project_address_suggestions import (
    LANGUAGES, tr as address_tr, validate_items,
)

logger = logging.getLogger(__name__)
LABELS = {
    "continue": (
        "Перейти до підтвердження", "Перейти к подтверждению",
        "Review changes", "Rever alterações", "Przejdź do potwierdzenia",
        "Änderungen prüfen", "Wijzigingen controleren",
    ),
    "note": (
        "Це можливі збіги. Зміни об’єкта буде збережено лише після вашого підтвердження.",
        "Это возможные совпадения. Изменения объекта сохранятся только после вашего подтверждения.",
        "These are possible matches. Project changes are saved only after your confirmation.",
        "Estas são possíveis correspondências. As alterações só são guardadas após confirmação.",
        "To możliwe dopasowania. Zmiany obiektu zostaną zapisane dopiero po potwierdzeniu.",
        "Dies sind mögliche Treffer. Änderungen werden erst nach Ihrer Bestätigung gespeichert.",
        "Dit zijn mogelijke overeenkomsten. Wijzigingen worden pas na bevestiging opgeslagen.",
    ),
}


def tr(key, language):
    language = str(language or "ru").lower().replace("_", "-").split("-", 1)[0]
    index = LANGUAGES.index(language) if language in LANGUAGES else LANGUAGES.index("ru")
    return LABELS[key][index]


def bound(data, form, event):
    source = getattr(event, "message", None)
    menu = source.message_id if source is not None else data.get("last_menu_message_id")
    return (
        isinstance(form, dict)
        and data.get(editor.FORM) == form
        and form.get("field") == "address_raw"
        and form.get("stage") in ("review", "address_suggestions")
        and form.get("attempted") is False
        and editor.selected(data, form, event.from_user.id, menu) is not None
    )


async def render(event, state, form, *, items=None, notice=None, expected_snapshot=None):
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
        address_tr("title", language),
        form["value"][:160],
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
        [editor.button(tr("continue", language), editor.cb(form, "address_continue"))],
        [editor.button(address_tr("change", language), editor.cb(form, "address_change"))],
        [editor.button(address_tr("retry", language), editor.cb(form, "address_retry"))],
        [editor.button(editor.tr("cancel", language), editor.cb(form, "cancel"))],
        [editor.button(editor.tr("back", language), form["back"])],
    ]
    await editor.render(
        event, state, form, "\n\n".join(lines)[:2500], rows,
        expected_snapshot=expected_snapshot,
    )


async def lookup(event, state, form, *, bot, event_update):
    data = await state.get_data()
    if not bound(data, form, event):
        return
    form = {
        **deepcopy(form),
        "stage": "address_suggestions", "nonce": uuid4().hex,
        "address_suggestion_state": "checking",
        "address_suggestion_count": 0,
    }
    await state.update_data(**{editor.FORM: form})
    saved = editor.snapshot(await state.get_data())
    items = None
    try:
        query = form["value"]
        if not isinstance(query, str) or not query.strip() or len(query) > 4096:
            raise ValueError("Address suggestion query unavailable.")
        async with editor.construction_bot_api_entry(bot=bot, event_update=event_update) as service:
            result = await service.project_edit_address_suggestions(
                telegram_user_id=event.from_user.id,
                tenant_id=UUID(form["tenant_id"]),
                project_id=UUID(form["project_id"]),
                expected_user_id=UUID(form["actor_user_id"]),
                query=query,
            )
        if (
            result["actor_user_id"] != form["actor_user_id"]
            or result["tenant_id"] != form["tenant_id"]
            or result["project_id"] != form["project_id"]
        ):
            raise ValueError("Address suggestion context changed.")
        validate_items(result["items"])
        # The project being edited is not a duplicate of itself.
        # Preserve the API ordering of every remaining result.
        items = [
            item for item in result["items"]
            if not (
                item["entity_type"] == "project"
                and item["entity_id"] == form["project_id"]
            )
        ]
        form = {
            **form,
            "address_suggestion_state": "matched" if items else "empty",
            "address_suggestion_count": len(items),
        }
    except Exception as error:
        logger.warning(
            "construction_project_edit_address_suggestions_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        items = None
        form = {
            **form,
            "address_suggestion_state": "unavailable",
            "address_suggestion_count": 0,
        }
    if not editor.same(await state.get_data(), saved):
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
    form["nonce"] = uuid4().hex
    form.pop("address_suggestion_state", None)
    form.pop("address_suggestion_count", None)
    if action == "address_change":
        form["stage"] = "input"
        form.pop("value", None)
    else:
        form["stage"] = "review"
    await state.update_data(**{editor.FORM: form})
    await editor.display(event, state, form)
    return True
