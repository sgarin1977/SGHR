import {ApiProblem} from "./flow.mjs";

export const BINDING_PATH = "/api/v1/platform/integrations/telegram/bindings";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const KEY_WINDOW_MS = 86340000;
const LANGUAGES = ["uk", "ru", "en", "pt", "pl", "de", "nl"];

export const LABELS = {
  title: ["Telegram", "Telegram", "Telegram", "Telegram", "Telegram", "Telegram", "Telegram"],
  note: [
    "Відкрийте посилання в Telegram-акаунті, який хочете пов’язати зі своїм Platform-акаунтом. Посилання одноразове, діє 15 хвилин. Не передавайте його іншим.",
    "Откройте ссылку в Telegram-аккаунте, который хотите связать со своим Platform-аккаунтом. Ссылка одноразовая, действует 15 минут. Не передавайте её другим.",
    "Open the link in the Telegram account you want to connect to your Platform account. The link is single-use and valid for 15 minutes. Do not share it.",
    "Abra a ligação na conta Telegram que pretende associar à sua conta Platform. A ligação é de utilização única e válida por 15 minutos. Não a partilhe.",
    "Otwórz link na koncie Telegram, które chcesz połączyć ze swoim kontem Platform. Link jest jednorazowy i ważny przez 15 minut. Nie udostępniaj go innym.",
    "Öffnen Sie den Link im Telegram-Konto, das Sie mit Ihrem Platform-Konto verbinden möchten. Der Link ist einmalig und 15 Minuten gültig. Geben Sie ihn nicht weiter.",
    "Open de link in het Telegram-account dat je aan je Platform-account wilt koppelen. De link is eenmalig en 15 minuten geldig. Deel hem niet.",
  ],
  create: [
    "Прив’язати Telegram", "Привязать Telegram", "Connect Telegram",
    "Associar Telegram", "Połącz Telegram", "Telegram verbinden", "Telegram koppelen",
  ],
  retry: [
    "Отримати результат того самого запиту", "Получить результат того же запроса",
    "Recover the same request", "Recuperar o mesmo pedido",
    "Odzyskaj wynik tego samego żądania", "Ergebnis derselben Anfrage abrufen",
    "Resultaat van dezelfde aanvraag ophalen",
  ],
  open: [
    "Відкрити в потрібному Telegram-акаунті", "Открыть в нужном Telegram-аккаунте",
    "Open in the intended Telegram account", "Abrir na conta Telegram pretendida",
    "Otwórz na właściwym koncie Telegram", "Im gewünschten Telegram-Konto öffnen",
    "Openen in het juiste Telegram-account",
  ],
  close: [
    "Закрити прив’язку", "Закрыть привязку", "Close connection setup",
    "Fechar associação", "Zamknij łączenie", "Verknüpfung schließen", "Koppeling sluiten",
  ],
  ready: [
    "Запрошення прийняте. Тепер можна пов’язати Telegram зі своїм Platform-акаунтом.",
    "Приглашение принято. Теперь можно связать Telegram со своим Platform-аккаунтом.",
    "Invitation accepted. You can now connect Telegram to your Platform account.",
    "Convite aceite. Pode agora associar o Telegram à sua conta Platform.",
    "Zaproszenie przyjęte. Możesz teraz połączyć Telegram ze swoim kontem Platform.",
    "Einladung angenommen. Sie können Telegram jetzt mit Ihrem Platform-Konto verbinden.",
    "Uitnodiging geaccepteerd. Je kunt Telegram nu aan je Platform-account koppelen.",
  ],
  pending: [
    "Результат запиту ще не підтверджений. Повтор поверне результат тієї самої операції.",
    "Результат запроса ещё не подтверждён. Повтор вернёт результат той же операции.",
    "The request result is unconfirmed. Retry to recover the same operation.",
    "O resultado ainda não foi confirmado. Repita para recuperar a mesma operação.",
    "Wynik żądania nie jest potwierdzony. Ponów, aby odzyskać tę samą operację.",
    "Das Ergebnis ist noch unbestätigt. Erneut versuchen, um dieselbe Operation abzurufen.",
    "Het resultaat is nog niet bevestigd. Probeer opnieuw om dezelfde bewerking op te halen.",
  ],
  issued: [
    "Посилання готове. Завершіть прив’язку в Telegram; бот підтвердить результат.",
    "Ссылка готова. Завершите привязку в Telegram; бот подтвердит результат.",
    "The link is ready. Finish connecting in Telegram; the bot will confirm the result.",
    "A ligação está pronta. Conclua a associação no Telegram; o bot confirmará o resultado.",
    "Link jest gotowy. Dokończ łączenie w Telegramie; bot potwierdzi wynik.",
    "Der Link ist bereit. Schließen Sie die Verknüpfung in Telegram ab; der Bot bestätigt das Ergebnis.",
    "De link is klaar. Rond de koppeling af in Telegram; de bot bevestigt het resultaat.",
  ],
  expired: [
    "Строк посилання сплив. За потреби створіть нове посилання.",
    "Срок ссылки истёк. При необходимости создайте новую ссылку.",
    "The link has expired. Create a new link if needed.",
    "A ligação expirou. Crie uma nova ligação, se necessário.",
    "Link wygasł. W razie potrzeby utwórz nowy link.",
    "Der Link ist abgelaufen. Erstellen Sie bei Bedarf einen neuen Link.",
    "De link is verlopen. Maak zo nodig een nieuwe link.",
  ],
  auth: [
    "Сесія недоступна. Продовжіть прив’язку після входу до свого Platform-акаунта.",
    "Сессия недоступна. Продолжите привязку после входа в свой Platform-аккаунт.",
    "The session is unavailable. Continue after signing in to your Platform account.",
    "A sessão não está disponível. Continue após iniciar sessão na sua conta Platform.",
    "Sesja jest niedostępna. Kontynuuj po zalogowaniu do swojego konta Platform.",
    "Die Sitzung ist nicht verfügbar. Melden Sie sich in Ihrem Platform-Konto an.",
    "De sessie is niet beschikbaar. Ga verder nadat je bent ingelogd op je Platform-account.",
  ],
  rate: [
    "Забагато запитів. Зачекайте й повторіть той самий запит.",
    "Слишком много запросов. Подождите и повторите тот же запрос.",
    "Too many requests. Wait and retry the same request.",
    "Demasiados pedidos. Aguarde e repita o mesmo pedido.",
    "Zbyt wiele żądań. Poczekaj i ponów to samo żądanie.",
    "Zu viele Anfragen. Warten Sie und wiederholen Sie dieselbe Anfrage.",
    "Te veel aanvragen. Wacht en probeer dezelfde aanvraag opnieuw.",
  ],
  unavailable: [
    "Не вдалося отримати посилання. Можна повторити той самий запит.",
    "Не удалось получить ссылку. Можно повторить тот же запрос.",
    "The link could not be retrieved. You can retry the same request.",
    "Não foi possível obter a ligação. Pode repetir o mesmo pedido.",
    "Nie udało się pobrać linku. Możesz ponowić to samo żądanie.",
    "Der Link konnte nicht abgerufen werden. Wiederholen Sie dieselbe Anfrage.",
    "De link kon niet worden opgehaald. Je kunt dezelfde aanvraag opnieuw proberen.",
  ],
  window: [
    "Строк безпечного повтору запиту сплив. Продовжіть керування прив’язкою у своєму Platform-акаунті.",
    "Срок безопасного повтора запроса истёк. Продолжите управление привязкой в своём Platform-аккаунте.",
    "The safe retry window has ended. Continue managing the connection in your Platform account.",
    "O prazo seguro para repetir terminou. Continue a gerir a associação na sua conta Platform.",
    "Upłynął bezpieczny czas ponawiania. Zarządzaj połączeniem na swoim koncie Platform.",
    "Das sichere Wiederholungsfenster ist abgelaufen. Verwalten Sie die Verknüpfung in Ihrem Platform-Konto.",
    "De veilige herhaaltermijn is verstreken. Beheer de koppeling verder in je Platform-account.",
  ],
  until: [
    "Діє до", "Действует до", "Valid until", "Válida até",
    "Ważny do", "Gültig bis", "Geldig tot",
  ],
};

