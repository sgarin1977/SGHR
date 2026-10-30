"""Status filter labels; statuses reuse the approved lead lifecycle."""
from ui.construction_lead_status import STATUS_ORDER, status_label

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {}


def label(key, *values):
    if len(values) != len(LANGUAGES):
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))


label("button", "Фільтр за статусом", "Фильтр по статусу", "Filter by status",
      "Filtrar por estado", "Filtruj według statusu", "Nach Status filtern", "Filteren op status")
label("choose", "Оберіть статус звернень.", "Выберите статус обращений.",
      "Choose a lead status.", "Escolha o estado dos contactos.",
      "Wybierz status zgłoszeń.", "Wählen Sie einen Anfragestatus.", "Kies een aanvraagstatus.")
label("all", "Усі статуси", "Все статусы", "All statuses",
      "Todos os estados", "Wszystkie statusy", "Alle Status", "Alle statussen")
label("current", "Статус", "Статус", "Status", "Estado", "Status", "Status", "Status")
label("refresh", "Оновити список", "Обновить список", "Refresh list",
      "Atualizar lista", "Odśwież listę", "Liste aktualisieren", "Lijst vernieuwen")
label("empty", "За цим фільтром звернень немає.", "По этому фильтру обращений нет.",
      "No leads match this filter.", "Nenhum contacto corresponde a este filtro.",
      "Brak zgłoszeń pasujących do filtra.", "Keine Anfragen entsprechen diesem Filter.",
      "Er zijn geen aanvragen voor dit filter.")
label("back", "До всіх звернень", "Ко всем обращениям", "All leads",
      "Todos os contactos", "Wszystkie zgłoszenia", "Alle Anfragen", "Alle aanvragen")


def tr(key, language):
    language = (language or "ru").lower().split("-", 1)[0]
    return LABELS[key].get(language, LABELS[key]["ru"])


def status_caption(status, language):
    return tr("all", language) if status is None else status_label(status, language)

# Current responsible user filter labels v1.
label("responsible", "Відповідальний", "Ответственный", "Responsible person",
      "Responsável", "Osoba odpowiedzialna", "Verantwortlich", "Verantwoordelijke")
label("me", "Я", "Я", "Me", "Eu", "Ja", "Ich", "Ik")
label("any_responsible", "Без фільтра", "Без фильтра", "No filter",
      "Sem filtro", "Bez filtra", "Ohne Filter", "Geen filter")
label("mine_button", "Я відповідальний", "Я ответственный", "Assigned to me",
      "Atribuídos a mim", "Przypisane do mnie", "Mir zugewiesen", "Aan mij toegewezen")
label("all_responsible", "Усі відповідальні", "Все ответственные", "All assignees",
      "Todos os responsáveis", "Wszyscy odpowiedzialni", "Alle Verantwortlichen",
      "Alle verantwoordelijken")
