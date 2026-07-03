# groq_rotation.py — Cliente Groq COMPARTIDO con rotación de API keys.
# Lo usan run_job.py (resúmenes) e intelligence/mitre_tagger.py (TTPs): la cuota
# diaria (TPD) es por organización/key, así que TODOS los consumidores deben
# rotar juntos — si solo rota el resumidor, el tagger se queda clavado en la
# key agotada devolviendo 429 el resto del run.

import os
import logging
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


def _es_rate_limit(e):
    s = str(e).lower()
    return "rate_limit" in s or "429" in s or "too many requests" in s

def _es_limite_diario(e):
    s = str(e).lower()
    return "per day" in s or "tpd" in s or "tokens per day" in s


def groq_chat(**kwargs):
    """Llama a Groq rotando entre las API keys cuando una agota su cuota.

    - Límite DIARIO (TPD): marca la key como agotada para el resto del run.
    - 429 transitorio (por minuto): solo rota, sin descartarla.
    Devuelve la respuesta de la API o None si no queda ninguna key utilizable.
    """
    global _idx
    n = len(_clients)
    if n == 0:
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
            if _es_rate_limit(e):
                if _es_limite_diario(e):
                    _exhausted.add(_idx)
                    logger.warning(f"Groq key #{_idx+1} agotó su cuota DIARIA. Rotando a la siguiente...")
                else:
                    logger.warning(f"Groq key #{_idx+1} con rate limit transitorio. Rotando...")
                _idx = (_idx + 1) % n
                intentos += 1
                continue
            logger.error(f"Groq error (no rate-limit): {e}")
            return None
    logger.error("Groq: todas las API keys agotaron su cuota. No se puede llamar más en este run.")
    return None
