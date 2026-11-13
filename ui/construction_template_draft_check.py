"""Readiness is a versioned observation, never publication authority."""
from ui.construction_template_drafts import tr as common_tr

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {}


def label(key, *values):
    if len(values) != 7:
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))


label("check", "Перевірити готовність", "Проверить готовность", "Check readiness",
      "Verificar preparação", "Sprawdź gotowość", "Bereitschaft prüfen", "Gereedheid controleren")
label("checked", "Перевірку цієї версії пройдено.", "Проверка этой версии пройдена.",
      "This version passed validation.", "Esta versão passou na validação.",
      "Ta wersja przeszła walidację.", "Diese Version hat die Prüfung bestanden.",
      "Deze versie heeft de validatie doorstaan.")
label("bindings", "Прив’язок до видів робіт", "Привязок к видам работ",
      "Work-type bindings", "Ligações a tipos de trabalho", "Powiązania z rodzajami prac",
      "Verknüpfungen mit Arbeitsarten", "Koppelingen met werktypen")
label("note",
      "Це результат перевірки конкретної версії. Шаблон ще не опубліковано; під час публікації сервер перевірить його повторно.",
      "Это результат проверки конкретной версии. Шаблон ещё не опубликован; при публикации сервер проверит его повторно.",
      "This is a check of a specific version. The template is not published; the server will validate it again during publication.",
      "Esta verificação refere-se a uma versão específica. O modelo não está publicado; o servidor voltará a validá-lo na publicação.",
      "To wynik sprawdzenia konkretnej wersji. Szablon nie jest opublikowany; serwer sprawdzi go ponownie podczas publikacji.",
      "Dies ist die Prüfung einer bestimmten Version. Die Vorlage ist nicht veröffentlicht; bei der Veröffentlichung prüft der Server erneut.",
      "Dit is een controle van een specifieke versie. Het sjabloon is niet gepubliceerd; de server controleert het opnieuw bij publicatie.")
label("failed",
      "Готовність не підтверджено. Оновіть чернетку; її структура та прив’язки мають відповідати серверним правилам.",
      "Готовность не подтверждена. Обновите черновик; его структура и привязки должны соответствовать серверным правилам.",
      "Readiness is not confirmed. Refresh the draft; its structure and bindings must satisfy server rules.",
      "A preparação não foi confirmada. Atualize o rascunho; a estrutura e as ligações devem cumprir as regras do servidor.",
      "Gotowość nie została potwierdzona. Odśwież wersję roboczą; struktura i powiązania muszą spełniać reguły serwera.",
      "Die Bereitschaft wurde nicht bestätigt. Aktualisieren Sie den Entwurf; Struktur und Verknüpfungen müssen den Serverregeln entsprechen.",
      "De gereedheid is niet bevestigd. Vernieuw het concept; de structuur en koppelingen moeten aan de serverregels voldoen.")
label("edit_pending",
      "Спочатку отримайте результат незавершеного редагування.",
      "Сначала получите результат незавершённого редактирования.",
      "Retrieve the outstanding edit result first.",
      "Obtenha primeiro o resultado da edição pendente.",
      "Najpierw pobierz wynik oczekującej edycji.",
      "Rufen Sie zuerst das Ergebnis der ausstehenden Bearbeitung ab.",
      "Haal eerst het resultaat van de openstaande bewerking op.")


def tr(key, language):
    if key not in LABELS:
        return common_tr(key, language)
    code = (language or "ru").lower().split("-", 1)[0]
    return LABELS[key].get(code, LABELS[key]["ru"])
