"""Original header editing labels in seven interface languages."""
from ui.construction_template_drafts import tr as common_tr

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {}


def label(key, *values):
    if len(values) != 7:
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))


label("title", "Назва чернетки", "Название черновика", "Draft title",
      "Título do rascunho", "Nazwa wersji roboczej", "Entwurfstitel", "Concepttitel")
label("description", "Опис чернетки", "Описание черновика", "Draft description",
      "Descrição do rascunho", "Opis wersji roboczej", "Entwurfsbeschreibung", "Conceptbeschrijving")
label("original_language", "Мова оригіналу", "Язык оригинала", "Original language",
      "Idioma original", "Język oryginału", "Originalsprache", "Oorspronkelijke taal")
label("current", "Поточний оригінальний текст", "Текущий оригинальный текст",
      "Current original text", "Texto original atual", "Bieżący tekst oryginalny",
      "Aktueller Originaltext", "Huidige oorspronkelijke tekst")
label("enter", "Введіть оригінальний текст: один рядок, 1–120 символів.",
      "Введите оригинальный текст: одна строка, 1–120 символов.",
      "Enter the original text: one line, 1–120 characters.",
      "Introduza o texto original: uma linha, 1–120 caracteres.",
      "Wpisz tekst oryginalny: jeden wiersz, 1–120 znaków.",
      "Geben Sie den Originaltext ein: eine Zeile, 1–120 Zeichen.",
      "Voer de oorspronkelijke tekst in: één regel, 1–120 tekens.")
label("empty", "Не задано", "Не задано", "Not set", "Não definido",
      "Nie ustawiono", "Nicht festgelegt", "Niet ingesteld")
label("clear", "Очистити опис", "Очистить описание", "Clear description",
      "Limpar descrição", "Wyczyść opis", "Beschreibung löschen", "Beschrijving wissen")
label("review", "Перевірте зміну оригінального тексту перед збереженням.",
      "Проверьте изменение оригинального текста перед сохранением.",
      "Review the original-text change before saving.",
      "Reveja a alteração do texto original antes de guardar.",
      "Sprawdź zmianę tekstu oryginalnego przed zapisaniem.",
      "Prüfen Sie die Änderung des Originaltextes vor dem Speichern.",
      "Controleer de wijziging van de oorspronkelijke tekst voordat u opslaat.")
label("translation_note", "Переклади зміненого поля в цій чернетці потребуватимуть оновлення.",
      "Переводы изменённого поля в этом черновике потребуют обновления.",
      "Translations of the changed field in this draft will need updating.",
      "As traduções do campo alterado neste rascunho terão de ser atualizadas.",
      "Tłumaczenia zmienionego pola w tej wersji roboczej będą wymagały aktualizacji.",
      "Übersetzungen des geänderten Felds in diesem Entwurf müssen aktualisiert werden.",
      "Vertalingen van het gewijzigde veld in dit concept moeten worden bijgewerkt.")
label("saved", "Збереження підтверджено. Оновіть чернетку, щоб прочитати її поточний стан.",
      "Сохранение подтверждено. Обновите черновик, чтобы прочитать его текущее состояние.",
      "Saving is confirmed. Refresh the draft to read its current state.",
      "A gravação foi confirmada. Atualize o rascunho para consultar o estado atual.",
      "Potwierdzono zapis. Odśwież wersję roboczą, aby odczytać bieżący stan.",
      "Das Speichern ist bestätigt. Aktualisieren Sie den Entwurf für den aktuellen Stand.",
      "Het opslaan is bevestigd. Vernieuw het concept om de huidige status te lezen.")
label("pending", "Результат редагування не підтверджено. Отримайте результат тієї самої операції.",
      "Результат редактирования не подтверждён. Получите результат той же операции.",
      "The edit result is unconfirmed. Retrieve the result of the same operation.",
      "O resultado da edição não foi confirmado. Obtenha o resultado da mesma operação.",
      "Wynik edycji jest niepotwierdzony. Pobierz wynik tej samej operacji.",
      "Das Bearbeitungsergebnis ist unbestätigt. Rufen Sie das Ergebnis derselben Operation ab.",
      "Het bewerkingsresultaat is niet bevestigd. Haal het resultaat van dezelfde bewerking op.")
