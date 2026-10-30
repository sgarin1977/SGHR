import test from "node:test";
import assert from "node:assert/strict";
import {createInvitationFlow, createHttpClient, ApiProblem, STORAGE_KEY}
  from "../../ui/web/invitation_recipient/flow.mjs";
import {createTelegramBinding, BINDING_PATH, LABELS}
  from "../../ui/web/invitation_recipient/telegram-binding.mjs";

const USER = "00000000-0000-0000-0000-000000000002";
const OTHER = "00000000-0000-0000-0000-000000000003";
const BINDING = "00000000-0000-0000-0000-000000000005";
const CODE = "A".repeat(43);
const INITIAL = Date.UTC(2026, 9, 28, 12);
const INVITE = "00000000-0000-0000-0000-000000000001";
const TENANT = "00000000-0000-0000-0000-000000000004";

function receipt(now) {
  return {
    binding_id: BINDING, row_version: 1, code: CODE,
    issued_at: new Date(now).toISOString(),
    expires_at: new Date(now + 900000).toISOString(),
    deep_link: "https://t.me/SGHR_TEST_bot?start=bind_" + CODE,
  };
}

function setup(options = {}) {
  let clock = INITIAL;
  const calls = [], keys = [], receipts = new Map();
  const request = async (path, inputs) => {
    calls.push({path, ...inputs});
    let value;
    if (path === "/api/v1/platform/identity") value = {id: USER, status: "active"};
    else if (path === "/api/v1/platform/organizations/session") value = {
      user_id: USER, tenant_id: TENANT, access_token: "private-selected",
      refresh_token: "private-selected-refresh", token_type: "bearer", expires_in: 900,
    };
    else if (path === BINDING_PATH) {
      if (!receipts.has(inputs.key)) receipts.set(inputs.key, receipt(clock));
      value = structuredClone(receipts.get(inputs.key));
    } else throw Error("Unexpected request");
    if (options.request) {
      const replacement = await options.request(path, inputs, value);
      if (replacement !== undefined) return replacement;
    }
    return value;
  };
  const controller = createTelegramBinding({
    request, now: () => clock,
    randomKey: () => { const key = "key_" + keys.length; keys.push(key); return key; },
  });
  return {
    controller, calls, keys,
    attach() { controller.acceptSession({bearer: "private-access", userId: USER}); },
    advance(ms) { clock += ms; },
  };
}

function invitation(onAccepted) {
  const records = new Map(), calls = [];
  let logins = 0;
  const storage = {
    getItem: key => records.get(key) || null,
    setItem: (key, value) => records.set(key, value),
    removeItem: key => records.delete(key),
  };
  const request = async (path, inputs) => {
    calls.push({path, ...inputs});
    if (path.endsWith("/email/request")) return {
      challenge_id: OTHER, challenge_type: "otp", expires_at: new Date(INITIAL + 600000).toISOString(),
    };
    if (path.endsWith("/email/verify")) {
      logins++;
      return {
        access_token: logins === 1 ? "private-access" : "private-access-" + logins,
        refresh_token: logins === 1 ? "private-refresh" : "private-refresh-" + logins,
        token_type: "bearer",
      };
    }
    if (path.endsWith("/identity")) return {id: USER, status: "active"};
    if (path.endsWith("/preview")) return {
      id: INVITE, row_version: 3, status: "pending", role: "OBSERVER",
      scopes: [{scope_type: "tenant", scope_id: TENANT}],
      expires_at: new Date(INITIAL + 604800000).toISOString(), grant_expires_at: null,
    };
    if (path.endsWith("/accept")) return {data: {id: INVITE, status: "accepted", row_version: 4}, meta: {organization_id: TENANT}};
    if (path.endsWith("/decline")) return {id: INVITE, status: "declined", row_version: 4};
    throw Error("Unexpected request");
  };
  const flow = createInvitationFlow({
    hash: "#invitation_context=private-context&token=private-invitation",
    storage, replaceUrl() {}, request, randomKey: () => "invitation-key", onAccepted,
  });
  return {flow, storage, calls};
}

async function login(c) {
  await c.flow.sendCode("invited@example.test");
  await c.flow.verifyCode("123456");
}

test("accept offers binding without issuing a link or persisting credentials", async () => {
  const c = setup();
  const i = invitation(session => c.controller.acceptSession(session));
  await login(i);
  await i.flow.decide("accept");
  assert.equal(i.flow.snapshot().message, "accepted");
  assert.equal(i.flow.snapshot().authenticated, false);
  assert.equal(i.storage.getItem(STORAGE_KEY), null);
  assert.equal(c.controller.snapshot().phase, "ready");
  assert.equal(c.calls.length, 0);
  assert.equal(c.controller.getLink(), null);
});

