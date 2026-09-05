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
                                                                           │  Groq (config.)  │
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
3. **Sources** (`sources/`): RSS/Atom, NVD CVE API, Exploit-DB, GreyNoise y previews públicos de canales de Telegram. Vulners está desactivado en el job.
4. **Intelligence** (`intelligence/`): Análisis avanzado — clasificación MITRE ATT&CK, extracción de IoCs, clasificación de severidad.
5. **Persistencia** (`DevCop95/cYHBernews`): `bot-state/noticias.json` (rama `bot-state`, archivo `noticias.json`) conserva el historial y los estados de entrega. `main/noticias.json` recibe una única instantánea por ejecución, solo con noticias confirmadas y sin metadatos de Telegram.
6. **CI** (`.github/workflows/ci.yml`): Pruebas offline Python/Worker, cobertura y auditoría de dependencias antes de publicar.

---

## 🚀 Setup

### 1. GitHub Secrets

Crear el environment **production** en GitHub, restringir sus ramas de despliegue a **main** y mover allí los secretos del publicador. Eliminar las copias equivalentes de los secretos del repositorio para que otra rama no pueda acceder a ellos modificando el workflow. Si se exigen revisores, cada ejecución programada esperará aprobación; elegir esa política conscientemente para el bot automático.

| Secreto | Descripción |
|---------|-------------|
| `GIT_TOKEN` | Token dedicado fine-grained: solo `DevCop95/cYHBernews`, permiso Contents: read/write |
| `TELEGRAM_TOKEN` | Token del bot obtenido con @BotFather |
| `TELEGRAM_CHAT_ID` | Tu ID de Telegram (obtenido con @userinfobot) |
| `GROQ_API_KEY` | API Key de [console.groq.com](https://console.groq.com/) |
| `UNSPLASH_ACCESS_KEY` | (Opcional) Para imágenes aleatorias |
| `NVD_API_KEY` | (Opcional) API Key de NVD para mejor rate limit |
| `GREYNOISE_API_KEY` | (Opcional) API Key de GreyNoise Community |
| `GROQ_API_KEY_2`, `GROQ_API_KEY_3`, `GROQ_API_KEYS` | (Opcional) Claves adicionales; la última variable admite CSV |
| `NVIDIA_API_KEY` | (Opcional) Proveedor alternativo si Groq no está disponible |

El fallback histórico `GH_PAT` sigue admitido para no romper instalaciones existentes; migrarlo a `GIT_TOKEN` con alcance limitado. No reutilizar este token en el Worker. Los modelos se configuran con `GROQ_MODEL`, `GROQ_FALLBACK_MODEL` y `NVIDIA_MODEL` como variables de Actions.

### 2. Cloudflare Workers — Variables de entorno

En tu dashboard de Cloudflare Workers > tu worker > **Settings > Variables and Secrets**:

| Variable | Valor |
|----------|-------|
| `TELEGRAM_TOKEN_ENV` | El mismo token de Telegram |
| `TELEGRAM_CHAT_ID_ENV` | Tu ID de Telegram |
| `GH_PAT_ENV` | Token distinto: solo `DevCop95/dev101_bot`, permiso Actions: write |
| `TELEGRAM_WEBHOOK_SECRET` | Secreto aleatorio independiente, 1-256 caracteres de `A-Z`, `a-z`, `0-9`, `_`, `-`; usar al menos 32 caracteres aleatorios |
| `TELEGRAM_USER_ID_ENV` | (Opcional) ID numérico positivo del usuario autorizado, además del chat |

Configurar cada entorno por separado. `wrangler.toml` declara el Durable Object SQLite `WEBHOOK_STATE` y su migración para default y `production`. El despliegue del Worker debe aplicar esa migración; no basta con copiar el archivo JavaScript. La configuración incompleta devuelve 503 y no procesa comandos.

Workers Builds instala automáticamente `requirements.txt` cuando la raíz del build es `/`. `.python-version` fija Python 3.11, igual que Actions, para usar los wheels Linux cuyos hashes incluye el lock y evitar compilar `lxml` sin `libxml2`/`libxslt`. Mantener esa versión alineada con CI; no eliminar hashes para resolver un fallo de instalación.

### 3. Registrar el Webhook en Telegram

Registrar el webhook **después** de configurar secretos y desplegar el Worker. El siguiente ejemplo es una operación real de configuración, no una prueba: requiere `TELEGRAM_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` y `WEBHOOK_URL` exportados en un entorno de confianza. No introducir tokens en el navegador ni imprimir respuestas que puedan contener datos sensibles.

```python
import os
import requests

token = os.environ["TELEGRAM_TOKEN"]
response = requests.post(
    f"https://api.telegram.org/bot{token}/setWebhook",
    json={
        "url": os.environ["WEBHOOK_URL"],
        "secret_token": os.environ["TELEGRAM_WEBHOOK_SECRET"],
        "allowed_updates": ["message"],
    },
    timeout=15,
)
print("Webhook registrado" if response.ok and response.json().get("ok") else "Registro fallido")
```

El Worker comprueba `X-Telegram-Bot-Api-Secret-Token`, chat y usuario opcional. Deduplica `update_id` durante siete días con almacenamiento persistente y reserva atómica; `/noticias` tiene un intervalo mínimo de 60 segundos. Cada entorno mantiene su propio estado. Ante un dispatch incierto, revisar Actions antes de enviar otro comando: no existe una garantía de ejecución exactamente una vez.

---

## 🤖 Comandos disponibles

| Comando | Descripción |
|---------|-------------|
| `/noticias` | Fuerza búsqueda y envío de noticias |
| `/help` | Muestra comandos disponibles |
| `/start` | Muestra la ayuda inicial |

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
│   ├── exploitdb.py                # Exploit-DB RSS; helper Vulners no conectado
│   ├── greynoise.py                # GreyNoise Community API
│   └── telegram_monitor.py         # Previews oficiales t.me/s
├── intelligence/                   # Análisis de inteligencia
│   ├── mitre_tagger.py             # Clasificación MITRE ATT&CK (Groq)
│   ├── ioc_extractor.py            # Extracción de IoCs (regex + STIX)
│   └── severity_classifier.py     # Clasificación de severidad
├── run_job.py                      # Orquestador principal
├── groq_rotation.py                # Rotación, cooldown y fallback NVIDIA
├── tests/                          # Regresiones de fuentes, inteligencia y entregas
├── tools/run_tests.py              # Runner aislado, sin red ni .env
├── tools/update_attack_catalog.py  # Generador del catálogo MITRE versionado
├── api/webhook.test.mjs            # Pruebas Node del Worker y almacenamiento
├── intelligence/attack_catalog.json # Enterprise ATT&CK v17.1 completo
├── requirements.txt
├── requirements-dev.txt
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
| NVD CVE API 2.0 | Vulnerabilidades CVSS v4/v3 ≥ 7.0, incluyendo críticas | Gratuita |
| Exploit-DB RSS | Exploits públicos | Gratuita |
| GreyNoise Community | IPs maliciosas trending | Gratuita |

Vulners conserva un helper probado, pero no está conectado al job porque su API anónima dejó de funcionar. Los permisos y límites de GreyNoise dependen de la cuenta. NVD ordena por CVSS después de paginar, con máximo de 10 páginas de 2.000 registros; un fallo parcial o límite alcanzado se registra en logs.

### Canales de Telegram

| Canal | Temática |
|-------|----------|
| vx-underground (`vxunderground`) | Malware e inteligencia de amenazas |
| CVE Notify (`cveNotify`) | Vulnerabilidades |
| Security Harvester (`secharvester`) | Inteligencia de amenazas |
| Cyber Security Channel (`Cyber_Security_Channel`) | Ciberseguridad |
| Android Malware (`androidMalware`) | Amenazas Android |

---

## 🧠 Pipeline de Inteligencia

Cada noticia pasa por este pipeline:

1. **Recolección** → RSS, APIs, Telegram channels
2. **Filtro de relevancia** → Groq (modelo configurable por `GROQ_MODEL`)
3. **Resumen IA** → Estilo analista CTI senior
4. **Extracción IoCs** → URLs, IPs públicas normalizadas, dominios, hashes y CVEs, incluyendo indicadores defanged. Una coincidencia no demuestra maliciosidad.
5. **Clasificación MITRE** → IDs y nombres del catálogo Enterprise ATT&CK v17.1, incluyendo históricos. IDs desconocidos se descartan; la inferencia del modelo no es evidencia confirmada.
6. **Severidad** → 🔴 Crítica / 🟠 Alta / 🟡 Media / 🟢 Baja / 🔵 Info
7. **Deduplicación** → Similitud Jaccard + URLs ya publicadas
8. **Persistencia** → GitHub `noticias.json` en la rama `bot-state`, mensaje preparado en estado `pending`
9. **Distribución** → Reserva `sending`, envío a Telegram y confirmación `sent` con `message_id`
10. **Publicación web** → Una instantánea en `main/noticias.json`, sin estados intermedios

La utilidad `iocs_to_stix()` está disponible y probada, pero el job no exporta bundles STIX automáticamente. RSS y Telegram comparan timestamps completos en UTC: se rechazan fechas futuras o inválidas y se conservan entradas sin fecha.

El límite es de cinco noticias nuevas y doce llamadas lógicas de IA por ejecución, contando resumen y MITRE; los reintentos internos de proveedor pueden producir más peticiones HTTP. Groq aplica cooldown recuperable a fallos transitorios en vez de invalidar permanentemente una clave válida.

### Recuperación de entregas

La primera ejecución crea automáticamente la rama `bot-state` desde `main`; requiere que `GIT_TOKEN` pueda crear esa rama con su permiso Contents: read/write. La copia conserva los estados existentes, incluidos pendientes e inciertos. Una rama ya existente nunca se reinicializa. No eliminarla ni fusionarla a `main`: es el historial autoritativo del bot y su cola de entregas.

Los registros históricos sin campo `telegram` se consideran ya publicados y no se reenvían. Los nuevos registros incluyen estado, número de intentos y un identificador único de reserva. La deduplicación y el recorte del historial nunca eliminan entregas pendientes o inciertas.

| Estado | Comportamiento |
|--------|----------------|
| `pending` | Guardado, todavía no enviado |
| `sending` | Reserva persistida antes de la petición; si sobrevive a un reinicio pasa a `uncertain` |
| `sent` | Telegram confirmó el envío; se conserva `message_id` y `sent_at` |
| `failed` | Rechazo confirmado o fallo de conexión seguro; reintento en otro run, respetando `retry_at`, hasta cinco intentos |
| `uncertain` | No puede determinarse si Telegram aceptó; no se reenvía automáticamente |

Una entrega bloqueada detiene el procesamiento y hace fallar Actions, en vez de perder noticias silenciosamente. Las entregas ya confirmadas sí se publican, leyendo el estado persistido y no una copia en memoria. Los pendientes anteriores se resuelven antes de recolectar más noticias. Un conflicto de SHA aborta sin sobrescribir datos ajenos; una escritura de GitHub con respuesta perdida se reconcilia por lectura antes de continuar. Si falla solo la publicación web, el siguiente run vuelve a intentarla sin reenviar las noticias a Telegram, incluso si no hay noticias nuevas.

Para recuperar una entrega incierta, pausar los workflows y cualquier proceso local y revisar el mensaje en el chat. Corregir `noticias.json` **en la rama `bot-state`**, no en `main`. Si ya existe el mensaje, marcar `telegram.status` como `sent` y registrar el `message_id`. Solo si se confirma que no existe, cambiar a `pending`, conservar `text`, reiniciar `attempts` a 0 y eliminar `retry_at`/`error`. Hacer la corrección mediante una escritura que respete el SHA actual y reanudar el job. **No eliminar el campo `telegram` ni reiniciar estados a ciegas:** eso puede perder o duplicar entregas.

Ambas ramas pueden ser públicas: no introducir secretos en mensajes ni metadatos. Los checkpoints se guardan en `bot-state`, fuera de la rama de Pages y del filtro del workflow `Split news data` (`main`, `noticias.json`). Así, las escrituras internas no disparan publicaciones concurrentes. Solo se modifica `main` una vez por ejecución si la instantánea pública cambió; la rama de estado no debe configurarse como fuente de Pages.

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

Requisitos: Python 3.11 o 3.12 y Node 22 para las pruebas del Worker. `requirements.txt` fija versiones directas y transitivas con hashes para Linux/Windows x64. Usar un entorno virtual; los comandos de instalación y auditoría consultan índices externos, pero las pruebas no necesitan red ni credenciales.

```bash
python -m pip install --require-hashes --only-binary=:all: -r requirements.txt
python -m pip install -r requirements-dev.txt
python -B tools/run_tests.py
node --test api/webhook.test.mjs
python -m coverage run tools/run_tests.py
python -m coverage report
python -m pip_audit --strict --require-hashes -r requirements.txt --progress-spinner off
```

El runner elimina credenciales del entorno, bloquea lecturas `.env` y conexiones de red, y solo descubre `test_run_job.py` y `tests/test_*.py`. Los diagnósticos locales ignorados por Git (`test_scrapers.py`, `verify_rss.py`) no forman parte de la suite y pueden estar obsoletos; no usar descubrimiento indiscriminado desde la raíz. `python run_job.py` sí realiza publicación real.

CI ejecuta estas comprobaciones para PRs/push y auditoría periódica; el publicador depende de que pasen y solo admite `main`. La cobertura combinada de líneas/ramas exige un mínimo global del 80% en los módulos de producción. Los requisitos de desarrollo están fijados directamente, pero sus transitivas aún no tienen lock con hashes.

Para actualizar MITRE, revisar `ATTACK_VERSION` en `tools/update_attack_catalog.py`, ejecutar el generador y revisar el diff de `intelligence/attack_catalog.json`. El generador consulta únicamente el bundle público oficial, registra su SHA256 y crea un artefacto determinista; el bot utiliza ese artefacto sin descargar catálogos en producción.

---

## 📝 Licencia

MIT © 2026 DevYHB
