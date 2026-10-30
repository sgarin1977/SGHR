"""Optional address comparisons in the standalone project creation form."""
from copy import deepcopy
import logging
from uuid import UUID, uuid4

from handlers.construction import construction_project_create as parent

logger = logging.getLogger(__name__)
LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {
    "title": (
        "Перевірка адреси", "Проверка адреса", "Address check",
        "Verificação da morada", "Sprawdzenie adresu", "Adressprüfung", "Adrescontrole",
    ),
    "found": (
        "Знайдено можливі збіги:", "Найдены возможные совпадения:",
        "Possible matches found:", "Possíveis correspondências:",
        "Znaleziono możliwe dopasowania:", "Mögliche Treffer gefunden:",
        "Mogelijke overeenkomsten gevonden:",
    ),
    "empty": (
        "За цією адресою збігів не знайдено.",
        "По этому адресу совпадений не найдено.",
        "No matches found for this address.",
        "Não foram encontradas correspondências para esta morada.",
        "Nie znaleziono dopasowań dla tego adresu.",
        "Für diese Adresse wurden keine Treffer gefunden.",
        "Geen overeenkomsten voor dit adres gevonden.",
    ),
    "unavailable": (
        "Перевірити адресу зараз не вдалося. Можна повторити перевірку або продовжити.",
        "Проверить адрес сейчас не удалось. Можно повторить проверку или продолжить.",
        "The address could not be checked. Retry the check or continue.",
        "Não foi possível verificar a morada. Tente novamente ou continue.",
        "Nie udało się sprawdzić adresu. Ponów sprawdzenie lub kontynuuj.",
        "Die Adresse konnte nicht geprüft werden. Erneut prüfen oder fortfahren.",
        "Het adres kon niet worden gecontroleerd. Probeer opnieuw of ga verder.",
    ),
    "summary": (
        "Попередньо знайдено збігів: {count}. Повторіть перевірку, щоб побачити актуальний список.",
        "Ранее найдено совпадений: {count}. Повторите проверку для актуального списка.",
        "Previous matches: {count}. Check again to see the current list.",
        "Correspondências anteriores: {count}. Verifique novamente para atualizar a lista.",
        "Poprzednie dopasowania: {count}. Sprawdź ponownie, aby zobaczyć aktualną listę.",
        "Vorherige Treffer: {count}. Für die aktuelle Liste erneut prüfen.",
        "Eerdere overeenkomsten: {count}. Controleer opnieuw voor de actuele lijst.",
    ),
    "note": (
        "Це підказки про можливі наявні записи. Створення нового об’єкта залишається доступним.",
        "Это подсказки о возможных существующих записях. Можно продолжить создание нового объекта.",
        "These are possible existing records. You can still create a new project.",
        "Estes são possíveis registos existentes. Pode continuar a criar um novo projeto.",
        "To możliwe istniejące rekordy. Nadal możesz utworzyć nowy obiekt.",
        "Dies sind mögliche vorhandene Einträge. Ein neues Objekt kann weiterhin erstellt werden.",
        "Dit zijn mogelijk bestaande gegevens. Je kunt nog steeds een nieuw project maken.",
    ),
    "continue": (
        "Продовжити з новим об’єктом", "Продолжить с новым объектом",
        "Continue with a new project", "Continuar com um novo projeto",
        "Kontynuuj z nowym obiektem", "Mit neuem Objekt fortfahren",
        "Doorgaan met een nieuw project",
    ),
    "change": (
        "Змінити адресу", "Изменить адрес", "Change address", "Alterar morada",
        "Zmień adres", "Adresse ändern", "Adres wijzigen",
    ),
    "retry": (
        "Перевірити ще раз", "Проверить ещё раз", "Check again",
        "Verificar novamente", "Sprawdź ponownie", "Erneut prüfen", "Opnieuw controleren",
    ),
    "client": (
        "Клієнт", "Клиент", "Client", "Cliente", "Klient", "Kunde", "Klant",
    ),
    "project": (
        "Об’єкт", "Объект", "Project", "Projeto", "Obiekt", "Objekt", "Project",
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
    current = data.get(parent.FORM)
    source = getattr(event, "message", None)
    return (
        isinstance(current, dict) and current == form
        and isinstance(context, dict)
        and form.get("telegram_user_id") == event.from_user.id
        and context.get("telegram_user_id") == event.from_user.id
        and context.get("tenant_id") == form.get("tenant_id")
        and form.get("attempted_at") is None
        and form.get("step") == 2
        and form.get("menu_id") is not None
        and form["menu_id"] == data.get("last_menu_message_id")
        and (source is None or source.message_id == form["menu_id"])
    )


def validate_items(items):
    if not isinstance(items, list) or len(items) > 10:
        raise ValueError("Invalid address suggestions.")
    seen = set()
    for item in items:
        if (
            not isinstance(item, dict)
            or item.get("entity_type") not in ("client", "project")
            or not isinstance(item.get("entity_id"), str)
            or str(UUID(item["entity_id"])) != item["entity_id"]
            or not isinstance(item.get("label"), str)
            or not isinstance(item.get("address_raw"), str)
        ):
            raise ValueError("Invalid address suggestion.")
        identity = item["entity_type"], item["entity_id"]
        if identity in seen:
            raise ValueError("Duplicate address suggestion.")
        seen.add(identity)


async def render(event, state, form, *, items=None, notice=None, expected_snapshot=None):
    data = await state.get_data()
    saved = expected_snapshot if expected_snapshot is not None else snapshot(data)
    if not same(data, saved):
        return
    language = form["language"]
    status = form.get("address_suggestion_state")
    if status == "empty":
        headline = tr("empty", language)
    elif status == "matched" and items is not None:
        headline = tr("found", language)
    elif status == "matched":
        headline = tr("summary", language).format(count=form["address_suggestion_count"])
    else:
        headline = tr("unavailable", language)
    lines = [
        tr("title", language), str(form["organization"])[:100],
        form["payload"]["address_raw"][:160],
        headline, tr("note", language),
    ]
    if items is not None:
        for index, item in enumerate(items):
            lines.append(
                f'{index + 1}. {tr(item["entity_type"], language)}: '
                + item["label"][:30] + "\n" + item["address_raw"][:50]
            )
    if notice:
        lines.insert(0, notice)
    rows = [
        [parent.button(tr("continue", language), parent.cb(form, "address_continue"))],
        [parent.button(tr("change", language), parent.cb(form, "address_change"))],
        [parent.button(tr("retry", language), parent.cb(form, "address_retry"))],
        [parent.button(parent.common("cancel", language), parent.cb(form, "cancel"))],
        *parent.back_rows(language),
    ]
    await parent.show(
        event, state, form, "\n\n".join(lines)[:1750], rows,
        expected_snapshot=saved,
    )


async def lookup(event, state, form, *, bot, event_update):
    data = await state.get_data()
    if not bound(data, form, event):
        return
    form = {
        **deepcopy(form), "id": uuid4().hex,
        "phase": "address_suggestions",
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
            result = await service.project_address_suggestions(
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
            "construction_project_address_suggestions_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        items = None
        form = {**form, "address_suggestion_state": "unavailable", "address_suggestion_count": 0}
    if not same(await state.get_data(), saved):
        return
    await render(event, state, form, items=items, expected_snapshot=saved)


async def dispatch(event, state, form, action, *, bot, event_update):
    data = await state.get_data()
    if not bound(data, form, event):
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
        form.update(step=1, payload={"name": form["payload"]["name"]}, assign_self=False)
    else:
        form["step"] = 2
    await state.update_data(**{parent.FORM: form})
    await parent.display(event, state, form)
    return True
