const GITHUB_REPO = "DevCop95/dev101_bot"

async function sendMessage(token, chatId, text) {
  await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text, parse_mode: "Markdown" })
  })
}

async function triggerGithubAction(ghPat) {
  const res = await fetch(
    `https://api.github.com/repos/${GITHUB_REPO}/actions/workflows/bot.yml/dispatches`,
    {
      method: "POST",
      headers: {
        "Authorization": `token ${ghPat}`,
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ ref: "main" })
    }
  )
  return res.status === 204
}

export default {
  async fetch(request, env) {  // ← env es el segundo parámetro
    // ✅ Secrets accedidos aquí, no en el scope global
    const TELEGRAM_TOKEN   = env.TELEGRAM_TOKEN_ENV
    const TELEGRAM_CHAT_ID = env.TELEGRAM_CHAT_ID_ENV
    const GH_PAT           = env.GH_PAT_ENV

    if (request.method !== "POST") {
      return new Response("OK", { status: 200 })
    }

    const body    = await request.json()
    const message = body?.message || {}
    const chatId  = String(message?.chat?.id || "")
    const text    = (message?.text || "").trim()

    if (chatId !== TELEGRAM_CHAT_ID) {
      await sendMessage(TELEGRAM_TOKEN, chatId, "⛔ No estás autorizado para usar este bot.")
      return new Response("OK", { status: 200 })
    }

    if (text === "/noticias") {
      const ok = await triggerGithubAction(GH_PAT)
      await sendMessage(TELEGRAM_TOKEN, chatId, ok
        ? "🚀 Buscando noticias... llegan en unos segundos."
        : "❌ Error al lanzar el job. Revisa GitHub Actions."
      )

    } else if (text === "/start" || text === "/help") {
      await sendMessage(TELEGRAM_TOKEN, chatId,
        "👾 *dev101\\_bot*\n\n" +
        "Comandos:\n" +
        "/noticias — busca y envía noticias ahora\n" +
        "/help — muestra este mensaje\n\n" +
        "_Envío automático cada 3 horas vía GitHub Actions._"
      )

    } else {
      await sendMessage(TELEGRAM_TOKEN, chatId, "Comando no reconocido. Usa /help.")
    }

    return new Response("OK", { status: 200 })
  }
}