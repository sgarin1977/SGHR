"""Explicit publication controls in seven interface languages."""
from ui.construction_template_draft_check import tr as common_tr

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {}

def label(key, *values):
    if len(values) != 7:
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))

label("begin", "Підготувати публікацію", "Подготовить публикацию",
      "Prepare publication", "Preparar publicação", "Przygotuj publikację",
      "Veröffentlichung vorbereiten", "Publicatie voorbereiden")
label("confirm", "Опублікувати цю версію", "Опубликовать эту версию",
      "Publish this version", "Publicar esta versão", "Opublikuj tę wersję",
      "Diese Version veröffentlichen", "Deze versie publiceren")
label("review", "Підтвердіть публікацію цієї версії шаблону.",
      "Подтвердите публикацию этой версии шаблона.",
      "Confirm publication of this template version.",
      "Confirme a publicação desta versão do modelo.",
      "Potwierdź publikację tej wersji szablonu.",
      "Bestätigen Sie die Veröffentlichung dieser Vorlagenversion.",
      "Bevestig de publicatie van deze sjabloonversie.")
label("original", "Мова оригіналу", "Язык оригинала", "Original language",
      "Idioma original", "Język oryginału", "Originalsprache", "Oorspronkelijke taal")
label("immutable",
      "Опублікована версія незмінна. Подальші зміни виконуються в новій чернетці. Сервер повторно перевірить шаблон під час публікації.",
      "Опубликованная версия неизменна. Дальнейшие изменения выполняются в новом черновике. Сервер повторно проверит шаблон при публикации.",
      "A published version is immutable. Further changes use a new draft. The server validates the template again during publication.",
      "Uma versão publicada é imutável. As alterações seguintes usam um novo rascunho. O servidor valida novamente o modelo na publicação.",
      "Opublikowana wersja jest niezmienna. Kolejne zmiany wymagają nowej wersji roboczej. Serwer ponownie sprawdza szablon podczas publikacji.",
      "Eine veröffentlichte Version ist unveränderlich. Weitere Änderungen erfolgen in einem neuen Entwurf. Der Server prüft bei der Veröffentlichung erneut.",
      "Een gepubliceerde versie is onveranderlijk. Verdere wijzigingen gebruiken een nieuw concept. De server controleert opnieuw bij publicatie.")
label("retry", "Отримати результат публікації", "Получить результат публикации",
      "Retrieve publication result", "Obter resultado da publicação", "Pobierz wynik publikacji",
      "Veröffentlichungsergebnis abrufen", "Publicatieresultaat ophalen")
label("pending",
      "Результат публікації не підтверджено. Отримайте результат тієї самої операції.",
      "Результат публикации не подтверждён. Получите результат той же операции.",
      "Publication is unconfirmed. Retrieve the result of the same operation.",
      "A publicação não foi confirmada. Obtenha o resultado da mesma operação.",
      "Publikacja nie jest potwierdzona. Pobierz wynik tej samej operacji.",
      "Die Veröffentlichung ist unbestätigt. Rufen Sie das Ergebnis derselben Operation ab.",
      "De publicatie is niet bevestigd. Haal het resultaat van dezelfde bewerking op.")
label("saved",
      "Публікацію підтверджено. Це початковий результат операції. Відкрийте версію, щоб прочитати її поточний стан.",
      "Публикация подтверждена. Это первоначальный результат операции. Откройте версию, чтобы прочитать её текущее состояние.",
      "Publication is confirmed. This is the original operation result. Open the version to read its current state.",
      "A publicação foi confirmada. Este é o resultado original da operação. Abra a versão para consultar o estado atual.",
      "Publikacja została potwierdzona. To pierwotny wynik operacji. Otwórz wersję, aby odczytać jej aktualny stan.",
      "Die Veröffentlichung ist bestätigt. Dies ist das ursprüngliche Operationsergebnis. Öffnen Sie die Version für ihren aktuellen Stand.",
      "De publicatie is bevestigd. Dit is het oorspronkelijke bewerkingsresultaat. Open de versie voor de huidige status.")
label("open", "Відкрити опубліковану версію", "Открыть опубликованную версию",
      "Open published version", "Abrir versão publicada", "Otwórz opublikowaną wersję",
      "Veröffentlichte Version öffnen", "Gepubliceerde versie openen")
label("blocked",
      "Публікацію не виконано. Оновіть чернетку та повторно перевірте її готовність.",
      "Публикация не выполнена. Обновите черновик и повторно проверьте готовность.",
      "Publication was not completed. Refresh the draft and check its readiness again.",
      "A publicação não foi concluída. Atualize o rascunho e volte a verificar a preparação.",
      "Publikacja nie została wykonana. Odśwież wersję roboczą i ponownie sprawdź gotowość.",
      "Die Veröffentlichung wurde nicht durchgeführt. Aktualisieren Sie den Entwurf und prüfen Sie erneut.",
      "De publicatie is niet uitgevoerd. Vernieuw het concept en controleer de gereedheid opnieuw.")
label("back", "До чернетки", "К черновику", "Back to draft", "Voltar ao rascunho",
      "Do wersji roboczej", "Zum Entwurf", "Terug naar concept")

def tr(key, language):
    if key not in LABELS:
        return common_tr(key, language)
    code = (language or "ru").lower().split("-", 1)[0]
    return LABELS[key].get(code, LABELS[key]["ru"])
