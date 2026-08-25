import ast
import json
import stat
from pathlib import Path
from uuid import uuid4

from services.privacy import PrivacyService


SOURCE_PATH = Path("services/privacy.py")


def test_privacy_export_file_is_private(tmp_path):
    service = object.__new__(
        PrivacyService
    )
    export_dir = tmp_path / "exports"
    request_id = uuid4()

    file_path = service._write_secure_export(
        export_dir=export_dir,
        request_id=request_id,
        export_data={
            "private": "personal data",
        },
    )

    assert file_path.parent == export_dir
    assert file_path.name.startswith(
        f"dsr_export_{request_id}_"
    )
    assert file_path.suffix == ".json"

    directory_mode = stat.S_IMODE(
        export_dir.stat().st_mode
    )
    file_mode = stat.S_IMODE(
        file_path.stat().st_mode
    )

    assert directory_mode == 0o700
    assert file_mode == 0o600

    payload = json.loads(
        file_path.read_text(
            encoding="utf-8"
        )
    )
    assert payload == {
        "private": "personal data",
    }


def test_privacy_job_uses_secure_export_writer():
    source = SOURCE_PATH.read_text(
        encoding="utf-8-sig"
    )
    tree = ast.parse(source)

    node = next(
        item
        for item in ast.walk(tree)
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name
        == "process_requested_data_exports"
    )

    block = (
        ast.get_source_segment(
            source,
            node,
        )
        or ""
    )

    assert "_write_secure_export(" in block
    assert ".write_text(" not in block
    assert (
        'f"dsr_export_{request.id}.json"'
        not in block
    )


def test_expired_privacy_exports_are_removed(
    tmp_path,
):
    import os

    service = object.__new__(
        PrivacyService
    )
    export_dir = tmp_path / "exports"
    export_dir.mkdir()

    expired = (
        export_dir
        / "dsr_export_old_token.json"
    )
    current = (
        export_dir
        / "dsr_export_current_token.json"
    )
    unrelated = export_dir / "keep.json"

    expired.write_text(
        "{}",
        encoding="utf-8",
    )
    current.write_text(
        "{}",
        encoding="utf-8",
    )
    unrelated.write_text(
        "{}",
        encoding="utf-8",
    )

    now_timestamp = 10_000.0
    old_timestamp = 1_000.0

    os.utime(
        expired,
        (
            old_timestamp,
            old_timestamp,
        ),
    )
    os.utime(
        current,
        (
            now_timestamp,
            now_timestamp,
        ),
    )
    os.utime(
        unrelated,
        (
            old_timestamp,
            old_timestamp,
        ),
    )

    removed = (
        service
        ._cleanup_expired_exports(
            export_dir=export_dir,
            retention_seconds=3600,
            now_timestamp=now_timestamp,
        )
    )

    assert removed == 1
    assert not expired.exists()
    assert current.exists()
    assert unrelated.exists()


def test_privacy_job_runs_export_cleanup():
    source = SOURCE_PATH.read_text(
        encoding="utf-8-sig"
    )
    tree = ast.parse(source)

    node = next(
        item
        for item in ast.walk(tree)
        if isinstance(
            item,
            ast.AsyncFunctionDef,
        )
        and item.name
        == "process_requested_data_exports"
    )

    block = (
        ast.get_source_segment(
            source,
            node,
        )
        or ""
    )

    assert "_cleanup_expired_exports(" in block
    assert "retention_seconds" in block
