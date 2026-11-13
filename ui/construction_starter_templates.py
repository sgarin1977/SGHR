"""Starter-template labels; template content is supplied by the API."""
from ui.construction_onboarding import tr as common_tr

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {}


def label(key, *values):
    if len(values) != 7:
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))


label("title", "Початкові шаблони", "Начальные шаблоны", "Starter templates",
      "Modelos iniciais", "Szablony początkowe", "Startvorlagen", "Startsjablonen")
label("empty", "Доступних початкових шаблонів поки немає.",
      "Доступных начальных шаблонов пока нет.", "No starter templates are available yet.",
      "Ainda não existem modelos iniciais disponíveis.", "Brak dostępnych szablonów początkowych.",
      "Es sind noch keine Startvorlagen verfügbar.", "Er zijn nog geen startsjablonen beschikbaar.")
label("version", "Версія", "Версия", "Version", "Versão", "Wersja", "Version", "Versie")
label("sections", "Розділів", "Разделов", "Sections", "Secções", "Sekcje", "Abschnitte", "Secties")
label("questions", "Питань", "Вопросов", "Questions", "Perguntas", "Pytania", "Fragen", "Vragen")
label("copy", "Скопіювати в організацію", "Скопировать в организацию",
      "Copy to organization", "Copiar para a organização", "Skopiuj do organizacji",
      "In die Organisation kopieren", "Naar organisatie kopiëren")
label("list", "До каталогу шаблонів", "К каталогу шаблонов", "Back to template catalog",
      "Voltar ao catálogo", "Wróć do katalogu", "Zum Vorlagenkatalog", "Terug naar de catalogus")
label("guide", "До майстра налаштування", "К мастеру настройки", "Back to setup guide",
      "Voltar ao assistente", "Wróć do kreatora", "Zum Einrichtungsassistenten",
      "Terug naar de inrichtingswizard")
label("note",
      "Буде створено окрему копію вибраної версії: структуру питань без комерційних цін.",
      "Будет создана отдельная копия выбранной версии: структура вопросов без коммерческих цен.",
      "A separate copy of this version will be created: question structure without commercial prices.",
      "Será criada uma cópia separada desta versão: estrutura de perguntas sem preços comerciais.",
      "Powstanie osobna kopia tej wersji: struktura pytań bez cen handlowych.",
      "Eine separate Kopie dieser Version wird erstellt: Fragenstruktur ohne kommerzielle Preise.",
      "Er wordt een afzonderlijke kopie van deze versie gemaakt: vraagstructuur zonder commerciële prijzen.")
label("copied",
      "Шаблон скопійовано в організацію. Завершення кроку підтвердьте окремо в майстрі.",
      "Шаблон скопирован в организацию. Завершение шага подтвердите отдельно в мастере.",
      "Template copied to the organization. Confirm step completion separately in the guide.",
      "Modelo copiado para a organização. Confirme a conclusão do passo separadamente no assistente.",
      "Szablon skopiowano do organizacji. Osobno potwierdź ukończenie kroku w kreatorze.",
      "Vorlage in die Organisation kopiert. Bestätigen Sie den Schrittabschluss separat im Assistenten.",
      "Sjabloon naar de organisatie gekopieerd. Bevestig de voltooiing van de stap afzonderlijk in de wizard.")
label("pending",
      "Результат копіювання не підтверджено. Отримайте результат тієї самої операції перед новим копіюванням.",
      "Результат копирования не подтверждён. Получите результат той же операции перед новым копированием.",
      "The copy result is unconfirmed. Retrieve the same operation's result before starting another copy.",
      "O resultado da cópia não foi confirmado. Obtenha o resultado da mesma operação antes de outra cópia.",
      "Wynik kopiowania jest niepotwierdzony. Pobierz wynik tej samej operacji przed kolejną kopią.",
      "Das Kopierergebnis ist unbestätigt. Rufen Sie vor einer weiteren Kopie das Ergebnis derselben Operation ab.",
      "Het kopieerresultaat is niet bevestigd. Haal het resultaat van dezelfde bewerking op voordat u opnieuw kopieert.")
label("retry", "Отримати результат копіювання", "Получить результат копирования",
      "Retrieve copy result", "Obter resultado da cópia", "Pobierz wynik kopiowania",
      "Kopierergebnis abrufen", "Kopieerresultaat ophalen")
label("exists", "Копіювання конфліктує з наявними шаблонами організації. Оновіть каталог або поверніться до майстра.",
      "Копирование конфликтует с существующими шаблонами организации. Обновите каталог или вернитесь в мастер.",
      "Copying conflicts with existing organization templates. Refresh the catalog or return to the guide.",
      "A cópia entra em conflito com modelos existentes. Atualize o catálogo ou volte ao assistente.",
      "Kopiowanie koliduje z istniejącymi szablonami. Odśwież katalog lub wróć do kreatora.",
      "Die Kopie steht im Konflikt mit vorhandenen Vorlagen. Aktualisieren Sie den Katalog oder kehren Sie zum Assistenten zurück.",
      "De kopie conflicteert met bestaande sjablonen. Vernieuw de catalogus of ga terug naar de wizard.")
label("binding", "Для цього шаблону ще недоступна перевірка пов’язаних видів робіт.",
      "Для этого шаблона пока недоступна проверка связанных видов работ.",
      "Validation of linked work types is not yet available for this template.",
      "A validação dos tipos de trabalho associados ainda não está disponível.",
      "Sprawdzanie powiązanych rodzajów prac nie jest jeszcze dostępne.",
      "Die Prüfung verknüpfter Arbeitsarten ist für diese Vorlage noch nicht verfügbar.",
      "Validatie van gekoppelde werksoorten is voor dit sjabloon nog niet beschikbaar.")


def tr(key, language):
    if key not in LABELS:
        return common_tr(key, language)
    code = (language or "ru").lower().split("-", 1)[0]
    return LABELS[key].get(code, LABELS[key]["ru"])