label("retry", "Отримати результат редагування", "Получить результат редактирования",
      "Retrieve edit result", "Obter resultado da edição", "Pobierz wynik edycji",
      "Bearbeitungsergebnis abrufen", "Bewerkingsresultaat ophalen")


label("label", "Назва розділу або питання", "Название раздела или вопроса",
      "Section or question label", "Nome da secção ou pergunta",
      "Nazwa sekcji lub pytania", "Abschnitts- oder Fragenbezeichnung",
      "Sectie- of vraagnaam")
label("edit_label", "Змінити оригінальну назву", "Изменить исходное название",
      "Edit original label", "Editar nome original", "Zmień oryginalną nazwę",
      "Originalbezeichnung ändern", "Oorspronkelijke naam wijzigen")


label("change_active", "Змінити активність", "Изменить активность",
      "Change active state", "Alterar estado ativo", "Zmień aktywność",
      "Aktivstatus ändern", "Actieve status wijzigen")
label("change_required", "Змінити обов’язковість", "Изменить обязательность",
      "Change required state", "Alterar obrigatoriedade", "Zmień wymagalność",
      "Pflichtstatus ändern", "Verplichte status wijzigen")
label("active_flag", "Активне", "Активно", "Active", "Ativo",
      "Aktywne", "Aktiv", "Actief")
label("required_flag", "Обов’язкове", "Обязательно", "Required", "Obrigatório",
      "Wymagane", "Erforderlich", "Verplicht")
label("review_flags", "Перевірте зміну перед збереженням.",
      "Проверьте изменение перед сохранением.", "Review the change before saving.",
      "Reveja a alteração antes de guardar.", "Sprawdź zmianę przed zapisaniem.",
      "Prüfen Sie die Änderung vor dem Speichern.",
      "Controleer de wijziging voordat u opslaat.")
label("flags_note",
      "Обов’язковість питання застосовується лише коли воно активне за умовою показу.",
      "Обязательность вопроса применяется только когда он активен по условию показа.",
      "A question is required only when it is active under its display condition.",
      "Uma pergunta é obrigatória apenas quando está ativa pela condição de apresentação.",
      "Pytanie jest wymagane tylko wtedy, gdy jest aktywne zgodnie z warunkiem wyświetlania.",
      "Eine Frage ist nur verpflichtend, wenn sie gemäß ihrer Anzeigebedingung aktiv ist.",
      "Een vraag is alleen verplicht wanneer deze volgens de weergavevoorwaarde actief is.")
label("invalid_change",
      "Зміна порушує правила шаблону. Стартове питання етапу має залишатися активним і обов’язковим.",
      "Изменение нарушает правила шаблона. Начальный вопрос этапа должен оставаться активным и обязательным.",
      "The change violates template rules. A phase’s opening question must remain active and required.",
      "A alteração viola as regras do modelo. A pergunta inicial da fase deve permanecer ativa e obrigatória.",
      "Zmiana narusza reguły szablonu. Pierwsze pytanie etapu musi pozostać aktywne i wymagane.",
      "Die Änderung verletzt die Vorlagenregeln. Die erste Frage einer Phase muss aktiv und verpflichtend bleiben.",
      "De wijziging schendt de sjabloonregels. De eerste vraag van een fase moet actief en verplicht blijven.")


label("change_order", "Змінити порядок", "Изменить порядок",
      "Change order", "Alterar ordem", "Zmień kolejność",
      "Reihenfolge ändern", "Volgorde wijzigen")
label("sort_order", "Порядкове число", "Порядковое число",
      "Sort number", "Número de ordenação", "Numer porządkowy",
      "Sortiernummer", "Sorteernummer")
