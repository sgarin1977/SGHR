"""Draft metadata and explicit creation labels."""
from ui.construction_organization_templates import tr as common_tr

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {}


def label(key, *values):
    if len(values) != 7:
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))


label("new", "Створити нову чернетку", "Создать новый черновик", "Create new draft",
      "Criar novo rascunho", "Utwórz nową wersję roboczą", "Neuen Entwurf erstellen",
      "Nieuw concept maken")
label("review", "Підтвердьте створення чернетки з цієї опублікованої версії.",
      "Подтвердите создание черновика из этой опубликованной версии.",
      "Confirm creating a draft from this published version.",
      "Confirme a criação de um rascunho a partir desta versão publicada.",
      "Potwierdź utworzenie wersji roboczej z tej opublikowanej wersji.",
      "Bestätigen Sie den Entwurf aus dieser veröffentlichten Version.",
      "Bevestig het maken van een concept van deze gepubliceerde versie.")
label("confirm", "Створити чернетку", "Создать черновик", "Create draft",
      "Criar rascunho", "Utwórz wersję roboczą", "Entwurf erstellen", "Concept maken")
label("note", "Опублікована версія залишиться незмінною.",
      "Опубликованная версия останется неизменной.",
      "The published version will remain unchanged.",
      "A versão publicada permanecerá inalterada.",
      "Opublikowana wersja pozostanie bez zmian.",
      "Die veröffentlichte Version bleibt unverändert.",
      "De gepubliceerde versie blijft ongewijzigd.")
label("created", "Створення чернетки підтверджено. Відкрийте її, щоб отримати поточний стан.",
      "Создание черновика подтверждено. Откройте его, чтобы получить текущее состояние.",
      "Draft creation is confirmed. Open it to read its current state.",
      "A criação foi confirmada. Abra o rascunho para consultar o estado atual.",
      "Potwierdzono utworzenie wersji roboczej. Otwórz ją, aby odczytać bieżący stan.",
      "Die Entwurfserstellung ist bestätigt. Öffnen Sie ihn für den aktuellen Stand.",
      "Het maken van het concept is bevestigd. Open het om de huidige status te lezen.")
label("open_created", "Відкрити створену чернетку", "Открыть созданный черновик",
      "Open created draft", "Abrir rascunho criado", "Otwórz utworzoną wersję roboczą",
      "Erstellten Entwurf öffnen", "Gemaakt concept openen")
label("draft", "Чернетка", "Черновик", "Draft", "Rascunho", "Wersja robocza", "Entwurf", "Concept")
label("number", "Номер версії", "Номер версии", "Version number", "Número da versão",
      "Numer wersji", "Versionsnummer", "Versienummer")
label("refresh_draft", "Оновити чернетку", "Обновить черновик", "Refresh draft",
      "Atualizar rascunho", "Odśwież wersję roboczą", "Entwurf aktualisieren", "Concept vernieuwen")
label("draft_note", "Ця версія ще не опублікована.",
      "Эта версия ещё не опубликована.", "This version has not been published.",
      "Esta versão ainda não foi publicada.", "Ta wersja nie została opublikowana.",
      "Diese Version wurde noch nicht veröffentlicht.", "Deze versie is nog niet gepubliceerd.")
label("pending", "Результат створення не підтверджено. Отримайте результат тієї самої операції.",
      "Результат создания не подтверждён. Получите результат той же операции.",
      "Creation is unconfirmed. Retrieve the result of the same operation.",
      "A criação não foi confirmada. Obtenha o resultado da mesma operação.",
      "Utworzenie jest niepotwierdzone. Pobierz wynik tej samej operacji.",
      "Die Erstellung ist unbestätigt. Rufen Sie das Ergebnis derselben Operation ab.",
      "Het maken is niet bevestigd. Haal het resultaat van dezelfde bewerking op.")
label("retry", "Отримати результат створення", "Получить результат создания",
      "Retrieve creation result", "Obter resultado da criação", "Pobierz wynik utworzenia",
      "Erstellungsergebnis abrufen", "Resultaat van het maken ophalen")
label("unavailable", "Чернетка недоступна. Поверніться до списку шаблонів.",
      "Черновик недоступен. Вернитесь к списку шаблонов.",
      "The draft is unavailable. Return to the template list.",
      "O rascunho está indisponível. Volte à lista de modelos.",
      "Wersja robocza jest niedostępna. Wróć do listy szablonów.",
      "Der Entwurf ist nicht verfügbar. Kehren Sie zur Vorlagenliste zurück.",
      "Het concept is niet beschikbaar. Ga terug naar de sjabloonlijst.")


def tr(key, language):
    if key not in LABELS:
        return common_tr(key, language)
    code = (language or "ru").lower().split("-", 1)[0]
    return LABELS[key].get(code, LABELS[key]["ru"])
