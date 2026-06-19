# sources/greynoise.py — GreyNoise Community API
# Free tier: IP lookup, detección de ruido de internet
# Genera alertas cuando hay IPs maliciosas trending

import os
import logging
import requests

logger = logging.getLogger(__name__)

GREYNOISE_API_KEY = os.getenv("GREYNOISE_API_KEY", "")
GREYNOISE_BASE_URL = "https://api.greynoise.io/v3"
GREYNOISE_COMMUNITY_URL = "https://api.greynoise.io/v3/community"


def scrape_greynoise_trends():
    """
    Consulta GreyNoise para obtener actividad maliciosa trending.
    Usa GNQL (GreyNoise Query Language) para buscar actividad reciente.
    Si no hay API key, retorna vacío silenciosamente.
    """
    if not GREYNOISE_API_KEY:
        logger.info("GreyNoise: Sin API key, saltando...")
        return []
    
    try:
        headers = {
            "Accept": "application/json",
            "key": GREYNOISE_API_KEY,
        }
        
        # Buscar actividad maliciosa reciente: CVEs explotados activamente
        queries = [
            {
                "query": "classification:malicious last_seen:1d",
                "label": "IPs Maliciosas (24h)",
            },
            {
                "query": 'tags:"CVE*" last_seen:1d',
                "label": "CVEs Explotados Activamente",
            },
        ]
        
        items = []
        
        for q in queries:
            try:
                logger.info(f"FETCH GreyNoise: {q['label']}...")
                r = requests.get(
                    f"{GREYNOISE_BASE_URL}/experimental/gnql",
                    params={"query": q["query"], "size": 5},
                    headers=headers,
                    timeout=15,
                )
                
                if r.status_code == 401:
                    logger.warning("GreyNoise: API key inválida o sin permisos GNQL")
                    # Fallback: usar endpoint community (free)
                    return _fallback_community_lookup()
                
                if r.status_code != 200:
                    logger.warning(f"GreyNoise GNQL Error: {r.status_code}")
                    continue
                
                data = r.json()
                count = data.get("count", 0)
                
                if count > 0:
                    # Generar una noticia sobre la actividad detectada
                    top_tags = set()
                    top_cves = set()
                    
                    for entry in data.get("data", [])[:10]:
                        for tag in entry.get("tags", []):
                            if tag.startswith("CVE"):
                                top_cves.add(tag)
                            else:
                                top_tags.add(tag)
                    
                    tags_str = ", ".join(list(top_tags)[:5]) if top_tags else "N/A"
                    cves_str = ", ".join(list(top_cves)[:5]) if top_cves else "N/A"
                    
                    content = (
                        f"GreyNoise detectó {count} IPs con actividad maliciosa en las últimas 24h.\n"
                        f"Tags principales: {tags_str}\n"
                        f"CVEs explotados: {cves_str}"
                    )
                    
                    items.append({
                        'title': f"⚠️ GreyNoise: {count} IPs maliciosas detectadas — {q['label']}",
                        'link': f"https://viz.greynoise.io/query?gnql={requests.utils.quote(q['query'])}",
                        'source': 'GreyNoise',
                        'content': content,
                    })
                    
            except Exception as e:
                logger.error(f"GreyNoise query error: {e}")
                continue
        
        return items
        
    except Exception as e:
        logger.error(f"GreyNoise Error: {e}")
    return []


def _fallback_community_lookup():
    """
    Fallback para el free tier: lookup de IPs conocidas.
    Usa la lista de IPs más reportadas públicamente.
    """
    try:
        # El community endpoint solo permite lookup individual
        # Usamos IPs de honeypots conocidos como semilla
        known_malicious_ips = [
            "71.6.135.131",     # Censys scanner
            "185.142.236.34",   # Known scanner
            "198.235.24.39",    # Known scanner
        ]
        
        items = []
        for ip in known_malicious_ips[:2]:  # Limitar consultas
            try:
                r = requests.get(
                    f"{GREYNOISE_COMMUNITY_URL}/{ip}",
                    headers={
                        "Accept": "application/json",
                        "key": GREYNOISE_API_KEY,
                    },
                    timeout=10,
                )
                
                if r.status_code == 200:
                    data = r.json()
                    if data.get("classification") == "malicious" and data.get("last_seen", ""):
                        name = data.get("name", "Unknown")
                        noise = data.get("noise", False)
                        items.append({
                            'title': f"🔍 GreyNoise: IP maliciosa activa — {ip} ({name})",
                            'link': f"https://viz.greynoise.io/ip/{ip}",
                            'source': 'GreyNoise',
                            'content': f"IP: {ip}\nClasificación: Maliciosa\nNombre: {name}\nRuido: {'Sí' if noise else 'No'}",
                        })
            except:
                continue
        
        return items
        
    except Exception as e:
        logger.error(f"GreyNoise Community fallback error: {e}")
    return []
