# intelligence/ioc_extractor.py — Extracción de Indicadores de Compromiso
# Regex patterns para: IPv4/IPv6, dominios, URLs, hashes, emails, CVE IDs
# Output en formato STIX 2.1 simplificado

import re
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── Regex Patterns ────────────────────────────────────────────────────────────

PATTERNS = {
    "ipv4": re.compile(
        r'\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b'
    ),
    "ipv6": re.compile(
        r'\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b'
        r'|\b(?:[0-9a-fA-F]{1,4}:){1,7}:\b'
        r'|\b::(?:[0-9a-fA-F]{1,4}:){0,5}[0-9a-fA-F]{1,4}\b'
    ),
    "domain": re.compile(
        r'\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)'
        r'+(?:com|net|org|io|ru|cn|tk|xyz|top|info|biz|cc|pw|me|co|de|uk|fr|br|in|su|onion)\b',
        re.IGNORECASE
    ),
    "url": re.compile(
        r'https?://[^\s<>"\')\]]+',
        re.IGNORECASE
    ),
    "md5": re.compile(
        r'\b[0-9a-fA-F]{32}\b'
    ),
    "sha1": re.compile(
        r'\b[0-9a-fA-F]{40}\b'
    ),
    "sha256": re.compile(
        r'\b[0-9a-fA-F]{64}\b'
    ),
    "email": re.compile(
        r'\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b'
    ),
    "cve": re.compile(
        r'\bCVE-\d{4}-\d{4,7}\b',
        re.IGNORECASE
    ),
}

# Dominios a excluir (legítimos que aparecen en noticias)
DOMAIN_WHITELIST = {
    "google.com", "twitter.com", "x.com", "github.com", "microsoft.com",
    "apple.com", "amazon.com", "facebook.com", "instagram.com", "youtube.com",
    "linkedin.com", "wikipedia.org", "reddit.com", "cloudflare.com",
    "telegram.org", "t.me", "whatsapp.com", "xataka.com", "bleepingcomputer.com",
    "thehackernews.com", "krebsonsecurity.com", "darkreading.com",
    "welivesecurity.com", "schneier.com", "wired.com", "nvd.nist.gov",
    "exploit-db.com", "greynoise.io", "cve.mitre.org", "cybersecuritynews.es",
}

# IPs privadas/reservadas a excluir
PRIVATE_IP_PREFIXES = [
    "10.", "172.16.", "172.17.", "172.18.", "172.19.",
    "172.20.", "172.21.", "172.22.", "172.23.", "172.24.",
    "172.25.", "172.26.", "172.27.", "172.28.", "172.29.",
    "172.30.", "172.31.", "192.168.", "127.", "0.", "169.254.",
]


def _is_private_ip(ip):
    """Verifica si una IP es privada/reservada."""
    return any(ip.startswith(prefix) for prefix in PRIVATE_IP_PREFIXES)


def _defang_ioc(ioc, ioc_type):
    """Defanging de IoCs para presentación segura."""
    if ioc_type == "url":
        return ioc.replace("http://", "hxxp://").replace("https://", "hxxps://").replace(".", "[.]")
    elif ioc_type in ("domain", "email"):
        return ioc.replace(".", "[.]")
    elif ioc_type in ("ipv4", "ipv6"):
        return ioc.replace(".", "[.]")
    return ioc


def extract_iocs(text):
    """
    Extrae todos los IoCs de un texto.
    Retorna dict con categorías y listas de IoCs encontrados.
    """
    if not text:
        return {}
    
    results = {}
    
    for ioc_type, pattern in PATTERNS.items():
        matches = set(pattern.findall(text))
        
        # Filtrar según tipo
        if ioc_type == "ipv4":
            matches = {ip for ip in matches if not _is_private_ip(ip)}
        elif ioc_type == "domain":
            matches = {d.lower() for d in matches if d.lower() not in DOMAIN_WHITELIST}
        elif ioc_type == "url":
            # Filtrar URLs de dominios legítimos
            matches = {u for u in matches 
                       if not any(legit in u.lower() for legit in DOMAIN_WHITELIST)}
        elif ioc_type == "cve":
            matches = {c.upper() for c in matches}
        
        if matches:
            results[ioc_type] = sorted(matches)
    
    return results