function languageIndex(language) {
  const normalized = String(language || "en").toLowerCase().replace("_", "-").split("-")[0];
  const index = LANGUAGES.indexOf(normalized);
  return index < 0 ? LANGUAGES.indexOf("en") : index;
}

function checkedReceipt(value) {
  if (!value || !UUID.test(value.binding_id) || value.row_version !== 1
      || typeof value.code !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(value.code)
      || typeof value.deep_link !== "string"
      || typeof value.issued_at !== "string" || typeof value.expires_at !== "string"
      || !/(Z|[+-]\d\d:\d\d)$/.test(value.issued_at)
      || !/(Z|[+-]\d\d:\d\d)$/.test(value.expires_at)) {
    throw new ApiProblem(0, "invalid_response");
  }
  const issued = Date.parse(value.issued_at), expires = Date.parse(value.expires_at);
  if (!Number.isFinite(issued) || !Number.isFinite(expires) || expires - issued !== 900000) {
    throw new ApiProblem(0, "invalid_response");
  }
  let url;
  try { url = new URL(value.deep_link); } catch { throw new ApiProblem(0, "invalid_response"); }
  if (url.protocol !== "https:" || url.hostname !== "t.me" || url.port
      || url.username || url.password || url.hash
      || !/^\/[A-Za-z0-9_]{5,32}$/.test(url.pathname)
      || value.deep_link !== "https://t.me" + url.pathname + "?start=bind_" + value.code) {
    throw new ApiProblem(0, "invalid_response");
  }
  // Keep only the link needed by the user, never the raw code or credentials
  // in public state snapshots or browser storage.
  return {link: value.deep_link, expiresAt: value.expires_at, expires};
}

