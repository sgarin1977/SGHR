"""Explicit tax-profile selection labels in seven interface languages."""
from ui.construction_onboarding import tr as common_tr

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")
LABELS = {}


def label(key, *values):
    if len(values) != len(LANGUAGES):
        raise ValueError("Translation count differs.")
    LABELS[key] = dict(zip(LANGUAGES, values))


label("choose", "Обрати податковий профіль", "Выбрать налоговый профиль",
      "Choose tax profile", "Escolher perfil fiscal", "Wybierz profil podatkowy",
      "Steuerprofil auswählen", "Belastingprofiel kiezen")
label("title", "Податковий профіль для початкового налаштування",
      "Налоговый профиль для первичной настройки",
      "Tax profile for initial setup", "Perfil fiscal da configuração inicial",
      "Profil podatkowy konfiguracji początkowej", "Steuerprofil für die Ersteinrichtung",
      "Belastingprofiel voor de eerste inrichting")
label("selected", "Зараз обрано", "Сейчас выбран", "Currently selected",
      "Selecionado atualmente", "Obecnie wybrany", "Aktuell ausgewählt", "Momenteel gekozen")
label("unselected", "Профіль ще не обрано.", "Профиль ещё не выбран.",
      "No profile has been selected.", "Ainda não foi selecionado um perfil.",
      "Nie wybrano jeszcze profilu.", "Es wurde noch kein Profil ausgewählt.",
      "Er is nog geen profiel gekozen.")
label("empty", "Податкових профілів немає. Створіть профіль у налаштуваннях податків.",
      "Налоговых профилей нет. Создайте профиль в налоговых настройках.",
      "No tax profiles exist. Create one in tax settings.",
      "Não existem perfis fiscais. Crie um nas definições fiscais.",
      "Brak profili podatkowych. Utwórz profil w ustawieniach podatkowych.",
      "Es gibt keine Steuerprofile. Erstellen Sie eines in den Steuereinstellungen.",
      "Er zijn geen belastingprofielen. Maak er een in de belastinginstellingen.")
label("refresh_list", "На початок списку", "В начало списка", "Back to first page",
      "Voltar à primeira página", "Do pierwszej strony", "Zur ersten Seite",
      "Terug naar de eerste pagina")
label("review", "Підтвердьте вибір цього профілю для початкового налаштування.",
      "Подтвердите выбор этого профиля для первичной настройки.",
      "Confirm this profile for initial setup.",
      "Confirme este perfil para a configuração inicial.",
      "Potwierdź ten profil dla konfiguracji początkowej.",
      "Bestätigen Sie dieses Profil für die Ersteinrichtung.",
      "Bevestig dit profiel voor de eerste inrichting.")
label("saved",
      "Податковий профіль обрано. Поверніться до майстра й окремо підтвердьте завершення кроку.",
      "Налоговый профиль выбран. Вернитесь в мастер и отдельно подтвердите завершение шага.",
      "Tax profile selected. Return to the guide and separately confirm step completion.",
      "Perfil fiscal selecionado. Volte ao assistente e confirme separadamente a conclusão do passo.",
      "Wybrano profil podatkowy. Wróć do kreatora i osobno potwierdź ukończenie kroku.",
      "Steuerprofil ausgewählt. Kehren Sie zum Assistenten zurück und bestätigen Sie den Schrittabschluss separat.",
      "Belastingprofiel gekozen. Ga terug naar de wizard en bevestig de voltooiing van de stap afzonderlijk.")
label("back", "До майстра налаштування", "К мастеру настройки",
      "Back to setup guide", "Voltar ao assistente", "Wróć do kreatora",
      "Zum Einrichtungsassistenten", "Terug naar de inrichtingswizard")
label("note",
      "Вибір профілю не створює податкових ставок. Для завершення цього кроку ставки не обов’язкові.",
      "Выбор профиля не создаёт налоговые ставки. Для завершения этого шага ставки не обязательны.",
      "Selecting a profile does not create tax rates. Rates are not required to complete this setup step.",
      "Selecionar um perfil não cria taxas fiscais. As taxas não são obrigatórias para concluir este passo.",
      "Wybór profilu nie tworzy stawek podatkowych. Stawki nie są wymagane do ukończenia tego kroku.",
      "Die Profilauswahl erstellt keine Steuersätze. Für diesen Einrichtungsschritt sind keine Steuersätze erforderlich.",
      "Een profiel kiezen maakt geen belastingtarieven aan. Tarieven zijn niet vereist om deze inrichtingsstap te voltooien.")


def tr(key, language):
    if key not in LABELS:
        return common_tr(key, language)
    code = (language or "ru").lower().split("-", 1)[0]
    return LABELS[key].get(code, LABELS[key]["ru"])
