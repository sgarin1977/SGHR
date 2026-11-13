"""Read-only project presentation using approved public project fields."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from utils.construction_display_timezone import validate_display_timezone
from ui.construction_project_translation import project_field_text, translation_notice

from ui.construction_client_card import clip, tr as client_tr

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {}

def label(key, *values):
    if len(values) != len(LANGUAGES):
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))

label("title",
    "Картка об’єкта", "Карточка объекта", "Project card",
    "Ficha da obra", "Karta obiektu", "Projektkarte", "Projectkaart")
label("status",
    "Статус", "Статус", "Status", "Estado", "Status", "Status", "Status")
label("address",
    "Адреса", "Адрес", "Address", "Morada", "Adres", "Adresse", "Adres")
label("formatted",
    "Форматована адреса", "Форматированный адрес", "Formatted address",
    "Morada formatada", "Sformatowany adres", "Formatierte Adresse",
    "Geformatteerd adres")
label("responsible",
    "Відповідальний", "Ответственный", "Responsible person",
    "Responsável", "Osoba odpowiedzialna", "Verantwortlich", "Verantwoordelijke")
label("you",
    "Ви", "Вы", "You", "Você", "Ty", "Sie", "Jij")
label("assigned",
    "Призначений користувач", "Назначенный пользователь", "Assigned user",
    "Utilizador atribuído", "Przypisany użytkownik",
    "Zugewiesener Benutzer", "Toegewezen gebruiker")
label("unassigned",
    "Не призначено", "Не назначен", "Unassigned", "Não atribuído",
    "Nie przypisano", "Nicht zugewiesen", "Niet toegewezen")
label("comment",
    "Коментар", "Комментарий", "Comment", "Comentário", "Komentarz",
    "Kommentar", "Opmerking")
label("created",
    "Створено", "Создано", "Created", "Criado", "Utworzono", "Erstellt", "Aangemaakt")
label("updated",
    "Оновлено", "Обновлено", "Updated", "Atualizado", "Zaktualizowano",
    "Aktualisiert", "Bijgewerkt")
label("refresh",
    "Оновити об’єкт", "Обновить объект", "Refresh project",
    "Atualizar obra", "Odśwież obiekt", "Projekt aktualisieren",
    "Project verversen")
label("back",
    "До списку об’єктів клієнта", "К списку объектов клиента",
    "Back to this client's projects", "Voltar às obras do cliente",
    "Wróć do obiektów klienta", "Zur Projektliste des Kunden",
    "Terug naar projecten van deze klant")
label("client",
    "Картка клієнта", "Карточка клиента", "Client card",
    "Ficha do cliente", "Karta klienta", "Kundenkarte", "Klantkaart")
label("pending",
    "Адреса очікує перевірки", "Адрес ожидает проверки",
    "Address verification pending", "Verificação da morada pendente",
    "Adres oczekuje na weryfikację", "Adressprüfung ausstehend",
    "Adresverificatie in behandeling")
label("verified",
    "Адресу перевірено", "Адрес проверен", "Address verified",
    "Morada verificada", "Adres zweryfikowany", "Adresse bestätigt",
    "Adres geverifieerd")
label("failed",
    "Адресу не вдалося перевірити", "Адрес не удалось проверить",
    "Address verification failed", "Não foi possível verificar a morada",
    "Nie udało się zweryfikować adresu", "Adressprüfung fehlgeschlagen",
    "Adresverificatie mislukt")

label("currency",
    "Валюта об’єкта", "Валюта объекта", "Project currency",
    "Moeda da obra", "Waluta obiektu", "Projektwährung", "Projectvaluta")
label("currency_unknown",
    "Невідома", "Неизвестна", "Unknown",
    "Desconhecida", "Nieznana", "Unbekannt", "Onbekend")


label("timezone_unconfigured",
    "Часовий пояс не налаштовано. Дати показано в UTC.",
    "Часовой пояс не настроен. Даты показаны в UTC.",
    "No timezone is configured. Dates are shown in UTC.",
    "Fuso horário não configurado. Datas apresentadas em UTC.",
    "Nie ustawiono strefy czasowej. Daty są wyświetlane w UTC.",
    "Keine Zeitzone konfiguriert. Datumsangaben werden in UTC angezeigt.",
    "Geen tijdzone ingesteld. Datums worden in UTC weergegeven.")


def project_currency_text(item, language):
    currency = item.get("currency")
    if currency is None:
        return tr("currency_unknown", language)
    if (
        not isinstance(currency, str) or len(currency) != 3
        or any(not "A" <= character <= "Z" for character in currency)
    ):
        raise ValueError("Canonical project currency code required.")
    return currency


def tr(key, language):
    language = (language or "ru").lower().split("-", 1)[0]
    return LABELS[key].get(language, LABELS[key]["ru"])

def timestamp(value, display_timezone=None):
    if type(value) is not str:
        raise ValueError("Aware project timestamp required.")
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("Aware project timestamp required.")
    context = (
        {"timezone": None, "source": "UNCONFIGURED"}
        if display_timezone is None
        else validate_display_timezone(display_timezone)
    )
    code = context["timezone"] or "UTC"
    local = instant.astimezone(ZoneInfo(code))
    if code == "UTC":
        suffix = "UTC"
    else:
        offset = local.strftime("%z")
        suffix = code + " (UTC" + offset[:3] + ":" + offset[3:] + ")"
    return local.strftime("%d.%m.%Y %H:%M ") + suffix

def project_text(result, language):
    item = result["item"]
    responsible = item["responsible_user_id"]
    responsible_label = (
        tr("unassigned", language) if responsible is None
        else tr("you", language) if responsible == result["actor_user_id"]
        else tr("assigned", language)
    )
    lines = [
        tr("title", language),
        clip(result["organization"], 100),
        project_field_text(result, "name", language),
        tr("status", language) + ": " + client_tr("project_" + item["status"], language),
        tr("currency", language) + ": " + project_currency_text(item, language),
        tr("address", language) + ":\n" + clip(item["address_raw"], 400),
    ]
    if item["address_formatted"] and item["address_formatted"] != item["address_raw"]:
        lines.append(
            tr("formatted", language) + ":\n"
            + clip(item["address_formatted"], 300)
        )
    lines.extend([
        tr(item["address_verification_status"], language),
        tr("responsible", language) + ": " + responsible_label,
        tr("comment", language) + ":\n" + project_field_text(result, "comment", language),
        tr("created", language) + ": " + timestamp(item["created_at"], result.get("display_timezone")),
        tr("updated", language) + ": " + timestamp(item["updated_at"], result.get("display_timezone")),
    ])
    zone_context = result.get("display_timezone")
    if zone_context is None or validate_display_timezone(zone_context)["source"] == "UNCONFIGURED":
        lines.append(tr("timezone_unconfigured", language))
    notice = translation_notice(result, language)
    if notice:
        lines.append(notice)
    text = "\n\n".join(lines)
    if len(text) > 3500:
        raise ValueError("Project presentation is too large.")
    return text
