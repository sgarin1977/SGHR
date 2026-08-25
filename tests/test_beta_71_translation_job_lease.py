import ast
from pathlib import Path


REPOSITORY_PATH = Path(
    "database/repositories/translation.py"
)
SERVICE_PATH = Path(
    "services/translation.py"
)


def get_method(
    source,
    class_name,
    method_name,
):
    tree = ast.parse(source)
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == class_name
    )

    return next(
        node
        for node in class_node.body
        if isinstance(
            node,
            ast.AsyncFunctionDef,
        )
        and node.name == method_name
    )


def test_translation_claim_has_recoverable_lease():
    source = REPOSITORY_PATH.read_text(
        encoding="utf-8-sig"
    )
    node = get_method(
        source,
        "TranslationRepository",
        "claim_pending_job_for_message",
    )
    block = (
        ast.get_source_segment(
            source,
            node,
        )
        or ""
    )
    compact = "".join(block.split())

    assert 'job.status="processing"' in compact
    assert "job.updated_at=" in compact
    assert "lease_seconds" in block
    assert "timedelta(" in block
    assert '"processing"' in block
    assert (
        ".with_for_update(skip_locked=True)"
        in compact
    )


def test_pending_jobs_include_expired_leases():
    source = REPOSITORY_PATH.read_text(
        encoding="utf-8-sig"
    )
    node = get_method(
        source,
        "TranslationRepository",
        "list_pending_jobs",
    )
    block = (
        ast.get_source_segment(
            source,
            node,
        )
        or ""
    )

    assert "lease_seconds" in block
    assert '"processing"' in block
    assert "updated_at" in block
    assert "timedelta(" in block


def test_translation_http_runs_after_transaction_commit():
    source = SERVICE_PATH.read_text(
        encoding="utf-8-sig"
    )
    node = get_method(
        source,
        "TranslationService",
        "translate_message",
    )

    awaited_methods = []

    for item in ast.walk(node):
        if not isinstance(item, ast.Await):
            continue

        call = item.value

        if not isinstance(call, ast.Call):
            continue

        function = call.func

        if isinstance(function, ast.Attribute):
            awaited_methods.append(
                (
                    item.lineno,
                    function.attr,
                )
            )

    claim_line = next(
        line
        for line, method in awaited_methods
        if method
        == "claim_pending_job_for_message"
    )
    detect_line = next(
        line
        for line, method in awaited_methods
        if method == "detect_language"
    )
    translate_line = next(
        line
        for line, method in awaited_methods
        if method == "translate"
    )
    cache_line = next(
        line
        for line, method in awaited_methods
        if method
        == "get_cached_translation"
    )

    commit_lines = [
        line
        for line, method in awaited_methods
        if method == "commit"
    ]
    flush_lines = [
        line
        for line, method in awaited_methods
        if method == "flush"
    ]

    assert any(
        claim_line < line < detect_line
        for line in commit_lines
    )
    assert any(
        cache_line < line < translate_line
        for line in commit_lines
    )
    assert not flush_lines
