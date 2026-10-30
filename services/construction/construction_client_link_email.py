"""Exact email selection for operator client links; never an authenticator."""
from services.construction.construction_clients import (
    ConstructionClientNotFoundError,
    ConstructionClientValidationError,
)
from services.construction.construction_invitations import (
    normalize_construction_invitation_email,
)

class ClientLinkPlatformUserNotFoundError(ConstructionClientNotFoundError):
    code = "PLATFORM_USER_NOT_FOUND"

    def __init__(self):
        super().__init__("Verified Platform user is not available.")

def normalize_client_link_email(value):
    try:
        return normalize_construction_invitation_email(value)
    except ValueError:
        raise ConstructionClientValidationError(
            "Target email is invalid."
        ) from None

# client_link_exact_verified_email_v1
