import {createConfirmationFlow, createHttpClient} from "./flow.mjs";
const LANGUAGES = ["uk","ru","en","pt","pl","de","nl"];
const TEXT = {
  title:["Підтвердження зв’язку клієнта","Подтверждение связи клиента","Confirm client link","Confirmar ligação ao cliente","Potwierdź powiązanie klienta","Kundenverknüpfung bestätigen","Klantkoppeling bevestigen"],
  intro:["Увійди за email, на який надійшов запит.","Войдите по email, на который пришёл запрос.","Sign in with the email that received the request.","Entre com o email que recebeu o pedido.","Zaloguj się adresem email odbiorcy prośby.","Mit der E-Mail-Adresse des Empfängers anmelden.","Meld je aan met het e-mailadres dat het verzoek ontving."],
  meaning:["Це зв’язок картки клієнта з твоїм Platform-акаунтом. Він не додає до організації й не надає Construction-доступу.","Это связь карточки клиента с вашим Platform-аккаунтом. Она не добавляет в организацию и не даёт Construction-доступ.","This links a client record to your Platform account. It does not join an organization or grant Construction access.","Isto liga uma ficha de cliente à sua conta Platform. Não adiciona à organização nem dá acesso ao Construction.","To łączy kartę klienta z kontem Platform. Nie dodaje do organizacji ani nie daje dostępu do Construction.","Dies verknüpft einen Kundendatensatz mit Ihrem Platform-Konto. Es gewährt keine Organisationsmitgliedschaft und keinen Construction-Zugriff.","Dit koppelt een klantkaart aan je Platform-account. Het geeft geen organisatielidmaatschap of Construction-toegang."],
  emailLabel:["Email","Email","Email","Email","Email","E-Mail","E-mail"],
  sendCode:["Надіслати код входу","Отправить код входа","Send sign-in code","Enviar código de entrada","Wyślij kod logowania","Anmeldecode senden","Inlogcode verzenden"],
  codeLabel:["Код підтвердження","Код подтверждения","Verification code","Código de confirmação","Kod weryfikacyjny","Bestätigungscode","Verificatiecode"],
  verifyCode:["Увійти","Войти","Sign in","Entrar","Zaloguj się","Anmelden","Aanmelden"],
  reviewTitle:["Твоє рішення","Ваше решение","Your decision","A sua decisão","Twoja decyzja","Ihre Entscheidung","Jouw beslissing"],
  consent:["Підтвердь лише якщо погоджуєшся зв’язати картку клієнта з цього запиту зі своїм Platform-акаунтом.","Подтвердите, только если согласны связать карточку клиента из этого запроса со своим Platform-аккаунтом.","Confirm only if you agree to link the client record from this request to your Platform account.","Confirme apenas se concordar em ligar a ficha deste pedido à sua conta Platform.","Potwierdź tylko, jeśli zgadzasz się powiązać tę kartę klienta ze swoim kontem Platform.","Bestätigen Sie nur, wenn Sie der Verknüpfung aus dieser Anfrage mit Ihrem Platform-Konto zustimmen.","Bevestig alleen als je de klantkaart uit dit verzoek aan je Platform-account wilt koppelen."],
  confirm:["Підтвердити зв’язок","Подтвердить связь","Confirm client link","Confirmar ligação","Potwierdź powiązanie","Verknüpfung bestätigen","Koppeling bevestigen"],
  recover:["Отримати попередній результат","Получить предыдущий результат","Recover previous result","Recuperar resultado anterior","Odzyskaj poprzedni wynik","Vorheriges Ergebnis abrufen","Vorig resultaat ophalen"],
  changeEmail:["Увійти за іншим email","Войти по другому email","Use another email","Usar outro email","Użyj innego emaila","Andere E-Mail verwenden","Ander e-mailadres gebruiken"],
  forget:["Закрити запит у цій вкладці","Закрыть запрос в этой вкладке","Close request in this tab","Fechar pedido neste separador","Zamknij prośbę w tej karcie","Anfrage in diesem Tab schließen","Verzoek in dit tabblad sluiten"],
  footnote:["Відкриття посилання й вхід не підтверджують зв’язок. Закриття сторінки не відкликає запит.","Открытие ссылки и вход не подтверждают связь. Закрытие страницы не отзывает запрос.","Opening the link or signing in does not confirm the link. Closing the page does not revoke the request.","Abrir a ligação ou entrar não confirma a ligação. Fechar a página não revoga o pedido.","Otwarcie linku i logowanie nie potwierdzają powiązania. Zamknięcie strony nie cofa prośby.","Das Öffnen des Links und die Anmeldung bestätigen keine Verknüpfung. Das Schließen der Seite widerruft die Anfrage nicht.","De link openen of aanmelden bevestigt de koppeling niet. De pagina sluiten trekt het verzoek niet in."],
  noStorage:["Браузер не зберігає запит після перезавантаження. Не закривай вкладку до завершення.","Браузер не сохраняет запрос после перезагрузки. Не закрывайте вкладку до завершения.","The browser cannot retain this request across reloads. Keep this tab open until you finish.","O navegador não guarda o pedido após recarregar. Mantenha este separador aberto.","Przeglądarka nie zachowa prośby po odświeżeniu. Nie zamykaj karty.","Der Browser kann die Anfrage beim Neuladen nicht speichern. Tab geöffnet lassen.","De browser bewaart dit verzoek niet na herladen. Houd dit tabblad open."],
  invalid:["Відкрий повне посилання з листа.","Откройте полную ссылку из письма.","Open the complete link from the email.","Abra a ligação completa do email.","Otwórz pełny link z wiadomości.","Vollständigen Link aus der E-Mail öffnen.","Open de volledige link uit de e-mail."],
  pending:["Попередній результат не підтверджено. Можна отримати результат того самого запиту.","Предыдущий результат не подтверждён. Можно получить результат того же запроса.","The previous result is unconfirmed. Recover the result of the same request.","O resultado anterior não foi confirmado. Recupere o resultado do mesmo pedido.","Poprzedni wynik nie jest potwierdzony. Odzyskaj wynik tej samej prośby.","Das vorherige Ergebnis ist unbestätigt. Ergebnis derselben Anfrage abrufen.","Het vorige resultaat is onbevestigd. Haal het resultaat van hetzelfde verzoek op."],
  session:["Сесія недоступна. Увійди знову; запит збережено в цій вкладці.","Сессия недоступна. Войдите снова; запрос сохранён в этой вкладке.","Your session is unavailable. Sign in again; the request is retained in this tab.","A sessão não está disponível. Entre novamente; o pedido mantém-se neste separador.","Sesja jest niedostępna. Zaloguj się ponownie; prośba pozostaje w karcie.","Sitzung nicht verfügbar. Erneut anmelden; die Anfrage bleibt in diesem Tab.","Je sessie is niet beschikbaar. Meld je opnieuw aan; het verzoek blijft in dit tabblad."],
  unavailable:["Запит недоступний цьому акаунту, використаний або більше не діє. Перевір email адресата.","Запрос недоступен этому аккаунту, использован или больше не действует. Проверьте email получателя.","The request is unavailable to this account, used or no longer valid. Check the recipient email.","O pedido não está disponível para esta conta, foi usado ou expirou. Verifique o email.","Prośba jest niedostępna dla tego konta, wykorzystana lub nieważna. Sprawdź email odbiorcy.","Anfrage für dieses Konto nicht verfügbar, verwendet oder ungültig. Empfänger-E-Mail prüfen.","Het verzoek is voor dit account niet beschikbaar, gebruikt of ongeldig. Controleer het e-mailadres."],
  conflict:["Запит або картка змінилися. Потрібен новий запит від оператора.","Запрос или карточка изменились. Нужен новый запрос от оператора.","The request or client record changed. A new request from the operator is needed.","O pedido ou a ficha mudou. É necessário um novo pedido do operador.","Prośba lub karta uległa zmianie. Potrzebna jest nowa prośba operatora.","Anfrage oder Kundendatensatz geändert. Eine neue Anfrage des Operators ist erforderlich.","Het verzoek of de klantkaart is gewijzigd. Een nieuw verzoek van de operator is nodig."],
  rate:["Забагато запитів. Зачекай і повтори.","Слишком много запросов. Подождите и повторите.","Too many requests. Wait and try again.","Demasiados pedidos. Aguarde e tente novamente.","Za dużo żądań. Poczekaj i spróbuj ponownie.","Zu viele Anfragen. Warten und erneut versuchen.","Te veel verzoeken. Wacht en probeer opnieuw."],
  invalidInput:["Перевір email або код.","Проверьте email или код.","Check the email or code.","Verifique o email ou o código.","Sprawdź email lub kod.","E-Mail oder Code prüfen.","Controleer het e-mailadres of de code."],
  network:["Результат не підтверджено. Перевір з’єднання; для повтору збережено той самий запит.","Результат не подтверждён. Проверьте соединение; для повтора сохранён тот же запрос.","The result is unconfirmed. Check your connection; the same request is retained for retry.","O resultado não foi confirmado. Verifique a ligação; o mesmo pedido foi guardado.","Wynik nie jest potwierdzony. Sprawdź połączenie; zachowano tę samą prośbę.","Ergebnis unbestätigt. Verbindung prüfen; dieselbe Anfrage bleibt für den erneuten Abruf gespeichert.","Het resultaat is onbevestigd. Controleer je verbinding; hetzelfde verzoek blijft bewaard."],
  codeSent:["Код входу запитано. Перевір пошту.","Код входа запрошен. Проверьте почту.","Sign-in code requested. Check your email.","Código pedido. Consulte o email.","Kod logowania został zamówiony. Sprawdź pocztę.","Anmeldecode angefordert. E-Mail prüfen.","Inlogcode aangevraagd. Controleer je e-mail."],
  done:["✅ Зв’язок підтверджено.","✅ Связь подтверждена.","✅ Client link confirmed.","✅ Ligação confirmada.","✅ Powiązanie potwierdzone.","✅ Verknüpfung bestätigt.","✅ Koppeling bevestigd."],
  cleared:["Запит закрито в цій вкладці. Це не відкликає його у Platform.","Запрос закрыт в этой вкладке. Это не отзывает его в Platform.","The request was closed in this tab. This does not revoke it in Platform.","O pedido foi fechado neste separador. Não foi revogado na Platform.","Prośba została zamknięta w tej karcie. Nie została cofnięta w Platform.","Anfrage in diesem Tab geschlossen. Sie wurde in Platform nicht widerrufen.","Het verzoek is in dit tabblad gesloten. Het is niet ingetrokken in Platform."],
};
const $ = id => document.getElementById(id);
let language = (navigator.language || "en").toLowerCase().split("-")[0];
if (!LANGUAGES.includes(language)) language = "en";
$("language").value = language;
let current;
const tr = key => TEXT[key]?.[LANGUAGES.indexOf(language)] || "";
function render(state) {
  current = state;
  document.documentElement.lang = language;
  document.title = "SGHR · " + tr("title");
  document.querySelectorAll("[data-i18n]").forEach(node => {
    node.textContent = tr(node.dataset.i18n);
  });
  $("message").textContent = tr(state.message);
  $("message").className = state.phase === "done" ? "success"
    : state.message && !["pending","codeSent","cleared"].includes(state.message) ? "error" : "";
  $("storage-note").hidden = state.storageOk;
  $("email-form").hidden = state.phase !== "email";
  $("code-form").hidden = state.phase !== "code";
  $("review").hidden = !state.authenticated || !["review","recover"].includes(state.phase);
  $("account").textContent = state.email;
  $("confirm").textContent = tr(state.pending ? "recover" : "confirm");
  $("change-email").hidden = !["code","review","recover","blocked"].includes(state.phase);
  $("forget").hidden = ["invalid","done","closed"].includes(state.phase);
  $("challenge-note").textContent = state.challengeExpiresAt
    ? new Intl.DateTimeFormat(language,{dateStyle:"medium",timeStyle:"short"})
      .format(new Date(state.challengeExpiresAt)) : "";
  ["email","code","send-code","verify-code","confirm","change-email","forget","language"]
    .forEach(id => { $(id).disabled = state.busy; });
}
let storage;
try { storage = window.sessionStorage; }
catch {
  storage = {
    getItem(){throw Error();}, setItem(){throw Error();}, removeItem(){throw Error();},
  };
}
const flow = createConfirmationFlow({
  hash: location.hash, storage,
  replaceUrl: () => history.replaceState(null, "", location.pathname),
  request: createHttpClient(window.fetch.bind(window)),
  randomKey: () => crypto.randomUUID(), onChange: render,
});
render(flow.snapshot());
$("email-form").addEventListener("submit", event => {
  event.preventDefault(); flow.sendCode($("email").value);
});
$("code-form").addEventListener("submit", event => {
  event.preventDefault();
  const code = $("code").value;
  $("code").value = "";
  flow.verifyCode(code, language);
});
$("confirm").addEventListener("click", () => flow.confirm());
$("change-email").addEventListener("click", () => {
  $("email").value = ""; $("code").value = ""; flow.changeEmail();
});
$("forget").addEventListener("click", () => {
  $("email").value = ""; $("code").value = ""; flow.forget();
});
$("language").addEventListener("change", () => {
  language = $("language").value; render(current);
});
window.addEventListener("pagehide", () => flow.close());
window.addEventListener("pageshow", event => {
  if (event.persisted) location.reload();
});

// client_link_confirmation_page_v1
