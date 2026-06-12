import pytest
from sqlalchemy import delete

from database.models import ContactRequest, ReputationScore, Review, Specialist
from database.repositories.reviews import ReviewRepository
from services.reviews import ReviewService, ReviewServiceError
from tests.test_beta_04_specialist_registration import cleanup_test_user
from tests.test_beta_08_admin_moderation import (
    create_pending_specialist,
    create_user_with_accepted_consents,
)


pytestmark = pytest.mark.asyncio


async def cleanup_reviews_for_specialist(session, specialist_id):
    await session.rollback()
    await session.execute(
        delete(ReputationScore).where(
            ReputationScore.target_type == "specialist",
            ReputationScore.target_id == specialist_id,
        )
    )
    await session.execute(
        delete(Review).where(
            Review.target_type == "specialist",
            Review.target_id == specialist_id,
        )
    )
    await session.execute(
        delete(ContactRequest).where(ContactRequest.specialist_id == specialist_id)
    )
    await session.commit()


async def create_completed_contact_request(session):
    client_platform_user_id, client_user_id, tenant_id = await create_user_with_accepted_consents(session)
    specialist_platform_user_id, specialist_user_id, tenant_id, specialist = (
        await create_pending_specialist(session)
    )
    specialist.status = "active"

    contact_request = ContactRequest(
        tenant_id=tenant_id,
        from_user_id=client_user_id,
        specialist_id=specialist.id,
        message="review test contact",
        original_language="ru",
        status="completed",
    )
    session.add(contact_request)
    await session.commit()

    return (
        client_platform_user_id,
        client_user_id,
        specialist_platform_user_id,
        specialist_user_id,
        tenant_id,
        specialist.id,
        contact_request.id,
    )


async def test_review_requires_completed_contact_request(db_session):
    (
        client_platform_user_id,
        client_user_id,
        specialist_platform_user_id,
        specialist_user_id,
        tenant_id,
        specialist_id,
        contact_request_id,
    ) = await create_completed_contact_request(db_session)

    contact_request = await db_session.get(ContactRequest, contact_request_id)
    contact_request.status = "accepted"
    await db_session.commit()

    try:
        service = ReviewService(ReviewRepository(db_session))

        with pytest.raises(ReviewServiceError):
            await service.create_contact_review(
                tenant_id=tenant_id,
                reviewer_user_id=client_user_id,
                contact_request_id=contact_request_id,
                rating=5,
                text="good specialist",
            )
    finally:
        await cleanup_reviews_for_specialist(db_session, specialist_id)
        await cleanup_test_user(db_session, client_platform_user_id)
        await cleanup_test_user(db_session, specialist_platform_user_id)


async def test_completed_contact_request_can_be_reviewed_once(db_session):
    (
        client_platform_user_id,
        client_user_id,
        specialist_platform_user_id,
        specialist_user_id,
        tenant_id,
        specialist_id,
        contact_request_id,
    ) = await create_completed_contact_request(db_session)

    try:
        service = ReviewService(ReviewRepository(db_session))

        review = await service.create_contact_review(
            tenant_id=tenant_id,
            reviewer_user_id=client_user_id,
            contact_request_id=contact_request_id,
            rating=5,
            text="Great service",
        )

        assert review.rating == 5
        assert review.status == "pending_moderation"
        assert review.target_type == "specialist"
        assert review.target_id == specialist_id
        assert review.context_type == "contact_request"
        assert review.context_id == contact_request_id

        with pytest.raises(ReviewServiceError):
            await service.create_contact_review(
                tenant_id=tenant_id,
                reviewer_user_id=client_user_id,
                contact_request_id=contact_request_id,
                rating=4,
                text="second review",
            )
    finally:
        await cleanup_reviews_for_specialist(db_session, specialist_id)
        await cleanup_test_user(db_session, client_platform_user_id)
        await cleanup_test_user(db_session, specialist_platform_user_id)


async def test_published_review_recalculates_reputation_and_specialist_rating(db_session):
    (
        client_platform_user_id,
        client_user_id,
        specialist_platform_user_id,
        specialist_user_id,
        tenant_id,
        specialist_id,
        contact_request_id,
    ) = await create_completed_contact_request(db_session)

    try:
        service = ReviewService(ReviewRepository(db_session))

        review = await service.create_contact_review(
            tenant_id=tenant_id,
            reviewer_user_id=client_user_id,
            contact_request_id=contact_request_id,
            rating=4,
            text="Solid work",
        )

        result = await service.publish_review(review_id=review.id)

        assert result.review.status == "published"
        assert result.reputation is not None
        assert float(result.reputation.score) == 4.0
        assert result.reputation.review_count == 1

        refreshed_specialist = await db_session.get(Specialist, specialist_id)
        assert float(refreshed_specialist.rating) == 4.0
        assert refreshed_specialist.reviews_count == 1

        refreshed_contact = await db_session.get(ContactRequest, contact_request_id)
        assert refreshed_contact.status == "reviewed"
    finally:
        await cleanup_reviews_for_specialist(db_session, specialist_id)
        await cleanup_test_user(db_session, client_platform_user_id)
        await cleanup_test_user(db_session, specialist_platform_user_id)


