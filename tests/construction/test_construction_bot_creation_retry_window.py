from copy import deepcopy

import pytest

from handlers.construction import construction_lead_create as leads
from handlers.construction import construction_project_create as projects
from handlers.construction import construction_lead_convert as conversions
from services.construction.construction_api_client import ConstructionApiClientError

from test_construction_bot_lead_create import (
    wizard as lead_wizard,
    review as lead_review,
    click as lead_click,
)
from test_construction_bot_project_create import (
    wizard as project_wizard,
    review as project_review,
    click as project_click,
)
from test_construction_bot_lead_convert import (
    case as conversion_case,
    review as conversion_review,
    click as conversion_click,
)


CASES = {
    "lead": (
        leads, "lead_wizard", lead_review, lead_click, "create_lead",
    ),
    "project": (
        projects, "project_wizard", project_review, project_click, "create_project",
    ),
    "conversion": (
        conversions, "conversion_case", conversion_review, conversion_click,
        "convert_lead",
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", tuple(CASES))
@pytest.mark.parametrize(
    "age,allowed",
    [
        (0, True),
        (30, True),
        (86339, True),
        (-1, False),
        (86340, False),
        (90000, False),
        (float("nan"), False),
        (float("inf"), False),
    ],
    ids=(
        "immediate", "normal", "last-valid-second", "clock-backwards",
        "boundary", "expired", "invalid-clock", "infinite-clock",
    ),
)
async def test_actual_callback_obeys_retry_window_without_changing_command(
    request, monkeypatch, kind, age, allowed,
):
    module, fixture_name, prepare, click, operation_name = CASES[kind]
    c = request.getfixturevalue(fixture_name)
    clock = [100000.0]
    monkeypatch.setattr(module, "time", lambda: clock[0])

    shown = []
    original_show = module.show

    async def capture(event, state, form, text, rows, **kwargs):
        shown.append(rows)
        return await original_show(event, state, form, text, rows, **kwargs)

    monkeypatch.setattr(module, "show", capture)
    await prepare(c)

    operation = getattr(c.service, operation_name)
    receipt = deepcopy(operation.return_value)
    operation.side_effect = [
        ConstructionApiClientError(status_code=504, code="api_timeout"),
        receipt,
    ]

    # The first request may have succeeded remotely; preserve its command.
    await click(c, "submit")
    assert operation.await_count == 1
    assert c.data[module.FORM]["phase"] == "pending"
    original_request = deepcopy(operation.await_args.kwargs)
    frozen = deepcopy(c.data[module.FORM])
    assert frozen["attempted_at"] == 100000.0

    clock[0] = 100000.0 + age

    # Invoke the existing button directly as well as checking its visibility.
    # A hidden button alone must not permit an out-of-window write.
    await click(c, "submit")

    if allowed:
        assert operation.await_count == 2
        assert operation.await_args.kwargs == original_request
        assert c.data[module.FORM]["phase"] == "done"
    else:
        assert operation.await_count == 1
        assert c.data[module.FORM] == frozen
        commands = [
            button.callback_data
            for row in shown[-1] for button in row
        ]
        assert module.cb(frozen, "submit") not in commands
