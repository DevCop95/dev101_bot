# 🤖 dev101_bot

> Bot de Telegram que envía noticias de Ciberseguridad e IA directamente a tu chat, funcionando 24/7 mediante GitHub Actions y Cloudflare Workers.

---

## 🧩 Arquitectura

```
┌─────────────┐     Telegram      ┌─────────────────────┐    dispatches    ┌──────────────────┐
│   Usuario   │ ◄───────────────► │ Cloudflare Workers  │ ───────────────► │ GitHub Actions   │
└─────────────┘     Webhook       │    (api/webhook.js) │    Workflow      │ (run_job.py)     │
                                  └─────────────────────┘                  └────────┬─────────┘
                                                                                    │
                                                                           ┌────────▼─────────┐
                                                                           │  scraping + IA   │
                                                                           │  (Groq LLaMA 3.3)│
                                                                           └────────┬─────────┘
                                                                                    │
                                  ┌─────────────────────┐     guarda datos   ┌──────▼──────────┐
                                  │ Repo: cYHBernews    │ ◄────────────────  │ noticias.json   │
                                  └─────────────────────┘                    └─────────────────┘
```

### Componentes

1. **GitHub Actions** (`.github/workflows/bot.yml`): El motor del bot. Se ejecuta automáticamente cada 3 horas o manualmente vía webhook. Realiza el scraping (RSS), resume con IA y envía a Telegram.
2. **Cloudflare Workers** (`api/webhook.js`): El receptor. Recibe mensajes de Telegram y dispara el Action de GitHub cuando se usa `/noticias`.
3. **Repositorio externo** (`DevCop95/cYHBernews`): El historial. Las noticias se guardan en `noticias.json` para deduplicación y persistencia.

---

## 🚀 Setup

### 1. GitHub Secrets

Ve a **Settings > Secrets and variables > Actions** en este repositorio y configura:

| Secreto | Descripción |
|---------|-------------|
| `GIT_TOKEN` | Personal Access Token (Classic) con scopes `repo` y `workflow` |
| `TELEGRAM_TOKEN` | Token del bot obtenido con @BotFather |
| `TELEGRAM_CHAT_ID` | Tu ID de Telegram (obtenido con @userinfobot) |
| `GROQ_API_KEY` | API Key de [console.groq.com](https://console.groq.com/) |
| `UNSPLASH_ACCESS_KEY` | (Opcional) Para imágenes aleatorias en las noticias |

### 2. Cloudflare Workers — Variables de entorno

En tu dashboard de Cloudflare Workers > tu worker > **Settings > Variables and Secrets**:

| Variable | Valor |
|----------|-------|
| `TELEGRAM_TOKEN_ENV` | El mismo token de Telegram |
| `TELEGRAM_CHAT_ID_ENV` | Tu ID de Telegram |
| `GH_PAT_ENV` | El mismo `GIT_TOKEN` de GitHub |

### 3. Registrar el Webhook en Telegram

Ejecuta una sola vez en el navegador o con curl:

```
https://api.telegram.org/bot<TU_TOKEN>/setWebhook?url=https://dev101_bot.dev101-bot.workers.dev
```

---

## 🤖 Comandos disponibles

| Comando | Descripción |
|---------|-------------|
| `/noticias` | Fuerza la búsqueda y envío de noticias al instante |
| `/help` | Muestra los comandos disponibles |

El bot también envía noticias automáticamente **cada 3 horas** vía cron en GitHub Actions.

---

## 📁 Estructura del proyecto

```
dev101_bot/
├── .github/
│   └── workflows/
│       └── bot.yml          # Workflow de GitHub Actions (cron + dispatch)
├── api/
│   └── webhook.js           # Cloudflare Worker — recibe mensajes de Telegram
├── run_job.py               # Script principal — scraping, IA y publicación
├── requirements.txt         # Dependencias Python
├── wrangler.toml            # Config de Cloudflare Workers
├── .env.example             # Plantilla de variables de entorno
└── .gitignore
```

---

## 📰 Fuentes de noticias

| Fuente | Categoría | Idioma |
|--------|-----------|--------|
| CyberSecurity News | Ciberseguridad / IA | 🇪🇸 |
| WeLiveSecurity (ESET) | Ciberseguridad | 🇪🇸 |
| DragonJAR | Ciberseguridad | 🇪🇸 |
| El Lado Del Mal | Ciberseguridad | 🇪🇸 |
| Una al Día (Hispasec) | Ciberseguridad | 🇪🇸 |
| Bleeping Computer | Ciberseguridad | 🇬🇧 (auto-traducido) |
| The Hacker News | Ciberseguridad | 🇬🇧 (auto-traducido) |
| IA en Español (Substack) | IA | 🇪🇸 |
| Xataka IA | IA | 🇪🇸 |

---

## 📦 Dependencias

```
requests
beautifulsoup4
lxml
groq
httpx
python-dotenv
cloudscraper
```

---

## 📝 Licencia

MIT © 2026 DevYHB
