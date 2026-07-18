from uuid import UUID

from database.repositories.favorites import FavoriteRepository


class FavoriteService:
    def __init__(self, repository: FavoriteRepository):
        self.repository = repository

    async def save_specialist(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        specialist_id: UUID,
    ) -> bool:
        return await self.repository.save_specialist(
            tenant_id=tenant_id,
            user_id=user_id,
            specialist_id=specialist_id,
        )
    async def toggle_specialist(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        specialist_id: UUID,
    ) -> bool:
        return await self.repository.toggle_specialist(
            tenant_id=tenant_id,
            user_id=user_id,
            specialist_id=specialist_id,
        )