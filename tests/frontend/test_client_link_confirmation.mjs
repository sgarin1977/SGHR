import test from "node:test";
import assert from "node:assert/strict";
import {
  STORAGE_KEY, ApiProblem, createHttpClient, createConfirmationFlow,
} from "../../ui/web/client_link_confirmation/flow.mjs";

const ids = {
  challenge:"11111111-1111-4111-8111-111111111111",
  request:"22222222-2222-4222-8222-222222222222",
  tenant:"33333333-3333-4333-8333-333333333333",
  client:"44444444-4444-4444-8444-444444444444",
  user:"55555555-5555-4555-8555-555555555555",
};
const link = {
  recipient_context:"cclrc2_" + "A".repeat(64),
  token:"ccl1_" + "A".repeat(43),
};
const hash = "#" + new URLSearchParams(link);
const result = {
  request_id:ids.request, tenant_id:ids.tenant, client_id:ids.client,
  linked_user_id:ids.user, client_row_version:7, request_row_version:2,
  consumed_at:"2026-10-29T12:00:00Z", client_changed:true,
};
function memory() {
  const values = new Map();
  return {
    getItem:key => values.get(key) ?? null,
    setItem:(key,value) => values.set(key,value),
    removeItem:key => values.delete(key),
  };
}
function make(options = {}) {
  const calls = [];
  const storage = options.storage || memory();
  let removed = 0, key = 0;
  const request = async (path, args) => {
    calls.push({path,...args});
    const override = await options.request?.(path,args);
    if (override !== undefined) return override;
    if (path.endsWith("/email/request")) return {
      challenge_id:ids.challenge, challenge_type:"otp",
      expires_at:"2026-10-29T12:10:00Z",
    };
    if (path.endsWith("/email/verify")) return {
      access_token:"private-access", refresh_token:"private-refresh",
      token_type:"bearer", expires_in:600,
    };
    if (path.endsWith("/confirm")) return {...result};
    throw Error("Unexpected endpoint");
  };
  const flow = createConfirmationFlow({
    hash: options.hash === undefined ? hash : options.hash,
    storage, replaceUrl:() => {removed += 1;}, request,
    randomKey:() => "same-key-" + key++,
  });
  return {flow,calls,storage,removed:() => removed};
}
async function login(c) {
  await c.flow.sendCode("person@example.com");
  await c.flow.verifyCode("123456","uk");
}

test("fragment is removed before requests; no automatic login or confirm", () => {
  const c = make();
  assert.equal(c.removed(),1);
  assert.equal(c.calls.length,0);
  assert.equal(c.flow.snapshot().phase,"email");
  assert.ok(c.storage.getItem(STORAGE_KEY).includes(link.token));
});

test("malformed, duplicate and extra proof fields are rejected", () => {
  for (const candidate of [
    "#token=x", hash + "&token=" + link.token, hash + "&return_to=https://foreign.example",
  ]) {
    const c = make({hash:candidate});
    assert.equal(c.flow.snapshot().phase,"invalid");
    assert.equal(c.calls.length,0);
  }
});

test("existing Platform OTP returns to explicit review without tenant/me calls", async () => {
  const c = make();
  await login(c);
  assert.equal(c.flow.snapshot().phase,"review");
  assert.deepEqual(c.calls.map(call => call.path),[
    "/api/v1/auth/email/request","/api/v1/auth/email/verify",
  ]);
  assert.deepEqual(c.calls[0].body,{
    email:"person@example.com", challenge_type:"otp",
  });
  const saved = c.storage.getItem(STORAGE_KEY);
  for (const secret of ["private-access","private-refresh","123456"]) {
    assert.ok(!saved.includes(secret));
    assert.ok(!JSON.stringify(c.flow.snapshot()).includes(secret));
  }
});

test("only explicit confirmation sends the two proof fields and mandatory key", async () => {
  const c = make();
  await login(c);
  await c.flow.confirm();
  assert.deepEqual(c.calls.at(-1).body,link);
  assert.equal(c.calls.at(-1).key,"same-key-0");
  assert.equal(c.calls.at(-1).bearer,"private-access");
  assert.equal(c.flow.snapshot().phase,"done");
  assert.equal(c.flow.snapshot().authenticated,false);
  assert.equal(c.storage.getItem(STORAGE_KEY),null);
});

test("uncertain result survives reload and login with original proof/key", async () => {
  const c = make({request:async path => {
    if (path.endsWith("/confirm")) throw new ApiProblem(0,"network");
  }});
  await login(c);
  await c.flow.confirm();
  const first = c.calls.at(-1);
  assert.equal(c.flow.snapshot().pending,true);
  const restored = make({hash:"",storage:c.storage});
  await login(restored);
  assert.equal(restored.flow.snapshot().phase,"recover");
  assert.equal(restored.calls.length,2);
  await restored.flow.confirm();
  assert.deepEqual(restored.calls.at(-1).body,first.body);
  assert.equal(restored.calls.at(-1).key,first.key);
  assert.equal(restored.flow.snapshot().phase,"done");
});

test("reopening original email keeps an unresolved key", async () => {
  const c = make({request:async path => {
    if (path.endsWith("/confirm")) throw new ApiProblem(0,"network");
  }});
  await login(c); await c.flow.confirm();
  const saved = JSON.parse(c.storage.getItem(STORAGE_KEY));
  const reopened = make({storage:c.storage});
  assert.equal(JSON.parse(reopened.storage.getItem(STORAGE_KEY)).pending.key,saved.pending.key);
});