def format_iocs_telegram(iocs):
    """
    Formatea IoCs para mensaje de Telegram.
    Retorna string con IoCs defanged o None si no hay.
    """
    if not iocs:
        return None
    
    lines = []
    
    # Orden de prioridad para presentación
    order = ["cve", "ipv4", "domain", "sha256", "sha1", "md5", "url", "email"]
    
    emoji_map = {
        "cve": "🔴",
        "ipv4": "🌐",
        "ipv6": "🌐",
        "domain": "🔗",
        "url": "🔗",
        "sha256": "#️⃣",
        "sha1": "#️⃣",
        "md5": "#️⃣",
        "email": "📧",
    }
    
    label_map = {
        "cve": "CVEs",
        "ipv4": "IPs",
        "ipv6": "IPv6",
        "domain": "Dominios",
        "url": "URLs",
        "sha256": "SHA256",
        "sha1": "SHA1",
        "md5": "MD5",
        "email": "Emails",
    }
    
    for ioc_type in order:
        if ioc_type in iocs:
            emoji = emoji_map.get(ioc_type, "•")
            label = label_map.get(ioc_type, ioc_type)
            defanged = [_defang_ioc(i, ioc_type) for i in iocs[ioc_type][:3]]
            lines.append(f"{emoji} {label}: {', '.join(defanged)}")
    
    # También incluir tipos no en el orden predefinido
    for ioc_type in iocs:
        if ioc_type not in order:
            defanged = [_defang_ioc(i, ioc_type) for i in iocs[ioc_type][:3]]
            lines.append(f"• {ioc_type}: {', '.join(defanged)}")
    
    return "\n".join(lines) if lines else None


def iocs_to_stix(iocs, title="", source=""):
    """
    Convierte IoCs a formato STIX 2.1 simplificado.
    Retorna dict STIX Bundle.
    """
    if not iocs:
        return None
    
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    
    stix_type_map = {
        "ipv4": ("ipv4-addr", "value"),
        "ipv6": ("ipv6-addr", "value"),
        "domain": ("domain-name", "value"),
        "url": ("url", "value"),
        "md5": ("file", "hashes.'MD5'"),
        "sha1": ("file", "hashes.'SHA-1'"),
        "sha256": ("file", "hashes.'SHA-256'"),
        "email": ("email-addr", "value"),
    }
    
    indicators = []
    
    for ioc_type, values in iocs.items():
        if ioc_type == "cve":
            # CVEs se representan como vulnerabilidades, no indicadores
            for cve_id in values:
                indicators.append({
                    "type": "vulnerability",
                    "spec_version": "2.1",
                    "name": cve_id,
                    "created": now,
                    "modified": now,
                })
            continue
        
        stix_info = stix_type_map.get(ioc_type)
        if not stix_info:
            continue
        
        stix_obj_type, stix_field = stix_info
        
        for value in values[:5]:  # Limitar por tipo
            if stix_obj_type == "file":
                # Hashes van como pattern especial
                hash_alg = stix_field.split("'")[1]
                pattern = f"[file:hashes.'{hash_alg}' = '{value}']"
            else:
                pattern = f"[{stix_obj_type}:{stix_field} = '{value}']"
            
            indicators.append({
                "type": "indicator",
                "spec_version": "2.1",
                "name": f"{ioc_type}: {value}",
                "description": f"Extracted from: {title}" if title else "",
                "pattern": pattern,
                "pattern_type": "stix",
                "valid_from": now,
                "created": now,
                "modified": now,
                "labels": ["malicious-activity"],
            })
    
    if not indicators:
        return None
    
    return {
        "type": "bundle",
        "spec_version": "2.1",
        "objects": indicators,
    }
