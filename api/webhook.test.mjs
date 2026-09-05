import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import test from "node:test"

// Keep the Worker as .js without adding package.json or a Cloudflare dependency.
const source = await readFile(new URL("./webhook.js", import.meta.url), "utf8")
const { default: worker, WebhookState } = await import(
  `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`
)
const TTL = 7 * 24 * 60 * 60 * 1000
const MAX_BODY = 64 * 1024
const SECRET = "offline_webhook_secret"
const TOKEN = "123456:offline_bot_token"
const PAT = "offline_github_pat"

class FakeStorage {
  constructor(snapshot = { data: new Map(), alarm: null }) {
    this.data = structuredClone(snapshot.data)
    this.alarmTime = snapshot.alarm
    this.queue = Promise.resolve()
    this.listCalls = []
    this.deleteCalls = []
    this.syncCalls = 0
  }

  snapshot() {
    return structuredClone({ data: this.data, alarm: this.alarmTime })
  }

  async transaction(callback) {
    const previous = this.queue
    const unlocked = Promise.withResolvers()
    this.queue = unlocked.promise
    await previous
    const draft = this.snapshot()
    this.active = draft
    const tx = {
      get: async key => {
        await Promise.resolve()
        return structuredClone(draft.data.get(key))
      },
      put: async (key, value) => {
        await Promise.resolve()
        if (this.failPut?.(key, value)) throw new Error(`storage failed ${PAT}`)
        draft.data.set(key, structuredClone(value))
      },
      delete: async keys => {
        await Promise.resolve()
        assert.ok(Array.isArray(keys) && keys.length <= 128)
        this.deleteCalls.push(keys)
        let deleted = 0
        for (const key of keys) deleted += Number(draft.data.delete(key))
        return deleted
      },
      list: async options => {
        await Promise.resolve()
        assert.ok(options.limit > 0 && options.limit <= 64, "all scans must be bounded")
        this.listCalls.push(options)
        return new Map([...draft.data.entries()]
          .sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)
          .filter(([key]) => (!options.prefix || key.startsWith(options.prefix)) &&
            (!options.end || key < options.end))
          .slice(0, options.limit)
          .map(([key, value]) => [key, structuredClone(value)]))
      }
    }
    try {
      const result = await callback(tx)
      this.data = draft.data
      this.alarmTime = draft.alarm
      return result
    } finally {
      this.active = undefined
      unlocked.resolve()
    }
  }

  async setAlarm(time) {
    assert.ok(this.active, "alarm scheduling must share the SQLite transaction")
    this.active.alarm = time
  }

  async deleteAlarm() {
    assert.ok(this.active)
    this.active.alarm = null
  }

  async sync() {
    this.syncCalls++
    if (this.failSync) throw new Error(`interrupted ${TOKEN}`)
  }
}

function update(id = 1, text = "/noticias", chat = -123, user = 42) {
  return { update_id: id, message: { chat: { id: chat }, from: { id: user }, text } }
}

