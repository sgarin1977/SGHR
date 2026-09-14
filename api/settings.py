import os
from collections.abc import Mapping
from dataclasses import dataclass

from dotenv import load_dotenv
from cryptography.fernet import Fernet


MINIMUM_JWT_SECRET_LENGTH = 32
DEFAULT_JWT_ISSUER = "sghr-api"
DEFAULT_JWT_AUDIENCE = "sghr-core"
DEFAULT_ACCESS_TOKEN_TTL_SECONDS = 900
DEFAULT_REFRESH_TOKEN_TTL_SECONDS = 2592000
DEFAULT_TELEGRAM_AUTH_MAX_AGE_SECONDS = 300

MINIMUM_EMAIL_AUTH_SECRET_LENGTH = 32
DEFAULT_EMAIL_AUTH_CODE_TTL_SECONDS = 600
DEFAULT_EMAIL_AUTH_MAX_ATTEMPTS = 5
DEFAULT_EMAIL_AUTH_REQUEST_LIMIT = 5
DEFAULT_EMAIL_AUTH_REQUEST_WINDOW_SECONDS = 900


class ApiConfigurationError(Exception):
    pass


@dataclass(frozen=True)
class ApiAuthSettings:
    jwt_secret: str
    jwt_issuer: str
    jwt_audience: str
    access_token_ttl_seconds: int
    refresh_token_ttl_seconds: int

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str]
        | None = None,
    ) -> "ApiAuthSettings":
        if environ is None:
            load_dotenv()
            values: Mapping[str, str] = (
                os.environ
            )
        else:
            values = environ

        jwt_secret = (
            values.get(
                "API_JWT_SECRET",
                "",
            )
            .strip()
        )

        if (
            len(jwt_secret)
            < MINIMUM_JWT_SECRET_LENGTH
        ):
            raise ApiConfigurationError(
                "API_JWT_SECRET must contain "
                f"at least "
                f"{MINIMUM_JWT_SECRET_LENGTH} "
                "characters."
            )

        jwt_issuer = (
            values.get(
                "API_JWT_ISSUER",
                DEFAULT_JWT_ISSUER,
            )
            .strip()
        )
        jwt_audience = (
            values.get(
                "API_JWT_AUDIENCE",
                DEFAULT_JWT_AUDIENCE,
            )
            .strip()
        )

        if not jwt_issuer:
            raise ApiConfigurationError(
                "API_JWT_ISSUER must not be empty."
            )

        if not jwt_audience:
            raise ApiConfigurationError(
                "API_JWT_AUDIENCE must not be empty."
            )

        raw_ttl = values.get(
            "API_ACCESS_TOKEN_TTL_SECONDS",
            str(
                DEFAULT_ACCESS_TOKEN_TTL_SECONDS
            ),
        )

        try:
            access_token_ttl_seconds = int(
                raw_ttl
            )
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise ApiConfigurationError(
                "API access token TTL must "
                "be an integer."
            ) from exc

        if access_token_ttl_seconds <= 0:
            raise ApiConfigurationError(
                "API access token TTL must "
                "be positive."
            )

        raw_refresh_ttl = values.get(
            "API_REFRESH_TOKEN_TTL_SECONDS",
            str(
                DEFAULT_REFRESH_TOKEN_TTL_SECONDS
            ),
        )

        try:
            refresh_token_ttl_seconds = int(
                raw_refresh_ttl
            )
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise ApiConfigurationError(
                "API refresh token TTL must "
                "be an integer."
            ) from exc

        if refresh_token_ttl_seconds <= 0:
            raise ApiConfigurationError(
                "API refresh token TTL must "
                "be positive."
            )

        return cls(
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
            access_token_ttl_seconds=(
                access_token_ttl_seconds
            ),
            refresh_token_ttl_seconds=(
                refresh_token_ttl_seconds
            ),
        )


@dataclass(frozen=True)
class ApiTelegramAuthSettings:
    bot_token: str
    max_age_seconds: int

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str]
        | None = None,
    ) -> "ApiTelegramAuthSettings":
        if environ is None:
            load_dotenv()
            values: Mapping[str, str] = (
                os.environ
            )
        else:
            values = environ

        bot_token = (
            values.get(
                "BOT_TOKEN",
                "",
            )
            .strip()
        )

        if not bot_token:
            raise ApiConfigurationError(
                "BOT_TOKEN is required for "
                "Telegram authentication."
            )

        raw_max_age = values.get(
            "API_TELEGRAM_AUTH_MAX_AGE_SECONDS",
            str(
                DEFAULT_TELEGRAM_AUTH_MAX_AGE_SECONDS
            ),
        )

        try:
            max_age_seconds = int(
                raw_max_age
            )
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise ApiConfigurationError(
                "Telegram authentication TTL "
                "must be an integer."
            ) from exc

        if max_age_seconds <= 0:
            raise ApiConfigurationError(
                "Telegram authentication TTL "
                "must be positive."
            )

        return cls(
            bot_token=bot_token,
            max_age_seconds=max_age_seconds,
        )


