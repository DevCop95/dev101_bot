from http.server import BaseHTTPRequestHandler
import json, os, requests

TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]   # solo tú puedes usar el bot
GITHUB_TOKEN     = os.environ["GH_PAT"]
GITHUB_REPO      = "DevCop95/dev101_bot"

def trigger_github_action():
    """Dispara el workflow de noticias manualmente vía GitHub API."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/bot.yml/dispatches"
    r = requests.post(url, headers={
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }, json={"ref": "main"})
    return r.status_code == 204

def send_message(chat_id, text):
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": text}
    )

class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        length  = int(self.headers.get("Content-Length", 0))
        body    = json.loads(self.rfile.read(length))

        message = body.get("message", {})
        chat_id = str(message.get("chat", {}).get("id", ""))
        text    = message.get("text", "").strip()

        # ── Solo responde al dueño ────────────────────────────────────────
        if chat_id != TELEGRAM_CHAT_ID:
            send_message(chat_id, "⛔ No estás autorizado para usar este bot.")
            self._ok()
            return

        # ── Comandos ──────────────────────────────────────────────────────
        if text == "/noticias":
            ok = trigger_github_action()
            if ok:
                send_message(chat_id, "🚀 Buscando noticias... llegará en unos segundos.")
            else:
                send_message(chat_id, "❌ Error al lanzar el job. Revisa los logs en GitHub Actions.")

        elif text == "/start" or text == "/help":
            send_message(chat_id, (
                "👾 *dev101_bot*\n\n"
                "Comandos disponibles:\n"
                "/noticias — busca y envía noticias ahora\n"
                "/help — muestra este mensaje\n\n"
                "_El bot también envía noticias automáticamente cada 3 horas._"
            ))

        else:
            send_message(chat_id, "Comando no reconocido. Usa /help.")

        self._ok()

    def _ok(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *args):
        pass  # silencia logs de acceso