function harness(t, overrides = {}) {
  const h = {
    now: 1_800_000_000_000,
    storage: new FakeStorage(),
    calls: [],
    objectCalls: 0,
    github: () => new Response(null, { status: 204 }),
    telegram: () => Response.json({ ok: true }),
    env: {
      TELEGRAM_WEBHOOK_SECRET: SECRET,
      TELEGRAM_TOKEN_ENV: TOKEN,
      TELEGRAM_CHAT_ID_ENV: "-123",
      GH_PAT_ENV: PAT,
      ...overrides
    }
  }
  t.mock.method(Date, "now", () => h.now)
  t.mock.method(globalThis, "fetch", async (url, init) => {
    const service = new URL(url).hostname
    assert.ok(service === "api.github.com" || service === "api.telegram.org", "no real network")
    const call = { url, init, body: JSON.parse(init.body), service }
    h.calls.push(call)
    assert.equal(init.method, "POST")
    assert.equal(init.redirect, "manual")
    assert.ok(init.signal instanceof AbortSignal)
    return await (service === "api.github.com" ? h.github(call) : h.telegram(call))
  })
  h.object = new WebhookState({ storage: h.storage }, h.env)
  h.env.WEBHOOK_STATE = {
    idFromName(name) {
      assert.equal(name, "telegram-webhook")
      return name
    },
    get(id) {
      assert.equal(id, "telegram-webhook")
      return {
        fetch(request) {
          h.objectCalls++
          assert.equal(request.headers.get("X-Telegram-Bot-Api-Secret-Token"), null)
          return h.object.fetch(request)
        }
      }
    }
  }
  h.restart = () => {
    h.storage = new FakeStorage(h.storage.snapshot())
    h.object = new WebhookState({ storage: h.storage }, h.env)
  }
  h.send = (body = update(), options = {}) => {
    const headers = new Headers({
      "Content-Type": "application/json",
      "X-Telegram-Bot-Api-Secret-Token": SECRET
    })
    for (const [key, value] of Object.entries(options.headers || {})) {
      if (value === null) headers.delete(key)
      else headers.set(key, value)
    }
    return worker.fetch(new Request("https://bot.example/webhook", {
      method: "POST",
      headers,
      body: body instanceof ReadableStream || typeof body === "string" ? body : JSON.stringify(body),
      duplex: "half"
    }), h.env)
  }
  h.count = service => h.calls.filter(call => call.service === `api.${service}.org` ||
    call.service === `api.${service}.com`).length
  return h
}

async function result(response, code = 200) {
  assert.equal(response.status, code)
  assert.equal(response.headers.get("Cache-Control"), "no-store")
  const text = await response.text()
  for (const secret of [SECRET, TOKEN, PAT]) assert.ok(!text.includes(secret), "response leaks secret")
  return JSON.parse(text)
}

function seed(storage, count, expiresAt, status = "handled") {
  for (let i = 0; i < count; i++) {
    const key = `update:${i}`
    storage.data.set(key, { status, expiresAt, retryAfter: 0 })
    storage.data.set(`expiry:${String(expiresAt).padStart(16, "0")}:${i}`, key)
  }
  storage.data.set("meta", { count, cooldownUntil: 0 })
  storage.alarmTime = expiresAt
}

test("missing/invalid webhook authentication never reads the body or contacts a service", async t => {
  for (const secret of [null, "wrong", SECRET + "x", SECRET.slice(0, -1), ""]) {
    await t.test(String(secret), async t => {
      const h = harness(t)
      const response = await h.send("not json", { headers: { "X-Telegram-Bot-Api-Secret-Token": secret } })
      assert.equal((await result(response, 401)).status, "unauthorized")
      assert.equal(h.objectCalls, 0)
      assert.equal(h.calls.length, 0)
      assert.equal(h.storage.data.size, 0)
    })
  }
})

test("missing or invalid configuration fails closed", async t => {
  const cases = [
    ["TELEGRAM_WEBHOOK_SECRET", undefined], ["TELEGRAM_WEBHOOK_SECRET", ""],
    ["TELEGRAM_WEBHOOK_SECRET", "bad secret"], ["TELEGRAM_WEBHOOK_SECRET", "a".repeat(257)],
    ["TELEGRAM_TOKEN_ENV", undefined], ["TELEGRAM_TOKEN_ENV", " "],
    ["GH_PAT_ENV", undefined], ["GH_PAT_ENV", 123],
    ["TELEGRAM_CHAT_ID_ENV", undefined], ["TELEGRAM_CHAT_ID_ENV", "@channel"],
    ["TELEGRAM_CHAT_ID_ENV", "0"], ["TELEGRAM_CHAT_ID_ENV", "9007199254740992"],
    ["TELEGRAM_USER_ID_ENV", ""], ["TELEGRAM_USER_ID_ENV", "-42"],
    ["WEBHOOK_STATE", undefined], ["WEBHOOK_STATE", {}]
  ]
  for (const [key, value] of cases) {
    await t.test(`${key}=${String(value)}`, async t => {
      const h = harness(t)
      h.env[key] = value
      const response = await h.send()
      assert.equal(response.headers.get("Retry-After"), "60")
      assert.equal((await result(response, 503)).status, "unavailable")
      assert.equal(h.objectCalls, 0)
      assert.equal(h.calls.length, 0)
    })
  }
})