@dataclass(frozen=True)
class ApiEmailAuthSettings:
    challenge_secret: str
    code_ttl_seconds: int
    max_attempts: int
    request_limit: int
    request_window_seconds: int

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str]
        | None = None,
    ) -> "ApiEmailAuthSettings":
        if environ is None:
            load_dotenv()
            values: Mapping[str, str] = (
                os.environ
            )
        else:
            values = environ

        challenge_secret = (
            values.get(
                "API_EMAIL_AUTH_SECRET",
                "",
            )
            .strip()
        )

        if (
            len(challenge_secret)
            < MINIMUM_EMAIL_AUTH_SECRET_LENGTH
        ):
            raise ApiConfigurationError(
                "API_EMAIL_AUTH_SECRET must "
                "contain at least "
                f"{MINIMUM_EMAIL_AUTH_SECRET_LENGTH} "
                "characters."
            )

        def positive_integer(
            name: str,
            default: int,
        ) -> int:
            raw_value = values.get(
                name,
                str(default),
            )

            try:
                value = int(raw_value)
            except (
                TypeError,
                ValueError,
            ) as exc:
                raise ApiConfigurationError(
                    f"{name} must be an integer."
                ) from exc

            if value <= 0:
                raise ApiConfigurationError(
                    f"{name} must be positive."
                )

            return value

        return cls(
            challenge_secret=challenge_secret,
            code_ttl_seconds=positive_integer(
                (
                    "EMAIL_AUTH_TOKEN_TTL"
                    if values.get(
                        "EMAIL_AUTH_TOKEN_TTL"
                    )
                    is not None
                    else (
                        "API_EMAIL_AUTH_"
                        "CODE_TTL_SECONDS"
                    )
                ),
                DEFAULT_EMAIL_AUTH_CODE_TTL_SECONDS,
            ),
            max_attempts=positive_integer(
                "API_EMAIL_AUTH_MAX_ATTEMPTS",
                DEFAULT_EMAIL_AUTH_MAX_ATTEMPTS,
            ),
            request_limit=positive_integer(
                "API_EMAIL_AUTH_REQUEST_LIMIT",
                DEFAULT_EMAIL_AUTH_REQUEST_LIMIT,
            ),
            request_window_seconds=positive_integer(
                (
                    "API_EMAIL_AUTH_REQUEST_"
                    "WINDOW_SECONDS"
                ),
                DEFAULT_EMAIL_AUTH_REQUEST_WINDOW_SECONDS,
            ),
        )



@dataclass(frozen=True)
class ApiEmailDeliverySettings:
    provider: str
    from_address: str
    web_app_url: str
    mobile_deep_link_scheme: str
    resend_api_key: str
    token_ttl_seconds: int

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str]
        | None = None,
    ) -> "ApiEmailDeliverySettings":
        from urllib.parse import urlsplit

        if environ is None:
            load_dotenv()
            values: Mapping[str, str] = (
                os.environ
            )
        else:
            values = environ

        provider = values.get(
            "EMAIL_PROVIDER",
            "resend",
        ).strip().lower()

        if provider != "resend":
            raise ApiConfigurationError(
                "Unsupported email provider."
            )

        from_address = values.get(
            "EMAIL_FROM",
            "",
        ).strip()

        if (
            from_address.count("@") != 1
            or any(
                character.isspace()
                for character in from_address
            )
        ):
            raise ApiConfigurationError(
                "EMAIL_FROM is invalid."
            )

        web_app_url = values.get(
            "WEB_APP_URL",
            "",
        ).strip().rstrip("/")

        parsed_url = urlsplit(web_app_url)
        if (
            parsed_url.scheme != "https"
            or not parsed_url.netloc
        ):
            raise ApiConfigurationError(
                "WEB_APP_URL must be an HTTPS URL."
            )

        mobile_scheme = values.get(
            "MOBILE_DEEP_LINK_SCHEME",
            "sghr",
        ).strip().lower().rstrip(":/")

        if (
            not mobile_scheme
            or not mobile_scheme[0].isalpha()
            or not all(
                character.isalnum()
                or character in "+-."
                for character in mobile_scheme
            )
        ):
            raise ApiConfigurationError(
                "MOBILE_DEEP_LINK_SCHEME "
                "is invalid."
            )

        resend_api_key = values.get(
            "RESEND_API_KEY",
            "",
        ).strip()

        if not resend_api_key.startswith("re_"):
            raise ApiConfigurationError(
                "RESEND_API_KEY is invalid."
            )

        raw_ttl = values.get(
            "EMAIL_AUTH_TOKEN_TTL",
            "600",
        )

        try:
            token_ttl_seconds = int(raw_ttl)
        except (TypeError, ValueError) as exc:
            raise ApiConfigurationError(
                "EMAIL_AUTH_TOKEN_TTL must "
                "be an integer."
            ) from exc

        if token_ttl_seconds <= 0:
            raise ApiConfigurationError(
                "EMAIL_AUTH_TOKEN_TTL must "
                "be positive."
            )

        return cls(
            provider=provider,
            from_address=from_address,
            web_app_url=web_app_url,
            mobile_deep_link_scheme=(
                mobile_scheme
            ),
            resend_api_key=resend_api_key,
            token_ttl_seconds=(
                token_ttl_seconds
            ),
        )



