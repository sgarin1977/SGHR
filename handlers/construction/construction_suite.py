"""Private Telegram navigation and CRM reads. All domain access is checked afresh."""
import logging
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services.api_identity import ApiIdentityAccessError
from services.api_rate_limits import ApiRequestRateLimitExceededError
from services.construction.construction_foundation import ConstructionOrganizationUnavailableError
from services.construction.construction_lead_read import ConstructionLeadReadNotFoundError
from services.construction.construction_lead_rules import ConstructionLeadValidationError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from services.module_access import ModuleDisabledError, SuiteDisabledError
from database.repositories.user import PlatformTelegramAccountUnlinkedError
from services.construction.construction_bot_api_entry import construction_bot_api_entry
from services.construction.construction_api_client import ConstructionApiClientError
from api.telegram_auth import TelegramAuthenticationError
from ui.texts import t
from handlers.construction.construction_activity_terminal import next_action_buttons
from ui.construction_activity import activity_label
from ui.construction_lead_status import status_label, available_statuses
from utils.telegram_cleanup import edit_or_replace_menu_message

logger = logging.getLogger(__name__)
construction_suite_router = Router(name='construction_suite')
STATE_KEY = 'construction_suite_context'


def button(key, language, callback):
    return InlineKeyboardButton(text=t(key, language), callback_data=callback)


def back_rows(language):
    return [[button('construction_bot_home_btn', language, 'CS_HOME')],
            [button('all_services_suites_btn', language, 'M_SUITES')]]


def safe_text(value, limit=350):
    # The existing bot sends plain text (no global HTML/Markdown parse mode).
    return str(value or '')[:limit]


def action_text(action, language):
    if not action:
        return t('construction_bot_no_action', language)
    instant = datetime.fromisoformat(action['due_at'].replace('Z', '+00:00'))
    due = instant.astimezone(ZoneInfo(action['timezone'])).strftime('%Y-%m-%d %H:%M')
    return safe_text(f"{activity_label(action['type'], language)} · {due} ({action['timezone']})")


