import ast
from pathlib import Path

import pytest


SOURCE_PATH = Path(
    "handlers/specialist_portfolio.py"
)


def get_async_function(
    source: str,
    function_name: str,
) -> ast.AsyncFunctionDef:
    tree = ast.parse(source)
    matches = [
        node
        for node in tree.body
        if isinstance(
            node,
            ast.AsyncFunctionDef,
        )
        and node.name == function_name
    ]

    assert len(matches) == 1, (
        f"{function_name}: expected 1, "
        f"found {len(matches)}"
    )
    return matches[0]


def function_source(
    source: str,
    function_name: str,
) -> str:
    node = get_async_function(
        source,
        function_name,
    )
    return (
        ast.get_source_segment(
            source,
            node,
        )
        or ""
    )


def test_portfolio_receive_stores_only_telegram_metadata():
    source = SOURCE_PATH.read_text(
        encoding="utf-8-sig"
    )
    block = function_source(
        source,
        "receive_portfolio_file",
    )
    compact = "".join(block.split())

    assert "BytesIO(" not in block
    assert "portfolio_content=" not in compact
    assert "portfolio_file_id=" in compact
    assert ".file_size" in block

    file_size_position = block.index(
        ".file_size"
    )
    download_position = block.find(
        ".download("
    )

    assert (
        download_position == -1
        or file_size_position
        < download_position
    )


def test_portfolio_confirmation_downloads_without_fsm_bytes():
    source = SOURCE_PATH.read_text(
        encoding="utf-8-sig"
    )
    block = function_source(
        source,
        "confirm_portfolio_upload",
    )
    compact = "".join(block.split())

    assert (
        'data.get("portfolio_file_id")'
        in compact
    )
    assert "portfolio_content" not in block
    assert "BytesIO(" in block
    assert ".download(" in block


@pytest.mark.parametrize(
    (
        "filename",
        "mime_type",
        "size_bytes",
        "limit_mb",
    ),
    [
        (
            "photo.jpg",
            "image/jpeg",
            10 * 1024 * 1024 + 1,
            10,
        ),
        (
            "document.pdf",
            "application/pdf",
            20 * 1024 * 1024 + 1,
            20,
        ),
    ],
)
def test_portfolio_metadata_rejects_oversized_file(
    filename,
    mime_type,
    size_bytes,
    limit_mb,
):
    from services.portfolio_storage import (
        PortfolioFileValidationError,
        validate_portfolio_file_metadata,
    )

    with pytest.raises(
        PortfolioFileValidationError,
        match=(
            rf"exceeds the {limit_mb} MB limit"
        ),
    ):
        validate_portfolio_file_metadata(
            filename=filename,
            mime_type=mime_type,
            size_bytes=size_bytes,
        )


def test_portfolio_metadata_accepts_valid_file():
    from services.portfolio_storage import (
        validate_portfolio_file_metadata,
    )

    validated = (
        validate_portfolio_file_metadata(
            filename="portfolio.pdf",
            mime_type="application/pdf",
            size_bytes=1024,
        )
    )

    assert validated.file_type == "pdf"
    assert validated.mime_type == "application/pdf"
    assert validated.size_bytes == 1024
    assert validated.extension == ".pdf"
