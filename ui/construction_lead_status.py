"""Lead status presentation using the existing shared lifecycle contract."""
from uuid import UUID

from services.construction.construction_lead_rules import CONSTRUCTION_LEAD_STATUS_TRANSITIONS
from ui.texts import t

STATUS_ORDER = (
    "new", "contacted", "measurement_scheduled", "measurement_completed",
    "estimate_preparation", "proposal_sent", "negotiation", "won", "lost",
)


def status_label(status, language):
    if status not in CONSTRUCTION_LEAD_STATUS_TRANSITIONS:
        raise ValueError("Unknown lead status.")
    return t("construction_lead_status_" + status, language)


def available_statuses(status):
    allowed = CONSTRUCTION_LEAD_STATUS_TRANSITIONS[status]
    return tuple(value for value in STATUS_ORDER if value in allowed)


def transition_callback(lead_id, version, status):
    if (
        not isinstance(lead_id, UUID)
        or type(version) is not int
        or not 0 < version < 2**63
        or status not in STATUS_ORDER
    ):
        raise ValueError("Invalid lead status button.")
    value = f"LS:set:{lead_id.hex}:{version:x}:{STATUS_ORDER.index(status)}"
    if len(value.encode("utf-8")) > 64:
        raise ValueError("Lead status callback is too long.")
    return value
