# 🤖 dev101_bot

> Bot de Telegram que envía noticias de Ciberseguridad e IA directamente a tu chat, funcionando 24/7 en Render.






***

## 🧩 Arquitectura

```
┌─────────────┐     Telegram      ┌──────────────────┐    scraping     ┌──────────────────┐
│   Usuario   │ ◄───────────────► │   dev101_bot     │ ◄─────────────► │  Fuentes news    │
└─────────────┘                   │(Flask + Gunicorn)│                 │  (4 websites)    │
                                  └────────┬─────────┘                 └──────────────────┘
                                           │
                                           │ AI summarize
                                           ▼
                                  ┌─────────────────┐
                                  │      Groq        │
                                  │  LLaMA 3.3 70B   │
                                  └────────┬─────────┘
                                           │
                                           │ sendMessage
                                           ▼
                                  ┌─────────────────┐
                                  │    Telegram     │
                                  │    Bot API      │
                                  └─────────────────┘
```

```
┌──────────────┐    ping cada 5min    ┌──────────────────────┐
│  UptimeRobot │ ─────────────────►   │    Render            │
└──────────────┘                      │  (Docker Web Service)│
                                      └──────────┬───────────┘
                                                 │
                                      ┌──────────▼───────────┐
                                      │     Flask + Gunicorn │
                                      │       /health        │
                                      └──────────────────────┘
```

**Render** aloja el bot 24/7 en Docker. **UptimeRobot** lo mantiene despierto con pings cada 5 minutos. El scheduler interno ejecuta el job de noticias cada 3 horas en un hilo separado.

***

## 📋 Requisitos Previos

- Python 3.11+
- Docker (opcional, para pruebas locales)
- Cuenta en [Render](https://render.com/) (gratis)
- Cuenta en [Groq](https://console.groq.com/) (gratis)
- Bot de Telegram (creado vía @BotFather)

***

## 🚀 Setup Completo

### Paso 1 — Crear el Bot de Telegram

1. Abre Telegram y busca **@BotFather**
2. Envía `/newbot`
3. Dale un nombre y username al bot
4. Copia el **TOKEN** que te da (ejemplo: `123456789:ABCDEF...`)
5. Busca tu bot en Telegram y envía `/start` para activar el chat

### Paso 2 — Obtener tu Chat ID

1. En Telegram, busca **@userinfobot**
2. Envía `/start`
3. Copia el **Id** que te responde

### Paso 3 — Obtener la API Key de Groq

1. Ve a [console.groq.com](https://console.groq.com/)
2. Regístrate con Google o GitHub
3. Ve a **API Keys** → **Create Key**
4. Copia la key (empieza con `gsk_`)

### Paso 4 — Configurar Variables de Entorno

Crea un archivo `.env` en la raíz del proyecto:

```env
TELEGRAM_TOKEN=tu_token_aqui         # Del BotFather
TELEGRAM_CHAT_ID=tu_chat_id_aqui     # De @userinfobot
GROQ_API_KEY=tu_groq_key_aqui        # De Groq
VERIFY_TOKEN=token_seguro            # Token para webhook (elige uno)
PORT=8080
```

### Paso 5 — Probar Localmente

```bash
# Instalar dependencias
pip install -r requirements.txt

# Correr el bot
python bot.py
```

Deberías ver en tu Telegram un mensaje con noticias. Si funciona, continúa con el despliegue.

***

## ☁️ Despliegue en Render (24/7 gratis)

El proyecto usa **Docker** para el despliegue, lo que garantiza un entorno reproducible.

### Paso 1 — Subir a GitHub

```bash
git add .
git commit -m "feat: migrate to Telegram, add Gunicorn + Docker"
git push origin main
```

### Paso 2 — Crear Web Service en Render

1. Ve a [dashboard.render.com](https://dashboard.render.com/)
2. Click **New +** → **Web Service**
3. Conecta tu repositorio de GitHub
4. Selecciona **Docker** como entorno (Render detecta el `Dockerfile` automáticamente)
5. Configura:

| Campo | Valor |
|-------|-------|
| **Name** | `dev101-bot` |
| **Region** | Oregon (US West) |
| **Branch** | `main` |
| **Runtime** | `Docker` |
| **Instance Type** | `Free` |

6. Click **Create Web Service**

### Paso 3 — Agregar Variables de Entorno

En Render: tu servicio → **Environment** → **Edit** y agrega:

```
TELEGRAM_TOKEN     = tu_token_aqui
TELEGRAM_CHAT_ID   = tu_chat_id_aqui
GROQ_API_KEY       = tu_groq_key_aqui
VERIFY_TOKEN       = token_seguro
```

### Paso 4 — Configurar UptimeRobot

Render Free duerme el servicio si no recibe tráfico. UptimeRobot lo mantiene activo:

1. Regístrate en [uptimerobot.com](https://uptimerobot.com/)
2. Click **Add New Monitor**
3. Configura:

| Campo | Valor |
|-------|-------|
| **Monitor Type** | `HTTP(s)` |
| **Friendly Name** | `dev101-bot` |
| **URL** | `https://tu-app.onrender.com/` |
| **Monitoring Interval** | `5 minutes` |

4. Click **Create Monitor**

***

## 🔄 Flujo de Trabajo del Bot

1. **Scheduler** — Al iniciar y cada 3 horas, el hilo del scheduler lanza `job()`
2. **Scraping** — Recoge el artículo más reciente de 4 fuentes de noticias
3. **Filtrado** — Elimina noticias ya enviadas y de años anteriores
4. **Resumen IA** — Groq (LLaMA 3.3 70B) genera titular impactante + resumen en 2 frases
5. **Envío** — Manda el resultado formateado a Telegram vía Bot API

***

## 📁 Estructura del Proyecto

```
dev101_bot/
├── bot.py              # Lógica principal del bot
├── Dockerfile          # Imagen Docker (python:3.11-slim + Gunicorn)
├── requirements.txt    # Dependencias Python
├── .env.example        # Plantilla de variables de entorno
├── .gitignore          # Excluye .env y sent_news.json
├── sent_news.json      # Cache de noticias enviadas (se crea automáticamente)
└── README.md
```

***

## 📦 Dependencias

```
flask
gunicorn
requests
beautifulsoup4
groq
schedule
python-dotenv
httpx
```

***

## 📰 Fuentes de Noticias

| Fuente | Categoría | URL |
|--------|-----------|-----|
| CyberSecurity News | IA + Ciberseguridad | cybersecuritynews.es |
| WeLiveSecurity (ESET) | Ciberseguridad | welivesecurity.com |
| Impacto TIC | IA Colombia | impactotic.co |
| WIRED en Español | IA | es.wired.com |

***

## ⚠️ Notas Importantes

- El archivo `sent_news.json` se crea automáticamente y evita noticias duplicadas. **No lo subas a GitHub** (está en `.gitignore`)
- El bot usa **mensajes de texto libre** por Telegram — sin restricciones de plantillas
- WhatsApp Cloud API requiere número propio registrado para mensajes de texto libre; queda como mejora futura
- Render Free reinicia el servicio al hacer nuevo deploy, lo que borra `sent_news.json`. Es comportamiento esperado

***

## 🗺️ Roadmap

- [ ] Soporte para WhatsApp con número propio registrado
- [ ] Comando `/noticias` para pedir noticias manualmente desde Telegram
- [ ] Más fuentes de noticias configurables
- [ ] Filtro de temas por preferencias del usuario

***

## 📝 Licencia

MIT © 2026 DevYHB