test("decline never offers binding", async () => {
  let accepted = 0;
  const i = invitation(() => accepted++);
  await login(i); await i.flow.decide("decline");
  assert.equal(accepted, 0);
  assert.equal(i.flow.snapshot().message, "declined");
});

test("binding observer failure cannot turn accepted invitation into a failure", async () => {
  const i = invitation(() => { throw Error("Observer failed"); });
  await login(i); await i.flow.decide("accept");
  assert.equal(i.flow.snapshot().phase, "done");
  assert.equal(i.flow.snapshot().message, "accepted");
  assert.equal(i.storage.getItem(STORAGE_KEY), null);
});

test("explicit issue uses fresh canonical identity and the existing empty-body POST", async () => {
  const c = setup(); c.attach();
  await c.controller.issue();
  assert.deepEqual(c.calls.map(call => call.path), ["/api/v1/platform/identity", BINDING_PATH]);
  const call = c.calls.at(-1);
  assert.deepEqual(call.body, {});
  assert.equal(call.bearer, "private-access");
  assert.equal(call.key, "web-tg-binding:key_0");
  assert.equal(c.controller.snapshot().phase, "issued");
  assert.equal(c.controller.getLink(), receipt(INITIAL).deep_link);
  const publicState = JSON.stringify(c.controller.snapshot());
  for (const secret of ["private-access", CODE, "web-tg-binding:key_0"]) {
    assert.equal(publicState.includes(secret), false);
  }
});

test("unknown outcome retries the original key and preserves original expiry", async () => {
  let first = true;
  const c = setup({request: async path => {
    if (path === BINDING_PATH && first) { first = false; throw new ApiProblem(0, "network"); }
  }});
  c.attach(); await c.controller.issue();
  const original = structuredClone(c.calls.at(-1));
  assert.equal(c.controller.snapshot().phase, "pending");
  c.advance(600000);
  await c.controller.issue();
  assert.deepEqual(c.calls.at(-1), original);
  assert.equal(c.controller.snapshot().expiresAt, receipt(INITIAL).expires_at);
  assert.equal(c.keys.length, 1);
});

test("expired historical receipt does not renew itself", async () => {
  let first = true;
  const c = setup({request: async path => {
    if (path === BINDING_PATH && first) { first = false; throw new ApiProblem(0, "network"); }
  }});
  c.attach(); await c.controller.issue();
  c.advance(900001);
  await c.controller.issue();
  assert.equal(c.controller.snapshot().phase, "expired");
  assert.equal(c.controller.snapshot().expiresAt, receipt(INITIAL).expires_at);
  assert.equal(c.controller.getLink(), null);
  assert.equal(c.keys.length, 1);
});

test("known expired receipt permits a new operation only after another explicit click", async () => {
  const c = setup(); c.attach(); await c.controller.issue();
  c.advance(900001);
  const before = c.calls.length;
  assert.equal(c.controller.snapshot().phase, "expired");
  assert.equal(c.controller.getLink(), null);
  assert.equal(c.calls.length, before);
  await c.controller.issue();
  assert.equal(c.keys.length, 2);
  assert.equal(c.calls.at(-1).key, "web-tg-binding:key_1");
  assert.equal(c.controller.snapshot().phase, "issued");
});

test("unresolved operation cannot silently restart after the replay window", async () => {
  const c = setup({request: async path => {
    if (path === BINDING_PATH) throw new ApiProblem(0, "network");
  }});
  c.attach(); await c.controller.issue();
  c.advance(86400000);
  const before = c.calls.length;
  await c.controller.issue();
  assert.equal(c.calls.length, before);
  assert.equal(c.keys.length, 1);
  assert.equal(c.controller.snapshot().message, "window");
  assert.equal(c.controller.snapshot().canIssue, false);
});

test("double click sends one issuance", async () => {
  let release;
  const waiting = new Promise(resolve => { release = resolve; });
  const c = setup({request: async path => { if (path === BINDING_PATH) await waiting; }});
  c.attach();
  const first = c.controller.issue(), second = c.controller.issue();
  release(); await Promise.all([first, second]);
  assert.equal(c.calls.filter(call => call.path === BINDING_PATH).length, 1);
});

test("changed or inactive identity is denied before issuance", async () => {
  for (const me of [{id: OTHER, status: "active"}, {id: USER, status: "blocked"}]) {
    const c = setup({request: async path => path === "/api/v1/platform/identity" ? me : undefined});
    c.attach(); await c.controller.issue();
    assert.equal(c.calls.filter(call => call.path === BINDING_PATH).length, 0);
    assert.equal(c.controller.snapshot().message, "auth");
    assert.equal(c.controller.snapshot().canIssue, false);
  }
});