test("health checks have no side effects and unsupported methods return 405", async t => {
  const h = harness(t)
  for (const method of ["GET", "HEAD", "PUT", "DELETE"]) {
    const response = await worker.fetch(new Request("https://bot.example", { method }), {})
    await result(response, method === "GET" || method === "HEAD" ? 200 : 405)
    if (response.status === 405) assert.equal(response.headers.get("Allow"), "GET, HEAD, POST")
  }
  assert.equal(h.calls.length, 0)
})

test("body limit counts streamed bytes, not Content-Length, and cancels excess input", async t => {
  for (const declared of [null, "1"]) {
    await t.test(`Content-Length ${declared}`, async t => {
      const h = harness(t)
      let cancelled = false
      const stream = new ReadableStream({
        pull(controller) { controller.enqueue(new Uint8Array(16 * 1024)) },
        cancel() { cancelled = true }
      })
      const response = await h.send(stream, { headers: { "Content-Length": declared } })
      assert.equal((await result(response, 413)).status, "body_too_large")
      assert.equal(cancelled, true)
      assert.equal(h.objectCalls, 0)
    })
  }
})

test("exactly 64 KiB is accepted; larger declared lengths and multibyte bodies are rejected", async t => {
  const h = harness(t)
  const json = JSON.stringify(update())
  assert.equal((await result(await h.send(json.padEnd(MAX_BODY)))).status, "dispatched")
  await result(await h.send(json, { headers: { "Content-Length": String(MAX_BODY + 1) } }), 413)
  await result(await h.send(JSON.stringify({ padding: "\u00e9".repeat(MAX_BODY / 2) })), 413)
  assert.equal(h.count("github"), 1)
})

test("invalid JSON, invalid UTF-8 and stream failures are sanitized", async t => {
  const h = harness(t)
  for (const body of ["", "{", "undefined"]) await result(await h.send(body), 400)
  const utf8 = new ReadableStream({ start(c) { c.enqueue(new Uint8Array([255])); c.close() } })
  await result(await h.send(utf8), 400)
  const broken = new ReadableStream({ start(c) { c.error(new Error(TOKEN)) } })
  await result(await h.send(broken), 400)
  await result(await h.send(update(), { headers: { "Content-Type": "text/plain" } }), 415)
  assert.equal(h.calls.length, 0)
})

test("a stalled input stream times out and is cancelled", async t => {
  const h = harness(t)
  t.mock.timers.enable({ apis: ["setTimeout"] })
  let cancelled = false
  const pending = h.send(new ReadableStream({ cancel() { cancelled = true } }))
  t.mock.timers.tick(10_000)
  assert.equal((await result(await pending, 408)).status, "request_timeout")
  assert.equal(cancelled, true)
  assert.equal(h.calls.length, 0)
})

test("update_id, chat, text and sender types are validated", async t => {
  const malformed = [null, [], true, 1, "string", {},
    ...[null, "1", -1, 1.5, Number.MAX_SAFE_INTEGER + 1].map(update_id => ({ ...update(), update_id })),
    ...[null, [], "message", {}, { chat: null }, { chat: [] }].map(message => ({ update_id: 1, message })),
    ...[null, "-123", 0, 1.5, Number.MAX_SAFE_INTEGER + 1].map(id => update(1, "/noticias", id)),
    ...[null, 12, {}, [], true, "a".repeat(4097)].map(text => update(1, text)),
    ...[null, [], { id: "42" }, { id: -1 }, { id: 1.5 }].map(from => ({
      ...update(), message: { ...update().message, from }
    }))
  ]
  const h = harness(t)
  for (const body of malformed) {
    const raw = typeof body === "string" ? JSON.stringify(body) : body
    await result(await h.send(raw), 400)
  }
  assert.equal(h.objectCalls, 0)
  assert.equal(h.calls.length, 0)
})

