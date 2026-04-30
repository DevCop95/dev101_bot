# 🤖 CyberPulse Bot

> **Tu asistente inteligente de noticias de Ciberseguridad e Inteligencia Artificial.**

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

CyberPulse Bot es un bot automatizado que monitorea las principales fuentes de noticias sobre ciberseguridad e IA en español, genera resúmenes inteligentes usando IA de Groq, y te los entrega directamente en WhatsApp cada 3 horas.

---

## ⚡ Características

- **Scraping inteligente** de 4 fuentes líderes en español:
  - 🛡️ CyberSecurity News
  - 🛡️ We Live Security
  - 🛡️ Impacto TIC
  - 🛡️ WIRED en Español

- **Resúmenes con IA** generados por Groq (LLaMA 3.3)
- **Filtrado anti-spam** de noticias antiguas o duplicadas
- **Entrega automática** a WhatsApp cada 3 horas
- **Diseño cloud-native** listo para Docker + Render

---

## 🚀 Inicio Rápido

### 1. Clona el repositorio

```bash
git clone <tu-repo>
cd telegram_bot
```

### 2. Configura las variables de entorno

Crea un archivo `.env` en la raíz del proyecto:

```env
# WhatsApp (WHAPI)
WHAPI_TOKEN=tu_whapi_token
WHAPI_URL=https://gate.whapi.com
WHATSAPP_RECIPIENT=+34600000000

# Groq AI
GROQ_API_KEY=tu_groq_api_key

# Puerto (para Render)
PORT=8080
```

### 3. Instala dependencias

```bash
pip install -r requirements.txt
```

### 4. Ejecuta

```bash
python bot.py
```

---

## 🐳 Despliegue con Docker

```bash
docker build -t cyberpulse-bot .
docker run -d --env-file .env cyberpulse-bot
```

---

## 📁 Estructura del Proyecto

```
telegram_bot/
├── bot.py              # Lógica principal del bot
├── Dockerfile          # Imagen Docker
├── requirements.txt    # Dependencias Python
├── .env                # Variables de entorno (no commitear)
└── sent_news.json      # Cache de noticias enviadas (auto-generado)
```

---

## 🔧 API Keys Requeridas

| Servicio | Propósito | Dónde obtenerla |
|----------|-----------|-----------------|
| **WHAPI** | Envío de mensajes WhatsApp | [whapi.com](https://whapi.com/) |
| **Groq** | Generación de resúmenes con IA | [console.groq.com](https://console.groq.com/) |

---

## ⚙️ Funcionamiento

1. **Scraping** → Cada 3 horas, el bot extrae noticias de las 4 fuentes configuradas
2. **Filtrado** → Elimina noticias duplicadas, antiguas o fuera de tema
3. **Resumen IA** → Groq genera un titular impactante + resumen de 2 frases
4. **Distribución** → El mensaje formateado se envía a WhatsApp

---

## 📝 Licencia

MIT © 2026