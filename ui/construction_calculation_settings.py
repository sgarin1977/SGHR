"""Calculation configuration presentation in the supported bot languages."""
from ui.construction_tax_profiles import tr as common_tr

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {}


def label(key, *values):
    if len(values) != 7:
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))


label("title", "Розрахункові налаштування", "Расчётные настройки",
      "Calculation settings", "Definições de cálculo", "Ustawienia obliczeń",
      "Berechnungseinstellungen", "Berekeningsinstellingen")
label("empty", "Розрахункові налаштування ще не задані.",
      "Расчётные настройки ещё не заданы.", "Calculation settings are not configured yet.",
      "As definições de cálculo ainda não foram configuradas.",
      "Ustawienia obliczeń nie zostały jeszcze określone.",
      "Die Berechnungseinstellungen sind noch nicht festgelegt.",
      "De berekeningsinstellingen zijn nog niet ingesteld.")
label("create", "Налаштувати", "Настроить", "Configure", "Configurar",
      "Skonfiguruj", "Einrichten", "Instellen")
label("money_input_precision", "Точність грошового вводу", "Точность денежного ввода",
      "Money input precision", "Precisão da entrada monetária",
      "Precyzja wprowadzania kwot", "Genauigkeit der Geldeingabe", "Precisie van geldinvoer")
label("quantity_precision", "Точність кількості", "Точность количества",
      "Quantity precision", "Precisão da quantidade", "Precyzja ilości",
      "Mengengenauigkeit", "Hoeveelheidsprecisie")
label("supplier_stale_price_days", "Актуальність ціни постачальника, днів",
      "Актуальность цены поставщика, дней", "Supplier price lifetime, days",
      "Validade do preço do fornecedor, dias", "Ważność ceny dostawcy, dni",
      "Gültigkeit des Lieferantenpreises, Tage", "Geldigheid leveranciersprijs, dagen")
label("minimum_job", "Мінімальна сума замовлення", "Минимальная сумма заказа",
      "Minimum job amount", "Valor mínimo do trabalho", "Minimalna kwota zlecenia",
      "Mindestauftragswert", "Minimaal opdrachtbedrag")
label("minimum_job_amount", "Мінімальна сума", "Минимальная сумма", "Minimum amount",
      "Valor mínimo", "Minimalna kwota", "Mindestbetrag", "Minimumbedrag")
label("minimum_job_currency", "Валюта мінімальної суми", "Валюта минимальной суммы",
      "Minimum amount currency", "Moeda do valor mínimo", "Waluta minimalnej kwoty",
      "Währung des Mindestbetrags", "Valuta van het minimumbedrag")
label("default_technological_waste_percent", "Стандарт технологічних втрат, %",
      "Стандарт технологических потерь, %", "Default technological waste, %",
      "Desperdício tecnológico predefinido, %", "Domyślne straty technologiczne, %",
      "Standard-Technologieverlust, %", "Standaard technologisch verlies, %")
label("default_local_repair_percent", "Стандарт локального ремонту, %",
      "Стандарт локального ремонта, %", "Default local repair, %",
      "Reparação local predefinida, %", "Domyślna naprawa miejscowa, %",
      "Standard für lokale Reparatur, %", "Standaard lokale reparatie, %")
label("unset", "Не задано", "Не задано", "Not set", "Não definido",
      "Nie ustawiono", "Nicht festgelegt", "Niet ingesteld")
label("clear", "Очистити значення", "Очистить значение", "Clear value",
      "Limpar valor", "Wyczyść wartość", "Wert löschen", "Waarde wissen")
label("currency_prompt", "Введіть код валюти, наприклад EUR або UAH.",
      "Введите код валюты, например EUR или UAH.",
      "Enter a currency code, for example EUR or UAH.",
      "Introduza um código de moeda, por exemplo EUR ou UAH.",
      "Wpisz kod waluty, na przykład EUR lub UAH.",
      "Geben Sie einen Währungscode ein, zum Beispiel EUR oder UAH.",
      "Voer een valutacode in, bijvoorbeeld EUR of UAH.")
label("currency_invalid", "Код валюти не знайдено в каталозі.",
      "Код валюты не найден в каталоге.", "Currency code not found in the catalog.",
      "Código de moeda não encontrado no catálogo.", "Nie znaleziono kodu waluty w katalogu.",
      "Währungscode nicht im Katalog gefunden.", "Valutacode niet gevonden in de catalogus.")
label("invalid", "Значення не відповідає вказаному діапазону.",
      "Значение не соответствует указанному диапазону.",
      "The value is outside the stated range.",
      "O valor está fora do intervalo indicado.", "Wartość jest poza podanym zakresem.",
      "Der Wert liegt außerhalb des angegebenen Bereichs.", "De waarde valt buiten het opgegeven bereik.")
label("review", "Перевірте розрахункові налаштування перед збереженням.",
      "Проверьте расчётные настройки перед сохранением.",
      "Review the calculation settings before saving.",
      "Reveja as definições de cálculo antes de guardar.",
      "Sprawdź ustawienia obliczeń przed zapisaniem.",
      "Prüfen Sie die Berechnungseinstellungen vor dem Speichern.",
      "Controleer de berekeningsinstellingen voordat u opslaat.")
