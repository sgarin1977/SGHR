"""Create a project for a selected live client through existing APIs."""
from copy import deepcopy
from uuid import UUID, uuid4

from handlers.construction import construction_project_create as parent
from ui.construction_client_card import tr as card_tr

CARD = "construction_client_card_view"
KEYS = (parent.FORM, parent.CONTEXT, CARD, "last_menu_message_id")


def snapshot(data):
    return deepcopy({key: data.get(key) for key in KEYS})


def same(data, saved):
    return all(data.get(key) == value for key, value in saved.items())


def caption(form, language):
    if not form.get("client_id"):
        return parent.tr("no_client", language)
    return (
        card_tr("summary", language) + "\n"
        + str(form.get("client_name") or "—")[:180]
    )


def back_rows(form, language):
    if not form.get("client_id"):
        return []
    return [[parent.button(
        card_tr("summary", language),
        "CC:open:" + UUID(form["client_id"]).hex,
    )]]


async def start(callback, state, data, *, bot, event_update):
    parts = callback.data.split(":")
    context, card = data.get(parent.CONTEXT), data.get(CARD)
    if (
        len(parts) != 3 or parts[1] != "client"
        or not isinstance(context, dict) or not isinstance(card, dict)
        or context.get("telegram_user_id") != callback.from_user.id
        or card.get("telegram_user_id") != callback.from_user.id
        or card.get("nonce") != parts[2]
        or card.get("tenant_id") != context.get("tenant_id")
        or card.get("menu_id") != callback.message.message_id
        or card.get("menu_id") != data.get("last_menu_message_id")
        or card.get("section") not in ("summary", "projects")
    ):
        raise ValueError("Client project origin is stale.")
    tenant_id = UUID(card["tenant_id"])
    client_id = UUID(card["client_id"])
    actor_id = UUID(card["actor_user_id"])
    previous = data.get(parent.FORM)
    resume = (
        isinstance(previous, dict)
        and previous.get("attempted_at") is not None
        and previous.get("phase") != "done"
    )
    if resume and (
        previous.get("telegram_user_id") != callback.from_user.id
        or previous.get("tenant_id") != str(tenant_id)
        or previous.get("actor_user_id") != str(actor_id)
        or previous.get("client_id") != str(client_id)
    ):
        raise ValueError("Another project request is unresolved.")

    saved = snapshot(data)
    async with parent.construction_bot_api_entry(
        bot=bot, event_update=event_update,
    ) as service:
        if resume:
            # Recover the original operation without re-reading a mutable client.
            prepared = await service.project_form(
                telegram_user_id=callback.from_user.id, tenant_id=tenant_id,
            )
        else:
            prepared = await service.client_project_form(
                telegram_user_id=callback.from_user.id,
                tenant_id=tenant_id, client_id=client_id,
                expected_user_id=actor_id,
            )
    if await state.get_state() is not None or not same(await state.get_data(), saved):
        return
    if prepared["actor_user_id"] != str(actor_id):
        raise ValueError("Canonical identity changed.")
    if resume:
        form = deepcopy(previous)
    else:
        if prepared["client_id"] != str(client_id):
            raise ValueError("Client selection changed.")
        form = {
            "id": uuid4().hex,
            "telegram_user_id": callback.from_user.id,
            "tenant_id": str(tenant_id), "actor_user_id": str(actor_id),
            "client_id": str(client_id), "client_name": prepared["client_name"],
            "organization": prepared["organization"],
            "language": prepared["language"] or callback.from_user.language_code or "ru",
            "phase": "input", "step": 0, "payload": {},
            "assign_self": False, "attempted_at": None, "menu_id": None,
            "can_choose_responsible": prepared.get("can_choose_responsible") is True,
        }

    from handlers.construction.construction_lead_create import suspend_lead_form
    from handlers.construction.construction_activity_plan import suspend_activity_form
    from handlers.construction.construction_activity_terminal import suspend_terminal_form
    from handlers.construction.construction_lead_convert import suspend_conversion_form
    from handlers.construction.construction_client_create import suspend_client_form

    for suspend in (
        suspend_lead_form, suspend_activity_form, suspend_terminal_form,
        suspend_conversion_form, suspend_client_form,
    ):
        await suspend(state)
    await state.update_data(**{
        parent.FORM: form,
        parent.CONTEXT: {
            "telegram_user_id": callback.from_user.id, "tenant_id": str(tenant_id),
        },
        CARD: {}, "construction_project_card_view": {},
    })
    await parent.display(callback, state, form)
