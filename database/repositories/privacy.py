from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    ContactRequest,
    DataSubjectRequest,
    DeletionJob,
    EventLog,
    Message,
    Review,
    Specialist,
    SpecialistLocation,
    SupportMessage,
    SupportTicket,
    User,
    UserAccount,
    UserConsent,
    UserLanguageSetting,
    UserLocation,
    UserRoleMapping,
)


DELETED_TEXT = "[deleted by user request]"
ADMIN_ROLES = {"super_admin", "admin", "moderator", "support", "finance_admin"}


class PrivacyRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_specialist_by_user_id(self, user_id: UUID) -> Specialist | None:
        result = await self.session.execute(
            select(Specialist).where(Specialist.user_id == user_id)
        )
        return result.scalar_one_or_none()


    async def schedule_profile_deletion(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        platform: str = "telegram",
    ) -> DeletionJob:
        existing_result = await self.session.execute(
            select(DeletionJob)
            .where(
                DeletionJob.tenant_id == tenant_id,
                DeletionJob.user_id == user_id,
                DeletionJob.status.in_(["scheduled", "processing"]),
            )
            .order_by(DeletionJob.scheduled_at.desc())
            .limit(1)
        )
        existing = existing_result.scalar_one_or_none()
        if existing:
            return existing

        job = DeletionJob(
            tenant_id=tenant_id,
            user_id=user_id,
            status="scheduled",
            anonymization_report={},
            scheduled_at=datetime.utcnow(),
        )
        self.session.add(job)
        await self.session.flush()

        self.session.add(
            EventLog(
                tenant_id=tenant_id,
                user_id=user_id,
                event_type="deletion_job_scheduled",
                entity_type="deletion_job",
                entity_id=job.id,
                platform=platform,
                payload={},
            )
        )

        await self.session.flush()
        return job

    async def request_data_export(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        platform: str = "telegram",
    ) -> DataSubjectRequest:
        request = DataSubjectRequest(
            tenant_id=tenant_id,
            user_id=user_id,
            request_type="export_data",
            status="requested",
            requested_at=datetime.utcnow(),
        )
        self.session.add(request)
        await self.session.flush()

        self.session.add(
            EventLog(
                tenant_id=tenant_id,
                user_id=user_id,
                event_type="data_export_requested",
                entity_type="data_subject_request",
                entity_id=request.id,
                platform=platform,
                payload={"request_type": "export_data"},
            )
        )

        await self.session.commit()
        return request

    async def clear_user_geo(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        platform: str = "telegram",
    ) -> int:
        specialist = await self.get_specialist_by_user_id(user_id)

        deleted_user_locations = await self.session.execute(
            delete(UserLocation).where(
                UserLocation.tenant_id == tenant_id,
                UserLocation.user_id == user_id,
            )
        )

        deleted_specialist_locations_count = 0
        if specialist:
            deleted_specialist_locations = await self.session.execute(
                delete(SpecialistLocation).where(
                    SpecialistLocation.specialist_id == specialist.id,
                )
            )
            deleted_specialist_locations_count = deleted_specialist_locations.rowcount or 0

            specialist.country_id = None
            specialist.city_id = None
            specialist.latitude = None
            specialist.longitude = None
            specialist.service_radius_km = 0
            specialist.updated_at = datetime.utcnow()

        deleted_count = (deleted_user_locations.rowcount or 0) + deleted_specialist_locations_count

        self.session.add(
            EventLog(
                tenant_id=tenant_id,
                user_id=user_id,
                event_type="geo_deleted",
                entity_type="user",
                entity_id=user_id,
                platform=platform,
                payload={
                    "deleted_locations": deleted_count,
                    "specialist_id": str(specialist.id) if specialist else None,
                },
            )
        )

        await self.session.commit()
        return deleted_count

    async def list_scheduled_deletion_jobs(
        self,
        *,
        limit: int = 20,
    ) -> list[DeletionJob]:
        result = await self.session.execute(
            select(DeletionJob)
            .where(DeletionJob.status == "scheduled")
            .order_by(DeletionJob.scheduled_at.asc())
            .limit(max(1, min(int(limit), 100)))
        )
        return list(result.scalars().all())

    async def mark_deletion_job_processing(self, job: DeletionJob) -> DeletionJob:
        job.status = "processing"
        job.error_message = None
        await self.session.flush()
        return job

    async def anonymize_user_for_deletion(
        self,
        *,
        job: DeletionJob,
    ) -> dict:
        now = datetime.utcnow()
        user = await self.session.get(User, job.user_id)

        if not user:
            report = {
                "user_found": False,
                "completed_at": now.isoformat(),
            }
            job.status = "completed"
            job.completed_at = now
            job.anonymization_report = report
            await self.session.flush()
            return report

        tenant_id = job.tenant_id
        user_id = job.user_id

        report = {
            "user_found": True,
            "user_id": str(user_id),
            "tenant_id": str(tenant_id),
            "tables": {},
            "completed_at": now.isoformat(),
        }

        specialist = await self.get_specialist_by_user_id(user_id)

        accounts = (
            await self.session.execute(
                select(UserAccount).where(UserAccount.user_id == user_id)
            )
        ).scalars().all()

        for account in accounts:
            account.platform_user_id = f"deleted:{account.id}"
            account.username = None
            account.first_name = None
            account.last_name = None
            account.display_name = None
            account.email = None
            account.phone = None
            account.referral_code = None
            account.raw_profile = {}
            account.updated_at = now

        report["tables"]["user_accounts_anonymized"] = len(accounts)

        roles = (
            await self.session.execute(
                select(UserRoleMapping).where(UserRoleMapping.user_id == user_id)
            )
        ).scalars().all()

        revoked_roles = 0
        for role in roles:
            if role.role not in ADMIN_ROLES and role.status == "active":
                role.status = "revoked"
                revoked_roles += 1

        report["tables"]["user_roles_revoked"] = revoked_roles

        consents = (
            await self.session.execute(
                select(UserConsent).where(
                    UserConsent.tenant_id == tenant_id,
                    UserConsent.user_id == user_id,
                    UserConsent.revoked_at.is_(None),
                )
            )
        ).scalars().all()

        for consent in consents:
            consent.revoked_at = now

        report["tables"]["user_consents_revoked"] = len(consents)

        language_settings = (
            await self.session.execute(
                select(UserLanguageSetting).where(
                    UserLanguageSetting.user_id == user_id
                )
            )
        ).scalars().all()

        for settings in language_settings:
            settings.auto_translate_enabled = False
            settings.show_original_button = True
            settings.updated_at = now

        report["tables"]["user_language_settings_updated"] = len(language_settings)

        deleted_user_locations = await self.session.execute(
            delete(UserLocation).where(
                UserLocation.tenant_id == tenant_id,
                UserLocation.user_id == user_id,
            )
        )
        report["tables"]["user_locations_deleted"] = deleted_user_locations.rowcount or 0

        if specialist:
            deleted_specialist_locations = await self.session.execute(
                delete(SpecialistLocation).where(
                    SpecialistLocation.tenant_id == tenant_id,
                    SpecialistLocation.specialist_id == specialist.id,
                )
            )

            specialist.status = "deleted"
            specialist.display_name = "Deleted profile"
            specialist.short_description = "Profile deleted by user request."
            specialist.full_description = None
            specialist.country_id = None
            specialist.city_id = None
            specialist.latitude = None
            specialist.longitude = None
            specialist.service_radius_km = 0
            specialist.is_available = False
            specialist.is_premium = False
            specialist.priority_score = 0
            specialist.moderation_comment = "Deleted by DSR request."
            specialist.extra_metadata = {
                **(specialist.extra_metadata or {}),
                "deleted_by_dsr": True,
                "deletion_job_id": str(job.id),
            }
            specialist.updated_at = now

            report["tables"]["specialists_anonymized"] = 1
            report["tables"]["specialist_locations_deleted"] = (
                deleted_specialist_locations.rowcount or 0
            )
        else:
            report["tables"]["specialists_anonymized"] = 0
            report["tables"]["specialist_locations_deleted"] = 0

        contact_requests = (
            await self.session.execute(
                select(ContactRequest).where(
                    ContactRequest.tenant_id == tenant_id,
                    ContactRequest.from_user_id == user_id,
                )
            )
        ).scalars().all()

        for request in contact_requests:
            request.message = DELETED_TEXT
            request.updated_at = now

        report["tables"]["contact_requests_masked"] = len(contact_requests)

        messages = (
            await self.session.execute(
                select(Message).where(
                    Message.tenant_id == tenant_id,
                    (Message.sender_user_id == user_id)
                    | (Message.receiver_user_id == user_id),
                )
            )
        ).scalars().all()

        masked_messages = 0
        for message in messages:
            if message.sender_user_id == user_id:
                message.original_text = DELETED_TEXT
                message.translated_text = None
                message.translated_language = None
                message.translation_status = "not_needed"
                message.is_masked = True
                message.extra_metadata = {
                    **(message.extra_metadata or {}),
                    "masked_by_dsr": True,
                    "deletion_job_id": str(job.id),
                }
                masked_messages += 1

        report["tables"]["messages_checked"] = len(messages)
        report["tables"]["messages_masked"] = masked_messages

        support_messages = (
            await self.session.execute(
                select(SupportMessage).where(
                    SupportMessage.tenant_id == tenant_id,
                    SupportMessage.sender_user_id == user_id,
                )
            )
        ).scalars().all()

        for message in support_messages:
            message.message_text = DELETED_TEXT

        report["tables"]["support_messages_masked"] = len(support_messages)

        reviews = (
            await self.session.execute(
                select(Review).where(
                    Review.tenant_id == tenant_id,
                    Review.reviewer_user_id == user_id,
                )
            )
        ).scalars().all()

        for review in reviews:
            review.text = None
            review.updated_at = now

        report["tables"]["reviews_text_cleared"] = len(reviews)

        user.active_role = None
        user.timezone = None
        user.country_id = None
        user.city_id = None
        user.profile_completion_score = 0
        user.trust_score = 0
        user.risk_score = 0
        user.status = "deleted"
        user.extra_metadata = {
            **(user.extra_metadata or {}),
            "deleted_by_dsr": True,
            "deletion_job_id": str(job.id),
        }
        user.updated_at = now

        job.status = "completed"
        job.completed_at = now
        job.error_message = None
        job.anonymization_report = report

        self.session.add(
            EventLog(
                tenant_id=tenant_id,
                user_id=user_id,
                event_type="deletion_job_completed",
                entity_type="deletion_job",
                entity_id=job.id,
                platform="system",
                payload=report,
            )
        )

        await self.session.flush()
        return report

    async def mark_deletion_job_failed(
        self,
        *,
        job: DeletionJob,
        error_message: str,
    ) -> DeletionJob:
        job.status = "failed"
        job.error_message = error_message[:1000]
        job.anonymization_report = {
            **(job.anonymization_report or {}),
            "failed_at": datetime.utcnow().isoformat(),
            "error": error_message[:1000],
        }
        await self.session.flush()
        return job

    async def list_requested_data_exports(
        self,
        *,
        limit: int = 20,
    ) -> list[DataSubjectRequest]:
        result = await self.session.execute(
            select(DataSubjectRequest)
            .where(
                DataSubjectRequest.request_type == "export_data",
                DataSubjectRequest.status == "requested",
            )
            .order_by(DataSubjectRequest.requested_at.asc())
            .limit(max(1, min(int(limit), 100)))
        )
        return list(result.scalars().all())

    async def collect_user_export_data(
        self,
        *,
        request: DataSubjectRequest,
    ) -> dict:
        tenant_id = request.tenant_id
        user_id = request.user_id
        user = await self.session.get(User, user_id)
        specialist = await self.get_specialist_by_user_id(user_id)

        def serialize_model(item):
            if item is None:
                return None

            data = {}
            for column in item.__table__.columns:
                attr_name = "extra_metadata" if column.name == "metadata" else column.name
                value = getattr(item, attr_name)
                if isinstance(value, UUID):
                    value = str(value)
                elif isinstance(value, datetime):
                    value = value.isoformat()
                data[column.name] = value
            return data

        async def list_models(model, *conditions):
            result = await self.session.execute(select(model).where(*conditions))
            return [serialize_model(item) for item in result.scalars().all()]

        return {
            "request_id": str(request.id),
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "generated_at": datetime.utcnow().isoformat(),
            "user": serialize_model(user),
            "user_accounts": await list_models(
                UserAccount,
                UserAccount.user_id == user_id,
            ),
            "user_roles": await list_models(
                UserRoleMapping,
                UserRoleMapping.user_id == user_id,
            ),
            "user_consents": await list_models(
                UserConsent,
                UserConsent.tenant_id == tenant_id,
                UserConsent.user_id == user_id,
            ),
            "user_locations": await list_models(
                UserLocation,
                UserLocation.tenant_id == tenant_id,
                UserLocation.user_id == user_id,
            ),
            "user_language_settings": await list_models(
                UserLanguageSetting,
                UserLanguageSetting.user_id == user_id,
            ),
            "specialist": serialize_model(specialist),
            "support_tickets": await list_models(
                SupportTicket,
                SupportTicket.tenant_id == tenant_id,
                SupportTicket.user_id == user_id,
            ),
            "support_messages": await list_models(
                SupportMessage,
                SupportMessage.tenant_id == tenant_id,
                SupportMessage.sender_user_id == user_id,
            ),
            "contact_requests": await list_models(
                ContactRequest,
                ContactRequest.tenant_id == tenant_id,
                ContactRequest.from_user_id == user_id,
            ),
            "messages": await list_models(
                Message,
                Message.tenant_id == tenant_id,
                (Message.sender_user_id == user_id)
                | (Message.receiver_user_id == user_id),
            ),
            "reviews": await list_models(
                Review,
                Review.tenant_id == tenant_id,
                Review.reviewer_user_id == user_id,
            ),
        }

    async def mark_data_export_completed(
        self,
        *,
        request: DataSubjectRequest,
        result_comment: str,
    ) -> DataSubjectRequest:
        request.status = "completed"
        request.processed_at = datetime.utcnow()
        request.result_comment = result_comment[:2000]

        self.session.add(
            EventLog(
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                event_type="data_export_completed",
                entity_type="data_subject_request",
                entity_id=request.id,
                platform="system",
                payload={"result_comment": request.result_comment},
            )
        )

        await self.session.flush()
        return request

    async def mark_data_export_failed(
        self,
        *,
        request: DataSubjectRequest,
        error_message: str,
    ) -> DataSubjectRequest:
        request.status = "rejected"
        request.processed_at = datetime.utcnow()
        request.result_comment = error_message[:2000]
        await self.session.flush()
        return request