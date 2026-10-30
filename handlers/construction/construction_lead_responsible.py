"""Select a lead responsible from the already authorized team API."""
from copy import deepcopy
from uuid import UUID, uuid4

from handlers.construction import construction_lead_create as parent
from ui.construction_team import LANGUAGES, tr as team_tr
from ui.texts import t

PAGE_SIZE = 5
LABELS = {
    "choose": (
        "Обрати колегу", "Выбрать коллегу", "Choose a colleague",
        "Escolher colega", "Wybierz współpracownika",
        "Kollegen auswählen", "Collega kiezen",
    ),
    "back": (
        "Назад до вибору відповідального", "Назад к выбору ответственного",
        "Back to responsible selection", "Voltar à escolha do responsável",
        "Wróć do wyboru osoby odpowiedzialnej",
        "Zurück zur Auswahl der verantwortlichen Person",
        "Terug naar keuze van verantwoordelijke",
    ),
    "empty": (
        "Інших доступних учасників немає.", "Других доступных участников нет.",
        "No other available members.", "Não há outros membros disponíveis.",
        "Brak innych dostępnych uczestników.",
        "Keine weiteren verfügbaren Mitglieder.",
        "Geen andere beschikbare leden.",
    ),
    "note": (
        "Призначення відповідального не надає доступу до даних.",
        "Назначение ответственного не предоставляет доступ к данным.",
        "Assigning responsibility does not grant access to data.",
        "A atribuição de responsabilidade não concede acesso aos dados.",
        "Przypisanie odpowiedzialności nie nadaje dostępu do danych.",
        "Die Zuweisung der Verantwortung gewährt keinen Datenzugriff.",
        "Toewijzing van verantwoordelijkheid geeft geen toegang tot gegevens.",
    ),
}


def tr(key, language):
    language = str(language or "ru").lower().replace("_", "-").split("-", 1)[0]
    index = LANGUAGES.index(language) if language in LANGUAGES else LANGUAGES.index("ru")
    return LABELS[key][index]


def snapshot(data, *, driver=None):
    driver = parent if driver is None else driver
    return deepcopy({
        key: data.get(key)
        for key in (driver.FORM, driver.CONTEXT, "last_menu_message_id")
    })


def same(data, saved):
    return all(data.get(key) == value for key, value in saved.items())


def bound(data, form, event, *, driver=None):
    driver = parent if driver is None else driver
    context = data.get(driver.CONTEXT)
    return (
        data.get(driver.FORM) == form
        and isinstance(context, dict)
        and form.get("telegram_user_id") == event.from_user.id
        and context.get("telegram_user_id") == event.from_user.id
        and context.get("tenant_id") == form.get("tenant_id")
        and form.get("can_choose_responsible") is True
        and form.get("attempted_at") is None
        and form.get("step") == len(driver.FIELDS)
        and form.get("phase") in ("input", "responsibles")
        and form.get("menu_id") is not None
        and form["menu_id"] == data.get("last_menu_message_id")
        and event.message.message_id == form["menu_id"]
    )


def members_from(result, form):
    if UUID(str(result["actor_user_id"])) != UUID(form["actor_user_id"]):
        raise ValueError("Team actor changed.")
    if not isinstance(result["items"], list):
        raise ValueError("Invalid team response.")
    members = {}
    actor = UUID(form["actor_user_id"])
    for item in result["items"]:
        user_id = UUID(str(item["user_id"]))
        name = item.get("display_name")
        if name is not None and not isinstance(name, str):
            raise ValueError("Invalid team display name.")
        if user_id in members:
            raise ValueError("Duplicate team member.")
        members[user_id] = {
            "user_id": str(user_id),
            "display_name": " ".join((name or "").split())[:80],
        }
    return [
        members[user_id] for user_id in sorted(members, key=lambda value: value.hex)
        if user_id != actor
    ]


def member_name(member, position, language):
    return (
        member["display_name"]
        or team_tr("member", language) + " " + str(position + 1)
    )


