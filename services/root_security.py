import hashlib
import hmac
import io
import secrets
import string

import pyotp
import qrcode
from argon2 import PasswordHasher
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)
from cryptography.fernet import Fernet, InvalidToken


RECOVERY_ALPHABET = (
    string.ascii_uppercase
    + "23456789"
)


class RootSecurityError(RuntimeError):
    pass


class RootSecurityConfigurationError(
    RootSecurityError
):
    pass


class RootSecurity:
    def __init__(
        self,
        *,
        encryption_key: str,
        totp_issuer: str,
        password_min_length: int = 14,
        recovery_code_count: int = 10,
    ):
        if not encryption_key:
            raise RootSecurityConfigurationError(
                "Root encryption key is missing."
            )

        try:
            self._fernet = Fernet(
                encryption_key.encode("ascii")
            )
        except (TypeError, ValueError) as exc:
            raise RootSecurityConfigurationError(
                "Root encryption key is invalid."
            ) from exc

        self._issuer = totp_issuer.strip()

        if not self._issuer:
            raise RootSecurityConfigurationError(
                "TOTP issuer is missing."
            )

        self._password_min_length = max(
            14,
            int(password_min_length),
        )
        self._recovery_code_count = max(
            1,
            int(recovery_code_count),
        )
        self._password_hasher = PasswordHasher()
        self._recovery_hasher = PasswordHasher()

    def hash_password(
        self,
        password: str,
    ) -> str:
        if len(password) < self._password_min_length:
            raise ValueError(
                "Root password is too short."
            )

        return self._password_hasher.hash(password)

    def verify_password(
        self,
        password_hash: str,
        password: str,
    ) -> bool:
        try:
            return self._password_hasher.verify(
                password_hash,
                password,
            )
        except (
            InvalidHashError,
            VerificationError,
            VerifyMismatchError,
        ):
            return False

    @staticmethod
    def generate_totp_secret() -> str:
        return pyotp.random_base32()

    def encrypt_totp_secret(
        self,
        secret: str,
    ) -> str:
        if not secret:
            raise ValueError(
                "TOTP secret is required."
            )

        return self._fernet.encrypt(
            secret.encode("ascii")
        ).decode("ascii")

    def decrypt_totp_secret(
        self,
        encrypted_secret: str,
    ) -> str:
        try:
            return self._fernet.decrypt(
                encrypted_secret.encode("ascii")
            ).decode("ascii")
        except (
            InvalidToken,
            UnicodeError,
            ValueError,
        ) as exc:
            raise RootSecurityError(
                "Unable to decrypt TOTP secret."
            ) from exc

    def build_totp_uri(
        self,
        *,
        identity_name: str,
        secret: str,
    ) -> str:
        return pyotp.TOTP(secret).provisioning_uri(
            name=identity_name,
            issuer_name=self._issuer,
        )

    @staticmethod
    def build_qr_png(
        provisioning_uri: str,
    ) -> bytes:
        qr = qrcode.QRCode(
            version=None,
            error_correction=(
                qrcode.constants.ERROR_CORRECT_M
            ),
            box_size=8,
            border=4,
        )
        qr.add_data(provisioning_uri)
        qr.make(fit=True)

        image = qr.make_image(
            fill_color="black",
            back_color="white",
        )
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    @staticmethod
    def verify_totp(
        secret: str,
        code: str,
    ) -> bool:
        normalized_code = (
            code or ""
        ).strip().replace(" ", "")

        if (
            len(normalized_code) != 6
            or not normalized_code.isdigit()
        ):
            return False

        return bool(
            pyotp.TOTP(secret).verify(
                normalized_code,
                valid_window=1,
            )
        )

    def generate_recovery_codes(
        self,
    ) -> list[str]:
        codes: set[str] = set()

        while len(codes) < self._recovery_code_count:
            raw_code = "".join(
                secrets.choice(RECOVERY_ALPHABET)
                for _ in range(16)
            )
            codes.add(
                "-".join(
                    raw_code[index:index + 4]
                    for index in range(0, 16, 4)
                )
            )

        return sorted(codes)

    @staticmethod
    def normalize_recovery_code(
        code: str,
    ) -> str:
        return "".join(
            character
            for character in (code or "").upper()
            if character.isalnum()
        )

    def hash_recovery_code(
        self,
        code: str,
    ) -> str:
        normalized_code = (
            self.normalize_recovery_code(code)
        )

        if len(normalized_code) != 16:
            raise ValueError(
                "Recovery code is invalid."
            )

        return self._recovery_hasher.hash(
            normalized_code
        )

    def verify_recovery_code(
        self,
        *,
        code_hash: str,
        code: str,
    ) -> bool:
        normalized_code = (
            self.normalize_recovery_code(code)
        )

        if len(normalized_code) != 16:
            return False

        try:
            return self._recovery_hasher.verify(
                code_hash,
                normalized_code,
            )
        except (
            InvalidHashError,
            VerificationError,
            VerifyMismatchError,
        ):
            return False

    @staticmethod
    def generate_secure_token() -> str:
        return secrets.token_urlsafe(32)

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(
            token.encode("utf-8")
        ).hexdigest()

    @classmethod
    def verify_token(
        cls,
        *,
        token: str,
        token_hash: str,
    ) -> bool:
        return hmac.compare_digest(
            cls.hash_token(token),
            token_hash,
        )
