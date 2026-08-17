# groq_rotation.py — Cliente Groq COMPARTIDO con rotación de API keys.
# Lo usan run_job.py (resúmenes) e intelligence/mitre_tagger.py (TTPs): la cuota
# diaria (TPD) es por organización/key, así que TODOS los consumidores deben
# rotar juntos — si solo rota el resumidor, el tagger se queda clavado en la
# key agotada devolviendo 429 el resto del run.
#
# Fallback NVIDIA: si TODAS las keys de Groq agotan su cuota diaria, se usa la
# API de build.nvidia.com (compatible con OpenAI) con un Llama pequeño para que
# el run no deje noticias sin resumen ni TTPs.

import os
import logging
from types import SimpleNamespace

import requests
from groq import Groq

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
_clients = [Groq(api_key=k) for k in GROQ_API_KEYS]
_idx = 0            # índice de la key en uso
_exhausted = set()  # índices de keys con cuota DIARIA agotada (este run)

# ── Fallback NVIDIA ───────────────────────────────────────────────────────────
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

    try:
        r = requests.post(
            NVIDIA_URL,
            timeout=45,
            headers={"Authorization": f"Bearer {NVIDIA_API_KEY}",
                     "Accept": "application/json"},
            json=payload,
        )
        if r.status_code != 200:
            logger.error(f"NVIDIA fallback HTTP {r.status_code}: {r.text[:200]}")
            return None
        content = r.json()["choices"][0]["message"]["content"]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    except Exception as e:
        logger.error(f"NVIDIA fallback error: {e}")
        return None


def _es_rate_limit(e):
    s = str(e).lower()
    return "rate_limit" in s or "429" in s or "too many requests" in s

def _es_limite_diario(e):
    s = str(e).lower()
    return "per day" in s or "tpd" in s or "tokens per day" in s

def _es_modelo_no_encontrado(e):
    s = str(e).lower()
    return "model_not_found" in s or "does not exist" in s or "404" in s


def groq_chat(**kwargs):
    """Llama a Groq rotando entre las API keys cuando una agota su cuota o falla.

    - Si un modelo devuelve 404 (sin acceso en esa key), reintenta con llama-3.1-8b-instant.
    - Límite DIARIO (TPD) u otros fallos (401, 403, etc.): marca la key como agotada en este run y rota.
    - 429 transitorio (por minuto): solo rota, sin descartarla.
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
        if _idx in _exhausted:
            _idx = (_idx + 1) % n
            intentos += 1
            continue
        try:
            return _clients[_idx].chat.completions.create(**kwargs)
        except Exception as e:
            if _es_modelo_no_encontrado(e) and kwargs.get("model") != "llama-3.1-8b-instant":
                logger.warning(f"Groq key #{_idx+1}: modelo '{kwargs.get('model')}' no disponible ({e}). Reintentando con 'llama-3.1-8b-instant'...")
                try:
                    kwargs_fallback = dict(kwargs, model="llama-3.1-8b-instant")
                    return _clients[_idx].chat.completions.create(**kwargs_fallback)
                except Exception as e_fallback:
                    e = e_fallback

            if _es_rate_limit(e):
                if _es_limite_diario(e):
                    _exhausted.add(_idx)
                    logger.warning(f"Groq key #{_idx+1} agotó su cuota DIARIA. Rotando a la siguiente...")
                else:
                    logger.warning(f"Groq key #{_idx+1} con rate limit transitorio. Rotando...")
            else:
                _exhausted.add(_idx)
                logger.warning(f"Groq key #{_idx+1} falló ({e}). Marcando como inactiva y rotando...")
            _idx = (_idx + 1) % n
            intentos += 1
            continue
    if NVIDIA_API_KEY:
        logger.warning("Groq: todas las API keys fallaron o agotaron su cuota. Usando fallback NVIDIA...")
        return nvidia_chat(**kwargs)
    logger.error("Groq: todas las API keys agotaron su cuota o fallaron. No se puede llamar más en este run.")
    return None


