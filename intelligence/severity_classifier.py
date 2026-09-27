# intelligence/severity_classifier.py — Clasificación de severidad
# Clasifica noticias en: Crítica / Alta / Media / Baja / Info
# Basado en: CVE CVSS, keywords de impacto, tipo de amenaza

import re
import logging
import math
import unicodedata

logger = logging.getLogger(__name__)


def strip_accents(s):
    """Elimina acentos y diacríticos para comparación uniforme e insensible a tildes."""
    if not isinstance(s, str):
        return ""
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").lower()


# ── Clasificación por keywords (en minúsculas y sin acentos) ─────────────────

EXPLICIT_CRITICAL = [
    "critica", "critico", "critical", "severidad critica", "critical severity",
    "criticidad maxima", "fallo critico", "critical flaw", "vulnerabilidad critica",
    "critical vulnerability", "alerta critica", "critical alert",
]

EXPLICIT_HIGH = [
    "alta severidad", "severidad alta", "high severity", "vulnerabilidad alta",
    "vulnerabilidad de alta severidad", "riesgo alto", "high risk",
]

EXPLICIT_MEDIUM = [
    "severidad media", "media severidad", "medium severity", "moderada",
    "severidad moderada", "moderate severity", "riesgo medio", "medium risk",
]

EXPLICIT_LOW = [
    "baja severidad", "severidad baja", "low severity", "severidad menor",
    "menor severidad", "bajo impacto", "low impact", "fallo menor", "minor bug",
    "riesgo bajo", "low risk",
]

CRITICAL_KEYWORDS = [
    # Explotación activa y cero-days
    "zero-day", "0-day", "zero day", "0 day", "rce", "remote code execution",
    "ejecucion remota", "codigo remoto", "arbitrary code execution",
    "actively exploited", "explotado activamente", "in the wild", "explotacion activa",
    "wormable", "supply chain attack", "cadena de suministro",
    # Infraestructura y objetivos de alto impacto
    "nation-state", "estado-nacion", "critical infrastructure",
    "infraestructura critica", "infraestructura clave", "central nuclear",
    "infraestructura nuclear", "nuclear facility", "hospitales", "servicios de emergencia",
    # Incidentes de compromiso masivo y gubernamentales
    "data breach", "brecha de datos", "brecha masiva", "mass breach",
    "millones de registros", "millones de usuarios", "millones de afectados",
    "millions of records", "millions of users", "millions of victims",
    "ransomware attack", "ataque ransomware", "ataque de ransomware",
    "ataque a gobierno", "government breach", "gobierno comprometido",
    "espionaje gubernamental", "ciberataque estatal", "compromete bases de datos",
]

HIGH_KEYWORDS = [
    "ransomware", "malware", "exploit",
    "backdoor", "puerta trasera", "botnet", "ddos",
    "apt", "advanced persistent", "phishing campaign", "campana de phishing",
    "credential theft", "robo de credenciales", "expone credenciales", "filtracion de credenciales",
    "data leak", "filtracion", "filtracion de datos",
    "privilege escalation", "escalada de privilegios", "elevacion de privilegios",
    "authentication bypass", "bypass de autenticacion", "salto de autenticacion",
    "evasion de autenticacion", "auth bypass",
    "infostealer", "stealer", "inyeccion sql", "sql injection",
    "ciberataque", "ataque cibernetico",
]

MEDIUM_KEYWORDS = [
    "vulnerability", "vulnerabilidad",
    "phishing", "spam", "scam", "estafa", "trojan", "troyano",
    "spyware", "adware", "patch", "parche", "update", "actualizacion",
    "advisory", "aviso", "warning", "alerta", "risk", "riesgo",
    "information disclosure", "divulgacion de informacion", "exposicion de informacion",
    "gobierno", "government",
    "xss", "cross-site scripting", "csrf",
    "denegacion de servicio", "denial of service",
]

LOW_KEYWORDS = [
    "bug bounty", "responsible disclosure", "divulgacion responsable",
    "minor", "menor", "proof of concept", "poc", "best practice",
    "buena practica", "buenas practicas", "hardening",
]

