from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    ContactRequest,
    ConversationThread,
    Review,
    ServiceOrder,
)


StatisticsMetricValue = (
    int | Decimal | None
)


class SpecialistStatisticsRepository:
    def __init__(
        self,
        session: AsyncSession,
    ):
        self.session = session

    async def get_period_metrics(
        self,
        *,
        tenant_id: UUID,
        professional_cabinet_id: UUID,
        start_at: datetime,
        end_at: datetime,
    ) -> dict[
        str,
        StatisticsMetricValue,
    ]:
        requests = (
            select(
                func.count(ContactRequest.id)
            )
            .where(
                ContactRequest.tenant_id
                == tenant_id,
                ContactRequest.professional_cabinet_id
                == professional_cabinet_id,
                ContactRequest.created_at
                >= start_at,
                ContactRequest.created_at
                < end_at,
            )
            .scalar_subquery()
        )

        unique_clients = (
            select(
                func.count(
                    func.distinct(
                        ContactRequest.from_user_id
                    )
                )
            )
            .where(
                ContactRequest.tenant_id
                == tenant_id,
                ContactRequest.professional_cabinet_id
                == professional_cabinet_id,
                ContactRequest.created_at
                >= start_at,
                ContactRequest.created_at
                < end_at,
            )
            .scalar_subquery()
        )

        started_dialogs = (
            select(
                func.count(
                    ConversationThread.id
                )
            )
            .where(
                ConversationThread.tenant_id
                == tenant_id,
                ConversationThread.professional_cabinet_id
                == professional_cabinet_id,
                ConversationThread.created_at
                >= start_at,
                ConversationThread.created_at
                < end_at,
            )
            .scalar_subquery()
        )

        completed_dialogs = (
            select(
                func.count(
                    ConversationThread.id
                )
            )
            .where(
                ConversationThread.tenant_id
                == tenant_id,
                ConversationThread.professional_cabinet_id
                == professional_cabinet_id,
                ConversationThread.completed_at
                >= start_at,
                ConversationThread.completed_at
                < end_at,
            )
            .scalar_subquery()
        )

        orders = (
            select(
                func.count(ServiceOrder.id)
            )
            .where(
                ServiceOrder.tenant_id
                == tenant_id,
                ServiceOrder.professional_cabinet_id
                == professional_cabinet_id,
                ServiceOrder.created_at
                >= start_at,
                ServiceOrder.created_at
                < end_at,
            )
            .scalar_subquery()
        )

        completed_orders = (
            select(
                func.count(ServiceOrder.id)
            )
            .where(
                ServiceOrder.tenant_id
                == tenant_id,
                ServiceOrder.professional_cabinet_id
                == professional_cabinet_id,
                ServiceOrder.completed_at
                >= start_at,
                ServiceOrder.completed_at
                < end_at,
            )
            .scalar_subquery()
        )

        published_review_scope = (
            Review.tenant_id == tenant_id,
            Review.professional_cabinet_id
            == professional_cabinet_id,
            Review.status == "published",
            Review.published_at >= start_at,
            Review.published_at < end_at,
        )

        published_reviews = (
            select(func.count(Review.id))
            .where(*published_review_scope)
            .scalar_subquery()
        )

        average_published_rating = (
            select(func.avg(Review.rating))
            .where(*published_review_scope)
            .scalar_subquery()
        )

        result = await self.session.execute(
            select(
                requests.label("requests"),
                unique_clients.label(
                    "unique_clients"
                ),
                started_dialogs.label(
                    "started_dialogs"
                ),
                completed_dialogs.label(
                    "completed_dialogs"
                ),
                orders.label("orders"),
                completed_orders.label(
                    "completed_orders"
                ),
                published_reviews.label(
                    "published_reviews"
                ),
                average_published_rating.label(
                    "average_published_rating"
                ),
            )
        )

        return dict(
            result.mappings().one()
        )