export function createTelegramBinding({
  request, randomKey, now = () => Date.now(), onChange = () => {},
}) {
  let bearer = "", userId = null, active = false, busy = false;
  let phase = "closed", message = "", attempt = null, receipt = null, generation = 0;
  const safeWindow = () => !attempt || (
    now() - attempt.startedAt >= 0 && now() - attempt.startedAt < KEY_WINDOW_MS
  );
  function snapshot() {
    const expired = Boolean(receipt && now() >= receipt.expires);
    const windowEnded = Boolean(!receipt && attempt && !safeWindow());
    return {
      active, busy,
      phase: receipt ? expired ? "expired" : "issued" : phase,
      message: windowEnded ? "window" : receipt ? expired ? "expired" : "issued" : message,
      canIssue: active && Boolean(bearer) && !busy && (!receipt || expired)
        && (!attempt || Boolean(receipt) || safeWindow()),
      retry: Boolean(attempt && !receipt),
      expiresAt: receipt?.expiresAt || null,
    };
  }
  function emit() { onChange(snapshot()); }
  function close() {
    generation++; bearer = ""; userId = null; active = false; busy = false;
    attempt = null; receipt = null; phase = "closed"; message = ""; emit();
  }
  return {
    snapshot,
    close,
    getLink() { return active && receipt && now() < receipt.expires ? receipt.link : null; },
    acceptSession(session) {
      if (!session || !UUID.test(session.userId)
          || typeof session.bearer !== "string" || !session.bearer.trim()) {
        throw new ApiProblem(0, "invalid_session");
      }
      generation++; bearer = session.bearer; userId = session.userId;
      active = true; busy = false; attempt = null; receipt = null;
      phase = "ready"; message = "ready"; emit();
    },
    async issue() {
      if (!active || !bearer || busy) return;
      if (receipt && now() < receipt.expires) return;
      // A new key is allowed only after an explicit click and a known,
      // successfully received link has expired.
      if (receipt) { receipt = null; attempt = null; }
      if (!safeWindow()) { emit(); return; }
      if (!attempt) {
        const suffix = randomKey();
        if (typeof suffix !== "string" || !/^[A-Za-z0-9_-]{1,180}$/.test(suffix)) {
          message = "unavailable"; emit(); return;
        }
        attempt = {key: "web-tg-binding:" + suffix, startedAt: now()};
      }
      const current = generation;
      const operation = {...attempt};
      busy = true; phase = "pending"; message = "pending"; emit();
      try {
        const me = await request("/api/v1/me", {bearer});
        if (generation !== current) return;
        if (!me || me.id !== userId || me.status !== "active") {
          throw new ApiProblem(403, "account_unavailable");
        }
        const value = await request(BINDING_PATH, {
          body: {}, bearer, key: operation.key,
        });
        if (generation !== current) return;
        receipt = checkedReceipt(value);
        phase = "issued"; message = "issued";
      } catch (error) {
        if (generation !== current) return;
        if (error?.status === 401 || error?.status === 403) {
          bearer = ""; userId = null; receipt = null;
          phase = "auth"; message = "auth";
        } else {
          phase = "pending";
          message = error?.status === 429 ? "rate" : "unavailable";
        }
      } finally {
        if (generation === current) { busy = false; emit(); }
      }
    },
  };
}

