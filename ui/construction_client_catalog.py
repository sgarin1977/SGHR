"""Localized client catalog and CRM navigation."""
from ui.construction_project_catalog import tr as catalog_tr

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {}


def label(key, *values):
    if len(values) != len(LANGUAGES):
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))


label("hub", "📋 Клієнти та звернення", "📋 Клиенты и обращения",
      "📋 Clients and enquiries", "📋 Clientes e pedidos",
      "📋 Klienci i zgłoszenia", "📋 Kunden und Anfragen", "📋 Klanten en aanvragen")
label("title", "👤 Клієнти", "👤 Клиенты", "👤 Clients", "👤 Clientes",
      "👤 Klienci", "👤 Kunden", "👤 Klanten")
label("choose", "Оберіть клієнта.", "Выберите клиента.", "Choose a client.",
      "Escolha um cliente.", "Wybierz klienta.", "Wählen Sie einen Kunden.", "Kies een klant.")
label("empty", "Клієнтів поки немає.", "Клиентов пока нет.", "No clients yet.",
      "Ainda não há clientes.", "Brak klientów.", "Noch keine Kunden.", "Nog geen klanten.")
label("back", "До списку клієнтів", "К списку клиентов", "Back to clients",
      "Voltar aos clientes", "Wróć do klientów", "Zur Kundenliste", "Terug naar klanten")


def tr(key, language):
    language = (language or "ru").lower().split("-", 1)[0]
    if key in LABELS:
        return LABELS[key].get(language, LABELS[key]["ru"])
    return catalog_tr(key, language)
