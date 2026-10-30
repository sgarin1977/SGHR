"""Read-only project presentation using approved public project fields."""
from datetime import datetime, timezone

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

def tr(key, language):
    language = (language or "ru").lower().split("-", 1)[0]
    return LABELS[key].get(language, LABELS[key]["ru"])

def timestamp(value):
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise ValueError("Aware project timestamp required.")
    return instant.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")

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
        clip(item["name"], 180),
        tr("status", language) + ": " + client_tr("project_" + item["status"], language),
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
        tr("comment", language) + ":\n" + clip(item["comment"], 600),
        tr("created", language) + ": " + timestamp(item["created_at"]),
        tr("updated", language) + ": " + timestamp(item["updated_at"]),
    ])
    text = "\n\n".join(lines)
    if len(text) > 3500:
        raise ValueError("Project presentation is too large.")
    return text
