from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import hmac
import json
from urllib.parse import parse_qsl


ERROR_MESSAGE = (
    "Telegram authentication data is invalid."
)
DEFAULT_FUTURE_SKEW_SECONDS = 30


class TelegramAuthenticationError(Exception):
    pass


@dataclass(frozen=True)
class TelegramIdentity:
    platform_user_id: int
    username: str | None
    first_name: str
    last_name: str | None
    language_code: str | None
    auth_date: datetime


class TelegramInitDataVerifier:
    def __init__(
        self,
        *,
        bot_token: str,
        max_age_seconds: int,
        future_skew_seconds: int = (
            DEFAULT_FUTURE_SKEW_SECONDS
        ),
    ):
        if not bot_token.strip():
            raise ValueError(
                "Telegram bot token is required."
            )
        if max_age_seconds <= 0:
            raise ValueError(
                "Telegram auth TTL must be positive."
            )
        if future_skew_seconds < 0:
            raise ValueError(
                "Future skew must not be negative."
            )

        self.bot_token = bot_token
        self.max_age_seconds = max_age_seconds
        self.future_skew_seconds = (
            future_skew_seconds
        )

    def verify(
        self,
        init_data: str,
        *,
        now: datetime | None = None,
    ) -> TelegramIdentity:
        try:
            return self._verify(
                init_data,
                now=now,
            )
        except TelegramAuthenticationError:
            raise
        except (
            json.JSONDecodeError,
            OverflowError,
            TypeError,
            ValueError,
        ) as exc:
            raise TelegramAuthenticationError(
                ERROR_MESSAGE
            ) from None

    def _verify(
        self,
        init_data: str,
        *,
        now: datetime | None,
    ) -> TelegramIdentity:
        if not init_data:
            raise TelegramAuthenticationError(
                ERROR_MESSAGE
            )

        pairs = parse_qsl(
            init_data,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=64,
        )
        values = dict(pairs)

        if len(values) != len(pairs):
            raise TelegramAuthenticationError(
                ERROR_MESSAGE
            )

        received_hash = values.pop(
            "hash",
            None,
        )
        if (
            received_hash is None
            or len(received_hash) != 64
        ):
            raise TelegramAuthenticationError(
                ERROR_MESSAGE
            )

        data_check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(
                values.items()
            )
        )
        secret_key = hmac.new(
            b"WebAppData",
            self.bot_token.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        expected_hash = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(
            expected_hash,
            received_hash,
        ):
            raise TelegramAuthenticationError(
                ERROR_MESSAGE
            )

        current_time = now or datetime.now(UTC)
        if current_time.tzinfo is None:
            raise TelegramAuthenticationError(
                ERROR_MESSAGE
            )

        auth_timestamp = int(
            values["auth_date"]
        )
        age_seconds = (
            current_time.timestamp()
            - auth_timestamp
        )

        if (
            age_seconds > self.max_age_seconds
            or age_seconds
            < -self.future_skew_seconds
        ):
            raise TelegramAuthenticationError(
                ERROR_MESSAGE
            )

        user_data = json.loads(
            values["user"]
        )
        if not isinstance(user_data, dict):
            raise TelegramAuthenticationError(
                ERROR_MESSAGE
            )

        platform_user_id = user_data.get("id")
        first_name = user_data.get("first_name")

        if (
            isinstance(platform_user_id, bool)
            or not isinstance(
                platform_user_id,
                int,
            )
            or platform_user_id <= 0
            or not isinstance(first_name, str)
            or not first_name.strip()
        ):
            raise TelegramAuthenticationError(
                ERROR_MESSAGE
            )

        return TelegramIdentity(
            platform_user_id=platform_user_id,
            username=self._optional_string(
                user_data.get("username")
            ),
            first_name=first_name.strip(),
            last_name=self._optional_string(
                user_data.get("last_name")
            ),
            language_code=self._optional_string(
                user_data.get("language_code")
            ),
            auth_date=datetime.fromtimestamp(
                auth_timestamp,
                tz=UTC,
            ),
        )

    @staticmethod
    def _optional_string(
        value: object,
    ) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TelegramAuthenticationError(
                ERROR_MESSAGE
            )

        normalized = value.strip()
        return normalized or None