async def render(
    event, state, form, *, members=None, offset=0,
    notice=None, expected_snapshot=None, driver=None,
):
    driver = parent if driver is None else driver
    data = await state.get_data()
    saved = expected_snapshot if expected_snapshot is not None else snapshot(data, driver=driver)
    if await state.get_state() is not None or not same(data, saved):
        return
    language = form["language"]
    form = deepcopy(form)
    form.update(
        id=uuid4().hex, phase="responsibles",
        responsible_ids=[], responsible_offset=offset,
        responsible_has_next=False,
    )
    rows = []
    lines = [
        driver.tr("responsible", language),
        str(form["organization"])[:120],
        tr("note", language),
    ]
    if members is None:
        lines.append(t("construction_bot_unavailable", language))
    else:
        offset = min(offset, ((len(members) - 1) // PAGE_SIZE) * PAGE_SIZE) if members else 0
        page = members[offset:offset + PAGE_SIZE]
        form["responsible_offset"] = offset
        form["responsible_ids"] = [item["user_id"] for item in page]
        form["responsible_has_next"] = offset + PAGE_SIZE < len(members)
        if not page:
            lines.append(tr("empty", language))
        else:
            lines.append(f"{offset + 1}–{offset + len(page)} / {len(members)}")
        for index, item in enumerate(page):
            rows.append([driver.btn(
                member_name(item, offset + index, language),
                driver.cb(form, "r_pick" + str(index)),
            )])
        if offset:
            rows.append([driver.btn(
                team_tr("previous", language), driver.cb(form, "r_prev"),
            )])
        if form["responsible_has_next"]:
            rows.append([driver.btn(
                team_tr("next", language), driver.cb(form, "r_next"),
            )])
    if notice:
        lines.append(notice)
    rows.extend([
        [driver.btn(team_tr("refresh", language), driver.cb(form, "r_refresh"))],
        [driver.btn(tr("back", language), driver.cb(form, "r_back"))],
        [driver.btn(driver.tr("cancel", language), driver.cb(form, "cancel"))],
    ])
    await driver.show(
        event, state, form, "\n\n".join(lines), rows,
        expected_snapshot=saved,
    )


async def dispatch(event, state, form, action, *, bot, event_update, driver=None):
    driver = parent if driver is None else driver
    data = await state.get_data()
    if await state.get_state() is not None or not bound(data, form, event, driver=driver):
        return True
    saved = snapshot(data, driver=driver)
    if action == "r_back":
        form = deepcopy(form)
        form.update(
            id=uuid4().hex, phase="input", responsible_ids=[],
            responsible_offset=0, responsible_has_next=False,
        )
        await state.update_data(**{driver.FORM: form})
        await driver.display(event, state, form)
        return True

    phase = form.get("phase")
    if action == "r_open":
        if phase != "input":
            return True
        offset = 0
    elif phase == "responsibles" and action in ("r_prev", "r_next", "r_refresh"):
        offset = form.get("responsible_offset", 0)
        if type(offset) is not int or offset < 0 or offset % PAGE_SIZE:
            return True
        if action == "r_next":
            if form.get("responsible_has_next") is not True:
                return True
            offset += PAGE_SIZE
        elif action == "r_prev":
            if not offset:
                return True
            offset -= PAGE_SIZE
    elif phase == "responsibles" and action.startswith("r_pick"):
        index = action.removeprefix("r_pick")
        if index not in tuple(str(value) for value in range(PAGE_SIZE)):
            return True
        ids = form.get("responsible_ids", [])
        if not isinstance(ids, list) or int(index) >= len(ids):
            return True
        selected_id = str(UUID(ids[int(index)]))
        offset = form.get("responsible_offset", 0)
    else:
        return True

    try:
        async with driver.construction_bot_api_entry(
            bot=bot, event_update=event_update,
        ) as service:
            result = await service.team_members(
                telegram_user_id=event.from_user.id,
                tenant_id=UUID(form["tenant_id"]),
                expected_user_id=UUID(form["actor_user_id"]),
            )
        members = members_from(result, form)
        if await state.get_state() is not None or not same(await state.get_data(), saved):
            return True
        if action.startswith("r_pick"):
            selected = next(
                (item for item in members if item["user_id"] == selected_id),
                None,
            )
            if selected is not None:
                position = members.index(selected)
                form = deepcopy(form)
                form.update(
                    id=uuid4().hex, phase="review",
                    step=len(driver.FIELDS) + 1, assign_self=False,
                    responsible_user_id=selected_id,
                    responsible_name=member_name(selected, position, form["language"]),
                    responsible_ids=[],
                )
                await state.update_data(**{driver.FORM: form})
                await driver.display(event, state, form)
                return True
            notice = t("construction_lead_form_stale", form["language"])
        else:
            notice = None
        await render(
            event, state, form, members=members, offset=offset,
            notice=notice, expected_snapshot=saved, driver=driver,
        )
    except Exception as error:
        driver.logger.warning(
            "construction_responsible_picker_failed error_type=%s status=%s code=%s",
            type(error).__name__, getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        if await state.get_state() is None and same(await state.get_data(), saved):
            await render(event, state, form, expected_snapshot=saved, driver=driver)
    return True