test("valid non-text updates are acknowledged without dispatch", async t => {
  const h = harness(t)
  for (const body of [
    { update_id: 0 }, { update_id: 3, callback_query: {} },
    { update_id: 4, message: { chat: { id: -123 }, photo: [] } }, update(5, "  ")
  ]) assert.equal((await result(await h.send(body))).status, "ignored")
  assert.equal(h.objectCalls, 0)
  assert.equal(h.calls.length, 0)
})

test("chat authorization and optional user restriction have no unauthorized notifications", async t => {
  const h = harness(t)
  await result(await h.send(update(1, "/noticias", 999)), 403)
  h.env.TELEGRAM_USER_ID_ENV = "42"
  await result(await h.send(update(1, "/noticias", -123, 99)), 403)
  const missingSender = update()
  delete missingSender.message.from
  await result(await h.send(missingSender), 403)
  assert.equal(h.calls.length, 0)
  assert.equal(h.objectCalls, 0)
  assert.equal((await result(await h.send())).status, "dispatched")
})

test("sequential duplicates dispatch and notify once, including after a restart and rotation", async t => {
  const h = harness(t)
  assert.equal((await result(await h.send())).status, "dispatched")
  assert.deepEqual(await result(await h.send()), { status: "dispatched", duplicate: true })
  h.restart()
  h.env.TELEGRAM_WEBHOOK_SECRET = "rotated_secret"
  assert.deepEqual(await result(await h.send(update(1, "/help"), {
    headers: { "X-Telegram-Bot-Api-Secret-Token": "rotated_secret" }
  })), { status: "dispatched", duplicate: true })
  assert.equal(h.count("github"), 1)
  assert.equal(h.count("telegram"), 1)
  const github = h.calls[0]
  assert.equal(github.url, "https://api.github.com/repos/DevCop95/dev101_bot/actions/workflows/bot.yml/dispatches")
  assert.equal(github.init.headers["User-Agent"], "dev101-bot-webhook")
  assert.equal(github.init.headers.Authorization, `Bearer ${PAT}`)
  assert.deepEqual(github.body, { ref: "main" })
  assert.equal(h.calls[1].body.chat_id, "-123")
})

test("simultaneous duplicates see a durable claim while dispatch is still awaiting HTTP", async t => {
  const h = harness(t)
  const entered = Promise.withResolvers()
  const release = Promise.withResolvers()
  h.github = async () => {
    assert.equal(h.storage.data.get("update:1").status, "processing")
    assert.equal(h.storage.data.get("meta").cooldownUntil, h.now + 60_000)
    assert.equal(h.storage.syncCalls, 1)
    entered.resolve()
    await release.promise
    return new Response(null, { status: 204 })
  }
  const first = h.send()
  await entered.promise
  const duplicates = await Promise.all(Array.from({ length: 20 }, () => h.send()))
  for (const response of duplicates) {
    assert.deepEqual(await result(response), { status: "uncertain", duplicate: true })
  }
  assert.equal(h.count("github"), 1)
  assert.equal(h.count("telegram"), 0)
  release.resolve()
  assert.equal((await result(await first)).status, "dispatched")
  assert.equal(h.count("telegram"), 1)
})

test("a cold burst of identical updates has exactly one dispatch owner", async t => {
  const h = harness(t)
  const responses = await Promise.all(Array.from({ length: 20 }, () => h.send()))
  const bodies = await Promise.all(responses.map(response => result(response)))
  assert.equal(bodies.filter(body => !body.duplicate).length, 1)
  assert.equal(h.storage.data.get("meta").count, 1)
  assert.equal(h.count("github"), 1)
  assert.equal(h.count("telegram"), 1)
})

