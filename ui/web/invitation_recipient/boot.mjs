import {createInvitationFlow, createHttpClient} from "./flow.mjs";
import {mountTelegramBinding} from "./telegram-binding.mjs";
const $ = id => document.getElementById(id);
const translations = {
  en: {eyebrow:"YOUR NEXT PROJECT STARTS HERE",title:"Join the team.",intro:"Verify the email that received this invitation. Then review the access before you decide.",signIn:"Verify email",review:"Review invitation",decision:"Your decision",platform:"SGHR PLATFORM",cardTitle:"Team invitation",emailLabel:"Invited email address",emailHint:"Use the address this invitation was sent to. Sign-in or registration uses the existing Platform email flow.",sendCode:"Send verification code",codeLabel:"Verification code",verifyCode:"Verify and review",role:"Role",expires:"Invitation valid until",accessUntil:"Access valid until",scope:"Access scope",pending:"The previous result is unconfirmed. Retry the same decision to recover its result.",accept:"Accept invitation",decline:"Decline",retryPreview:"Review invitation again",changeEmail:"Use another email",forget:"Clear this invitation from this tab",footnote:"Opening this link does not grant access. Only your confirmed decision changes your membership.",footer:"One platform. A connected team.",invalidLink:"Open the complete invitation link from your email. This page cannot identify an invitation without it.",sessionExpired:"Your session expired. Verify your email again; the pending invitation is kept in this tab.",unavailable:"The invitation is unavailable for this account. Check the invited email. It may also have expired, changed or already been used.",conflict:"The invitation changed. Review the current invitation before deciding again.",rateLimit:"Too many requests. Wait a moment and try again.",invalidRequest:"Check the entered email or code and try again.",network:"The result could not be confirmed. Check your connection and retry. A pending decision retains its original request.",codeSent:"A verification code was requested. Check your email.",differentAccount:"The pending decision belongs to another signed-in account. Verify the original invited account.",accepted:"Invitation accepted. Your organization membership and approved access were saved.",declined:"Invitation declined. No organization access was granted.",cleared:"The invitation has been cleared from this tab. You can reopen the original email link.",noStorage:"This browser cannot retain the invitation across reloads. Keep this tab open until you finish.",codeDeadline:"Code expires",unlimited:"No fixed expiry",tenant:"Organization",project:"Project",confirmDecline:"Decline this invitation? This decision ends the invitation.",roles:{OWNER:"Owner",ADMIN:"Administrator",SURVEYOR:"Surveyor",ESTIMATOR:"Estimator",PROJECT_MANAGER:"Project manager",EXECUTOR:"Executor",OBSERVER:"Observer"}},
  ru: {eyebrow:"СЛЕДУЮЩИЙ ПРОЕКТ НАЧИНАЕТСЯ ЗДЕСЬ",title:"Присоединяйтесь к команде.",intro:"Подтвердите email, на который пришло приглашение. Затем проверьте доступ и примите решение.",signIn:"Подтвердить email",review:"Проверить приглашение",decision:"Ваше решение",platform:"ПЛАТФОРМА SGHR",cardTitle:"Приглашение в команду",emailLabel:"Email получателя приглашения",emailHint:"Используйте адрес, на который пришло письмо. Вход или регистрация выполняются существующим email-механизмом Platform.",sendCode:"Отправить код подтверждения",codeLabel:"Код подтверждения",verifyCode:"Подтвердить и посмотреть",role:"Роль",expires:"Приглашение действует до",accessUntil:"Доступ действует до",scope:"Область доступа",pending:"Результат предыдущей операции не подтверждён. Повторите то же решение, чтобы получить его результат.",accept:"Принять приглашение",decline:"Отклонить",retryPreview:"Просмотреть приглашение снова",changeEmail:"Использовать другой email",forget:"Удалить приглашение из этой вкладки",footnote:"Открытие ссылки не предоставляет доступ. Membership меняется только после вашего подтверждённого решения.",footer:"Одна платформа. Общая команда.",invalidLink:"Откройте полную ссылку из письма. Без неё эта страница не может определить приглашение.",sessionExpired:"Сессия истекла. Подтвердите email снова: приглашение сохранено в этой вкладке.",unavailable:"Приглашение недоступно этому аккаунту. Проверьте email получателя. Оно также могло истечь, измениться или уже быть использовано.",conflict:"Приглашение изменилось. Просмотрите его актуальное состояние перед новым решением.",rateLimit:"Слишком много запросов. Подождите и повторите попытку.",invalidRequest:"Проверьте email или код и повторите попытку.",network:"Не удалось подтвердить результат. Проверьте соединение и повторите попытку. Для незавершённого решения сохраняется исходный запрос.",codeSent:"Код подтверждения запрошен. Проверьте почту.",differentAccount:"Незавершённое решение принадлежит другому аккаунту. Подтвердите исходный аккаунт получателя.",accepted:"Приглашение принято. Участие в организации и согласованный доступ сохранены.",declined:"Приглашение отклонено. Доступ к организации не выдан.",cleared:"Приглашение удалено из этой вкладки. Его можно открыть снова из исходного письма.",noStorage:"Браузер не сохраняет приглашение после перезагрузки. Не закрывайте вкладку до завершения.",codeDeadline:"Код действует до",unlimited:"Без фиксированного срока",tenant:"Организация",project:"Проект",confirmDecline:"Отклонить приглашение? Это решение завершит приглашение.",roles:{OWNER:"Владелец",ADMIN:"Администратор",SURVEYOR:"Замерщик",ESTIMATOR:"Сметчик",PROJECT_MANAGER:"Руководитель проекта",EXECUTOR:"Исполнитель",OBSERVER:"Наблюдатель"}},
  pt: {eyebrow:"O SEU PRÓXIMO PROJETO COMEÇA AQUI",title:"Junte-se à equipa.",intro:"Confirme o email que recebeu este convite. Depois consulte o acesso antes de decidir.",signIn:"Confirmar email",review:"Consultar convite",decision:"A sua decisão",platform:"PLATAFORMA SGHR",cardTitle:"Convite para a equipa",emailLabel:"Email que recebeu o convite",emailHint:"Use o endereço que recebeu a mensagem. O início de sessão ou registo usa o mecanismo de email existente da Platform.",sendCode:"Enviar código de confirmação",codeLabel:"Código de confirmação",verifyCode:"Confirmar e consultar",role:"Função",expires:"Convite válido até",accessUntil:"Acesso válido até",scope:"Âmbito do acesso",pending:"O resultado anterior não foi confirmado. Repita a mesma decisão para recuperar o resultado.",accept:"Aceitar convite",decline:"Recusar",retryPreview:"Consultar o convite novamente",changeEmail:"Usar outro email",forget:"Remover o convite deste separador",footnote:"Abrir a ligação não concede acesso. Apenas a sua decisão confirmada altera a participação na organização.",footer:"Uma plataforma. Uma equipa ligada.",invalidLink:"Abra a ligação completa do email. Esta página não consegue identificar o convite sem essa ligação.",sessionExpired:"A sessão expirou. Confirme o email novamente; o convite continua neste separador.",unavailable:"O convite não está disponível para esta conta. Confirme o email. O convite também pode ter expirado, sido alterado ou utilizado.",conflict:"O convite foi alterado. Consulte o estado atual antes de decidir novamente.",rateLimit:"Demasiados pedidos. Aguarde e tente novamente.",invalidRequest:"Verifique o email ou o código e tente novamente.",network:"Não foi possível confirmar o resultado. Verifique a ligação e tente novamente. Uma decisão pendente mantém o pedido original.",codeSent:"Foi pedido um código de confirmação. Consulte o seu email.",differentAccount:"A decisão pendente pertence a outra conta. Confirme a conta original do destinatário.",accepted:"Convite aceite. A participação na organização e o acesso aprovado foram guardados.",declined:"Convite recusado. Não foi concedido acesso à organização.",cleared:"O convite foi removido deste separador. Pode abri-lo novamente pelo email original.",noStorage:"O navegador não consegue guardar o convite após recarregar. Mantenha este separador aberto até terminar.",codeDeadline:"Código válido até",unlimited:"Sem data fixa",tenant:"Organização",project:"Projeto",confirmDecline:"Recusar este convite? Esta decisão termina o convite.",roles:{OWNER:"Proprietário",ADMIN:"Administrador",SURVEYOR:"Técnico de medição",ESTIMATOR:"Orçamentista",PROJECT_MANAGER:"Gestor de projeto",EXECUTOR:"Executante",OBSERVER:"Observador"}},
};
let language = navigator.language?.startsWith("pt") ? "pt" : /^(ru|uk)/.test(navigator.language || "") ? "ru" : "en";
$("language").value = language;
let current;
const format = value => value ? new Intl.DateTimeFormat(language, {dateStyle:"medium",timeStyle:"short"}).format(new Date(value)) + " · " + Intl.DateTimeFormat().resolvedOptions().timeZone : translations[language].unlimited;
function render(state) {
  current = state; const t = translations[language]; document.documentElement.lang = language;
  document.title = "SGHR · " + t.cardTitle;
  document.querySelectorAll("[data-i18n]").forEach(node => { node.textContent = t[node.dataset.i18n]; });
  $("message").textContent = t[state.message] || (!state.storageOk ? t.noStorage : "");
  $("message").className = state.phase === "done" ? "success" : state.message && !["codeSent","pending","cleared"].includes(state.message) ? "error" : "";
  $("email-form").hidden = state.phase !== "email";
  $("code-form").hidden = state.phase !== "code";
  $("preview").hidden = !state.preview;
  $("pending-note").hidden = !state.pendingKind || state.phase === "email" || state.phase === "code";
  $("actions").hidden = !state.authenticated || (!state.preview && !state.pendingKind);
  $("accept").disabled = state.busy || Boolean(state.pendingKind && state.pendingKind !== "accept");
  $("decline").disabled = state.busy || Boolean(state.pendingKind && state.pendingKind !== "decline");
  $("retry-preview").hidden = !state.authenticated || Boolean(state.preview || state.pendingKind);
  $("change-email").hidden = ["invalid","done","email"].includes(state.phase);
  $("forget").hidden = ["invalid","done"].includes(state.phase);
  ["send-code","verify-code","retry-preview","change-email","forget","email","code","language"].forEach(id => { $(id).disabled = state.busy; });
  $("challenge-note").textContent = state.challengeExpiresAt ? t.codeDeadline + ": " + format(state.challengeExpiresAt) : "";
  const order = state.phase === "done" ? 2 : state.authenticated ? 1 : 0;
  ["step-auth","step-review","step-done"].forEach((id,index) => { $(id).className = index === order ? "active" : index < order ? "done" : ""; });
  if (state.preview) {
    $("role").textContent = t.roles[state.preview.role] || state.preview.role;
    $("expires").textContent = format(state.preview.expires_at);
    $("access-until").textContent = format(state.preview.grant_expires_at);
    $("scopes").replaceChildren(...state.preview.scopes.map(scope => { const item = document.createElement("li"); item.textContent = t[scope.scope_type] + " · " + scope.scope_id; return item; }));
  }
}
let storage;
try { storage = window.sessionStorage; } catch { storage = {getItem(){return null;}, setItem(){throw new Error();},removeItem(){}}; }
const telegramBinding = mountTelegramBinding({
  root: $("telegram-binding"),
  request: createHttpClient(window.fetch.bind(window)),
  randomKey: () => crypto.randomUUID(),
  getLanguage: () => language,
  onAuthenticate: () => flow.reauthenticateAccepted(),
});
window.addEventListener("pagehide", () => telegramBinding.close());
const flow = createInvitationFlow({hash: location.hash, storage,
  replaceUrl: () => history.replaceState(null,"",location.pathname),
  request: createHttpClient(window.fetch.bind(window)), randomKey: () => crypto.randomUUID(),
  onAccepted: session => telegramBinding.acceptSession(session), onChange: render});
render(flow.snapshot());
$("email-form").addEventListener("submit", event => { event.preventDefault(); flow.sendCode($("email").value); });
$("code-form").addEventListener("submit", event => { event.preventDefault(); const secret = $("code").value; $("code").value = ""; flow.verifyCode(secret,language); });
$("accept").addEventListener("click", () => flow.decide("accept"));
$("decline").addEventListener("click", () => { if (window.confirm(translations[language].confirmDecline)) flow.decide("decline"); });
$("retry-preview").addEventListener("click", () => flow.review());
$("change-email").addEventListener("click", () => { $("email").value = ""; $("code").value = ""; flow.changeEmail(); });
$("forget").addEventListener("click", () => flow.forget());
$("language").addEventListener("change", () => { language = $("language").value; render(current); telegramBinding.refreshLanguage(); });

// invitation_telegram_onboarding_v1

// invitation_binding_session_v1