@dataclass(frozen=True)
class ApiPartnerSettings:
    environment: str

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str]
        | None = None,
    ) -> "ApiPartnerSettings":
        if environ is None:
            load_dotenv()
            values: Mapping[str, str] = (
                os.environ
            )
        else:
            values = environ

        environment = (
            values.get(
                "API_ENVIRONMENT",
                "",
            )
            .strip()
            .lower()
        )

        if environment not in {
            "sandbox",
            "production",
        }:
            raise ApiConfigurationError(
                "API_ENVIRONMENT must be "
                "sandbox or production."
            )

        return cls(
            environment=environment,
        )



DEFAULT_IDEMPOTENCY_RETENTION_SECONDS = 86400


@dataclass(frozen=True)
class ApiIdempotencySettings:
    encryption_key: str
    retention_seconds: int = (
        DEFAULT_IDEMPOTENCY_RETENTION_SECONDS
    )

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str]
        | None = None,
    ) -> "ApiIdempotencySettings":
        if environ is None:
            load_dotenv()
            environ = os.environ

        encryption_key = environ.get(
            "API_IDEMPOTENCY_ENCRYPTION_KEY",
            "",
        ).strip()
        if not encryption_key:
            raise ApiConfigurationError(
                "API_IDEMPOTENCY_ENCRYPTION_KEY "
                "is required."
            )

        try:
            Fernet(
                encryption_key.encode("ascii")
            )
        except (UnicodeEncodeError, ValueError) as exc:
            raise ApiConfigurationError(
                "API_IDEMPOTENCY_ENCRYPTION_KEY "
                "must be a valid Fernet key."
            ) from exc

        return cls(
            encryption_key=encryption_key,
        )



@dataclass(frozen=True)
class ApiWebhookSettings:
    secret_encryption_key: str
    environment: str

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str]
        | None = None,
    ) -> "ApiWebhookSettings":
        if environ is None:
            load_dotenv()
            values: Mapping[str, str] = (
                os.environ
            )
        else:
            values = environ

        encryption_key = (
            values.get(
                "WEBHOOK_SECRET_ENCRYPTION_KEY",
                "",
            )
            .strip()
        )
        if not encryption_key:
            raise ValueError(
                "WEBHOOK_SECRET_ENCRYPTION_KEY "
                "is required."
            )

        try:
            Fernet(
                encryption_key.encode("ascii")
            )
        except (
            UnicodeEncodeError,
            ValueError,
        ) as exc:
            raise ValueError(
                "WEBHOOK_SECRET_ENCRYPTION_KEY "
                "must be a valid Fernet key."
            ) from exc

        environment = (
            values.get(
                "API_ENVIRONMENT",
                "",
            )
            .strip()
            .lower()
        )
        if environment not in {
            "sandbox",
            "production",
        }:
            raise ValueError(
                "API_ENVIRONMENT must be "
                "sandbox or production."
            )

        return cls(
            secret_encryption_key=(
                encryption_key
            ),
            environment=environment,
        )



@dataclass(frozen=True)
class ApiRateLimitSettings:
    environment: str
    redis_url: str | None

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str]
        | None = None,
    ) -> "ApiRateLimitSettings":
        if environ is None:
            load_dotenv()
            values: Mapping[str, str] = (
                os.environ
            )
        else:
            values = environ

        environment = (
            values.get(
                "API_ENVIRONMENT",
                "",
            )
            .strip()
            .lower()
        )

        if environment not in {
            "sandbox",
            "production",
        }:
            raise ApiConfigurationError(
                "API_ENVIRONMENT must be "
                "sandbox or production."
            )

        redis_url = (
            values.get(
                "REDIS_URL",
                "",
            )
            .strip()
        )

        if (
            redis_url
            and not redis_url.startswith(
                (
                    "redis://",
                    "rediss://",
                )
            )
        ):
            raise ApiConfigurationError(
                "REDIS_URL must use redis:// "
                "or rediss://."
            )

        if (
            environment == "production"
            and not redis_url
        ):
            raise ApiConfigurationError(
                "REDIS_URL is required for "
                "production API rate limits."
            )

        return cls(
            environment=environment,
            redis_url=redis_url or None,
        )