test("different concurrent updates reserve cooldown atomically and rate-limited retries stay consumed", async t => {
  const h = harness(t)
  const responses = await Promise.all([h.send(update(10)), h.send(update(11))])
  const bodies = await Promise.all(responses.map(response => result(response)))
  assert.deepEqual(bodies.map(body => body.status).sort(), ["dispatched", "rate_limited"])
  const limitedIndex = bodies.findIndex(body => body.status === "rate_limited")
  const limitedId = limitedIndex + 10
  assert.equal(bodies[limitedIndex].retry_after, 60)
  assert.equal(responses[limitedIndex].headers.get("Retry-After"), null)
  h.restart()
  assert.equal((await result(await h.send(update(12)))).status, "rate_limited")
  h.now += 60_000
  assert.deepEqual(await result(await h.send(update(limitedId))), { status: "rate_limited", duplicate: true })
  assert.equal((await result(await h.send(update(13)))).status, "dispatched")
  assert.equal(h.count("github"), 2)
  assert.equal(h.count("telegram"), 4)
})

test("help, start and unknown commands are deduplicated without consuming news cooldown", async t => {
  const h = harness(t)
  for (const [i, text] of ["/help", "/start", "/unknown"].entries()) {
    assert.equal((await result(await h.send(update(i, text)))).status, "handled")
    assert.deepEqual(await result(await h.send(update(i, text))), { status: "handled", duplicate: true })
  }
  assert.equal((await result(await h.send(update(3)))).status, "dispatched")
  assert.equal(h.count("github"), 1)
  assert.equal(h.count("telegram"), 4)
})

test("definitive dispatch rejection is terminal, sanitized, and still reserves cooldown", async t => {
  for (const status of [400, 401, 403, 404, 422, 429]) {
    await t.test(String(status), async t => {
      const h = harness(t)
      h.github = () => new Response(PAT, { status })
      assert.deepEqual(await result(await h.send()), { status: "dispatch_failed", notification: "sent" })
      h.restart()
      assert.deepEqual(await result(await h.send()), { status: "dispatch_failed", duplicate: true })
      assert.equal((await result(await h.send(update(2)))).status, "rate_limited")
      assert.equal(h.count("github"), 1)
      assert.ok(!JSON.stringify(h.calls[1].body).includes(PAT))
    })
  }
})

test("network and ambiguous HTTP failures are uncertain and are never redispatched", async t => {
  for (const status of [null, 200, 202, 302, 408, 500, 502, 503]) {
    await t.test(String(status), async t => {
      const h = harness(t)
      h.github = () => {
        if (status === null) throw new Error(`network ${TOKEN} ${PAT} ${SECRET}`)
        return new Response(PAT, { status })
      }
      assert.deepEqual(await result(await h.send()), { status: "uncertain", notification: "sent" })
      h.restart()
      h.now += 60_000
      assert.deepEqual(await result(await h.send()), { status: "uncertain", duplicate: true })
      assert.equal(h.count("github"), 1)
      assert.equal(h.count("telegram"), 1)
      assert.ok(h.calls[1].body.text.includes("Revisa GitHub Actions"))
      for (const secret of [TOKEN, PAT, SECRET]) assert.ok(!h.calls[1].body.text.includes(secret))
    })
  }
})

test("outbound requests use ten-second abort signals; dispatch timeout remains uncertain", async t => {
  const h = harness(t)
  let deadlines = 0
  t.mock.method(AbortSignal, "timeout", ms => {
    assert.equal(ms, 10_000)
    deadlines++
    return AbortSignal.abort(new Error(`timeout ${PAT}`))
  })
  h.github = ({ init }) => { init.signal.throwIfAborted() }
  h.telegram = ({ init }) => { init.signal.throwIfAborted() }
  assert.deepEqual(await result(await h.send()), { status: "uncertain", notification: "failed" })
  assert.equal(deadlines, 2)
  await result(await h.send())
  assert.equal(h.count("github"), 1)
})

