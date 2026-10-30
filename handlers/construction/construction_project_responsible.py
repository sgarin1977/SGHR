"""Use the shared responsible picker with the existing project form."""
from types import SimpleNamespace

from handlers.construction import construction_project_create as parent
from handlers.construction import construction_lead_responsible as shared


def driver():
    # Runtime references preserve the same dispatch, locks and form rendering.
    return SimpleNamespace(
        FORM=parent.FORM, CONTEXT=parent.CONTEXT, FIELDS=parent.FIELDS,
        tr=parent.common, btn=parent.button, cb=parent.cb,
        show=parent.show, display=parent.display,
        construction_bot_api_entry=parent.construction_bot_api_entry,
        logger=parent.logger,
    )


async def render(event, state, form, *, notice=None):
    return await shared.render(
        event, state, form, notice=notice, driver=driver(),
    )


async def dispatch(event, state, form, action, *, bot, event_update):
    return await shared.dispatch(
        event, state, form, action,
        bot=bot, event_update=event_update, driver=driver(),
    )