test("closing during issuance ignores the late response and clears the link", async () => {
  let release;
  const waiting = new Promise(resolve => { release = resolve; });
  let started;
  const entered = new Promise(resolve => { started = resolve; });
  const c = setup({request: async path => {
    if (path === BINDING_PATH) { started(); await waiting; }
  }});
  c.attach(); const pending = c.controller.issue();
  await entered; c.controller.close(); release(); await pending;
  assert.equal(c.controller.snapshot().active, false);
  assert.equal(c.controller.getLink(), null);
});

test("invalid links, versions and TTL never become usable links", async () => {
  const changes = [
    {deep_link: "https://foreign.example/link"},
    {deep_link: "https://t.me/SGHR_TEST_bot?start=wrong"},
    {row_version: true},
    {expires_at: new Date(INITIAL + 1800000).toISOString()},
    {issued_at: "invalid"},
  ];
  for (const change of changes) {
    const c = setup({request: async (path, inputs, value) => (
      path === BINDING_PATH ? {...value, ...change} : undefined
    )});
    c.attach(); await c.controller.issue();
    assert.equal(c.controller.getLink(), null);
    assert.equal(c.controller.snapshot().message, "unavailable");
  }
});

test("shared HTTP transport permits only the exact existing binding path", async () => {
  const calls = [];
  const request = createHttpClient(async (path, options) => {
    calls.push({path, options});
    return {ok: true, status: 201, json: async () => ({data: receipt(INITIAL)})};
  });
  await request(BINDING_PATH, {body: {}, bearer: "private-access", key: "original-key"});
  assert.equal(calls[0].path, BINDING_PATH);
  assert.equal(calls[0].options.body, "{}");
  assert.equal(calls[0].options.headers.Authorization, "Bearer private-access");
  assert.equal(calls[0].options.headers["Idempotency-Key"], "original-key");
  assert.equal(calls[0].options.credentials, "omit");
  assert.equal(calls[0].options.referrerPolicy, "no-referrer");
  assert.equal(calls[0].options.redirect, "error");
  await assert.rejects(request("https://foreign.example/path"), ApiProblem);
  await assert.rejects(request(BINDING_PATH + "?tenant_id=" + TENANT), ApiProblem);
  assert.equal(calls.length, 1);
});

test("all binding labels provide seven languages", () => {
  assert.ok(Object.values(LABELS).every(values => (
    values.length === 7 && values.every(value => typeof value === "string" && value.length > 0)
  )));
});

// invitation_identity_only_v1


const SESSION_PATH = "/api/v1/platform/organizations/session";

async function acceptedBinding(options = {}) {
  const c = setup(options);
  c.invitation = invitation(session => c.controller.acceptSession(session));
  await login(c.invitation);
  await c.invitation.flow.decide("accept");
  return c;
}

test("accepted recipient selects its organization before the empty-body binding POST", async () => {
  const c = await acceptedBinding();
  assert.equal(c.calls.length, 0);
  await c.controller.issue();
  assert.deepEqual(c.calls.map(call => call.path),
    ["/api/v1/platform/identity", SESSION_PATH, BINDING_PATH]);
  assert.deepEqual(c.calls[1].body, {tenant_id: TENANT, refresh_token: "private-refresh"});
  assert.equal(c.calls[1].bearer, "private-access");
  assert.deepEqual(c.calls[2].body, {});
  assert.equal(c.calls[2].bearer, "private-selected");
  assert.equal(c.controller.snapshot().phase, "issued");
  assert.equal(c.invitation.storage.getItem(STORAGE_KEY), null);
  const visible = JSON.stringify([c.controller.snapshot(), c.invitation.flow.snapshot()]);
  for (const secret of ["private-access", "private-refresh", "private-selected"]) {
    assert.equal(visible.includes(secret), false);
  }
});

test("uncertain selection signs in again without accepting or previewing again", async () => {
  let first = true;
  const c = await acceptedBinding({request: async path => {
    if (path === SESSION_PATH && first) { first = false; throw new ApiProblem(0, "network"); }
  }});
  await c.controller.issue();
  assert.equal(c.controller.snapshot().phase, "auth");
  assert.equal(c.calls.filter(call => call.path === BINDING_PATH).length, 0);
  assert.equal(c.invitation.flow.reauthenticateAccepted(), true);
  c.controller.pauseForAuthentication();
  await login(c.invitation);
  await c.controller.issue();
  assert.equal(c.controller.snapshot().phase, "issued");
  const selections = c.calls.filter(call => call.path === SESSION_PATH);
  assert.equal(selections[1].body.refresh_token, "private-refresh-2");
  assert.equal(c.invitation.calls.filter(call => call.path.endsWith("/accept")).length, 1);
  assert.equal(c.invitation.calls.filter(call => call.path.endsWith("/preview")).length, 1);
  assert.equal(c.keys.length, 1);
});