test("Telegram failure after successful dispatch never changes the dispatch outcome or retries it", async t => {
  const failures = {
    http: () => new Response(TOKEN, { status: 500 }),
    redirect: () => new Response(null, { status: 302, headers: { Location: "https://blocked.invalid/" } }),
    rateLimit: () => new Response(TOKEN, { status: 429 }),
    api: () => Response.json({ ok: false, description: TOKEN }),
    json: () => new Response(TOKEN),
    network: () => { throw new Error(`fetch https://api.telegram.org/bot${TOKEN}`) }
  }
  for (const [name, fail] of Object.entries(failures)) {
    await t.test(name, async t => {
      const h = harness(t)
      h.telegram = () => {
        assert.equal(h.storage.data.get("update:1").status, "dispatched")
        return fail()
      }
      assert.deepEqual(await result(await h.send()), { status: "dispatched", notification: "failed" })
      h.restart()
      assert.equal(h.storage.data.get("update:1").notification, "failed")
      assert.deepEqual(await result(await h.send()), { status: "dispatched", duplicate: true })
      assert.equal(h.count("github"), 1)
      assert.equal(h.count("telegram"), 1)
    })
  }
})

test("dispatch rejection and notification failure remain independent", async t => {
  const h = harness(t)
  h.github = () => new Response(PAT, { status: 403 })
  h.telegram = () => new Response(TOKEN, { status: 500 })
  assert.deepEqual(await result(await h.send()), { status: "dispatch_failed", notification: "failed" })
  assert.deepEqual(await result(await h.send()), { status: "dispatch_failed", duplicate: true })
  assert.equal(h.count("github"), 1)
  assert.equal(h.count("telegram"), 1)
})

test("DO transport errors return sanitized retryable failures", async t => {
  const h = harness(t)
  h.env.WEBHOOK_STATE.get = () => ({ fetch() { throw new Error(`${TOKEN} ${PAT} ${SECRET}`) } })
  assert.equal((await result(await h.send(), 503)).status, "unavailable")
  assert.equal(h.calls.length, 0)
})

test("reservation failures roll back partial writes and permit a safe retry", async t => {
  for (const prefix of ["update:", "expiry:", "meta"]) {
    await t.test(prefix, async t => {
      const h = harness(t)
      h.storage.failPut = key => key.startsWith(prefix)
      await result(await h.send(), 503)
      assert.equal(h.storage.data.size, 0)
      assert.equal(h.storage.alarmTime, null)
      assert.equal(h.calls.length, 0)
      h.storage.failPut = undefined
      assert.equal((await result(await h.send())).status, "dispatched")
    })
  }
})

test("crash after claim but before dispatch preserves processing across a fresh instance", async t => {
  const h = harness(t)
  h.storage.failSync = true
  await result(await h.send(), 503)
  assert.equal(h.storage.data.get("update:1").status, "processing")
  assert.equal(h.calls.length, 0)
  h.restart()
  assert.deepEqual(await result(await h.send()), { status: "uncertain", duplicate: true })
  assert.equal(h.storage.data.get("update:1").status, "uncertain")
  assert.equal(h.calls.length, 0)
})

test("crash after dispatch but before saving its outcome cannot cause redispatch", async t => {
  const h = harness(t)
  h.storage.failPut = (key, value) => key === "update:1" && value.status === "dispatched"
  assert.equal((await result(await h.send())).status, "uncertain")
  assert.equal(h.storage.data.get("update:1").status, "processing")
  h.restart()
  assert.deepEqual(await result(await h.send()), { status: "uncertain", duplicate: true })
  assert.equal(h.count("github"), 1)
  assert.equal(h.count("telegram"), 0)
})

