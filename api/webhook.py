const TELEGRAM_TOKEN   = TELEGRAM_TOKEN_ENV   // variables de entorno en CF
const TELEGRAM_CHAT_ID = TELEGRAM_CHAT_ID_ENV
const GH_PAT           = GH_PAT_ENV
const GITHUB_REPO      = "DevCop95/dev101_bot"

async function sendMessage(chatId, text) {
  await fetch(`https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text, parse_mode: "Markdown" })
  })
}

async function triggerGithubAction() {
  const res = await fetch(
    `https://api.github.com/repos/${GITHUB_REPO}/actions/workflows/bot.yml/dispatches`,
    {
      method: "POST",
      headers: {
        "Authorization": `token ${GH_PAT}`,
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ ref: "main" })
    }
  )
  return res.status === 204
}

export default {
  async fetch(request) {
    if (request.method !== "POST") {
      return new Response("OK", { status: 200 })
    }

    const body     = await request.json()
    const message  = body?.message || {}
    const chatId   = String(message?.chat?.id || "")
    const text     = (message?.text || "").trim()

    // ── Bloquea cualquier usuario no autorizado ───────────────────────────
    if (chatId !== TELEGRAM_CHAT_ID) {
      await sendMessage(chatId, "⛔ No estás autorizado para usar este bot.")
      return new Response("OK", { status: 200 })
    }

    // ── Comandos ──────────────────────────────────────────────────────────
    if (text === "/noticias") {
      const ok = await triggerGithubAction()
      await sendMessage(chatId, ok
        ? "🚀 Buscando noticias... llegan en unos segundos."
        : "❌ Error al lanzar el job. Revisa GitHub Actions."
      )

    } else if (text === "/start" || text === "/help") {
      await sendMessage(chatId,
        "👾 *dev101\\_bot*\n\n" +
        "Comandos:\n" +
        "/noticias — busca y envía noticias ahora\n" +
        "/help — muestra este mensaje\n\n" +
        "_Envío automático cada 3 horas vía GitHub Actions._"
      )

    } else {
      await sendMessage(chatId, "Comando no reconocido. Usa /help.")
    }

    return new Response("OK", { status: 200 })
  }
}