async def test_specialist_can_reply_to_published_review(db_session):
    (
        client_platform_user_id,
        client_user_id,
        specialist_platform_user_id,
        specialist_user_id,
        tenant_id,
        specialist_id,
        contact_request_id,
    ) = await create_completed_contact_request(db_session)

    try:
        service = ReviewService(ReviewRepository(db_session))

        review = await service.create_contact_review(
            tenant_id=tenant_id,
            reviewer_user_id=client_user_id,
            contact_request_id=contact_request_id,
            rating=5,
            text="Helpful",
        )
        await service.publish_review(review_id=review.id)

        replied = await service.add_specialist_reply(
            specialist_user_id=specialist_user_id,
            review_id=review.id,
            reply="Thank you!",
        )

        assert replied.specialist_reply == "Thank you!"
    finally:
        await cleanup_reviews_for_specialist(db_session, specialist_id)
        await cleanup_test_user(db_session, client_platform_user_id)
        await cleanup_test_user(db_session, specialist_platform_user_id)

async def test_review_moderation_lists_pending_and_requires_reason(db_session):
    (
        client_platform_user_id,
        client_user_id,
        specialist_platform_user_id,
        specialist_user_id,
        tenant_id,
        specialist_id,
        contact_request_id,
    ) = await create_completed_contact_request(db_session)

    try:
        service = ReviewService(ReviewRepository(db_session))

        review = await service.create_contact_review(
            tenant_id=tenant_id,
            reviewer_user_id=client_user_id,
            contact_request_id=contact_request_id,
            rating=3,
            text="Needs moderation",
        )

        pending_reviews = await service.list_pending_reviews(limit=10)
        assert any(item.id == review.id for item in pending_reviews)

        with pytest.raises(ReviewServiceError):
            await service.moderate_review(
                review_id=review.id,
                status="published",
                reason="",
            )
    finally:
        await cleanup_reviews_for_specialist(db_session, specialist_id)
        await cleanup_test_user(db_session, client_platform_user_id)
        await cleanup_test_user(db_session, specialist_platform_user_id)


async def test_review_moderation_can_publish_and_hide_review(db_session):
    (
        client_platform_user_id,
        client_user_id,
        specialist_platform_user_id,
        specialist_user_id,
        tenant_id,
        specialist_id,
        contact_request_id,
    ) = await create_completed_contact_request(db_session)

    try:
        service = ReviewService(ReviewRepository(db_session))

        review = await service.create_contact_review(
            tenant_id=tenant_id,
            reviewer_user_id=client_user_id,
            contact_request_id=contact_request_id,
            rating=2,
            text="Publish then hide",
        )

        published = await service.moderate_review(
            review_id=review.id,
            status="published",
            reason="valid user review",
        )
        assert published.review.status == "published"
        assert published.reputation is not None
        assert published.reputation.review_count == 1

        hidden = await service.moderate_review(
            review_id=review.id,
            status="hidden",
            reason="hidden after moderation",
        )
        assert hidden.review.status == "hidden"
        assert hidden.reputation is not None
        assert hidden.reputation.review_count == 0

        refreshed_specialist = await db_session.get(Specialist, specialist_id)
        assert refreshed_specialist.reviews_count == 0
        assert float(refreshed_specialist.rating) == 0.0
    finally:
        await cleanup_reviews_for_specialist(db_session, specialist_id)
        await cleanup_test_user(db_session, client_platform_user_id)
        await cleanup_test_user(db_session, specialist_platform_user_id)


