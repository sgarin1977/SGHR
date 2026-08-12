import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(
    __file__
).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(PROJECT_ROOT),
    )


from database.repositories.root_recovery import (
    RootRecoveryRepository,
)
from database.session import get_session
from services.root_recovery import (
    RootRecoveryService,
    build_root_security,
)


def write_private_file(
    path: Path,
    content: bytes,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
        mode=0o700,
    )

    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL,
        0o600,
    )

    try:
        with os.fdopen(
            descriptor,
            "wb",
        ) as output:
            output.write(content)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def build_service(
    session,
) -> RootRecoveryService:
    return RootRecoveryService(
        RootRecoveryRepository(session),
        build_root_security(),
    )


async def start_enrollment(
    *,
    identity_name: str,
    qr_path: Path,
) -> None:
    if qr_path.exists():
        raise RuntimeError(
            f"Output already exists: {qr_path}"
        )

    password = getpass.getpass(
        "Root password: "
    )
    confirmation = getpass.getpass(
        "Confirm Root password: "
    )

    if password != confirmation:
        raise RuntimeError(
            "Password confirmation does not match."
        )

    async with get_session() as session:
        result = await build_service(
            session
        ).start_enrollment(
            identity_name=identity_name,
            password=password,
        )

    write_private_file(
        qr_path,
        result.qr_png,
    )

    print(
        "Root enrollment started."
    )
    print(
        f"Identity: {result.identity_name}"
    )
    print(
        f"QR: {qr_path.resolve()}"
    )
    print(
        "Scan the QR and run the confirm command."
    )


async def confirm_enrollment(
    *,
    identity_name: str,
    recovery_codes_path: Path,
    qr_path: Path | None,
) -> None:
    if recovery_codes_path.exists():
        raise RuntimeError(
            "Recovery-code output already exists: "
            f"{recovery_codes_path}"
        )

    totp_code = getpass.getpass(
        "Authenticator code: "
    )

    async with get_session() as session:
        recovery_codes = await build_service(
            session
        ).confirm_enrollment(
            identity_name=identity_name,
            totp_code=totp_code,
        )

    content = (
        "SGHR Root Recovery codes\n"
        f"Identity: {identity_name}\n\n"
        + "\n".join(recovery_codes)
        + "\n"
    ).encode("utf-8")

    write_private_file(
        recovery_codes_path,
        content,
    )

    if qr_path:
        qr_path.unlink(missing_ok=True)

    print("Root enrollment confirmed.")
    print(
        "Recovery codes: "
        f"{recovery_codes_path.resolve()}"
    )
    print(
        "Move the codes to a secure password "
        "manager and delete the local file."
    )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "SGHR Root Recovery enrollment"
        )
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    start_parser = subparsers.add_parser(
        "start"
    )
    start_parser.add_argument(
        "--identity",
        required=True,
    )
    start_parser.add_argument(
        "--qr-path",
        required=True,
        type=Path,
    )

    confirm_parser = subparsers.add_parser(
        "confirm"
    )
    confirm_parser.add_argument(
        "--identity",
        required=True,
    )
    confirm_parser.add_argument(
        "--recovery-codes-path",
        required=True,
        type=Path,
    )
    confirm_parser.add_argument(
        "--qr-path",
        type=Path,
    )

    return parser.parse_args()


async def main() -> None:
    arguments = parse_arguments()

    if arguments.command == "start":
        await start_enrollment(
            identity_name=arguments.identity,
            qr_path=arguments.qr_path,
        )
        return

    await confirm_enrollment(
        identity_name=arguments.identity,
        recovery_codes_path=(
            arguments.recovery_codes_path
        ),
        qr_path=arguments.qr_path,
    )


if __name__ == "__main__":
    asyncio.run(main())