label("order_current", "Поточне порядкове число", "Текущее порядковое число",
      "Current sort number", "Número de ordenação atual", "Bieżący numer porządkowy",
      "Aktuelle Sortiernummer", "Huidig sorteernummer")
label("order_enter",
      "Введіть ціле число від 0 до 2147483647. Менші числа йдуть раніше; число має бути унікальним у цьому списку.",
      "Введите целое число от 0 до 2147483647. Меньшие числа идут раньше; число должно быть уникальным в этом списке.",
      "Enter an integer from 0 to 2147483647. Smaller numbers come first; the number must be unique in this list.",
      "Introduza um inteiro de 0 a 2147483647. Os menores vêm primeiro; o número deve ser único nesta lista.",
      "Wpisz liczbę całkowitą od 0 do 2147483647. Mniejsze liczby są wcześniej; numer musi być unikalny na tej liście.",
      "Geben Sie eine ganze Zahl von 0 bis 2147483647 ein. Kleinere Zahlen stehen zuerst; die Nummer muss in dieser Liste eindeutig sein.",
      "Voer een geheel getal van 0 tot 2147483647 in. Lagere nummers komen eerst; het nummer moet uniek zijn in deze lijst.")
label("order_neighbours", "Сусідні елементи оригіналу", "Соседние элементы оригинала",
      "Nearby original items", "Elementos originais próximos", "Sąsiednie elementy oryginału",
      "Benachbarte Originalelemente", "Nabijgelegen oorspronkelijke elementen")
label("order_review", "Перевірте зміну порядку перед збереженням.",
      "Проверьте изменение порядка перед сохранением.",
      "Review the order change before saving.",
      "Reveja a alteração da ordem antes de guardar.",
      "Sprawdź zmianę kolejności przed zapisaniem.",
      "Prüfen Sie die Änderung der Reihenfolge vor dem Speichern.",
      "Controleer de gewijzigde volgorde voordat u opslaat.")
label("order_note",
      "Послідовність технологічних етапів, стартові питання та залежності умов мають залишатися коректними.",
      "Последовательность технологических этапов, начальные вопросы и зависимости условий должны оставаться корректными.",
      "Technological phase order, opening questions and condition dependencies must remain valid.",
      "A ordem das fases tecnológicas, as perguntas iniciais e as dependências das condições devem permanecer válidas.",
      "Kolejność etapów technologicznych, pytania początkowe i zależności warunków muszą pozostać poprawne.",
      "Die Reihenfolge der technologischen Phasen, Einstiegsfragen und Bedingungsabhängigkeiten muss gültig bleiben.",
      "De volgorde van technologische fasen, openingsvragen en voorwaardelijke afhankelijkheden moet geldig blijven.")
label("invalid_order",
      "Такий порядок недопустимий: перевірте повторення чисел, технологічні етапи, стартові питання та залежності умов.",
      "Такой порядок недопустим: проверьте повторяющиеся числа, технологические этапы, начальные вопросы и зависимости условий.",
      "This order is invalid: check duplicate numbers, technological phases, opening questions and condition dependencies.",
      "Esta ordem é inválida: verifique números repetidos, fases tecnológicas, perguntas iniciais e dependências das condições.",
      "Ta kolejność jest nieprawidłowa: sprawdź powtarzające się numery, etapy technologiczne, pytania początkowe i zależności warunków.",
      "Diese Reihenfolge ist ungültig: Prüfen Sie doppelte Nummern, technologische Phasen, Einstiegsfragen und Bedingungsabhängigkeiten.",
      "Deze volgorde is ongeldig: controleer dubbele nummers, technologische fasen, openingsvragen en voorwaardelijke afhankelijkheden.")


def tr(key, language):
    if key not in LABELS:
        return common_tr(key, language)
    code = (language or "ru").lower().split("-", 1)[0]
    return LABELS[key].get(code, LABELS[key]["ru"])