test("reauthentication retains the original unresolved binding key and expiry", async () => {
  let lost = true, expired = false;
  const c = await acceptedBinding({request: async path => {
    if (path === BINDING_PATH && lost) { lost = false; throw new ApiProblem(0, "network"); }
    if (path === "/api/v1/platform/identity" && expired) throw new ApiProblem(401, "invalid_token");
  }});
  await c.controller.issue();
  const original = c.calls.at(-1);
  expired = true; await c.controller.issue();
  assert.equal(c.controller.snapshot().phase, "auth");
  c.invitation.flow.reauthenticateAccepted(); c.controller.pauseForAuthentication();
  expired = false; await login(c.invitation);
  c.advance(900001); await c.controller.issue();
  assert.equal(c.calls.at(-1).key, original.key);
  assert.deepEqual(c.calls.at(-1).body, original.body);
  assert.equal(c.keys.length, 1);
  assert.equal(c.controller.snapshot().phase, "expired");
  assert.equal(c.controller.getLink(), null);
});

test("an auth continuation cannot switch to a different Platform user", async () => {
  const c = await acceptedBinding({request: async path => {
    if (path === SESSION_PATH) throw new ApiProblem(403, "forbidden");
  }});
  await c.controller.issue();
  assert.throws(() => c.controller.acceptSession({
    bearer: "other-access", userId: OTHER, tenantId: TENANT, refreshToken: "other-refresh",
  }), ApiProblem);
  assert.equal(c.controller.getLink(), null);
  assert.equal(c.invitation.flow.snapshot().message, "accepted");
});

test("selection rate limit retries the same binding attempt", async () => {
  let first = true;
  const c = await acceptedBinding({request: async path => {
    if (path === SESSION_PATH && first) { first = false; throw new ApiProblem(429, "RATE_LIMIT_EXCEEDED"); }
  }});
  await c.controller.issue();
  assert.equal(c.controller.snapshot().message, "rate");
  await c.controller.issue();
  assert.equal(c.controller.snapshot().phase, "issued");
  assert.equal(c.keys.length, 1);
});

test("closing ignores a late selected session", async () => {
  let release, entered;
  const wait = new Promise(resolve => { release = resolve; });
  const started = new Promise(resolve => { entered = resolve; });
  const c = await acceptedBinding({request: async path => {
    if (path === SESSION_PATH) { entered(); await wait; }
  }});
  const pending = c.controller.issue();
  await started; c.controller.close(); release(); await pending;
  assert.equal(c.controller.snapshot().active, false);
  assert.equal(c.calls.filter(call => call.path === BINDING_PATH).length, 0);
});

test("a mismatched session response cannot issue a binding", async () => {
  for (const change of [{user_id: OTHER}, {tenant_id: OTHER}, {access_token: ""},
      {token_type: "other"}, {expires_in: 0}]) {
    const c = await acceptedBinding({request: async (path, inputs, value) =>
      path === SESSION_PATH ? {...value, ...change} : undefined});
    await c.controller.issue();
    assert.equal(c.controller.snapshot().phase, "auth");
    assert.equal(c.calls.filter(call => call.path === BINDING_PATH).length, 0);
  }
});

test("the shared transport retains acceptance metadata and bounds the session path", async () => {
  const calls = [];
  const request = createHttpClient(async (path, options) => {
    calls.push({path, options});
    return {ok: true, status: 200, json: async () => ({
      data: {id: INVITE}, meta: {organization_id: TENANT},
    })};
  });
  const result = await request("/api/v1/construction/invitations/accept", {
    body: {}, includeMeta: true, bearer: "private-access",
  });
  assert.equal(result.meta.organization_id, TENANT);
  await request(SESSION_PATH, {
    body: {tenant_id: TENANT, refresh_token: "private-refresh"}, bearer: "private-access",
  });
  assert.equal(calls.at(-1).options.credentials, "omit");
  assert.equal(calls.at(-1).options.cache, "no-store");
  await assert.rejects(request(SESSION_PATH + "?tenant_id=" + TENANT), ApiProblem);
  assert.equal(calls.length, 2);
});

test("sign-in cannot extend the unresolved binding replay window", async () => {
  const c = await acceptedBinding({request: async path => {
    if (path === SESSION_PATH) throw new ApiProblem(0, "network");
  }});
  await c.controller.issue(); c.advance(86400000);
  c.invitation.flow.reauthenticateAccepted(); c.controller.pauseForAuthentication();
  await login(c.invitation);
  const before = c.calls.length;
  await c.controller.issue();
  assert.equal(calls.length, before);
  assert.equal(c.controller.snapshot().message, "window");
});

// invitation_binding_session_v1
