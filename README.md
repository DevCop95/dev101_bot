# 🤖 CyberPulse Bot

> Bot de Telegram que envía noticias de Ciberseguridad e IA directamente a tu WhatsApp, funcionando 24/7 en Render.

![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)

---

## 🧩 Arquitectura

```
┌─────────────┐     Telegram      ┌─────────────┐    scraping     ┌──────────────────┐
│   Usuario   │ ◄───────────────► │  Telegram   │ ◄─────────────► │  Fuentes news    │
└─────────────┘                   │    Bot      │                 │  (4 websites)    │
                                  └──────┬──────┘                 └──────────────────┘
                                         │
                                         │ AI summarize
                                         ▼
                                  ┌─────────────┐
                                  │    Groq     │
                                  │   LLaMA     │
                                  └─────────────┘
                                         │
                                         │ WhatsApp
                                         ▼
                                  ┌─────────────┐
                                  │   WHAPI     │
                                  │  (gateway)  │
                                  └─────────────┘
                                         │
                                         ▼
                                  ┌─────────────┐
                                  │   Usuario   │
                                  │  WhatsApp   │
                                  └─────────────┘
```

El bot hace scraping de 4 fuentes de noticias (CyberSecurity News, We Live Security, Impacto TIC, WIRED), usa Groq (LLaMA 3.3) para resumir con IA y envía los resultados a WhatsApp cada 3 horas.

---

## 📋 Requisitos Previos

- Python 3.11+
- Cuenta en [Render](https://render.com/) (gratis)
- Cuenta en [Groq](https://console.groq.com/) (gratis)
- Cuenta en [WHAPI](https://whapi.com/) (prueba gratuita)
- Bot de Telegram (creado vía @BotFather)

---

## 🚀 Setup Completo

### Paso 1 — Crear el Bot de Telegram

1. Abre Telegram y busca **@BotFather**
2. Envía `/newbot`
3. Dale un nombre y username al bot
4. Copia el **TOKEN** que te da ( formato: `123456789:AABBcc...` )
5. Necesitarás este token más adelante

### Paso 2 — Obtener las API Keys

**Groq (gratis):**
1. Ve a [console.groq.com](https://console.groq.com/)
2. Regístrate con Google/GitHub
3. Ve a **API Keys** → **Create Key**
4. Copia la key (empieza con `gsk_`)

**WHAPI (prueba gratuita):**
1. Regístrate en [whapi.com](https://whapi.com/)
2. En el dashboard, crea un canal de **WhatsApp**
3. Copia tu **WHAPI Token**

### Paso 3 — Configurar el Proyecto

```bash
# Clona el repositorio
git clone https://github.com/TU_USUARIO/telegram_bot.git
cd telegram_bot

# Crea el archivo .env
cp .env.example .env
```

Edita `.env` con tus credenciales:

```env
TELEGRAM_TOKEN=123456789:AABBcc...      # Del BotFather
WHAPI_TOKEN=tu_whapi_token             # De whapi.com
WHAPI_URL=https://gate.whapi.com
GROQ_API_KEY=gsk_xxxx                   # De Groq
WHATSAPP_RECIPIENT=+34600000000         # Tu número (con +)
PORT=8080
```

### Paso 4 — Instalar y Probar Localmente

```bash
pip install -r requirements.txt
python bot.py
```

Deberías ver en tu WhatsApp un mensaje con una noticia. Si funciona, continuamos.

---

## ☁️ Despliegue en Render (24/7 gratis)

Render free tier es suficiente porque solo necesita estar "vivo" para ejecutar cada 3 horas.

### Paso 1 — Subir a GitHub

Si aún no tienes el repo en GitHub:
```bash
git init
git add .
git commit -m "initial commit"
git branch -M main
git remote add origin https://github.com/TU_USUARIO/repo.git
git push -u origin main
```

### Paso 2 — Crear Web Service en Render

1. Ve a [dashboard.render.com](https://dashboard.render.com/)
2. Click **New +** → **Web Service**
3. Conecta tu repositorio de GitHub
4. Configura:

   | Campo | Valor |
   |-------|-------|
   | **Name** | `cyberpulse-bot` |
   | **Region** | Choose closest |
   | **Branch** | `main` |
   | **Runtime** | `Python` |
   | **Build Command** | `pip install -r requirements.txt` |
   | **Start Command** | `python bot.py` |
   | **Instance Type** | `Free` |

5. Click **Create Web Service**

### Paso 3 — Agregar Variables de Entorno

En Render, ve a tu servicio → **Environment** → **Environment Variables** y agrega:

```
TELEGRAM_TOKEN = 123456789:AABBcc...
WHAPI_TOKEN = tu_whapi_token
WHAPI_URL = https://gate.whapi.com
GROQ_API_KEY = gsk_xxxx
WHATSAPP_RECIPIENT = +34600000000
PORT = 8080
```

### Paso 4 — Deploy

1. Click **Deploy** o espera a que haga deploy automáticamente
2. Revisa los logs en **Logs** tab para confirmar que funciona
3. Si todo está bien, verás el bot funcionando 24/7

---

## 🔄 Flujo de Trabajo del Bot

1. **Scraping** — Cada 3 horas recoge artículos de 4 fuentes
2. **Filtrado** — Elimina noticias duplicadas o de años anteriores
3. **Resumen IA** — Groq genera titular impactante + resumen de 2 líneas
4. **Envío** — Manda el resultado formateado a tu WhatsApp vía WHAPI

---

## 📁 Estructura del Proyecto

```
telegram_bot/
├── bot.py              # Lógica principal
├── Dockerfile          # Imagen Docker (opcional)
├── requirements.txt    # Dependencias
├── .env.example        # Plantilla de variables
├── sent_news.json      # Cache (se crea solo)
└── README.md
```

---

## ⚠️ Notas Importantes

- **Render free tier** duerme después de 15 min de inactividad, pero el scheduler interno del bot sigue funcionando y ejecuta el job cada 3 horas apenas se activa
- El archivo `sent_news.json` se crea automáticamente para evitar noticias duplicadas
- Si WHAPI deja de funcionar, el bot seguirá corriendo pero sin enviar mensajes

---

## 📝 Licencia

MIT © 2026