export function mountTelegramBinding({root, request, randomKey, getLanguage}) {
  let controller, timer = null;
  const text = (key, language) => LABELS[key][languageIndex(language)];
  function close() {
    if (timer !== null) { clearInterval(timer); timer = null; }
    controller.close();
  }
  function render(state) {
    root.replaceChildren();
    root.hidden = !state.active;
    if (!state.active) return;
    const language = getLanguage();
    const element = (tag, value, className) => {
      const node = document.createElement(tag);
      node.textContent = value;
      if (className) node.className = className;
      return node;
    };
    root.append(element("h3", text("title", language)));
    root.append(element("p", text("note", language), "hint"));
    const status = element("p", text(state.message || "ready", language));
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    root.append(status);
    if (state.expiresAt) {
      const locale = LANGUAGES[languageIndex(language)];
      const formatted = new Intl.DateTimeFormat(locale, {
        dateStyle: "medium", timeStyle: "short",
      }).format(new Date(state.expiresAt));
      root.append(element(
        "p",
        text("until", language) + ": " + formatted + " · "
          + Intl.DateTimeFormat().resolvedOptions().timeZone,
        "hint",
      ));
    }
    const actions = document.createElement("div");
    actions.className = "actions";
    const link = controller.getLink();
    if (link) {
      const anchor = element("a", text("open", language), "primary");
      anchor.href = link; anchor.target = "_blank"; anchor.rel = "noopener noreferrer";
      anchor.referrerPolicy = "no-referrer";
      actions.append(anchor);
    } else if (state.canIssue || state.busy) {
      const button = element("button", text(state.retry ? "retry" : "create", language), "primary");
      button.type = "button"; button.disabled = !state.canIssue;
      button.addEventListener("click", () => controller.issue());
      actions.append(button);
    }
    const dismiss = element("button", text("close", language), "text-button");
    dismiss.type = "button"; dismiss.addEventListener("click", close);
    actions.append(dismiss); root.append(actions);
  }
  controller = createTelegramBinding({request, randomKey, onChange: render});
  render(controller.snapshot());
  return {
    acceptSession(session) {
      controller.acceptSession(session);
      if (timer === null) timer = setInterval(() => render(controller.snapshot()), 1000);
    },
    close,
    refreshLanguage() { render(controller.snapshot()); },
  };
}
