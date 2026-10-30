export const STORAGE_KEY = "sghr.client-link.confirmation.v1";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const TOKEN = /^ccl1_[A-Za-z0-9_-]{43}$/;
const keyValid = value => typeof value === "string"
  && value.length >= 1 && value.length <= 255 && /^[!-~]+$/.test(value);
const timeValid = value => typeof value === "string"
  && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value)
  && Number.isFinite(Date.parse(value));
const linkValid = value => value
  && typeof value.recipient_context === "string"
  && value.recipient_context.length >= 1 && value.recipient_context.length <= 4096
  && /^[A-Za-z0-9_=-]+$/.test(value.recipient_context)
  && typeof value.token === "string" && TOKEN.test(value.token);

export class ApiProblem extends Error {
  constructor(status, code) {
    super("Request unavailable");
    this.status = status;
    this.code = code;
  }
}

export function createHttpClient(fetchImpl, timeoutMs = 20000) {
  const paths = new Set([
    "/api/v1/auth/email/request",
    "/api/v1/auth/email/verify",
    "/api/v1/construction/client-user-link-requests/confirm",
  ]);
  return async function request(path, {body, bearer, key} = {}) {
    if (!paths.has(path) || !body || typeof body !== "object" || Array.isArray(body)) {
      throw new ApiProblem(0, "invalid_request");
    }
    const headers = {Accept: "application/json", "Content-Type": "application/json"};
    if (bearer) headers.Authorization = "Bearer " + bearer;
    if (key) headers["Idempotency-Key"] = key;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetchImpl(path, {
        method: "POST", headers, body: JSON.stringify(body),
        signal: controller.signal, credentials: "omit", cache: "no-store",
        redirect: "error", referrerPolicy: "no-referrer",
      });
      let value;
      try { value = await response.json(); }
      catch { throw new ApiProblem(response.status, "invalid_response"); }
      if (!response.ok) {
        throw new ApiProblem(response.status, value?.error?.code || "request_failed");
      }
      if (!value || !value.data || typeof value.data !== "object" || Array.isArray(value.data)) {
        throw new ApiProblem(response.status, "invalid_response");
      }
      return value.data;
    } catch (error) {
      if (error instanceof ApiProblem) throw error;
      throw new ApiProblem(0, "network");
    } finally {
      clearTimeout(timer);
    }
  };
}

function validResult(value) {
  const fields = [
    "request_id", "tenant_id", "client_id", "linked_user_id",
    "client_row_version", "request_row_version", "consumed_at", "client_changed",
  ];
  return value && Object.keys(value).length === fields.length
    && fields.every(field => Object.hasOwn(value, field))
    && ["request_id", "tenant_id", "client_id", "linked_user_id"]
      .every(field => typeof value[field] === "string" && UUID.test(value[field]))
    && Number.isInteger(value.client_row_version) && value.client_row_version >= 1
    && value.request_row_version === 2
    && timeValid(value.consumed_at) && typeof value.client_changed === "boolean";
}

