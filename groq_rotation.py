# groq_rotation.py — Cliente Groq COMPARTIDO con rotación de API keys.
# Lo usan run_job.py (resúmenes) e intelligence/mitre_tagger.py (TTPs): la cuota
# diaria (TPD) es por organización/key, así que TODOS los consumidores deben
# rotar juntos — si solo rota el resumidor, el tagger se queda clavado en la
# key agotada devolviendo 429 el resto del run.
#
# Fallback NVIDIA: si TODAS las keys de Groq fallan o agotan su cuota diaria, se
# usa la API de build.nvidia.com (compatible con OpenAI) para que el run no deje
# noticias sin resumen ni TTPs.

import os
import logging
import math
import re
import time
from email.utils import parsedate_to_datetime
from types import SimpleNamespace

import requests
from groq import APIConnectionError, APIStatusError, Groq

logger = logging.getLogger(__name__)

# Prioridad: GROQ_API_KEY, luego GROQ_API_KEY_2/_3, luego GROQ_API_KEYS (lista CSV).
def _cargar_groq_keys():
    keys = []
    for name in ("GROQ_API_KEY", "GROQ_API_KEY_2", "GROQ_API_KEY_3"):
        v = os.getenv(name, "").strip()
        if v and v not in keys:
            keys.append(v)
    for v in os.getenv("GROQ_API_KEYS", "").split(","):
        v = v.strip()
        if v and v not in keys:
            keys.append(v)
    return keys

GROQ_API_KEYS = _cargar_groq_keys()
_clients = [Groq(api_key=k, max_retries=0, timeout=45.0) for k in GROQ_API_KEYS]
_idx = 0            # índice de la key en uso
_exhausted = set()  # cuota diaria agotada o credenciales rechazadas (este run)
_cooldowns = {}     # indice -> instante monotonic de recuperacion
_failures = {}      # fallos transitorios consecutivos por key
_BACKOFF_BASE = 5.0
_BACKOFF_MAX = 300.0

# ── Fallback NVIDIA ───────────────────────────────────────────────────────────
# Los modelos se pueden sobreescribir por entorno para no volver a depender de
# IDs retirados por el proveedor.
GROQ_PRIMARY_MODEL = (os.getenv("GROQ_MODEL") or "").strip() or "openai/gpt-oss-120b"
GROQ_FALLBACK_MODEL = (os.getenv("GROQ_FALLBACK_MODEL") or "").strip() or "openai/gpt-oss-20b"

# OJO: en integrate.api.nvidia.com solo los Llama 8b/11b responden rápido (<2s
# medidos); meta/llama-3.2-3b-instruct y nvidia/llama-3.1-nemotron-nano-8b-v1
# se cuelgan con timeout de más de 60s — no usarlos.
NVIDIA_API_KEY = (os.getenv("NVIDIA_API_KEY") or "").strip()
NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_MODEL = (os.getenv("NVIDIA_MODEL") or "").strip() or "meta/llama-3.1-8b-instruct"



def nvidia_chat(**kwargs):
    """Llama al Llama pequeño de NVIDIA como fallback de Groq.

    Reemplaza el `model` recibido (nombre de modelo Groq) por NVIDIA_MODEL y
    devuelve un objeto con la misma forma que la respuesta de Groq
    (r.choices[0].message.content), o None si falla o no hay NVIDIA_API_KEY.
    """
    if not NVIDIA_API_KEY:
        return None
    model_name = (os.getenv("NVIDIA_MODEL") or "").strip() or NVIDIA_MODEL or "meta/llama-3.1-8b-instruct"
    payload = dict(kwargs, model=model_name)
    # El fallback Llama no admite los controles de razonamiento de Groq/GPT-OSS.
    payload.pop("reasoning_effort", None)

    try:
        r = requests.post(
            NVIDIA_URL,
            timeout=45,
            headers={"Authorization": f"Bearer {NVIDIA_API_KEY}",
                     "Accept": "application/json"},
            json=payload,
        )
        if r.status_code != 200:
            logger.error("NVIDIA fallback HTTP %s", r.status_code)
            return None
        choice = r.json()["choices"][0]
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=choice["message"]["content"]),
            finish_reason=choice.get("finish_reason"),
        )])
    except Exception:
        logger.error("NVIDIA fallback: error de transporte o respuesta invalida")
        return None


