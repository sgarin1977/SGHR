"""Compatibility import for the shared Platform membership reader."""
from database.repositories.platform_organizations import (
    PlatformOrganizationsRepository,
)

ConstructionBotOrganizationRepository = PlatformOrganizationsRepository
