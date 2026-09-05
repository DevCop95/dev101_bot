const GITHUB_REPO = "DevCop95/dev101_bot"
const MAX_BODY_BYTES = 64 * 1024
const TIMEOUT_MS = 10_000
const COOLDOWN_MS = 60_000
const DEDUP_TTL_MS = 7 * 24 * 60 * 60 * 1000
const MAX_UPDATES = 10_000
const CLEANUP_BATCH = 64

function reply(status, code = 200, extra = {}, headers = {}) {
  return Response.json({ status, ...extra }, {
    status: code,
    headers: { "Cache-Control": "no-store", ...headers }
  })
}

async function readJson(request) {
  if (Number(request.headers.get("Content-Length")) > MAX_BODY_BYTES) {
    throw reply("body_too_large", 413)
  }
  const reader = request.body?.getReader()
  if (!reader) throw reply("invalid_json", 400)
  const bytes = new Uint8Array(MAX_BODY_BYTES)
  let size = 0
  let timer
  try {
    const read = async () => {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        if (size + value.byteLength > MAX_BODY_BYTES) throw reply("body_too_large", 413)
        bytes.set(value, size)
        size += value.byteLength
      }
      return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes.subarray(0, size)))
    }
    return await Promise.race([
      read(),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(reply("request_timeout", 408)), TIMEOUT_MS)
      })
    ])
  } catch (error) {
    throw error instanceof Response ? error : reply("invalid_json", 400)
  } finally {
    clearTimeout(timer)
    void reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}

function validIdSetting(value, allowNegative = false) {
  return typeof value === "string" &&
    (allowNegative ? /^-?[1-9]\d*$/ : /^[1-9]\d*$/).test(value) &&
    Number.isSafeInteger(Number(value))
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value)
}

async function sendMessage(token, chatId, text) {
  try {
    const response = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: chatId, text }),
      signal: AbortSignal.timeout(TIMEOUT_MS),
      // workerd supports manual, not error; never forward credentials on redirects.
      redirect: "manual"
    })
    if (!response.ok) {
      void response.body?.cancel().catch(() => {})
      return false
    }
    return (await readJson(response))?.ok === true
  } catch {
    // Fetch errors can contain the bot token in their URL. Never expose them.
    return false
  }
}