async def render(callback, state, text, rows):
    message = await edit_or_replace_menu_message(callback=callback, text=text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await state.update_data(last_menu_message_id=message.message_id)


async def open_construction(callback, state, bot=None, event_update=None):
    await dispatch_construction(callback, state, entry=True,
        bot=bot, event_update=event_update)


@construction_suite_router.callback_query(F.data.startswith('CS_'))
async def construction_callback(callback, state, bot=None, event_update=None):
    await dispatch_construction(callback, state, entry=False,
        bot=bot, event_update=event_update)


async def dispatch_construction(callback, state, *, entry, bot=None, event_update=None):
    language = (callback.from_user.language_code or 'ru').lower()
    message = callback.message
    if (message is None or message.chat.type != 'private'
            or message.chat.id != callback.from_user.id):
        await callback.answer(t('construction_bot_private', language), show_alert=True)
        return
    await callback.answer()
    from handlers.platform_organization_create import suspend_organization_form
    await suspend_organization_form(state, telegram_user_id=callback.from_user.id)
    from handlers.construction.construction_lead_create import suspend_lead_form
    from handlers.construction.construction_activity_plan import suspend_activity_form
    await suspend_lead_form(state)
    await suspend_activity_form(state)
    from handlers.construction.construction_activity_terminal import suspend_terminal_form
    await suspend_terminal_form(state)
    from handlers.construction.construction_lead_convert import suspend_conversion_form
    await suspend_conversion_form(state)
    from handlers.construction.construction_project_create import suspend_project_form
    await suspend_project_form(state)
    from handlers.construction.construction_client_create import suspend_client_form
    await suspend_client_form(state)
    from handlers.construction.construction_area_create import suspend_area_form
    await suspend_area_form(state)
    await state.update_data(
        construction_client_card_view={},
        construction_project_card_view={},
        construction_project_areas_view={},
    )
    try:
        async with construction_bot_api_entry(
                bot=bot, event_update=event_update) as service:
            data = await state.get_data()
            context = data.get(STATE_KEY, {})
            if not isinstance(context, dict) or context.get('telegram_user_id') != callback.from_user.id:
                context = {}
            command = 'CS_ORGS' if entry else callback.data
            if command in ('CS_ORGS', 'CS_ORGS_NEXT'):
                after = context.get('org_after') if command == 'CS_ORGS_NEXT' else None
                if command == 'CS_ORGS_NEXT' and not after:
                    raise ApiIdentityAccessError()
                page = await service.organizations(telegram_user_id=callback.from_user.id,
                    after=UUID(after) if after else None)
                language = page['language'] or language
                await state.update_data(**{STATE_KEY: dict(telegram_user_id=callback.from_user.id,
                    org_after=page['next_after'])})
                rows = [[InlineKeyboardButton(text=str(row['name'])[:60],
                    callback_data='CS_ORG:' + UUID(str(row['id'])).hex)] for row in page['items']]
                if page['next_after']:
                    rows.append([button('construction_bot_next_btn', language, 'CS_ORGS_NEXT')])
                from handlers.platform_organization_create import organization_entry_button
                rows.append(await organization_entry_button(
                    state, telegram_user_id=callback.from_user.id, language=language,
                ))
                rows.append([button('all_services_suites_btn', language, 'M_SUITES')])
                await render(callback, state, t('construction_bot_choose_org' if page['items']
                    else 'construction_bot_no_org', language), rows)
                return
            if command.startswith('CS_ORG:'):
                tenant_id = UUID(command.removeprefix('CS_ORG:'))
                # Validate membership and gates before saving the selected organization.
                result = await service.home(telegram_user_id=callback.from_user.id, tenant_id=tenant_id)
                context = dict(telegram_user_id=callback.from_user.id, tenant_id=str(tenant_id))
                await state.update_data(**{STATE_KEY: context})
                command = 'CS_HOME_READY'
            else:
                tenant_id = UUID(context['tenant_id'])
            if command in ('CS_HOME', 'CS_HOME_READY'):
                if command == 'CS_HOME':
                    result = await service.home(telegram_user_id=callback.from_user.id, tenant_id=tenant_id)
                language = result['language'] or language
                rows = []
                if result.get("can_read_projects") is True:
                    from ui.construction_project_catalog import tr as catalog_tr
                    rows.append([InlineKeyboardButton(
                        text=catalog_tr("title", language), callback_data="PL:open",
                    )])
                if result.get("can_read_leads") is True or result.get("can_read_clients") is True:
                    from ui.construction_client_catalog import tr as client_catalog_tr
                    rows.append([InlineKeyboardButton(
                        text=client_catalog_tr("hub", language), callback_data="CM:open",
                    )])
                # Creation actions are available inside the corresponding catalogs.
                if result.get("can_manage_team") is True:
                    from ui.construction_team import tr as team_tr
                    rows.insert(0, [InlineKeyboardButton(
                        text=team_tr("title", language), callback_data="CT:open",
                    )])
                from ui.platform_telegram_binding import tr as telegram_tr
                rows.append([InlineKeyboardButton(
                    text=telegram_tr("title", language), callback_data="TGB:open",
                )])
                from ui.construction_company_profile import tr as company_tr
                rows.append([InlineKeyboardButton(
                    text=company_tr("button", language), callback_data="CP:open",
                )])
                rows.extend([[button('construction_bot_change_org_btn', language, 'CS_ORGS')],
                    [button('all_services_suites_btn', language, 'M_SUITES')]])
                await render(callback, state, t('construction_bot_home', language).format(
                    name=safe_text(result['name']), roles=safe_text(', '.join(result['roles'])),
                    currency=safe_text(result['currency'])), rows)
            elif command in ('CS_LEADS', 'CS_LEADS_NEXT'):
                cursor = context.get('leads_cursor') if command == 'CS_LEADS_NEXT' else None
                if command == 'CS_LEADS_NEXT' and not cursor:
                    raise ApiIdentityAccessError()
                result = await service.leads(telegram_user_id=callback.from_user.id, tenant_id=tenant_id, cursor=cursor)
                language = result['language'] or language
                context['leads_cursor'] = result['next_cursor']
                await state.update_data(**{STATE_KEY: context})
                rows = [[InlineKeyboardButton(text=str(item['name'])[:50] + ' · ' + status_label(item['status'], language),
                    callback_data='CS_LEAD:' + UUID(item['id']).hex)] for item in result['items']]
                if result['next_cursor']:
                    rows.append([button('construction_bot_next_btn', language, 'CS_LEADS_NEXT')])
                rows.append([button(
                    'construction_lead_form_new', language, 'LC:new'
                )])
                rows.extend(back_rows(language))
                from ui.construction_lead_filters import tr as lead_filter_tr
                rows.insert(0, [InlineKeyboardButton(
                    text=lead_filter_tr("button", language), callback_data="LF:open",
                )])
                lines = [t('construction_bot_leads_title', language), safe_text(result['organization'])]
                if not result['items']:
                    lines.append(t('construction_bot_no_leads', language))
                await render(callback, state, '\n\n'.join(lines), rows)
            elif command.startswith('CS_LEAD:'):
                result = await service.lead(telegram_user_id=callback.from_user.id, tenant_id=tenant_id,
                    lead_id=UUID(command.removeprefix('CS_LEAD:')))
                language = result['language'] or language
                item = result['item']
                text = t('construction_bot_lead_detail', language).format(
                    name=safe_text(item['name']), status=status_label(item['status'], language),
                    source=safe_text(item['source']), work=safe_text(item['work_type_description']),
                    phone=safe_text(item['phone']) or '—', email=safe_text(item['email']) or '—',
                    address=safe_text(item['address_raw']) or '—', comment=safe_text(item['comment']) or '—',
                    next_action=action_text(item['next_action'], language))
                rows = [
                    [button(
                        'construction_activity_form_new', language,
                        'AP:new:' + UUID(item['id']).hex,
                    )],
                    [button('construction_bot_leads_btn', language, 'CS_LEADS')],
                    *back_rows(language),
                ]
                if item.get("converted_client_id") is not None:
                    rows.insert(0, [button(
                        "construction_client_card_open", language,
                        "CC:open:" + UUID(item["converted_client_id"]).hex,
                    )])
                if available_statuses(item['status']):
                    rows.insert(0, [button(
                        'construction_lead_status_button', language,
                        'LS:open:' + UUID(item['id']).hex,
                    )])
                if item['status'] not in ('won', 'lost'):
                    rows.insert(0, [button(
                        'construction_lead_convert_button', language,
                        'CV:new:' + UUID(item['id']).hex,
                    )])
                rows[0:0] = await next_action_buttons(
                    state, result,
                    telegram_user_id=callback.from_user.id,
                    tenant_id=tenant_id,
                )
                await render(callback, state, text, rows)
            else:
                raise ValueError('Invalid navigation command.')
    except ConstructionApiClientError as error:
        if error.status_code == 429:
            await render(callback, state, t('construction_bot_rate_limited', language), back_rows(language))
        elif error.status_code in (401, 403, 404, 422):
            logger.warning(
                'construction_bot_api_denied status=%s code=%s request_id=%s',
                error.status_code, error.code, error.request_id or '-',
            )
            await state.update_data(**{STATE_KEY: {}})
            await render(callback, state, t('construction_bot_unavailable', language),
                [[button('construction_bot_change_org_btn', language, 'CS_ORGS')],
                 [button('all_services_suites_btn', language, 'M_SUITES')]])
        else:
            logger.warning('construction_bot_api_failed status=%s code=%s',
                error.status_code, error.code)
            await render(callback, state, t('construction_bot_error', language), back_rows(language))
    except ApiRequestRateLimitExceededError:
        await render(callback, state, t('construction_bot_rate_limited', language), back_rows(language))
    except (TelegramAuthenticationError, ApiIdentityAccessError, PlatformTelegramAccountUnlinkedError, ConstructionPermissionDeniedError,
            ConstructionScopeDeniedError, ConstructionOrganizationUnavailableError, ConstructionLeadReadNotFoundError,
            ConstructionLeadValidationError, ModuleDisabledError, SuiteDisabledError, KeyError, ValueError):
        await state.update_data(**{STATE_KEY: {}})
        await render(callback, state, t('construction_bot_unavailable', language),
            [[button('construction_bot_change_org_btn', language, 'CS_ORGS')],
             [button('all_services_suites_btn', language, 'M_SUITES')]])
    except Exception as error:
        logger.warning('construction_bot_read_failed error_type=%s', type(error).__name__)
        await render(callback, state, t('construction_bot_error', language), back_rows(language))

from handlers.construction.construction_project_edit import (
    router as construction_project_edit_router,
    project_edit_navigation_middleware,
)
construction_suite_router.callback_query.outer_middleware(project_edit_navigation_middleware)
construction_suite_router.include_router(construction_project_edit_router)

from handlers.platform_telegram_binding_ui import (
    router as platform_telegram_binding_ui_router,
    telegram_binding_navigation_middleware,
)
construction_suite_router.callback_query.outer_middleware(telegram_binding_navigation_middleware)
construction_suite_router.include_router(platform_telegram_binding_ui_router)

from handlers.construction.construction_client_edit import (
    router as construction_client_edit_router,
    client_edit_navigation_middleware,
)
construction_suite_router.callback_query.outer_middleware(client_edit_navigation_middleware)
construction_suite_router.include_router(construction_client_edit_router)

from handlers.construction.construction_team_permissions import router as construction_team_permissions_router
construction_suite_router.include_router(construction_team_permissions_router)

from handlers.construction.construction_lead_filters import router as construction_lead_filters_router
construction_suite_router.include_router(construction_lead_filters_router)

from handlers.construction.construction_invitations import router as construction_invitations_router
construction_suite_router.include_router(construction_invitations_router)

from handlers.construction.construction_company_settings import router as construction_company_settings_router
construction_suite_router.include_router(construction_company_settings_router)

from handlers.construction.construction_company_logo import router as construction_company_logo_router
construction_suite_router.include_router(construction_company_logo_router)

from handlers.construction.construction_company_banks import router as construction_company_banks_router
construction_suite_router.include_router(construction_company_banks_router)

from handlers.construction.construction_company_identifiers import router as construction_company_identifiers_router
construction_suite_router.include_router(construction_company_identifiers_router)

from handlers.construction.construction_company_edit import router as construction_company_edit_router
construction_suite_router.include_router(construction_company_edit_router)

from handlers.construction.construction_organization_access import date_router as organization_access_date_router
construction_suite_router.include_router(organization_access_date_router)

# Date input is dispatched before legacy generic message forms.
from handlers.construction.construction_project_team import date_router as construction_project_date_router
construction_suite_router.include_router(construction_project_date_router)

# Separate callback namespace; legacy bot forms are not replaced.
from handlers.construction.construction_lead_create import router as construction_lead_create_router
construction_suite_router.include_router(construction_lead_create_router)

from handlers.construction.construction_lead_status import router as construction_lead_status_router
construction_suite_router.include_router(construction_lead_status_router)

from handlers.construction.construction_activity_plan import router as construction_activity_plan_router
construction_suite_router.include_router(construction_activity_plan_router)

from handlers.construction.construction_activity_terminal import router as construction_activity_terminal_router
construction_suite_router.include_router(construction_activity_terminal_router)

from handlers.construction.construction_lead_convert import router as construction_lead_convert_router
construction_suite_router.include_router(construction_lead_convert_router)

from handlers.construction.construction_client_link_operator import (
    router as construction_client_link_operator_router,
    navigation_middleware as client_link_operator_navigation_middleware,
)
construction_suite_router.callback_query.outer_middleware(
    client_link_operator_navigation_middleware,
)
construction_suite_router.include_router(construction_client_link_operator_router)

from handlers.construction.construction_client_card import router as construction_client_card_router
construction_suite_router.include_router(construction_client_card_router)

from handlers.construction.construction_project_card import router as construction_project_card_router
construction_suite_router.include_router(construction_project_card_router)

from handlers.construction.construction_project_areas import router as construction_project_areas_router
construction_suite_router.include_router(construction_project_areas_router)
from handlers.construction.construction_area_edit import router as construction_area_edit_router
construction_suite_router.include_router(construction_area_edit_router)
from handlers.construction.construction_area_create import router as construction_area_create_router
construction_suite_router.include_router(construction_area_create_router)

from handlers.construction.construction_project_create import router as construction_project_create_router
construction_suite_router.include_router(construction_project_create_router)
from handlers.construction.construction_client_create import router as construction_client_create_router
construction_suite_router.include_router(construction_client_create_router)

from handlers.construction.construction_team import router as construction_team_router
construction_suite_router.include_router(construction_team_router)

from handlers.construction.construction_project_team import (
    router as construction_project_team_router,
    project_team_navigation_middleware,
)
construction_suite_router.callback_query.outer_middleware(project_team_navigation_middleware)
construction_suite_router.include_router(construction_project_team_router)

from handlers.construction.construction_project_revoke import router as construction_project_revoke_router
construction_suite_router.include_router(construction_project_revoke_router)

from handlers.construction.construction_project_replace import router as construction_project_replace_router
construction_suite_router.include_router(construction_project_replace_router)
from handlers.platform_organization_create import router as platform_organization_create_router
construction_suite_router.include_router(platform_organization_create_router)

from handlers.construction.construction_project_catalog import router as construction_project_catalog_router
construction_suite_router.include_router(construction_project_catalog_router)

from handlers.construction.construction_client_catalog import router as construction_client_catalog_router
construction_suite_router.include_router(construction_client_catalog_router)

from handlers.construction.construction_company_profile import router as construction_company_profile_router
construction_suite_router.include_router(construction_company_profile_router)

from handlers.construction.construction_project_activities import router as construction_project_activities_router
construction_suite_router.include_router(construction_project_activities_router)

# universal_area_editor_v1

# client_link_operator_bot_v1
