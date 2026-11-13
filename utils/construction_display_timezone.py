"""Validated display context; this module never changes stored timestamps."""
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def validate_timezone(value):
    if value is None:
        return None
    if (
        type(value) is not str or not 1 <= len(value) <= 255
        or re.fullmatch(r"[A-Za-z0-9_+/-]+", value) is None
        or value in {"localtime", "posixrules"}
        or value.startswith(("posix/", "right/"))
    ):
        raise ValueError("An explicit IANA timezone is required.")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("The IANA timezone is unavailable.") from None
    return value


def validate_display_timezone(value):
    if type(value) is not dict or set(value) != {"timezone", "source"}:
        raise ValueError("Display timezone context differs.")
    zone = validate_timezone(value["timezone"])
    source = value["source"]
    if type(source) is not str or source not in {
        "USER", "ORGANIZATION", "UNCONFIGURED",
    }:
        raise ValueError("Display timezone source differs.")
    if (source == "UNCONFIGURED") != (zone is None):
        raise ValueError("Display timezone and source disagree.")
    return {"timezone": zone, "source": source}