export function createConfirmationFlow({
  hash, storage, replaceUrl, request, randomKey, onChange = () => {},
}) {
  let link = null, pending = null, bearer = "", challenge = null, email = "";
  let phase = "invalid", message = "invalid", busy = false, storageOk = true;
  let generation = 0, closed = false;

  const snapshot = () => Object.freeze({
    phase, message, busy, storageOk, email,
    authenticated: Boolean(bearer), pending: Boolean(pending),
    challengeExpiresAt: challenge?.expires_at || null,
  });
  function emit() {
    try { onChange(snapshot()); } catch {}
  }
  function persist() {
    try {
      if (link) storage.setItem(STORAGE_KEY, JSON.stringify({v: 1, ...link, pending}));
      else storage.removeItem(STORAGE_KEY);
    } catch {
      storageOk = false;
    }
  }
  function saved() {
    try {
      const value = JSON.parse(storage.getItem(STORAGE_KEY));
      if (!value || value.v !== 1
        || Object.keys(value).sort().join(",") !== "pending,recipient_context,token,v"
        || !linkValid(value)
        || (value.pending !== null && (
          !value.pending || Object.keys(value.pending).join(",") !== "key"
          || !keyValid(value.pending.key)
        ))) return null;
      return value;
    } catch {
      storageOk = false;
      return null;
    }
  }
  function restore() {
    try {
      if (hash) {
        replaceUrl();
        if (typeof hash !== "string" || hash.length > 20000) throw Error();
        const params = new URLSearchParams(hash.replace(/^#/, ""));
        if ([...params.keys()].length !== 2
          || params.getAll("recipient_context").length !== 1
          || params.getAll("token").length !== 1) throw Error();
        const candidate = {
          recipient_context: params.get("recipient_context"),
          token: params.get("token"),
        };
        if (!linkValid(candidate)) throw Error();
        const old = saved();
        if (old && old.recipient_context === candidate.recipient_context
          && old.token === candidate.token) pending = old.pending;
        link = candidate;
        persist();
      } else {
        const old = saved();
        if (!old) throw Error();
        link = {recipient_context: old.recipient_context, token: old.token};
        pending = old.pending;
      }
      phase = "email";
      message = pending ? "pending" : "";
    } catch {
      link = null;
      pending = null;
      persist();
    }
  }
  restore();

  function failure(error) {
    if (error.status === 401) {
      bearer = ""; challenge = null; phase = "email"; message = "session";
    } else if (error.status === 403) {
      phase = pending ? "recover" : phase; message = "unavailable";
    } else if (error.status === 409) {
      phase = "blocked"; message = "conflict";
    } else if (error.status === 429) {
      message = "rate";
    } else if (error.status === 422) {
      message = "invalidInput";
    } else {
      if (pending) phase = "recover";
      message = "network";
    }
  }
  async function task(callback) {
    if (closed || busy || !link) return;
    const revision = generation;
    const current = () => !closed && generation === revision;
    busy = true; message = ""; emit();
    try { await callback(current); }
    catch (error) { if (current()) failure(error); }
    finally { if (current()) { busy = false; emit(); } }
  }

  return Object.freeze({
    snapshot,
    async sendCode(value) {
      if (!["email", "code"].includes(phase)) return;
      return task(async current => {
        const candidate = typeof value === "string" ? value.trim() : "";
        if (!candidate || candidate.length > 320) throw new ApiProblem(422, "invalid_email");
        const result = await request("/api/v1/auth/email/request", {
          body: {email: candidate, challenge_type: "otp"},
        });
        if (!current()) return;
        if (!UUID.test(result.challenge_id) || result.challenge_type !== "otp"
          || !timeValid(result.expires_at)) throw new ApiProblem(0, "invalid_response");
        email = candidate;
        challenge = {challenge_id: result.challenge_id, expires_at: result.expires_at};
        phase = "code"; message = "codeSent";
      });
    },
    async verifyCode(value, language = "en") {
      if (phase !== "code" || !challenge) return;
      return task(async current => {
        if (typeof value !== "string" || !value.trim() || value.length > 512) {
          throw new ApiProblem(422, "invalid_code");
        }
        let tokens;
        try {
          tokens = await request("/api/v1/auth/email/verify", {
            body: {
              challenge_id: challenge.challenge_id, email,
              secret: value.trim(), language_code: language,
            },
          });
        } catch (error) {
          if (error.status === 401 && error.code === "invalid_email_challenge") {
            if (current()) message = "invalidInput";
            return;
          }
          throw error;
        }
        if (!current()) return;
        if (typeof tokens.access_token !== "string" || !tokens.access_token
          || tokens.access_token.length > 16384
          || tokens.token_type?.toLowerCase() !== "bearer") {
          throw new ApiProblem(0, "invalid_response");
        }
        // Credentials stay in this component's memory. Refresh tokens are unused.
        bearer = tokens.access_token;
        challenge = null;
        phase = pending ? "recover" : "review";
        message = pending ? "pending" : "";
        // Login never confirms the identity link automatically.
      });
    },
    async confirm() {
      if (!bearer || !["review", "recover"].includes(phase)) return;
      return task(async current => {
        if (!pending) {
          const key = randomKey();
          if (!keyValid(key)) throw new ApiProblem(0, "invalid_key");
          pending = {key};
          persist();
        }
        const proof = {...link};
        const key = pending.key;
        const result = await request(
          "/api/v1/construction/client-user-link-requests/confirm",
          {body: proof, bearer, key},
        );
        if (!current()) return;
        if (!validResult(result)) throw new ApiProblem(0, "invalid_response");
        link = null; pending = null; bearer = ""; challenge = null; email = "";
        persist();
        phase = "done"; message = "done";
      });
    },
    changeEmail() {
      if (closed || busy || !link) return;
      generation += 1;
      bearer = ""; challenge = null; email = ""; phase = "email";
      message = pending ? "pending" : "";
      emit();
    },
    forget() {
      if (closed || busy) return;
      generation += 1;
      link = null; pending = null; bearer = ""; challenge = null; email = "";
      persist();
      phase = "invalid"; message = "cleared";
      emit();
    },
    close() {
      // Preserve the per-tab proof/key for explicit recovery after navigation.
      // Ignore any late response from a request that was already in flight.
      closed = true; generation += 1; busy = false;
      link = null; pending = null; bearer = ""; challenge = null; email = "";
      phase = "closed";
    },
  });
}

// client_link_confirmation_page_v1
