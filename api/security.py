from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import secrets
from uuid import UUID, uuid4

import jwt
from jwt import InvalidTokenError


JWT_ALGORITHM = "HS256"
MINIMUM_SECRET_LENGTH = 32


class ApiAuthenticationError(Exception):
    pass


@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: UUID
    tenant_id: UUID
    token_id: UUID
    issued_at: datetime
    expires_at: datetime


class AccessTokenCodec:
    def __init__(
        self,
        *,
        secret: str,
        issuer: str,
        audience: str,
        access_ttl_seconds: int,
    ):
        if len(secret) < MINIMUM_SECRET_LENGTH:
            raise ValueError(
                "JWT secret must contain at least "
                f"{MINIMUM_SECRET_LENGTH} characters."
            )

        if access_ttl_seconds <= 0:
            raise ValueError(
                "Access token TTL must be positive."
            )

        self.secret = secret
        self.issuer = issuer
        self.audience = audience
        self.access_ttl_seconds = (
            access_ttl_seconds
        )

    def issue_access_token(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        now: datetime | None = None,
    ) -> str:
        issued_at = now or datetime.now(UTC)

        if issued_at.tzinfo is None:
            raise ValueError(
                "Access token time must be "
                "timezone-aware."
            )

        issued_at = issued_at.astimezone(UTC)
        expires_at = (
            issued_at
            + timedelta(
                seconds=self.access_ttl_seconds
            )
        )

        payload = {
            "sub": str(user_id),
            "tenant_id": str(tenant_id),
            "jti": str(uuid4()),
            "typ": "access",
            "iss": self.issuer,
            "aud": self.audience,
            "iat": issued_at,
            "nbf": issued_at,
            "exp": expires_at,
        }

        return jwt.encode(
            payload,
            self.secret,
            algorithm=JWT_ALGORITHM,
        )

    def decode_access_token(
        self,
        token: str,
    ) -> AccessTokenClaims:
        try:
            payload = jwt.decode(
                token,
                self.secret,
                algorithms=[
                    JWT_ALGORITHM,
                ],
                audience=self.audience,
                issuer=self.issuer,
                options={
                    "require": [
                        "sub",
                        "tenant_id",
                        "jti",
                        "typ",
                        "iss",
                        "aud",
                        "iat",
                        "nbf",
                        "exp",
                    ],
                },
            )

            if payload["typ"] != "access":
                raise ApiAuthenticationError(
                    "Invalid access token type."
                )

            return AccessTokenClaims(
                user_id=UUID(
                    str(payload["sub"])
                ),
                tenant_id=UUID(
                    str(payload["tenant_id"])
                ),
                token_id=UUID(
                    str(payload["jti"])
                ),
                issued_at=datetime.fromtimestamp(
                    payload["iat"],
                    tz=UTC,
                ),
                expires_at=datetime.fromtimestamp(
                    payload["exp"],
                    tz=UTC,
                ),
            )
        except ApiAuthenticationError:
            raise
        except (
            InvalidTokenError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            raise ApiAuthenticationError(
                "Invalid access token."
            ) from exc


@dataclass(frozen=True)
class RefreshTokenMaterial:
    token: str
    token_hash: str


class RefreshTokenCodec:
    TOKEN_BYTES = 48

    def issue_refresh_token(
        self,
    ) -> RefreshTokenMaterial:
        token = secrets.token_urlsafe(
            self.TOKEN_BYTES
        )

        return RefreshTokenMaterial(
            token=token,
            token_hash=self.hash_refresh_token(
                token
            ),
        )

    def hash_refresh_token(
        self,
        token: str,
    ) -> str:
        if (
            not isinstance(token, str)
            or not token
        ):
            raise ValueError(
                "Refresh token is required."
            )

        return hashlib.sha256(
            token.encode("utf-8")
        ).hexdigest()

    def verify_refresh_token(
        self,
        token: str,
        expected_hash: str,
    ) -> bool:
        if (
            not isinstance(token, str)
            or not token
            or not isinstance(
                expected_hash,
                str,
            )
            or not expected_hash
        ):
            return False

        calculated_hash = (
            self.hash_refresh_token(token)
        )

        return hmac.compare_digest(
            calculated_hash,
            expected_hash,
        )


@dataclass(frozen=True)
class EmailAuthChallengeMaterial:
    code: str
    challenge_hash: str


@dataclass(frozen=True)
class EmailAuthMagicLinkMaterial:
    token: str
    challenge_hash: str


class EmailAuthChallengeCodec:
    CODE_DIGITS = 6

    def __init__(
        self,
        *,
        secret: str,
    ):
        if (
            not isinstance(secret, str)
            or len(secret) < MINIMUM_SECRET_LENGTH
        ):
            raise ValueError(
                "Email Auth secret must contain "
                f"at least {MINIMUM_SECRET_LENGTH} "
                "characters."
            )

        self._secret = secret.encode("utf-8")

    @staticmethod
    def normalize_email(
        email: str,
    ) -> str:
        if not isinstance(email, str):
            raise ValueError(
                "Email address is required."
            )

        normalized = email.strip().casefold()

        if (
            not normalized
            or len(normalized) > 320
        ):
            raise ValueError(
                "Email address is invalid."
            )

        return normalized

    def issue_code(
        self,
        *,
        email: str,
    ) -> EmailAuthChallengeMaterial:
        code = str(
            secrets.randbelow(
                10 ** self.CODE_DIGITS
            )
        ).zfill(self.CODE_DIGITS)

        return EmailAuthChallengeMaterial(
            code=code,
            challenge_hash=self.hash_code(
                email=email,
                code=code,
            ),
        )

    def hash_code(
        self,
        *,
        email: str,
        code: str,
    ) -> str:
        normalized_email = (
            self.normalize_email(email)
        )

        if (
            not isinstance(code, str)
            or len(code) != self.CODE_DIGITS
            or not code.isdigit()
        ):
            raise ValueError(
                "Email Auth code is invalid."
            )

        payload = (
            normalized_email
            + "\0"
            + code
        ).encode("utf-8")

        return hmac.new(
            self._secret,
            payload,
            hashlib.sha256,
        ).hexdigest()

    def verify_code(
        self,
        *,
        email: str,
        code: str,
        expected_hash: str,
    ) -> bool:
        if (
            not isinstance(expected_hash, str)
            or len(expected_hash) != 64
        ):
            return False

        try:
            calculated_hash = self.hash_code(
                email=email,
                code=code,
            )
        except ValueError:
            return False

        return hmac.compare_digest(
            calculated_hash,
            expected_hash,
        )


    def issue_magic_link(
        self,
        *,
        email: str,
    ) -> EmailAuthMagicLinkMaterial:
        token = secrets.token_urlsafe(32)

        return EmailAuthMagicLinkMaterial(
            token=token,
            challenge_hash=(
                self.hash_magic_link_token(
                    email=email,
                    token=token,
                )
            ),
        )

    def hash_magic_link_token(
        self,
        *,
        email: str,
        token: str,
    ) -> str:
        normalized_email = (
            self.normalize_email(email)
        )

        if (
            not isinstance(token, str)
            or not token
            or len(token) > 512
        ):
            raise ValueError(
                "Magic Link token is invalid."
            )

        payload = (
            "magic_link\0"
            + normalized_email
            + "\0"
            + token
        ).encode("utf-8")

        return hmac.new(
            self._secret,
            payload,
            hashlib.sha256,
        ).hexdigest()

    def verify_magic_link(
        self,
        *,
        email: str,
        token: str,
        expected_hash: str,
    ) -> bool:
        if (
            not isinstance(expected_hash, str)
            or len(expected_hash) != 64
        ):
            return False

        try:
            calculated_hash = (
                self.hash_magic_link_token(
                    email=email,
                    token=token,
                )
            )
        except ValueError:
            return False

        return hmac.compare_digest(
            calculated_hash,
            expected_hash,
        )

@dataclass(frozen=True)
class ApiKeyMaterial:
    key: str
    key_prefix: str
    key_hash: str


class ApiKeyCodec:
    PREFIX_BYTES = 8
    SECRET_BYTES = 48

    def issue_api_key(self) -> ApiKeyMaterial:
        key_prefix = (
            "sghr_"
            + secrets.token_hex(
                self.PREFIX_BYTES
            )
        )
        key = (
            key_prefix
            + "."
            + secrets.token_urlsafe(
                self.SECRET_BYTES
            )
        )

        return ApiKeyMaterial(
            key=key,
            key_prefix=key_prefix,
            key_hash=self.hash_api_key(key),
        )

    def hash_api_key(
        self,
        key: str,
    ) -> str:
        if (
            not isinstance(key, str)
            or not key
            or len(key) > 512
        ):
            raise ValueError(
                "API key is invalid."
            )

        return hashlib.sha256(
            key.encode("utf-8")
        ).hexdigest()

    def verify_api_key(
        self,
        key: str,
        expected_hash: str,
    ) -> bool:
        if (
            not isinstance(expected_hash, str)
            or len(expected_hash) != 64
        ):
            return False

        try:
            calculated_hash = (
                self.hash_api_key(key)
            )
        except ValueError:
            return False

        return hmac.compare_digest(
            calculated_hash,
            expected_hash,
        )

    def extract_key_prefix(
        self,
        key: str,
    ) -> str:
        if (
            not isinstance(key, str)
            or not key
            or len(key) > 512
            or key.count(".") != 1
        ):
            raise ValueError(
                "API key is invalid."
            )

        key_prefix, secret = key.split(
            ".",
            1,
        )

        if not key_prefix or not secret:
            raise ValueError(
                "API key is invalid."
            )

        return key_prefix