test("401 keeps the pending operation and requires fresh login", async () => {
  const c = make({request:async path => {
    if (path.endsWith("/confirm")) throw new ApiProblem(401,"invalid_token");
  }});
  await login(c); await c.flow.confirm();
  assert.equal(c.flow.snapshot().phase,"email");
  assert.equal(c.flow.snapshot().authenticated,false);
  assert.equal(c.flow.snapshot().pending,true);
  assert.equal(c.calls.filter(call => call.path.endsWith("/confirm")).length,1);
});

test("wrong recipient is rejected by server; no automatic retry or new key", async () => {
  let denied = true;
  const c = make({request:async path => {
    if (path.endsWith("/confirm") && denied) throw new ApiProblem(403,"forbidden");
  }});
  await login(c); await c.flow.confirm();
  const key = c.calls.at(-1).key;
  assert.equal(c.flow.snapshot().message,"unavailable");
  c.flow.changeEmail();
  denied = false;
  await login(c);
  assert.equal(c.calls.filter(call => call.path.endsWith("/confirm")).length,1);
  await c.flow.confirm();
  assert.equal(c.calls.at(-1).key,key);
});

test("version conflict blocks mutation and never generates another key", async () => {
  const c = make({request:async path => {
    if (path.endsWith("/confirm")) throw new ApiProblem(409,"ROW_VERSION_CONFLICT");
  }});
  await login(c); await c.flow.confirm(); await c.flow.confirm();
  assert.equal(c.flow.snapshot().phase,"blocked");
  assert.equal(c.calls.filter(call => call.path.endsWith("/confirm")).length,1);
});

test("double click sends one confirmation", async () => {
  let release;
  const blocked = new Promise(resolve => { release = resolve; });
  const c = make({request:async path => {
    if (path.endsWith("/confirm")) await blocked;
  }});
  await login(c);
  const first = c.flow.confirm(), second = c.flow.confirm();
  release(); await Promise.all([first,second]);
  assert.equal(c.calls.filter(call => call.path.endsWith("/confirm")).length,1);
});

test("late response after page close cannot clear the stored recovery proof", async () => {
  let release;
  const blocked = new Promise(resolve => { release = resolve; });
  const c = make({request:async path => {
    if (path.endsWith("/confirm")) await blocked;
  }});
  await login(c);
  const operation = c.flow.confirm();
  c.flow.close(); release(); await operation;
  assert.equal(c.flow.snapshot().phase,"closed");
  assert.equal(c.flow.snapshot().authenticated,false);
  assert.ok(JSON.parse(c.storage.getItem(STORAGE_KEY)).pending.key);
});

test("wrong OTP stays in existing challenge", async () => {
  let wrong = true;
  const c = make({request:async path => {
    if (path.endsWith("/email/verify") && wrong) {
      throw new ApiProblem(401,"invalid_email_challenge");
    }
  }});
  await c.flow.sendCode("person@example.com");
  await c.flow.verifyCode("wrong");
  assert.equal(c.flow.snapshot().phase,"code");
  wrong = false; await c.flow.verifyCode("123456");
  assert.equal(c.flow.snapshot().phase,"review");
  assert.equal(c.calls.filter(call => call.path.endsWith("/email/request")).length,1);
});

test("storage failure uses memory and does not persist auth tokens elsewhere", async () => {
  const c = make({storage:{
    getItem(){throw Error();},setItem(){throw Error();},removeItem(){throw Error();},
  }});
  assert.equal(c.flow.snapshot().storageOk,false);
  await login(c); await c.flow.confirm();
  assert.equal(c.flow.snapshot().phase,"done");
});

test("invalid success receipt preserves recovery key", async () => {
  const c = make({request:async path => {
    if (path.endsWith("/confirm")) return {...result,request_row_version:1};
  }});
  await login(c); await c.flow.confirm();
  assert.equal(c.flow.snapshot().phase,"recover");
  assert.equal(c.flow.snapshot().pending,true);
});

test("transport uses only approved same-origin POSTs and hides private errors", async () => {
  const calls = [];
  const request = createHttpClient(async (path,options) => {
    calls.push({path,options});
    return {ok:true,status:200,json:async () => ({data:result})};
  });
  await request("/api/v1/construction/client-user-link-requests/confirm",{
    body:link,bearer:"private-access",key:"same-key",
  });
  assert.equal(calls[0].options.method,"POST");
  assert.equal(calls[0].options.credentials,"omit");
  assert.equal(calls[0].options.redirect,"error");
  assert.equal(calls[0].options.referrerPolicy,"no-referrer");
  assert.equal(calls[0].options.headers["Idempotency-Key"],"same-key");
  await assert.rejects(request("https://foreign.example/confirm",{body:link}),ApiProblem);
  await assert.rejects(request("/api/v1/me",{body:{}}),ApiProblem);
  assert.equal(calls.length,1);
  const failed = createHttpClient(async () => ({
    ok:false,status:403,
    json:async () => ({error:{code:"forbidden",message:"private-token"}}),
  }));
  await assert.rejects(
    failed("/api/v1/construction/client-user-link-requests/confirm",{body:link}),
    error => error.code === "forbidden" && !error.message.includes("private-token"),
  );
});

// client_link_confirmation_page_v1
