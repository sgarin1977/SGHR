import argparse
import asyncio
import getpass
import sys
from contextlib import suppress
from pathlib import Path
from uuid import UUID


PROJECT_ROOT = Path(
    __file__
).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from config import ROOT_MFA_MAX_ATTEMPTS
from database.repositories.root_recovery import (
    RootRecoveryRepository,
)
from database.session import get_session
from services.root_actions import (
    RootActionService,
)
from services.root_recovery import (
    ROOT_MANAGED_ADMIN_ROLES,
    RootAuthenticationError,
    RootRecoveryError,
    RootRecoveryService,
    build_root_security,
)


MENU_ACTIONS = {
    "1": "restore_super_admin",
    "2": "grant_administrative_role",
    "3": "revoke_administrative_role",
    "4": "change_regional_admin_scopes",
}


class RootRecoveryCli:
    def __init__(
        self,
        *,
        recovery: RootRecoveryService,
        actions: RootActionService,
        identity_name: str,
        tenant_id: UUID,
        use_recovery_code: bool,
    ):
        self.recovery = recovery
        self.actions = actions
        self.identity_name = identity_name
        self.tenant_id = tenant_id
        self.use_recovery_code = (
            use_recovery_code
        )
        self.session_token: str | None = None
        self.session_active = False

    async def authenticate(self) -> None:
        password = getpass.getpass(
            "Root password: "
        )
        authentication = await (
            self.recovery.start_authentication(
                identity_name=(
                    self.identity_name
                ),
                password=password,
                tenant_id=self.tenant_id,
            )
        )
        self.session_token = (
            authentication.session_token
        )

        for attempt in range(
            1,
            ROOT_MFA_MAX_ATTEMPTS + 1,
        ):
            prompt = (
                "Recovery code: "
                if self.use_recovery_code
                else "Authenticator code: "
            )
            code = getpass.getpass(prompt)

            try:
                await self.recovery.verify_mfa(
                    session_token=(
                        self.session_token
                    ),
                    code=code,
                    use_recovery_code=(
                        self.use_recovery_code
                    ),
                )
                break
            except RootAuthenticationError:
                if attempt >= (
                    ROOT_MFA_MAX_ATTEMPTS
                ):
                    raise

                print(
                    "Invalid MFA code. "
                    "Try again."
                )

        reason = input(
            "Root session reason: "
        ).strip()

        active = await (
            self.recovery.activate_session(
                session_token=(
                    self.session_token
                ),
                reason=reason,
            )
        )
        self.session_active = True

        print(
            "Root Recovery session active "
            f"until {active.expires_at.isoformat()}."
        )

    @staticmethod
    def read_codes(
        prompt: str,
    ) -> list[str]:
        raw_value = input(prompt).strip()

        if not raw_value:
            return []

        return [
            value.strip()
            for value in raw_value.split(",")
            if value.strip()
        ]

    async def request_and_execute(
        self,
        action_type: str,
    ) -> None:
        if not self.session_token:
            raise RootRecoveryError(
                "Active Root session required."
            )

        target_user_id = UUID(
            input("Target user UUID: ").strip()
        )
        reason = input(
            "Action reason: "
        ).strip()

        payload = {}

        if action_type in {
            "grant_administrative_role",
            "revoke_administrative_role",
        }:
            available_roles = ", ".join(
                sorted(ROOT_MANAGED_ADMIN_ROLES)
            )
            print(
                "Available administrative roles: "
                f"{available_roles}"
            )
            role = input(
                "Administrative role: "
            ).strip().lower()

            if role not in ROOT_MANAGED_ADMIN_ROLES:
                raise ValueError(
                    "Unsupported administrative role."
                )

            payload = {"role": role}

        if (
            action_type
            == "change_regional_admin_scopes"
        ):
            print(
                "Leave both scope lists empty to "
                "revoke every regional scope while "
                "keeping the admin role."
            )
            payload = {
                "country_codes": (
                    self.read_codes(
                        "Country codes, comma-separated: "
                    )
                ),
                "language_codes": (
                    self.read_codes(
                        "Language codes, comma-separated: "
                    )
                ),
            }

        print()
        print(f"Action: {action_type}")
        print(f"Target: {target_user_id}")
        print(f"Payload: {payload or 'none'}")
        print(f"Reason: {reason}")

        if input(
            "Type REQUEST to create action: "
        ).strip() != "REQUEST":
            print("Action not requested.")
            return

        request = await self.recovery.request_action(
            session_token=self.session_token,
            action_type=action_type,
            target_user_id=target_user_id,
            action_payload=payload,
            reason=reason,
        )

        expected = f"CONFIRM {target_user_id}"
        confirmation = input(
            f"Type '{expected}' to execute: "
        ).strip()

        if confirmation != expected:
            await self.actions.cancel_action(
                session_token=self.session_token,
                action_id=request.action_id,
            )
            print("Action cancelled.")
            return

        result = await (
            self.actions.confirm_and_execute(
                session_token=self.session_token,
                action_id=request.action_id,
                confirmation_token=(
                    request.confirmation_token
                ),
            )
        )

        print(
            "Action executed: "
            f"{result.action_type}"
        )
        print(f"Result: {result.result}")

    async def run_menu(self) -> None:
        while True:
            print()
            print("Root Recovery")
            print("1. Restore Super Admin")
            print("2. Grant administrative role")
            print("3. Revoke administrative role")
            print("4. Set regional admin scopes")
            print("0. Finish Root session")

            choice = input("Select: ").strip()

            if choice == "0":
                await self.finish()
                return

            action_type = MENU_ACTIONS.get(
                choice
            )

            if not action_type:
                print("Unknown menu option.")
                continue

            try:
                await self.request_and_execute(
                    action_type
                )
            except (
                RootRecoveryError,
                ValueError,
            ) as exc:
                print(f"FAILED: {exc}")

    async def finish(self) -> None:
        if (
            self.session_token
            and self.session_active
        ):
            await self.recovery.complete_session(
                session_token=(
                    self.session_token
                )
            )

        self.session_active = False
        self.session_token = None
        print("Root Recovery session completed.")

    async def cleanup(self) -> None:
        if not self.session_token:
            return

        if self.session_active:
            with suppress(RootRecoveryError):
                await self.recovery.complete_session(
                    session_token=(
                        self.session_token
                    )
                )
        else:
            with suppress(RootRecoveryError):
                await self.recovery.cancel_session(
                    session_token=(
                        self.session_token
                    )
                )

        self.session_active = False
        self.session_token = None


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SGHR Root Recovery"
    )
    parser.add_argument(
        "--identity",
        default="primary-root",
    )
    parser.add_argument(
        "--tenant-id",
        required=True,
        type=UUID,
    )
    parser.add_argument(
        "--recovery-code",
        action="store_true",
        help=(
            "Use a one-time recovery code "
            "instead of TOTP."
        ),
    )
    return parser.parse_args()


async def main() -> None:
    arguments = parse_arguments()

    async with get_session() as session:
        repository = RootRecoveryRepository(
            session
        )
        security = build_root_security()
        cli = RootRecoveryCli(
            recovery=RootRecoveryService(
                repository,
                security,
            ),
            actions=RootActionService(
                repository,
                security,
            ),
            identity_name=arguments.identity,
            tenant_id=arguments.tenant_id,
            use_recovery_code=(
                arguments.recovery_code
            ),
        )

        try:
            await cli.authenticate()
            await cli.run_menu()
        finally:
            await cli.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
