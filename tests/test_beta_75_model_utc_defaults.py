from datetime import datetime, timezone
from pathlib import Path


def test_models_do_not_use_deprecated_utcnow():
    source = Path(
        "database/models.py"
    ).read_text(encoding="utf-8-sig")

    assert "datetime.utcnow" not in source
    assert source.count(
        "default=utcnow_naive"
    ) == 75


def test_utcnow_naive_matches_current_schema():
    from database import models

    utcnow_naive = getattr(
        models,
        "utcnow_naive",
    )
    value = utcnow_naive()

    assert isinstance(value, datetime)
    assert value.tzinfo is None

    expected = (
        datetime.now(timezone.utc)
        .replace(tzinfo=None)
    )

    assert abs(
        (expected - value).total_seconds()
    ) < 1
