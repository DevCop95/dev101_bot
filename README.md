# 🤖 dev101_bot

> Plataforma de inteligencia de Ciberseguridad e IA que recolecta, analiza y distribuye noticias automáticamente vía Telegram. Funciona 24/7 mediante GitHub Actions y Cloudflare Workers.

---

## 🧩 Arquitectura

```
┌─────────────┐     Telegram      ┌─────────────────────┐    dispatches    ┌──────────────────┐
│   Usuario   │ ◄───────────────► │ Cloudflare Workers  │ ───────────────► │ GitHub Actions   │
└─────────────┘     Webhook       │    (api/webhook.js) │    Workflow      │ (run_job.py)     │
                                  └─────────────────────┘                  └────────┬─────────┘
                                                                                    │
                                                                           ┌────────▼─────────┐
                                                                           │  RECOLECCIÓN     │
                                                                           │  RSS + APIs +    │
                                                                           │  Telegram Chans  │
                                                                           └────────┬─────────┘
                                                                                    │
                                                                           ┌────────▼─────────┐
                                                                           │  INTELIGENCIA    │
                                                                           │  Groq LLaMA 3.3  │
                                                                           │  MITRE ATT&CK    │
                                                                           │  IoC Extraction  │
                                                                           │  Severity Class. │
                                                                           └────────┬─────────┘
                                                                                    │
                                  ┌─────────────────────┐     guarda datos   ┌──────▼──────────┐
                                  │ Repo: cYHBernews    │ ◄────────────────  │ DISTRIBUCIÓN    │
                                  └─────────────────────┘                    │ Telegram        │
                                          ▲                                  └─────────────────┘
                                          │
                                  ┌───────┴───────┐
                                  │ noticias.json │
                                  │ + TTPs + IoCs │
                                  └───────────────┘
```

### Componentes

1. **GitHub Actions** (`.github/workflows/bot.yml`): Motor del bot. Se ejecuta cada 3 horas o manualmente. Recolecta → Analiza → Distribuye.
2. **Cloudflare Workers** (`api/webhook.js`): Receptor de Telegram. Dispara el Action vía `/noticias`.
3. **Sources** (`sources/`): Módulos de recolección — RSS, NVD CVE API, Exploit-DB, Vulners, GreyNoise, canales de Telegram.
4. **Intelligence** (`intelligence/`): Análisis avanzado — clasificación MITRE ATT&CK, extracción de IoCs, clasificación de severidad.
5. **Persistencia** (`DevCop95/cYHBernews`): Persistencia en `noticias.json` con TTPs, IoCs y severidad.

---

## 🚀 Setup

### 1. GitHub Secrets

Ve a **Settings > Secrets and variables > Actions** en este repositorio:

| Secreto | Descripción |
|---------|-------------|
| `GIT_TOKEN` | Personal Access Token (Classic) con scopes `repo` y `workflow` |
| `TELEGRAM_TOKEN` | Token del bot obtenido con @BotFather |
| `TELEGRAM_CHAT_ID` | Tu ID de Telegram (obtenido con @userinfobot) |
| `GROQ_API_KEY` | API Key de [console.groq.com](https://console.groq.com/) |
| `UNSPLASH_ACCESS_KEY` | (Opcional) Para imágenes aleatorias |
| `NVD_API_KEY` | (Opcional) API Key de NVD para mejor rate limit |
| `GREYNOISE_API_KEY` | (Opcional) API Key de GreyNoise Community |

### 2. Cloudflare Workers — Variables de entorno

En tu dashboard de Cloudflare Workers > tu worker > **Settings > Variables and Secrets**:

| Variable | Valor |
|----------|-------|
| `TELEGRAM_TOKEN_ENV` | El mismo token de Telegram |
| `TELEGRAM_CHAT_ID_ENV` | Tu ID de Telegram |
| `GH_PAT_ENV` | El mismo `GIT_TOKEN` de GitHub |

### 3. Registrar el Webhook en Telegram

```
https://api.telegram.org/bot<TU_TOKEN>/setWebhook?url=https://dev101_bot.dev101-bot.workers.dev
```

---

## 🤖 Comandos disponibles

| Comando | Descripción |
|---------|-------------|
| `/noticias` | Fuerza búsqueda y envío de noticias |
| `/help` | Muestra comandos disponibles |

Envío automático **cada 3 horas** vía cron en GitHub Actions.

---

## 📁 Estructura del proyecto

```
dev101_bot/
├── .github/workflows/bot.yml      # GitHub Actions (cron + dispatch)
├── api/webhook.js                  # Cloudflare Worker — Telegram webhook
├── sources/                        # Módulos de recolección
│   ├── rss_feeds.py                # 15 fuentes RSS (ES + EN)
│   ├── nvd_cve.py                  # NVD CVE API 2.0 (NIST)
│   ├── exploitdb.py                # Exploit-DB RSS + Vulners API
│   ├── greynoise.py                # GreyNoise Community API
│   └── telegram_monitor.py         # Canales de Telegram via RSS bridge
├── intelligence/                   # Análisis de inteligencia
│   ├── mitre_tagger.py             # Clasificación MITRE ATT&CK (Groq)
│   ├── ioc_extractor.py            # Extracción de IoCs (regex + STIX)
│   └── severity_classifier.py     # Clasificación de severidad
├── run_job.py                      # Orquestador principal
├── requirements.txt
├── wrangler.toml
└── .env.example
```

---

## 📰 Fuentes de noticias

### RSS Feeds

| Fuente | Categoría | Idioma |
|--------|-----------|--------|
| CyberSecurity News | Ciberseguridad / IA | 🇪🇸 |
| WeLiveSecurity (ESET) | Ciberseguridad | 🇪🇸 |
| DragonJAR | Ciberseguridad | 🇪🇸 |
| El Lado Del Mal | Ciberseguridad | 🇪🇸 |
| Una al Día (Hispasec) | Ciberseguridad | 🇪🇸 |
| Bleeping Computer | Ciberseguridad | 🇬🇧 (auto-traducido) |
| The Hacker News | Ciberseguridad | 🇬🇧 (auto-traducido) |
| Krebs on Security | Ciberseguridad | 🇬🇧 (auto-traducido) |
| Dark Reading | Ciberseguridad | 🇬🇧 (auto-traducido) |
| Schneier on Security | Ciberseguridad | 🇬🇧 (auto-traducido) |
| SANS ISC | Ciberseguridad | 🇬🇧 (auto-traducido) |
| The Record | Ciberseguridad | 🇬🇧 (auto-traducido) |
| Wired Security | Ciberseguridad | 🇬🇧 (auto-traducido) |
| IA en Español (Substack) | IA | 🇪🇸 |
| Xataka IA | IA | 🇪🇸 |

### APIs de Inteligencia

| Fuente | Tipo | Costo |
|--------|------|-------|
| NVD CVE API 2.0 | Vulnerabilidades (CVSS ≥ 7.0) | Gratuita |
| Exploit-DB RSS | Exploits públicos | Gratuita |
| Vulners API | Vulnerabilidades + exploits | Gratuita |
| GreyNoise Community | IPs maliciosas trending | Gratuita |

### Canales de Telegram

| Canal | Temática |
|-------|----------|
| Threat Intel (Shakirov) | Inteligencia de amenazas |
| Ransomware News | Grupos de ransomware |
| Daily Dark Web | Dark web monitoring |
| Exploit.in | Exploits y vulnerabilidades |
| CyberWarfare Feed | APT tracking |

---

## 🧠 Pipeline de Inteligencia

Cada noticia pasa por este pipeline:

1. **Recolección** → RSS, APIs, Telegram channels
2. **Filtro de relevancia** → Groq LLaMA 3.3 (acepta/rechaza)
3. **Resumen IA** → Estilo analista CTI senior
4. **Extracción IoCs** → Regex: IPs, dominios, hashes, CVEs → formato STIX 2.1
5. **Clasificación MITRE** → TTPs con IDs validados (T1566, T1486, etc.)
6. **Severidad** → 🔴 Crítica / 🟠 Alta / 🟡 Media / 🟢 Baja / 🔵 Info
7. **Deduplicación** → Similitud Jaccard + URLs ya publicadas
8. **Distribución** → Telegram
9. **Persistencia** → GitHub `noticias.json` con todos los campos

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
