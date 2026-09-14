from base64 import (
    urlsafe_b64decode,
    urlsafe_b64encode,
)
from binascii import Error

from api.errors import ApiHttpError


def decode_page_cursor(
    cursor: str | None,
) -> int:
    if cursor is None:
        return 0

    try:
        padding = "=" * (
            (-len(cursor)) % 4
        )
        value = urlsafe_b64decode(
            cursor + padding
        ).decode("ascii")
        page = int(value)
    except (
        Error,
        UnicodeDecodeError,
        ValueError,
    ):
        raise ApiHttpError(
            status_code=422,
            code="invalid_cursor",
            message="Pagination cursor is invalid.",
        ) from None

    if page < 0:
        raise ApiHttpError(
            status_code=422,
            code="invalid_cursor",
            message="Pagination cursor is invalid.",
        )

    return page


def encode_page_cursor(page: int) -> str:
    return (
        urlsafe_b64encode(
            str(page).encode("ascii")
        )
        .decode("ascii")
        .rstrip("=")
    )
