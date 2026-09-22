# intelligence/ioc_extractor.py — Extracción de Indicadores de Compromiso
# Regex patterns para: IPv4/IPv6, dominios, URLs, hashes, emails, CVE IDs
# Output en formato STIX 2.1 simplificado

import re
import ipaddress
import logging
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import uuid4

logger = logging.getLogger(__name__)

# ── Regex Patterns ────────────────────────────────────────────────────────────

PATTERNS = {
    "ipv4": re.compile(
        r'(?<![\w:.])(?:\d{1,3}\.){3}\d{1,3}(?!\w|\.\w)'
    ),
    "ipv6": re.compile(
        r'(?<![\w:])(?:[0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F:.]*(?![\w:])'
    ),
    "domain": re.compile(
        r'(?<![\w.-])(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)'
        r'+(?:com|net|org|io|ru|cn|tk|xyz|top|info|biz|cc|pw|me|co|de|uk|fr|br|in|su|onion)(?![\w-]|\.[\w-])',
        re.IGNORECASE
    ),
    "url": re.compile(
        r'https?://[^\s<>"\')]+',
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
        r'\bCVE-\d{4}-\d{4,}\b',
        re.IGNORECASE
    ),
}

# Dominios a excluir (legítimos que aparecen en noticias y plataformas de fuentes)
DOMAIN_WHITELIST = {
    # Redes y Big Tech
    "google.com", "twitter.com", "x.com", "github.com", "microsoft.com",
    "apple.com", "amazon.com", "facebook.com", "instagram.com", "youtube.com",
    "linkedin.com", "wikipedia.org", "reddit.com", "cloudflare.com",
    "telegram.org", "t.me", "whatsapp.com",
    # Fuentes y Medios de Ciberseguridad / IA
    "xataka.com", "bleepingcomputer.com", "thehackernews.com", "krebsonsecurity.com",
    "darkreading.com", "welivesecurity.com", "schneier.com", "wired.com", "nvd.nist.gov",
    "exploit-db.com", "greynoise.io", "cve.mitre.org", "cybersecuritynews.es",
    "incibe-cert.es", "incibe.es", "cisa.gov", "paloaltonetworks.com", "unit42.paloaltonetworks.com",
    "talosintelligence.com", "cisco.com", "huggingface.co", "dragonjar.org", "elladodelmal.com",
    "unaaldia.hispasec.com", "hispasec.com", "therecord.media", "sans.edu", "isc.sans.edu",
    "substack.com", "iaenespanol.substack.com",
    # CDNs y servicios auxiliares legítimos comunes en feeds
    "0xword.com", "mypublicinbox.com", "blogger.com", "1.bp.blogspot.com", "blogspot.com",
    "googleusercontent.com", "feedburner.com", "github.io", "githubusercontent.com",
    "medium.com", "picsum.photos", "unsplash.com",
}


def _is_private_ip(ip):
    """Reject non-public addresses, including multicast/reserved in both families."""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return (not address.is_global or address.is_reserved or address.is_multicast or
            address.is_loopback or address.is_link_local or address.is_unspecified or
            getattr(address, "is_site_local", False))


def _extract_source_domain(source_url):
    if not source_url or not isinstance(source_url, str):
        return None
    try:
        parsed = urlsplit(source_url)
        return parsed.hostname.lower().rstrip(".") if parsed.hostname else None
    except Exception:
        return None


def _is_whitelisted(host, extra_domain=None):
    host = host.lower().rstrip(".")
    if extra_domain and (host == extra_domain or host.endswith("." + extra_domain)):
        return True
    return any(host == domain or host.endswith("." + domain) for domain in DOMAIN_WHITELIST)


def _defang_ioc(ioc, ioc_type):
    """Defanging de IoCs para presentación segura."""
    if ioc_type == "url":
        return ioc.replace("http://", "hxxp://").replace("https://", "hxxps://").replace(".", "[.]")
    elif ioc_type in ("domain", "email"):
        return ioc.replace(".", "[.]")
    elif ioc_type in ("ipv4", "ipv6"):
        return ioc.replace(".", "[.]")
    return ioc


def extract_iocs(text, source_url=None):
    """
    Extrae todos los IoCs de un texto.
    Retorna dict con categorías y listas de IoCs encontrados.
    Si se proporciona source_url, excluye dinámicamente el dominio de la propia fuente.
    """
    if not text:
        return {}
    source_domain = _extract_source_domain(source_url)
    text = re.sub(r'hxxps?', lambda match: "https" if match[0].lower() == "hxxps" else "http", text, flags=re.I)
    for defanged, plain in (("[.]", "."), ("(.)", "."), ("{.}", "."), ("[:]", ":"), ("[@]", "@")):
        text = text.replace(defanged, plain)

    results = {}
    
    for ioc_type, pattern in PATTERNS.items():
        matches = set(pattern.findall(text))
        
        # Filtrar según tipo
        if ioc_type in ("ipv4", "ipv6"):
            if ioc_type == "ipv6":
                matches = {ip.rstrip(".") for ip in matches}
            matches = {str(ipaddress.ip_address(ip)) for ip in matches if not _is_private_ip(ip)}
        elif ioc_type == "domain":
            matches = {d.lower() for d in matches if not _is_whitelisted(d, extra_domain=source_domain)}
        elif ioc_type == "url":
            urls = set()
            for url in matches:
                url = url.rstrip(".,;!?")
                try:
                    parsed = urlsplit(url)
                    host = parsed.hostname
                    if not host or _is_whitelisted(host, extra_domain=source_domain):
                        continue
                    parsed.port  # Reject malformed ports as well as malformed IPv6 brackets.
                    try:
                        address = ipaddress.ip_address(host)
                    except ValueError:
                        address = None
                    if address is not None and _is_private_ip(str(address)):
                        continue
                    urls.add(url)
                except ValueError:
                    continue
            matches = urls
        elif ioc_type == "cve":
            matches = {c.upper() for c in matches}
        elif ioc_type in ("md5", "sha1", "sha256"):
            matches = {value.lower() for value in matches}
        
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
    order = ["cve", "ipv4", "ipv6", "domain", "sha256", "sha1", "md5", "url", "email"]
    
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
                    "id": f"vulnerability--{uuid4()}",
                    "spec_version": "2.1",
                    "name": cve_id,
                    "created": now,
                    "modified": now,
                    "external_references": [{"source_name": "cve", "external_id": cve_id}],
                })
            continue
        
        stix_info = stix_type_map.get(ioc_type)
        if not stix_info:
            continue
        
        stix_obj_type, stix_field = stix_info
        
        for value in values[:5]:  # Limitar por tipo
            escaped = value.replace("\\", "\\\\").replace("'", "\\'")
            if stix_obj_type == "file":
                # Hashes van como pattern especial
                hash_alg = stix_field.split("'")[1]
                pattern = f"[file:hashes.'{hash_alg}' = '{escaped}']"
            else:
                pattern = f"[{stix_obj_type}:{stix_field} = '{escaped}']"
            
            indicators.append({
                "type": "indicator",
                "id": f"indicator--{uuid4()}",
                "spec_version": "2.1",
                "name": f"{ioc_type}: {value}",
                "description": f"Unvalidated observable extracted from: {title or source or 'text'}",
                "pattern": pattern,
                "pattern_type": "stix",
                "pattern_version": "2.1",
                "indicator_types": ["unknown"],
                "valid_from": now,
                "created": now,
                "modified": now,
                "labels": ["unvalidated-observable"],
            })
    
    if not indicators:
        return None
    
    return {
        "type": "bundle",
        "id": f"bundle--{uuid4()}",
        "objects": indicators,
    }