test("failure saving notification outcome preserves the already committed dispatch", async t => {
  const h = harness(t)
  h.storage.failPut = (key, value) => key === "update:1" && value.notification === "sent"
  assert.deepEqual(await result(await h.send()), { status: "dispatched", notification: "uncertain" })
  h.restart()
  assert.equal(h.storage.data.get("update:1").status, "dispatched")
  assert.equal(h.storage.data.get("update:1").notification, "pending")
  assert.deepEqual(await result(await h.send()), { status: "dispatched", duplicate: true })
  assert.equal(h.count("github"), 1)
  assert.equal(h.count("telegram"), 1)
})

test("dedup uses server retention, not message dates or monotonically increasing IDs", async t => {
  const h = harness(t)
  const body = update(100)
  body.message.date = 1
  await result(await h.send(body))
  assert.equal(h.storage.data.get("update:100").expiresAt, h.now + TTL)
  h.now += 60_000
  body.message.date = 9_999_999_999
  assert.equal((await result(await h.send(body))).duplicate, true)
  assert.equal((await result(await h.send(update(5)))).status, "dispatched")
  assert.equal(h.count("github"), 2)
})

test("expired records are removed by bounded alarm batches and alarms survive restart", async t => {
  const h = harness(t)
  seed(h.storage, 130, h.now + TTL)
  h.restart()
  assert.equal(h.storage.alarmTime, h.now + TTL)
  h.now += TTL
  await h.object.alarm()
  assert.equal(h.storage.data.get("meta").count, 66)
  assert.equal(h.storage.alarmTime, h.now + 1000)
  await h.object.alarm()
  assert.equal(h.storage.data.get("meta").count, 2)
  await h.object.alarm()
  assert.equal(h.storage.data.size, 1)
  assert.equal(h.storage.alarmTime, null)
  assert.deepEqual(h.storage.deleteCalls.map(keys => keys.length), [128, 128, 4])
})

test("storage capacity fails closed without evicting live dedup records; cleanup frees space", async t => {
  const h = harness(t)
  seed(h.storage, 10_000, h.now + TTL)
  const response = await h.send(update(10_001))
  assert.equal(response.headers.get("Retry-After"), "60")
  await result(response, 503)
  assert.equal(h.storage.data.get("meta").count, 10_000)
  assert.equal(h.storage.data.size, 20_001)
  assert.equal(h.calls.length, 0)
  assert.deepEqual(await result(await h.send(update(9999))), { status: "handled", duplicate: true })
  h.now += TTL
  assert.equal((await result(await h.send(update(10_001)))).status, "dispatched")
  assert.equal(h.storage.data.get("meta").count, 9937)
  assert.equal(h.storage.deleteCalls[0].length, 128)
})

test("TTL is an explicit at-most-once boundary: an expired ID can be dispatched again", async t => {
  const h = harness(t)
  await result(await h.send())
  h.now += TTL - 1
  assert.equal((await result(await h.send())).duplicate, true)
  h.now++
  assert.equal((await result(await h.send())).status, "dispatched")
  assert.equal(h.count("github"), 2)
  assert.equal(h.storage.data.get("meta").count, 1)
  assert.equal(h.storage.alarmTime, h.now + TTL)
})

test("Wrangler declares SQLite binding and migration independently in both environments", async () => {
  const config = await readFile(new URL("../wrangler.toml", import.meta.url), "utf8")
  const [defaults, production] = config.split("[env.production]")
  for (const [section, prefix] of [[defaults, ""], [production, "env.production."]]) {
    assert.ok(section.includes(`[[${prefix}durable_objects.bindings]]`))
    assert.ok(section.includes('name = "WEBHOOK_STATE"'))
    assert.ok(section.includes('class_name = "WebhookState"'))
    assert.ok(section.includes(`[[${prefix}migrations]]`))
    assert.ok(section.includes('tag = "v1-webhook-state"'))
    assert.ok(section.includes('new_sqlite_classes = ["WebhookState"]'))
  }
})