INFO_KEYWORDS = [
    "informational", "informativa", "severidad: info", "research", "investigacion",
    "tutorial", "guide", "guia", "manual", "informe anual", "annual report",
    "reporte anual", "encuesta", "survey", "estadisticas", "whitepaper",
]

# ── Severidad por CVSS ───────────────────────────────────────────────────────

def normalize_cvss_score(value):
    """Return a finite CVSS in [0, 10], or None (booleans are not scores)."""
    if isinstance(value, bool):
        return None
    try:
        score = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return score if math.isfinite(score) and 0 <= score <= 10 else None


def _cvss_to_severity(cvss_score):
    """Convierte CVSS score a nivel de severidad."""
    cvss_score = normalize_cvss_score(cvss_score)
    if cvss_score is None:
        return None
    if cvss_score >= 9.0:
        return "CRITICA"
    elif cvss_score >= 7.0:
        return "ALTA"
    elif cvss_score >= 4.0:
        return "MEDIA"
    elif cvss_score > 0:
        return "BAJA"
    return "INFO"


# ── Emojis y labels ──────────────────────────────────────────────────────────

SEVERITY_CONFIG = {
    "CRITICA": {
        "emoji": "🔴",
        "label": "CRÍTICA",
        "color": "#FF0000",
        "priority": 5,
    },
    "ALTA": {
        "emoji": "🟠",
        "label": "ALTA",
        "color": "#FF8C00",
        "priority": 4,
    },
    "MEDIA": {
        "emoji": "🟡",
        "label": "MEDIA",
        "color": "#FFD700",
        "priority": 3,
    },
    "BAJA": {
        "emoji": "🟢",
        "label": "BAJA",
        "color": "#32CD32",
        "priority": 2,
    },
    "INFO": {
        "emoji": "🔵",
        "label": "INFO",
        "color": "#4169E1",
        "priority": 1,
    },
}


def normalize_severity(severity_str):
    """Normaliza cualquier string de severidad a una clave canónica de SEVERITY_CONFIG."""
    if not isinstance(severity_str, str):
        return "INFO"
    s = strip_accents(severity_str.strip()).upper()
    mapping = {
        "CRITICA": "CRITICA",
        "CRITICAL": "CRITICA",
        "CRIT": "CRITICA",
        "ALTA": "ALTA",
        "HIGH": "ALTA",
        "MEDIA": "MEDIA",
        "MEDIUM": "MEDIA",
        "MODERATE": "MEDIA",
        "BAJA": "BAJA",
        "LOW": "BAJA",
        "INFO": "INFO",
        "INFORMATIONAL": "INFO",
        "NONE": "INFO",
    }
    return mapping.get(s, "INFO")


