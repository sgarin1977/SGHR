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
    if (path === "/api/v1/me") value = {id: USER, status: "active"};
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
    if (path.endsWith("/email/verify")) return {
      access_token: "private-access", refresh_token: "private-refresh", token_type: "bearer",
    };
    if (path.endsWith("/me")) return {id: USER, status: "active"};
    if (path.endsWith("/preview")) return {
      id: INVITE, row_version: 3, status: "pending", role: "OBSERVER",
      scopes: [{scope_type: "tenant", scope_id: TENANT}],
      expires_at: new Date(INITIAL + 604800000).toISOString(), grant_expires_at: null,
    };
    if (path.endsWith("/accept")) return {id: INVITE, status: "accepted", row_version: 4};
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
  assert.deepEqual(c.calls.map(call => call.path), ["/api/v1/me", BINDING_PATH]);
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
    const c = setup({request: async path => path === "/api/v1/me" ? me : undefined});
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