async function triggerGithubAction(ghPat) {
  try {
    const response = await fetch(
      `https://api.github.com/repos/${GITHUB_REPO}/actions/workflows/bot.yml/dispatches`,
      {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${ghPat}`,
          "Accept": "application/vnd.github+json",
          "Content-Type": "application/json",
          "User-Agent": "dev101-bot-webhook",
          "X-GitHub-Api-Version": "2022-11-28"
        },
        body: JSON.stringify({ ref: "main" }),
        signal: AbortSignal.timeout(TIMEOUT_MS),
        redirect: "manual"
      }
    )
    void response.body?.cancel().catch(() => {})
    if (response.status === 204) return "dispatched"
    if (response.status >= 400 && response.status < 500 && response.status !== 408) {
      return "dispatch_failed"
    }
    return "uncertain"
  } catch {
    // A timeout/network error does not prove GitHub rejected the dispatch.
    return "uncertain"
  }
}

export default {
  async fetch(request, env) {
    if (request.method === "GET" || request.method === "HEAD") return reply("ok")
    if (request.method !== "POST") {
      return reply("method_not_allowed", 405, {}, { Allow: "GET, HEAD, POST" })
    }
    const secret = env.TELEGRAM_WEBHOOK_SECRET
    if (typeof secret !== "string" || !/^[A-Za-z0-9_-]{1,256}$/.test(secret) ||
        !validIdSetting(env.TELEGRAM_CHAT_ID_ENV, true) ||
        (env.TELEGRAM_USER_ID_ENV !== undefined && !validIdSetting(env.TELEGRAM_USER_ID_ENV)) ||
        ![env.TELEGRAM_TOKEN_ENV, env.GH_PAT_ENV].every(value => typeof value === "string" && value.trim()) ||
        typeof env.WEBHOOK_STATE?.idFromName !== "function" || typeof env.WEBHOOK_STATE?.get !== "function") {
      return reply("unavailable", 503, {}, { "Retry-After": "60" })
    }
    const provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token") || ""
    let difference = provided.length ^ secret.length
    for (let i = 0; i < secret.length; i++) {
      difference |= secret.charCodeAt(i) ^ (provided.charCodeAt(i) || 0)
    }
    if (difference !== 0) return reply("unauthorized", 401)
    if (request.headers.get("Content-Type")?.split(";")[0].trim().toLowerCase() !== "application/json") {
      return reply("unsupported_media_type", 415)
    }

    try {
      const update = await readJson(request)
      if (!isObject(update) || !Number.isSafeInteger(update.update_id) || update.update_id < 0) {
        return reply("invalid_update", 400)
      }
      // Non-message updates and messages without text are normal Telegram events.
      if (update.message === undefined) return reply("ignored")
      const message = update.message
      if (!isObject(message) || !isObject(message.chat) ||
          !Number.isSafeInteger(message.chat.id) || message.chat.id === 0 ||
          (message.text !== undefined && (typeof message.text !== "string" || message.text.length > 4096)) ||
          (message.from !== undefined && (!isObject(message.from) ||
            !Number.isSafeInteger(message.from.id) || message.from.id <= 0))) {
        return reply("invalid_message", 400)
      }
      const chatId = String(message.chat.id)
      if (chatId !== env.TELEGRAM_CHAT_ID_ENV ||
          (env.TELEGRAM_USER_ID_ENV !== undefined && String(message.from?.id) !== env.TELEGRAM_USER_ID_ENV)) {
        return reply("forbidden", 403)
      }
      const text = message.text?.trim()
      if (!text) return reply("ignored")

      // One stable object per bot/environment, independent of secret rotation.
      const id = env.WEBHOOK_STATE.idFromName("telegram-webhook")
      return await env.WEBHOOK_STATE.get(id).fetch(new Request("https://webhook.internal/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ updateId: update.update_id, chatId, text })
      }))
    } catch (error) {
      if (error instanceof Response) return error
      return reply("unavailable", 503, {}, { "Retry-After": "60" })
    }
  }
}

// At-most-once attempts within the seven-day, server-clock retention window.
// Persist processing BEFORE dispatch. A surviving processing record is uncertain:
// ACK it without retrying, even after a crash before the actual network request.
// GitHub dispatch has no idempotency key, so exactly-once delivery is impossible.
// Reconcile uncertain outcomes in Actions before submitting a NEW update_id.
export class WebhookState {
  constructor(ctx, env) {
    this.storage = ctx.storage
    this.env = env
  }

  async cleanup(tx, now) {
    const meta = await tx.get("meta") || { count: 0, cooldownUntil: 0 }
    const expired = await tx.list({
      prefix: "expiry:",
      end: `expiry:${String(now + 1).padStart(16, "0")}:`,
      limit: CLEANUP_BATCH
    })
    if (expired.size) {
      await tx.delete([...expired].flatMap(([index, key]) => [index, key]))
      meta.count -= expired.size
      await tx.put("meta", meta)
    }
    // SQLite storage operations, including alarms, share the enclosing transaction.
    const next = await tx.list({ prefix: "expiry:", limit: 1 })
    if (next.size) {
      const expiresAt = Number(next.keys().next().value.split(":")[1])
      await this.storage.setAlarm(Math.max(now + 1000, expiresAt))
    } else {
      await this.storage.deleteAlarm()
    }
    return meta
  }

  async alarm() {
    await this.storage.transaction(tx => this.cleanup(tx, Date.now()))
  }

  async finish(key, expiresAt, changes) {
    await this.storage.transaction(async tx => {
      const record = await tx.get(key)
      // Do not resurrect a record that cleanup expired during an interrupted call.
      if (record?.expiresAt === expiresAt) await tx.put(key, { ...record, ...changes })
    })
  }

  async fetch(request) {
    const { updateId, chatId, text } = await request.json()
    const key = `update:${updateId}`
    const claim = await this.storage.transaction(async tx => {
      const now = Date.now()
      const meta = await this.cleanup(tx, now)
      const existing = await tx.get(key)
      if (existing) {
        if (existing.status === "processing") {
          existing.status = "uncertain"
          await tx.put(key, existing)
        }
        return { duplicate: true, record: existing }
      }
      // Never evict unexpired dedup records to make room: fail closed instead.
      if (meta.count >= MAX_UPDATES) return { full: true }
      const retryAfter = text === "/noticias" ? Math.max(0, Math.ceil((meta.cooldownUntil - now) / 1000)) : 0
      if (text === "/noticias" && !retryAfter) meta.cooldownUntil = now + COOLDOWN_MS
      const record = { status: "processing", expiresAt: now + DEDUP_TTL_MS, retryAfter }
      await tx.put(key, record)
      await tx.put(`expiry:${String(record.expiresAt).padStart(16, "0")}:${updateId}`, key)
      meta.count++
      await tx.put("meta", meta)
      if (meta.count === 1) await this.storage.setAlarm(record.expiresAt)
      return { record }
    })
    if (claim.full) return reply("unavailable", 503, {}, { "Retry-After": "60" })
    if (claim.duplicate) {
      return reply(claim.record.status, 200, { duplicate: true })
    }
    // Explicit durability barrier in addition to Cloudflare's default output gate.
    await this.storage.sync()
    const { expiresAt, retryAfter } = claim.record
    let status = "handled"
    let outcomeSaved = false
    try {
      let notificationText
      if (retryAfter) {
        status = "rate_limited"
        notificationText = `Espera ${retryAfter} segundos y envia /noticias de nuevo.`
      } else if (text === "/noticias") {
        status = await triggerGithubAction(this.env.GH_PAT_ENV)
        notificationText = {
          dispatched: "Buscando noticias... llegan en unos segundos.",
          dispatch_failed: "GitHub rechazo el job. Revisa GitHub Actions antes de intentarlo de nuevo.",
          uncertain: "No se pudo confirmar el job. Revisa GitHub Actions antes de volver a pedir noticias."
        }[status]
      } else if (text === "/start" || text === "/help") {
        notificationText = "dev101_bot\n\n/noticias - busca y envia noticias ahora\n/help - muestra este mensaje\n\nEnvio automatico cada 3 horas via GitHub Actions."
      } else {
        notificationText = "Comando no reconocido. Usa /help."
      }
      // Dispatch outcome is durable BEFORE notification; Telegram failure is separate.
      await this.finish(key, expiresAt, { status, notification: "pending" })
      outcomeSaved = true
      const sent = await sendMessage(this.env.TELEGRAM_TOKEN_ENV, chatId, notificationText)
      const notification = sent ? "sent" : "failed"
      await this.finish(key, expiresAt, { notification })
      // A legitimate cooldown consumes this update. A 429 would cause Telegram to
      // retry it later and unexpectedly dispatch it; only a new command may retry.
      return reply(status, 200, { notification, ...(retryAfter ? { retry_after: retryAfter } : {}) })
    } catch {
      // The claim is already durable. Safe ACK; never redispatch this update.
      return reply(outcomeSaved ? status : "uncertain", 200, { notification: "uncertain" })
    }
  }
}