def classify_severity(title, content="", cvss_score=None, iocs=None):
    """
    Clasifica la severidad de una noticia basado en múltiples señales.
    
    Args:
        title: Título de la noticia
        content: Contenido/resumen
        cvss_score: Score CVSS si está disponible (float)
        iocs: Dict de IoCs extraídos
    
    Returns:
        str: Nivel de severidad (CRITICA, ALTA, MEDIA, BAJA, INFO)
    """
    # 1. CVSS Score (señal definitiva si existe)
    if cvss_score is not None:
        cvss_severity = _cvss_to_severity(cvss_score)
        if cvss_severity:
            return cvss_severity

    raw_text = f"{title} {content}"
    text = strip_accents(raw_text)

    # 2. Declaraciones explícitas de nivel
    has_explicit_crit = False
    for kw in EXPLICIT_CRITICAL:
        if re.search(r'(?<!\w)' + re.escape(kw) + r'(?!\w)', text):
            if not re.search(r'(?<!\w)(no|not|non)\s+' + re.escape(kw) + r'(?!\w)', text):
                has_explicit_crit = True
                break

    has_explicit_low = any(re.search(r'(?<!\w)' + re.escape(kw) + r'(?!\w)', text) for kw in EXPLICIT_LOW)
    has_explicit_high = any(re.search(r'(?<!\w)' + re.escape(kw) + r'(?!\w)', text) for kw in EXPLICIT_HIGH)
    has_explicit_medium = any(re.search(r'(?<!\w)' + re.escape(kw) + r'(?!\w)', text) for kw in EXPLICIT_MEDIUM)

    score = 0
    matched_crit = set()
    matched_high = set()
    matched_med = set()

    # Keywords críticas (+10): ordenar por longitud descendente para evitar doble conteo de subfrases
    for keyword in sorted(CRITICAL_KEYWORDS, key=len, reverse=True):
        if re.search(r'(?<!\w)' + re.escape(keyword) + r'(?!\w)', text):
            if not any(keyword in ck for ck in matched_crit):
                score += 10
                matched_crit.add(keyword)

    # Keywords altas (+8): evitar doble conteo si ya es subfrase de una keyword crítica o alta más larga
    for keyword in sorted(HIGH_KEYWORDS, key=len, reverse=True):
        if re.search(r'(?<!\w)' + re.escape(keyword) + r'(?!\w)', text):
            if not any(keyword in k for k in (matched_crit | matched_high)):
                score += 8
                matched_high.add(keyword)

    # Keywords medias (+3): evitar doble conteo si ya es subfrase de una alta/crítica/media
    for keyword in sorted(MEDIUM_KEYWORDS, key=len, reverse=True):
        if re.search(r'(?<!\w)' + re.escape(keyword) + r'(?!\w)', text):
            if not any(keyword in k for k in (matched_crit | matched_high | matched_med)):
                score += 3
                matched_med.add(keyword)

    # Keywords bajas (-3) e info (-4)
    for keyword in LOW_KEYWORDS:
        if re.search(r'(?<!\w)' + re.escape(keyword) + r'(?!\w)', text):
            score -= 3

    for keyword in INFO_KEYWORDS:
        if re.search(r'(?<!\w)' + re.escape(keyword) + r'(?!\w)', text):
            score -= 4

    # 3. Presencia de IoCs (aumenta severidad)
    if iocs:
        if "ipv4" in iocs or "domain" in iocs:
            score += 3
        if "sha256" in iocs or "md5" in iocs:
            score += 4

    # 4. Señales contextuales (CVEs)
    cve_pattern = r'\bCVE-\d{4}-\d{4,}\b'
    cves = {c.upper() for c in re.findall(cve_pattern, raw_text, re.IGNORECASE)}
    if iocs:
        cves.update(c.upper() for c in iocs.get("cve", [])
                    if isinstance(c, str) and re.fullmatch(cve_pattern, c, re.IGNORECASE))
    score += len(cves) * 5

    # 5. Ajustes por declaraciones explícitas
    if has_explicit_crit:
        return "CRITICA"
    if has_explicit_low and not matched_crit:
        return "BAJA"
    if has_explicit_high and not matched_crit:
        score = max(score, 8)
    if has_explicit_medium and not (matched_crit or matched_high):
        score = max(score, 3)

    # 6. Convertir score a severidad
    if score >= 15:
        # Para escalar a CRITICA por texto se requiere al menos una señal crítica o 3+ CVEs
        if matched_crit or len(cves) >= 3:
            return "CRITICA"
        return "ALTA"
    elif score >= 8:
        return "ALTA"
    elif score >= 3:
        return "MEDIA"
    elif score >= 0:
        return "BAJA"
    else:
        return "INFO"


def get_severity_emoji(severity):
    """Retorna emoji para un nivel de severidad."""
    canonical = normalize_severity(severity)
    return SEVERITY_CONFIG.get(canonical, SEVERITY_CONFIG["INFO"])["emoji"]


def get_severity_label(severity):
    """Retorna label formateado para un nivel de severidad."""
    canonical = normalize_severity(severity)
    config = SEVERITY_CONFIG.get(canonical, SEVERITY_CONFIG["INFO"])
    return f"{config['emoji']} {config['label']}"


def format_severity_telegram(severity):
    """Formatea severidad para mensaje de Telegram."""
    canonical = normalize_severity(severity)
    config = SEVERITY_CONFIG.get(canonical, SEVERITY_CONFIG["INFO"])
    return f"{config['emoji']} Severidad: *{config['label']}*"
