"""Activity labels and explicit conversion of local user input to UTC."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ui.texts import t

ACTIVITY_ORDER = (
    "call", "message", "meeting", "site_visit", "follow_up", "task", "other",
)


def activity_label(value, language):
    if value not in ACTIVITY_ORDER:
        raise ValueError("Unknown activity type.")
    return t("construction_activity_form_" + value, language)


def local_deadlines(value, timezone_code):
    local = datetime.strptime(value.strip(), "%d.%m.%Y %H:%M")
    zone = ZoneInfo(timezone_code)
    instants = set()
    for fold in (0, 1):
        instant = local.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
        if instant.astimezone(zone).replace(tzinfo=None) == local:
            instants.add(instant)
    if not instants:
        raise ValueError("Local time does not exist.")
    # Two results require explicit user choice; no silent DST assumption.
    return tuple(
        instant.isoformat(timespec="minutes")
        for instant in sorted(instants)
    )


def display_deadline(value, timezone_code):
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise ValueError("Aware deadline required.")
    return instant.astimezone(ZoneInfo(timezone_code)).strftime(
        "%d.%m.%Y %H:%M %z"
    )
