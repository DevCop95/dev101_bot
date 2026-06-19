# intelligence/severity_classifier.py — Clasificación de severidad
# Clasifica noticias en: Crítica / Alta / Media / Baja / Info
# Basado en: CVE CVSS, keywords de impacto, tipo de amenaza

import re
import logging

logger = logging.getLogger(__name__)

# ── Clasificación por keywords ────────────────────────────────────────────────

CRITICAL_KEYWORDS = [
    "zero-day", "0-day", "zero day", "rce", "remote code execution",
    "ejecución remota", "actively exploited", "explotado activamente",
    "in the wild", "wormable", "supply chain attack", "cadena de suministro",
    "nation-state", "estado-nación", "critical infrastructure",
    "infraestructura crítica", "data breach", "brecha de datos",
    "millions", "millones", "billions", "ransomware attack",
    "ataque ransomware", "nuclear", "gobierno", "government",
]

HIGH_KEYWORDS = [
    "ransomware", "malware", "exploit", "vulnerability", "vulnerabilidad",
    "cve-", "backdoor", "puerta trasera", "botnet", "ddos",
    "apt", "advanced persistent", "phishing campaign", "campaña de phishing",
    "credential theft", "robo de credenciales", "data leak", "filtración",
    "privilege escalation", "escalada de privilegios", "authentication bypass",
]

MEDIUM_KEYWORDS = [
    "phishing", "spam", "scam", "estafa", "trojan", "troyano",
    "spyware", "adware", "patch", "parche", "update", "actualización",
    "advisory", "aviso", "warning", "alerta", "risk", "riesgo",
    "information disclosure", "divulgación de información",
]

LOW_KEYWORDS = [
    "bug bounty", "responsible disclosure", "divulgación responsable",
    "minor", "menor", "low severity", "baja severidad", "informational",
    "research", "investigación", "proof of concept", "poc",
    "tutorial", "guide", "guía", "best practice", "buena práctica",
]

# ── Severidad por CVSS ───────────────────────────────────────────────────────

def _cvss_to_severity(cvss_score):
    """Convierte CVSS score a nivel de severidad."""
    if cvss_score >= 9.0:
        return "CRITICA"
    elif cvss_score >= 7.0:
        return "ALTA"
    elif cvss_score >= 4.0:
        return "MEDIA"
    elif cvss_score > 0:
        return "BAJA"
    return None


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
    text = f"{title} {content}".lower()
    score = 0
    
    # 1. CVSS Score (señal más fuerte)
    if cvss_score:
        cvss_severity = _cvss_to_severity(float(cvss_score))
        if cvss_severity:
            return cvss_severity  # CVSS es definitivo si existe
    
    # 2. Keywords de severidad
    for keyword in CRITICAL_KEYWORDS:
        if keyword in text:
            score += 10
    
    for keyword in HIGH_KEYWORDS:
        if keyword in text:
            score += 5
    
    for keyword in MEDIUM_KEYWORDS:
        if keyword in text:
            score += 2
    
    for keyword in LOW_KEYWORDS:
        if keyword in text:
            score -= 3
    
    # 3. Presencia de IoCs (aumenta severidad)
    if iocs:
        if "cve" in iocs:
            score += 5 * len(iocs["cve"])
        if "ipv4" in iocs or "domain" in iocs:
            score += 3
        if "sha256" in iocs or "md5" in iocs:
            score += 4
    
    # 4. Señales contextuales
    # Múltiples CVEs = más severo
    cve_count = len(re.findall(r'CVE-\d{4}-\d+', text, re.IGNORECASE))
    score += cve_count * 3
    
    # Convertir score a severidad
    if score >= 15:
        return "CRITICA"
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
    return SEVERITY_CONFIG.get(severity, SEVERITY_CONFIG["INFO"])["emoji"]


def get_severity_label(severity):
    """Retorna label formateado para un nivel de severidad."""
    config = SEVERITY_CONFIG.get(severity, SEVERITY_CONFIG["INFO"])
    return f"{config['emoji']} {config['label']}"


def format_severity_telegram(severity):
    """Formatea severidad para mensaje de Telegram."""
    config = SEVERITY_CONFIG.get(severity, SEVERITY_CONFIG["INFO"])
    return f"{config['emoji']} Severidad: *{config['label']}*"