label("saved", "Розрахункові налаштування збережено.",
      "Расчётные настройки сохранены.", "Calculation settings saved.",
      "Definições de cálculo guardadas.", "Zapisano ustawienia obliczeń.",
      "Berechnungseinstellungen gespeichert.", "Berekeningsinstellingen opgeslagen.")
label("money_note",
      "Точність вводу — 0–6 знаків. Фінальне округлення документа визначає валюта.",
      "Точность ввода — 0–6 знаков. Финальное округление документа определяет валюта.",
      "Input precision is 0–6 digits. Final document rounding follows its currency.",
      "A precisão de entrada é de 0–6 casas. O arredondamento final segue a moeda.",
      "Precyzja wejściowa: 0–6 miejsc. Końcowe zaokrąglenie wynika z waluty.",
      "Eingabegenauigkeit: 0–6 Stellen. Die endgültige Rundung richtet sich nach der Währung.",
      "Invoerprecisie: 0–6 cijfers. De uiteindelijke afronding volgt de valuta.")
label("quantity_note",
      "0–6 знаків для канонічної розрахованої кількості. Погоджене стандартне значення — 4.",
      "0–6 знаков для канонического рассчитанного количества. Согласованное стандартное значение — 4.",
      "0–6 digits for canonical calculated quantity. The agreed default is 4.",
      "0–6 casas para a quantidade calculada canónica. O padrão acordado é 4.",
      "0–6 miejsc dla obliczonej ilości kanonicznej. Uzgodniona wartość domyślna to 4.",
      "0–6 Stellen für die kanonische berechnete Menge. Der vereinbarte Standard ist 4.",
      "0–6 cijfers voor de canonieke berekende hoeveelheid. De afgesproken standaard is 4.")
label("minimum_note",
      "Лише попередження в тій самій валюті. Без автоматичної доплати або конвертації.",
      "Только предупреждение в той же валюте. Без автоматической доплаты или конвертации.",
      "A warning in the same currency only. No automatic surcharge or conversion.",
      "Apenas um aviso na mesma moeda. Sem acréscimo ou conversão automática.",
      "Tylko ostrzeżenie w tej samej walucie. Bez automatycznej dopłaty lub konwersji.",
      "Nur ein Hinweis in derselben Währung. Kein automatischer Aufschlag oder Umrechnung.",
      "Alleen een waarschuwing in dezelfde valuta. Geen automatische toeslag of omrekening.")
label("days_note",
      "Введіть ціле число 0–3650. 0 означає застарівання одразу після часу отримання ціни.",
      "Введите целое число 0–3650. 0 означает устаревание сразу после времени получения цены.",
      "Enter an integer from 0 to 3650. Zero means stale immediately after the price was fetched.",
      "Introduza um inteiro de 0 a 3650. Zero significa desatualizado logo após a obtenção do preço.",
      "Wpisz liczbę całkowitą 0–3650. Zero oznacza utratę aktualności zaraz po pobraniu ceny.",
      "Geben Sie eine ganze Zahl von 0 bis 3650 ein. Null bedeutet direkt nach dem Abruf veraltet.",
      "Voer een geheel getal van 0 tot 3650 in. Nul betekent verouderd direct na het ophalen.")
label("percent_note",
      "Введіть 0–100%. Застосовується лише за явного вибору стандарту компанії.",
      "Введите 0–100%. Применяется только при явном выборе стандарта компании.",
      "Enter 0–100%. Applied only when the company default is explicitly selected.",
      "Introduza 0–100%. Aplica-se apenas ao selecionar explicitamente o padrão da empresa.",
      "Wpisz 0–100%. Stosowane tylko po jawnym wybraniu standardu firmy.",
      "Geben Sie 0–100% ein. Gilt nur bei ausdrücklicher Auswahl des Unternehmensstandards.",
      "Voer 0–100% in. Alleen toegepast bij expliciete keuze voor de bedrijfsstandaard.")


def tr(key, language):
    if key not in LABELS:
        return common_tr(key, language)
    code = (language or "ru").lower().split("-", 1)[0]
    return LABELS[key].get(code, LABELS[key]["ru"])


def fields_text(fields, language):
    return "\n".join(
        tr(key, language) + ": " + (
            tr("unset", language) if value is None else str(value)
        )
        for key, value in fields.items()
    )


def settings_text(item, language):
    if item is None:
        return tr("empty", language)
    fields = {
        "money_input_precision": item["money_input_precision"],
        "quantity_precision": item["quantity_precision"],
        "supplier_stale_price_days": item["supplier_stale_price_days"],
        "minimum_job": (
            None if item["minimum_job_amount"] is None
            else item["minimum_job_amount"] + " " + item["minimum_job_currency"]
        ),
        "default_technological_waste_percent": item["default_technological_waste_percent"],
        "default_local_repair_percent": item["default_local_repair_percent"],
    }
    return fields_text(fields, language)
