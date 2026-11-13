"""Fixed onboarding steps from rev.12; progress remains server-owned."""
from ui.construction_stage2_settings import tr as common_tr

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {}


def label(key, *values):
    if len(values) != len(LANGUAGES):
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))


label("title", "Початкове налаштування", "Первичная настройка", "Initial setup",
      "Configuração inicial", "Konfiguracja początkowa",
      "Ersteinrichtung", "Eerste inrichting")
label("COMPANY", "Компанія й оформлення", "Компания и оформление",
      "Company and branding", "Empresa e identidade visual",
      "Firma i wygląd", "Unternehmen und Gestaltung", "Bedrijf en vormgeving")
label("LOCALIZATION", "Мова, країна, валюта, одиниці",
      "Язык, страна, валюта, единицы", "Language, country, currency, units",
      "Idioma, país, moeda, unidades", "Język, kraj, waluta, jednostki",
      "Sprache, Land, Währung, Einheiten", "Taal, land, valuta, eenheden")
label("TAXES", "Податкові налаштування", "Налоговые настройки", "Tax settings",
      "Definições fiscais", "Ustawienia podatkowe", "Steuereinstellungen",
      "Belastinginstellingen")
label("PRICING_PROFILES", "Економний / Стандартний / Преміальний",
      "Экономный / Стандартный / Премиальный", "Economy / Standard / Premium",
      "Económico / Standard / Premium", "Ekonomiczny / Standardowy / Premium",
      "Economy / Standard / Premium", "Economy / Standaard / Premium")
label("WORK_PRICES", "Прайс робіт", "Прайс работ", "Work prices",
      "Preços de trabalhos", "Cennik prac", "Arbeitspreise", "Werkprijzen")
label("MATERIALS_SUPPLIERS", "Матеріали й постачальники", "Материалы и поставщики",
      "Materials and suppliers", "Materiais e fornecedores",
      "Materiały i dostawcy", "Materialien und Lieferanten", "Materialen en leveranciers")
label("MEASUREMENT_TEMPLATES", "Початкові шаблони замірів", "Начальные шаблоны замеров",
      "Starter measurement templates", "Modelos iniciais de medição",
      "Początkowe szablony pomiarów", "Messvorlagen für den Einstieg",
      "Startsjablonen voor metingen")
label("PENDING", "Не завершено", "Не завершено", "Pending", "Pendente",
      "Oczekuje", "Ausstehend", "Openstaand")
label("COMPLETED", "Завершено", "Завершено", "Completed", "Concluído",
      "Ukończono", "Abgeschlossen", "Voltooid")
label("SKIPPED", "Пропущено", "Пропущено", "Skipped", "Ignorado",
      "Pominięto", "Übersprungen", "Overgeslagen")
label("start", "Почати налаштування", "Начать настройку", "Start setup",
      "Iniciar configuração", "Rozpocznij konfigurację", "Einrichtung starten",
      "Inrichting starten")
label("open", "Відкрити крок", "Открыть шаг", "Open step",
      "Abrir passo", "Otwórz krok", "Schritt öffnen", "Stap openen")
label("complete", "Перевірити й завершити крок", "Проверить и завершить шаг",
      "Verify and complete step", "Verificar e concluir passo",
      "Sprawdź i ukończ krok", "Schritt prüfen und abschließen",
      "Stap controleren en voltooien")
label("skip", "Пропустити цей крок", "Пропустить этот шаг", "Skip this step",
      "Ignorar este passo", "Pomiń ten krok", "Diesen Schritt überspringen",
      "Deze stap overslaan")
label("confirm", "Підтвердити", "Подтвердить", "Confirm", "Confirmar",
      "Potwierdź", "Bestätigen", "Bevestigen")
label("current", "Поточний крок", "Текущий шаг", "Current step",
      "Passo atual", "Bieżący krok", "Aktueller Schritt", "Huidige stap")
label("not_started", "Початкове налаштування ще не розпочато.",
      "Первичная настройка ещё не начата.", "Initial setup has not started.",
      "A configuração inicial ainda não começou.",
      "Konfiguracja początkowa nie została rozpoczęta.",
      "Die Ersteinrichtung wurde noch nicht gestartet.",
      "De eerste inrichting is nog niet gestart.")
label("note",
      "Завершення кроку перевіряється за збереженими даними. Прайс робіт і матеріали можна пропустити.",
      "Завершение шага проверяется по сохранённым данным. Прайс работ и материалы можно пропустить.",
      "Step completion is verified against saved data. Work prices and materials can be skipped.",
      "A conclusão é verificada com os dados guardados. Os preços de trabalhos e materiais podem ser ignorados.",
      "Ukończenie kroku jest sprawdzane na podstawie zapisanych danych. Cennik prac i materiały można pominąć.",
      "Der Schrittabschluss wird anhand gespeicherter Daten geprüft. Arbeitspreise und Materialien können übersprungen werden.",
      "Voltooiing wordt gecontroleerd met opgeslagen gegevens. Werkprijzen en materialen kunnen worden overgeslagen.")
label("not_ready", "Спочатку збережіть необхідні дані цього кроку й оновіть майстер.",
      "Сначала сохраните необходимые данные этого шага и обновите мастер.",
      "Save the required step data first, then refresh the guide.",
      "Guarde primeiro os dados necessários e atualize o assistente.",
      "Najpierw zapisz wymagane dane i odśwież kreator.",
      "Speichern Sie zuerst die erforderlichen Daten und aktualisieren Sie den Assistenten.",
      "Sla eerst de vereiste gegevens op en vernieuw de wizard.")
label("step_unavailable", "Цей крок поки не можна підтвердити. Його стан не змінено.",
      "Этот шаг пока нельзя подтвердить. Его состояние не изменено.",
      "This step cannot be confirmed yet. Its state has not changed.",
      "Este passo ainda não pode ser confirmado. O estado não foi alterado.",
      "Nie można jeszcze potwierdzić tego kroku. Jego stan nie został zmieniony.",
      "Dieser Schritt kann noch nicht bestätigt werden. Sein Status wurde nicht geändert.",
      "Deze stap kan nog niet worden bevestigd. De status is niet gewijzigd.")
label("uncertain", "Результат не підтверджено. Оновіть майстер перед новою дією.",
      "Результат не подтверждён. Обновите мастер перед новым действием.",
      "The result is unconfirmed. Refresh the guide before another action.",
      "O resultado não foi confirmado. Atualize o assistente antes de outra ação.",
      "Wynik jest niepotwierdzony. Odśwież kreator przed kolejną operacją.",
      "Das Ergebnis ist unbestätigt. Aktualisieren Sie vor einer weiteren Aktion den Assistenten.",
      "Het resultaat is niet bevestigd. Vernieuw de wizard voordat u verdergaat.")


def tr(key, language):
    if key not in LABELS:
        return common_tr(key, language)
    code = (language or "ru").lower().split("-", 1)[0]
    return LABELS[key].get(code, LABELS[key]["ru"])