def _clasificar_error(error):
    if isinstance(error, (APIConnectionError, TimeoutError, ConnectionError)):
        return "transient"
    status = getattr(error, "status_code", None)
    body = getattr(error, "body", None)
    details = body.get("error", body) if isinstance(body, dict) else {}
    if not isinstance(details, dict):
        details = {}
    code = str(details.get("code", "")).lower()
    message = str(details.get("message", getattr(error, "message", ""))).lower()
    if type(error) is Exception:
        # Contrato existente de tests/adaptadores con errores sin tipo SDK.
        message = str(error).lower()
        match = re.match(r"(?:error code:\s*)?(\d{3})\b", message)
        if status is None and match:
            status = int(match.group(1))
    if status in (401, 403):
        return "auth"
    if status in (408, 409) or (isinstance(status, int) and status >= 500):
        return "transient"
    if status == 429 or code == "rate_limit_exceeded":
        if re.search(r"\b(tpd|rpd)\b|per day|daily", message) or code in (
            "daily_limit_exceeded", "insufficient_quota",
        ):
            return "quota"
        return "transient"
    if status == 404 or code == "model_not_found":
        return "model"
    if status is None and type(error) is Exception:
        if "model_not_found" in message or ("model" in message and "does not exist" in message):
            return "model"
        if "rate_limit" in message or "too many requests" in message:
            return "quota" if re.search(r"\b(tpd|rpd)\b|per day|daily", message) else "transient"
    return "request" if isinstance(error, APIStatusError) or status is not None else "unknown"


def groq_chat(**kwargs):
    """Llama a Groq rotando entre las API keys cuando una agota su cuota o falla.

    - Si un modelo devuelve 404 (sin acceso en esa key), reintenta con un modelo
      de respaldo activo.
    - Cuota diaria o autenticacion (401/403): excluye la key durante este run.
    - Transporte, 429 temporal y 5xx: cooldown exponencial, sin excluir la key.
      Las llamadas posteriores recuperan la key al expirar el cooldown.
    - Otros errores de peticion/modelo no invalidan las credenciales.
    - Sin keys utilizables: intenta el fallback NVIDIA (Llama pequeño).
    Devuelve la respuesta de la API o None si no queda ningún proveedor.
    """
    global _idx
    n = len(_clients)
    if n == 0:
        if NVIDIA_API_KEY:
            logger.warning("No hay GROQ_API_KEY configurada. Usando fallback NVIDIA...")
            return nvidia_chat(**kwargs)
        logger.error("No hay GROQ_API_KEY configurada")
        return None
    intentos = 0
    while intentos < n:
        if _idx in _exhausted or time.monotonic() < _cooldowns.get(_idx, 0):
            _idx = (_idx + 1) % n
            intentos += 1
            continue
        try:
            try:
                response = _clients[_idx].chat.completions.create(**kwargs)
            except Exception as error:
                if _clasificar_error(error) != "model" or kwargs.get("model") == GROQ_FALLBACK_MODEL:
                    raise
                logger.warning("Groq key #%s: modelo no disponible; usando modelo de respaldo", _idx + 1)
                response = _clients[_idx].chat.completions.create(
                    **dict(kwargs, model=GROQ_FALLBACK_MODEL)
                )
        except Exception as e:
            kind = _clasificar_error(e)
            if kind in ("auth", "quota"):
                _exhausted.add(_idx)
                logger.warning("Groq key #%s: %s; excluida durante este run", _idx + 1, kind)
            elif kind in ("transient", "unknown"):
                failures = _failures.get(_idx, 0) + 1
                _failures[_idx] = failures
                delay = min(_BACKOFF_BASE * 2 ** min(failures - 1, 6), _BACKOFF_MAX)
                headers = getattr(getattr(e, "response", None), "headers", {})
                retry_after = headers.get("retry-after")
                try:
                    try:
                        seconds = float(retry_after)
                    except ValueError:
                        seconds = parsedate_to_datetime(retry_after).timestamp() - time.time()
                    if math.isfinite(seconds):
                        delay = max(delay, min(seconds, 86400.0))
                except (TypeError, ValueError, OverflowError):
                    pass
                _cooldowns[_idx] = time.monotonic() + delay
                logger.warning("Groq key #%s: %s; cooldown %.1fs", _idx + 1, kind, delay)
            else:
                logger.warning("Groq key #%s: error %s; rotando sin excluir credenciales", _idx + 1, kind)
            _idx = (_idx + 1) % n
            intentos += 1
        else:
            _cooldowns.pop(_idx, None)
            _failures.pop(_idx, None)
            return response
    if NVIDIA_API_KEY:
        logger.warning("Groq: todas las API keys fallaron o agotaron su cuota. Usando fallback NVIDIA...")
        return nvidia_chat(**kwargs)
    logger.warning("Groq: no hay keys disponibles ahora (excluidas o en cooldown)")
    return None