async def test_public_reviews_list_only_published_visible_reviews(db_session):
    (
        client_platform_user_id,
        client_user_id,
        specialist_platform_user_id,
        specialist_user_id,
        tenant_id,
        specialist_id,
        contact_request_id,
    ) = await create_completed_contact_request(db_session)

    try:
        service = ReviewService(ReviewRepository(db_session))

        published_review = await service.create_contact_review(
            tenant_id=tenant_id,
            reviewer_user_id=client_user_id,
            contact_request_id=contact_request_id,
            rating=5,
            text="Visible public review",
        )
        await service.publish_review(review_id=published_review.id)

        hidden_review = Review(
            tenant_id=tenant_id,
            reviewer_user_id=client_user_id,
            target_type="specialist",
            target_id=specialist_id,
            context_type=None,
            context_id=None,
            rating=1,
            text="Hidden review must not be public",
            status="hidden",
        )
        db_session.add(hidden_review)
        await db_session.commit()

        public_page = await service.list_public_reviews_for_specialist(
            tenant_id=tenant_id,
            specialist_id=specialist_id,
            page=0,
            page_size=5,
        )

        assert public_page.total_count == 1
        assert public_page.reputation is not None
        assert public_page.reputation.review_count == 1
        assert len(public_page.reviews) == 1
        assert public_page.reviews[0].id == published_review.id
        assert public_page.reviews[0].text == "Visible public review"
        assert all(review.status == "published" for review in public_page.reviews)
    finally:
        await cleanup_reviews_for_specialist(db_session, specialist_id)
        await cleanup_test_user(db_session, client_platform_user_id)
        await cleanup_test_user(db_session, specialist_platform_user_id)

def test_beta_10_reviews_static_contract():
    models_source = open("database/models.py", encoding="utf-8").read()
    repository_source = open("database/repositories/reviews.py", encoding="utf-8").read()
    service_source = open("services/reviews.py", encoding="utf-8").read()
    admin_source = open("handlers/admin.py", encoding="utf-8").read()
    texts_source = open("ui/texts.py", encoding="utf-8").read()
    search_source = open("handlers/search.py", encoding="utf-8").read()
    moderation_source = open("services/moderation.py", encoding="utf-8").read()
    for fragment in [
        "class Review",
        '__tablename__ = "reviews"',
        "class ReputationScore",
        '__tablename__ = "reputation_scores"',
        "rating",
        "specialist_reply",
        "pending_moderation",
    ]:
        assert fragment in models_source

    for fragment in [
        "class ReviewRepository",
        "create_contact_review",
        "get_completed_contact_request_for_review",
        "get_existing_contact_review",
        "list_pending_reviews",
        "get_specialist_reputation",
        "list_public_reviews_for_specialist",
        "publish_review",
        "reject_review",
        "hide_review",
        "set_review_status",
        "add_specialist_reply",
        "recalculate_reputation",
        'ContactRequest.status == "completed"',
        'Review.status == "published"',
    ]:
        assert fragment in repository_source

    for fragment in [
        "class ReviewService",
        "PublicReviewPage",
        "create_contact_review",
        "list_pending_reviews",
        "list_public_reviews_for_specialist",
        "moderate_review",
        "publish_review",
        "reject_review",
        "add_specialist_reply",
        "_normalize_rating",
        "_normalize_reason",
    ]:
        assert fragment in service_source
    for fragment in [
        "ADM_REVIEWS",
        "ADM_RV_APPROVE:",
        "ADM_RV_REJECT:",
        "ADM_RV_HIDE:",
        "ReviewService(",
        "ReviewRepository(session)",
        "publish_review",
        "hide_review",
        "reject_review",
        "review_published",
        "review_hidden",
        "review_rejected",
    ]:
        assert fragment in admin_source
    for fragment in [
        "review_start:",
        "review_rating:",
        "review_text_skip",
        "choosing_review_rating",
        "entering_review_text",
        "contact_completed_keyboard",
        "review_rating_keyboard",
        "review_skip_text_keyboard",
        "create_review_from_state",
        "ReviewService(",
        "ReviewRepository(session)",
        "create_contact_review",
        "active_contact_request_id",
        "format_public_reviews",
        "public_reviews_keyboard",
        "render_selected_specialist_reviews",
        "search_reviews_page:",
        "search_review_report:",
        "reviews_viewed",
        "public_review_ids",
        "pending_report_target_type",
        "pending_report_target_id",
    ]:
        assert fragment in search_source

    for fragment in [
        "admin_pending_reviews",
        "admin_no_pending_reviews",
        "admin_review_title",
        "admin_review_updated",
        "review_leave_btn",
        "review_rating_prompt",
        "review_text_prompt",
        "review_skip_text_btn",
        "review_created",
        "review_error",
        "public_reviews_title",
        "public_reviews_summary",
        "public_reviews_empty",
        "public_review_item",
        "public_review_report_btn",
    ]:
        assert fragment in texts_source

    assert '"review"' in moderation_source