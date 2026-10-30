export const STORAGE_KEY = "sghr.construction.invitation.recipient.v1";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const validVersion = value => Number.isSafeInteger(value) && value > 0;
const linkValid = data => data && typeof data.invitation_context === "string" && data.invitation_context.length > 0 && data.invitation_context.length <= 4096
  && typeof data.token === "string" && data.token.length > 0 && data.token.length <= 1024;
function pendingValid(p) {
  return p && ["accept", "decline"].includes(p.kind) && UUID.test(p.id) && UUID.test(p.userId)
    && validVersion(p.version) && typeof p.key === "string" && p.key.length > 0 && p.key.length <= 255;
}
export class ApiProblem extends Error {
  constructor(status, code) { super("Request unavailable"); this.status = status; this.code = code; }
}
export function createHttpClient(fetchImpl, timeoutMs = 20000) {
  return async function request(path, {body, bearer, key} = {}) {
    if (!/^\/api\/v1\/(auth\/email\/(request|verify)|me|construction\/invitations\/(preview|accept|decline)|platform\/integrations\/telegram\/bindings)$/.test(path)) throw new ApiProblem(0, "invalid_request");
    const headers = {Accept: "application/json"};
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (bearer) headers.Authorization = "Bearer " + bearer;
    if (key) headers["Idempotency-Key"] = key;
    const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetchImpl(path, {method: body === undefined ? "GET" : "POST", headers,
        body: body === undefined ? undefined : JSON.stringify(body), signal: controller.signal,
        credentials: "omit", cache: "no-store", redirect: "error", referrerPolicy: "no-referrer"});
      let value; try { value = await response.json(); } catch { throw new ApiProblem(response.status, "invalid_response"); }
      if (!response.ok) throw new ApiProblem(response.status, value?.error?.code || "request_failed");
      if (!value || typeof value.data !== "object" || value.data === null) throw new ApiProblem(response.status, "invalid_response");
      return value.data;
    } catch (error) { if (error instanceof ApiProblem) throw error; throw new ApiProblem(0, "network"); }
    finally { clearTimeout(timer); }
  };
}
export function createInvitationFlow({hash, storage, replaceUrl, request, randomKey, onChange = () => {}, onAccepted = () => {}}) {
  let link = null, pending = null, bearer = "", userId = null, challenge = null, preview = null;
  let phase = "invalid", busy = false, message = "invalidLink", email = "", storageOk = true;
  const snapshot = () => ({phase, busy, message, email, preview, storageOk, pendingKind: pending?.kind || null,
    authenticated: Boolean(bearer), challengeExpiresAt: challenge?.expires_at || null});
  function emit() { onChange(snapshot()); }
  function persist() {
    try { if (link) storage.setItem(STORAGE_KEY, JSON.stringify({...link, pending})); else storage.removeItem(STORAGE_KEY); }
    catch { storageOk = false; }
  }
  function restore() {
    try {
      if (hash) {
        replaceUrl();
        if (hash.length > 20000) throw new Error();
        const params = new URLSearchParams(hash.replace(/^#/, ""));
        if ([...params.keys()].length !== 2 || params.getAll("invitation_context").length !== 1 || params.getAll("token").length !== 1) throw new Error();
        link = {invitation_context: params.get("invitation_context"), token: params.get("token")};
        if (!linkValid(link)) throw new Error();
        // Reopening the same original link retains an unresolved receipt.
        try { const old = JSON.parse(storage.getItem(STORAGE_KEY));
          if (old?.invitation_context === link.invitation_context && old?.token === link.token && pendingValid(old.pending)) pending = old.pending;
        } catch {}
        persist();
      } else {
        const old = JSON.parse(storage.getItem(STORAGE_KEY));
        if (!linkValid(old) || (old.pending != null && !pendingValid(old.pending))) throw new Error();
        link = {invitation_context: old.invitation_context, token: old.token}; pending = old.pending || null;
      }
      phase = "email"; message = "";
    } catch { link = null; pending = null; persist(); }
  }
  restore();
  function failure(error) {
    if (error.status === 401) { bearer = ""; userId = null; challenge = null; preview = null; phase = "email"; message = "sessionExpired"; }
    else if (error.status === 403) message = "unavailable";
    else if (error.status === 409) message = "conflict";
    else if (error.status === 429) message = "rateLimit";
    else if (error.status === 422) message = "invalidRequest";
    else message = "network";
  }
  async function task(callback) {
    if (busy || !link) return;
    busy = true; message = ""; emit();
    try { await callback(); } catch (error) { failure(error); }
    finally { busy = false; emit(); }
  }
  async function loadPreview() {
    const value = await request("/api/v1/construction/invitations/preview", {body: {...link}, bearer});
    if (!UUID.test(value.id) || !validVersion(value.row_version) || value.status !== "pending" || typeof value.role !== "string"
        || !Array.isArray(value.scopes) || !value.scopes.length || !Number.isFinite(Date.parse(value.expires_at))
        || (value.grant_expires_at !== null && !Number.isFinite(Date.parse(value.grant_expires_at)))
        || value.scopes.some(scope => !["tenant", "project"].includes(scope.scope_type) || !UUID.test(scope.scope_id))) throw new ApiProblem(0, "invalid_response");
    preview = {id: value.id, row_version: value.row_version, role: value.role, scopes: value.scopes,
      expires_at: value.expires_at, grant_expires_at: value.grant_expires_at}; phase = "review";
  }
  return {
    snapshot,
    async sendCode(value) { return task(async () => {
      if (!["email", "code"].includes(phase)) return;
      const candidate = String(value).trim();
      if (candidate.length < 3 || candidate.length > 320) throw new ApiProblem(422, "invalid_email");
      const data = await request("/api/v1/auth/email/request", {body: {email: candidate, challenge_type: "otp"}});
      if (!UUID.test(data.challenge_id) || data.challenge_type !== "otp" || !Number.isFinite(Date.parse(data.expires_at))) throw new ApiProblem(0, "invalid_response");
      email = candidate; challenge = data; phase = "code"; message = "codeSent";
    }); },
    async verifyCode(secret, language = "en") { return task(async () => {
      if (phase !== "code" || !challenge) return;
      if (typeof secret !== "string" || !secret.trim() || secret.length > 512) throw new ApiProblem(422, "invalid_code");
      let tokens;
      try { tokens = await request("/api/v1/auth/email/verify", {body: {challenge_id: challenge.challenge_id, email, secret: secret.trim(), language_code: language}}); }
      catch (error) { if (error.status === 401 && error.code === "invalid_email_challenge") { message = "invalidRequest"; return; } throw error; }
      if (typeof tokens.access_token !== "string" || !tokens.access_token || tokens.token_type?.toLowerCase() !== "bearer") throw new ApiProblem(0, "invalid_response");
      bearer = tokens.access_token;
      const me = await request("/api/v1/me", {bearer});
      if (!UUID.test(me.id) || me.status !== "active") throw new ApiProblem(401, "invalid_identity");
      userId = me.id; challenge = null; phase = "ready";
      if (pending) {
        if (pending.userId !== userId) { bearer = ""; userId = null; phase = "email"; message = "differentAccount"; return; }
        phase = "recover"; message = "pending";
      } else await loadPreview();
    }); },
    async review() { return task(async () => { if (bearer && !pending) await loadPreview(); }); },
    async decide(kind) { return task(async () => {
      if (!bearer || !userId || !["accept", "decline"].includes(kind) || (!preview && !pending)) return;
      if (pending && pending.kind !== kind) return;
      if (!pending) {
        const key = randomKey();
        if (typeof key !== "string" || !key || key.length > 255) throw new ApiProblem(0, "invalid_key");
        pending = {kind, id: preview.id, version: preview.row_version, key, userId}; persist();
      }
      const operation = pending;
      try {
        const result = await request("/api/v1/construction/invitations/" + kind, {bearer,
          body: {...link, expected_row_version: operation.version}, key: kind === "accept" ? operation.key : undefined});
        if (result.id !== operation.id || result.status !== (kind === "accept" ? "accepted" : "declined")
            || result.row_version !== operation.version + 1) throw new ApiProblem(0, "invalid_response");
        const acceptedSession = kind === "accept" ? Object.freeze({bearer, userId}) : null;
        link = null; pending = null; preview = null; bearer = ""; userId = null; persist();
        phase = "done"; message = kind === "accept" ? "accepted" : "declined";
        // This observer cannot change the already confirmed invitation result.
        // Credentials are handed to the optional in-memory Platform component.
        if (acceptedSession) { try { onAccepted(acceptedSession); } catch {} }
      } catch (error) {
        if (error.status === 409 && error.code === "ROW_VERSION_CONFLICT") { pending = null; preview = null; phase = "ready"; persist(); }
        // All ambiguous outcomes keep the original payload and accept key.
        throw error;
      }
    }); },
    changeEmail() { if (busy || !link) return; bearer = ""; userId = null; challenge = null; preview = null; email = ""; phase = "email"; message = ""; emit(); },
    forget() { if (busy) return; link = null; pending = null; bearer = ""; userId = null; challenge = null; preview = null; email = ""; persist(); phase = "invalid"; message = "cleared"; emit(); },
  };
}

// invitation_telegram_onboarding_v1
