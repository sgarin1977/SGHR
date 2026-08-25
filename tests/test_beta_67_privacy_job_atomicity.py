import ast
from pathlib import Path

import pytest


REPOSITORY_PATH = Path(
    "database/repositories/privacy.py"
)
SERVICE_PATH = Path("services/privacy.py")


def get_async_method(
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


@pytest.mark.parametrize(
    (
        "method_name",
        "status_fragment",
    ),
    [
        (
            "get_next_scheduled_deletion_job",
            'DeletionJob.status == "scheduled"',
        ),
        (
            "get_next_requested_data_export",
            (
                "DataSubjectRequest.status "
                '== "requested"'
            ),
        ),
    ],
)
def test_privacy_job_fetch_uses_skip_locked(
    method_name,
    status_fragment,
):
    source = REPOSITORY_PATH.read_text(
        encoding="utf-8-sig"
    )
    node = get_async_method(
        source,
        "PrivacyRepository",
        method_name,
    )
    block = (
        ast.get_source_segment(
            source,
            node,
        )
        or ""
    )
    compact = "".join(block.split())

    assert (
        "".join(status_fragment.split())
        in compact
    )
    assert (
        ".with_for_update(skip_locked=True)"
        in compact
    )
    assert ".limit(1)" in compact


@pytest.mark.parametrize(
    (
        "method_name",
        "next_method",
        "old_list_method",
    ),
    [
        (
            "process_scheduled_deletions",
            "get_next_scheduled_deletion_job",
            "list_scheduled_deletion_jobs",
        ),
        (
            "process_requested_data_exports",
            "get_next_requested_data_export",
            "list_requested_data_exports",
        ),
    ],
)
def test_privacy_service_processes_one_locked_job(
    method_name,
    next_method,
    old_list_method,
):
    source = SERVICE_PATH.read_text(
        encoding="utf-8-sig"
    )
    node = get_async_method(
        source,
        "PrivacyService",
        method_name,
    )
    block = (
        ast.get_source_segment(
            source,
            node,
        )
        or ""
    )
    compact = "".join(block.split())

    assert next_method in block
    assert old_list_method not in block
    assert "for_inrange(" in compact
    assert (
        "ifnotjob:"
        in compact
        or "ifnotrequest:" in compact
    )


def test_privacy_jobs_do_not_commit_processing_state():
    source = SERVICE_PATH.read_text(
        encoding="utf-8-sig"
    )

    deletion_node = get_async_method(
        source,
        "PrivacyService",
        "process_scheduled_deletions",
    )
    export_node = get_async_method(
        source,
        "PrivacyService",
        "process_requested_data_exports",
    )

    deletion_block = "".join(
        (
            ast.get_source_segment(
                source,
                deletion_node,
            )
            or ""
        ).split()
    )
    export_block = "".join(
        (
            ast.get_source_segment(
                source,
                export_node,
            )
            or ""
        ).split()
    )

    assert (
        "mark_deletion_job_processing(job)"
        "awaitself.repository.session.commit()"
        not in deletion_block
    )
    assert (
        'request.status="processing"'
        "awaitself.repository.session.commit()"
        not in export_block
    )
