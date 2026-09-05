# intelligence/mitre_tagger.py — Clasificación MITRE ATT&CK automática
# Usa Groq LLaMA 3.3 con prompt especializado para mapear TTPs
# Diccionario local de técnicas para validación

import re
import logging
import json
from pathlib import Path

# Cliente compartido con rotación de keys: si la key #1 agota su cuota diaria
# (TPD), el tagger rota igual que el resumidor en vez de fallar todo el run.
from groq_rotation import GROQ_API_KEYS, GROQ_PRIMARY_MODEL, NVIDIA_API_KEY, groq_chat

logger = logging.getLogger(__name__)

# Complete versioned Enterprise catalog, including historical/revoked IDs.
# Catalog membership validates IDs/names, not whether a TTP occurred in an article.
try:
    ATTACK_CATALOG = json.loads(Path(__file__).with_name("attack_catalog.json").read_text(encoding="utf-8"))
    KNOWN_TECHNIQUES = ATTACK_CATALOG["techniques"]
    TACTICS = ATTACK_CATALOG["tactics"]
    if (ATTACK_CATALOG["domain"] != "enterprise-attack" or not ATTACK_CATALOG["attack_version"] or
            not isinstance(KNOWN_TECHNIQUES, dict) or len(KNOWN_TECHNIQUES) < 600 or
            len(KNOWN_TECHNIQUES) != ATTACK_CATALOG["technique_count"] or
            any(not re.fullmatch(r'T\d{4}(?:\.\d{3})?', key) or not isinstance(name, str) or not name
                for key, name in KNOWN_TECHNIQUES.items())):
        raise ValueError("invalid Enterprise catalog")
except (OSError, ValueError, KeyError, TypeError):
    logger.error("MITRE: catalog unavailable/invalid; abstaining from tagging")
    ATTACK_CATALOG, KNOWN_TECHNIQUES, TACTICS = {}, {}, {}

MITRE_SYSTEM_PROMPT = """Eres un analista de inteligencia de amenazas (CTI) especializado en el framework MITRE ATT&CK.

Tu tarea es analizar noticias de ciberseguridad y extraer las Tácticas, Técnicas y Procedimientos (TTPs) relevantes.

REGLAS:
1. Solo incluye TTPs que se mencionen EXPLÍCITAMENTE o se INFIERAN CLARAMENTE del contenido
2. Usa el formato exacto: TXXXX o TXXXX.XXX (con subtécnica si aplica)
3. Incluye el nombre de la técnica junto al ID
4. Máximo 5 TTPs por noticia
5. Si la noticia NO es sobre un ataque/amenaza específica, responde SOLO: NONE

FORMATO DE RESPUESTA (una línea por TTP):
TXXXX - Nombre de la Técnica
TXXXX.XXX - Nombre de la Subtécnica

Ejemplo:
T1566.001 - Spearphishing Attachment
T1486 - Data Encrypted for Impact
T1078 - Valid Accounts"""


def tag_ttps(title, content=""):
    """
    Clasifica TTPs MITRE ATT&CK de una noticia usando Groq.
    Retorna lista de dicts con id y nombre, o lista vacía.
    """
    if not KNOWN_TECHNIQUES or (not GROQ_API_KEYS and not NVIDIA_API_KEY):
        return []

    text = f"Título: {title}\nContenido: {content[:3000]}"

    try:
        request = dict(
            model=GROQ_PRIMARY_MODEL,
            messages=[
                {"role": "system", "content": MITRE_SYSTEM_PROMPT},
                {"role": "user", "content": text}
            ],
            temperature=0.1,
            max_tokens=300,
        )
        if GROQ_PRIMARY_MODEL in {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}:
            request["reasoning_effort"] = "low"
        r = groq_chat(**request)
        if r is None:
            return []

        response = r.choices[0].message.content.strip()
        
        if response.upper() == "NONE":
            return []
        
        ttps = []
        seen = set()
        for line in response.split("\n"):
            line = line.strip()
            if not line:
                continue
            
            # Extraer ID de técnica (TXXXX o TXXXX.XXX)
            match = re.match(r'(T\d{4}(?:\.\d{3})?)\s*[-–—:]\s*(.+)', line)
            if match:
                tech_id = match.group(1)
                if tech_id not in KNOWN_TECHNIQUES or tech_id in seen:
                    continue
                seen.add(tech_id)
                ttps.append({
                    "id": tech_id,
                    "name": KNOWN_TECHNIQUES[tech_id],
                })
        
        if ttps:
            logger.info(f"MITRE TTPs detectados: {[t['id'] for t in ttps]}")
        
        return ttps[:5]  # Máximo 5
        
    except Exception as e:
        logger.error(f"MITRE Tagger Error: {e}")
    return []


def format_ttps_telegram(ttps):
    """
    Formatea TTPs para mensaje de Telegram.
    Retorna string o None si no hay TTPs.
    """
    if not ttps:
        return None
    
    lines = []
    for ttp in ttps:
        lines.append(f"⚔️ `{ttp['id']}` — {ttp['name']}")
    
    return "\n".join(lines)


def format_ttps_twitter(ttps):
    """
    Formatea TTPs para tweet (versión corta).
    """
    if not ttps:
        return ""
    
    ids = [ttp['id'] for ttp in ttps[:3]]
    return " ".join(f"#{tid.replace('.', '_')}" for tid in ids)
