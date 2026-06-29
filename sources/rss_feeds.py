# sources/rss_feeds.py — Fuentes RSS migradas del run_job.py original
# Cada función retorna lista de dicts con keys: title, link, source, content

import os
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import cloudscraper
import re

logger = logging.getLogger(__name__)

# Inicializamos el scraper de Cloudflare una sola vez
scraper = cloudscraper.create_scraper(
    browser={
        'browser': 'chrome',
        'platform': 'windows',
        'desktop': True
    }
)

HEADERS = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
    'Referer': 'https://www.google.com/',
}


def is_recent(date_str, max_age_days=2):
    """Verifica si una fecha está dentro de los últimos `max_age_days` días."""
    if not date_str:
        return False
    
    meses = {
        "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
        "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
        "septiembre": "09", "octubre": "10", "noviembre": "11", "diciembre": "12"
    }
    
    date_clean = date_str.lower().strip()
    for mes_nombre, mes_num in meses.items():
        if mes_nombre in date_clean:
            try:
                parts = re.findall(r'\d+', date_clean)
                if len(parts) >= 2:
                    dia = parts[0].zfill(2)
                    anio = parts[-1]
                    dt = datetime.strptime(f"{anio}-{mes_num}-{dia}", "%Y-%m-%d")
                    return datetime.now() - dt < timedelta(days=max_age_days)
            except:
                pass

    try:
        clean = date_str.split('T')[0].strip()
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%Y/%m/%d', '%a, %d %b %Y %H:%M:%S %z', '%Y-%m-%d %H:%M:%S'):
            try:
                if fmt == '%a, %d %b %Y %H:%M:%S %z':
                    d_str = date_str.replace('GMT', '+0000').replace('UTC', '+0000')
                    dt = datetime.strptime(d_str, fmt)
                elif fmt == '%Y-%m-%d %H:%M:%S':
                    dt = datetime.strptime(date_str, fmt)
                else:
                    dt = datetime.strptime(clean, fmt)
                if dt.tzinfo:
                    dt = dt.replace(tzinfo=None)
                return datetime.now() - dt < timedelta(days=max_age_days)
            except:
                continue
    except:
        pass
    return False


def scrape_rss_feed(url, source_name, limit=8, max_age_days=3):
    """Scraper genérico de RSS/Atom feeds."""
    try:
        r = scraper.get(url, headers=HEADERS, timeout=15)
        logger.info(f"FETCH {source_name}: Status {r.status_code}")

        if r.status_code != 200:
            logger.error(f"RSS Error ({source_name}): Status {r.status_code}")
            # Fallback to RSS2JSON
            logger.info(f"Intentando fallback RSS2JSON para {source_name}...")
            return scrape_rss2json(url, f"{source_name} (Fallback)")

        # Pasar bytes crudos (r.content) en vez de r.text: deja que el parser XML
        # detecte el encoding declarado en el propio feed y evita el mojibake
        # (UTF-8 decodificado como latin-1 → "â€™") cuando el feed no envía charset.
        soup = BeautifulSoup(r.content, 'xml')
        
        items = []
        entries = soup.find_all('entry', limit=limit)
        if not entries:
            entries = soup.find_all('item', limit=limit)

        # Some feeds use different capitalization or namespaces
        if not entries:
            entries = soup.find_all(re.compile('^entry$', re.IGNORECASE), limit=limit)
        if not entries:
            entries = soup.find_all(re.compile('^item$', re.IGNORECASE), limit=limit)

        for entry in entries:
            title = entry.title.text.strip() if entry.title else ""
            link = ""
            link_tag = entry.find('link')
            if link_tag:
                if link_tag.has_attr('href'):
                    link = link_tag['href'].strip()
                else:
                    link = link_tag.text.strip()

            pub_date = ""
            if entry.published:
                pub_date = entry.published.text.strip()
            elif entry.updated:
                pub_date = entry.updated.text.strip()
            elif entry.pubDate:
                pub_date = entry.pubDate.text.strip()

            description = entry.description.text.strip() if entry.description else ""
            if not description and entry.content:
                description = entry.content.text.strip()
            
            if not title or not link:
                continue
                
            if pub_date and not is_recent(pub_date, max_age_days=max_age_days):
                continue

            items.append({
                'title': title,
                'link': link,
                'source': source_name,
                'content': description
            })
        return items

    except Exception as e:
        logger.error(f"RSS Error ({source_name}): {e}")
    return []


def scrape_rss2json(rss_url, source_name, max_age_days=3):
    """Scraper que usa RSS2JSON como puente para feeds bloqueados."""
    api_url = f"https://api.rss2json.com/v1/api.json?rss_url={rss_url}"
    try:
        r = requests.get(api_url, timeout=15)
        logger.info(f"FETCH {source_name} (RSS2JSON): Status {r.status_code}")
        
        if r.status_code != 200:
            return []
            
        data = r.json()
        if data.get("status") != "ok":
            return []
            
        items = []
        for entry in data.get("items", []):
            title = entry.get("title", "")
            link = entry.get("link", "")
            pub_date = entry.get("pubDate", "")
            description = entry.get("description", "")
            
            if not title or not link:
                continue
                
            if pub_date and not is_recent(pub_date, max_age_days=max_age_days):
                continue

            items.append({
                'title': title,
                'link': link,
                'source': source_name,
                'content': description
            })
        return items
    except Exception as e:
        logger.error(f"RSS2JSON Error ({source_name}): {e}")
    return []


# ── Cybersecurity Sources ─────────────────────────────────────────────────────

def scrape_cybersecurity_news():
    return scrape_rss_feed("https://cybersecuritynews.es/feed/", "CyberSecurity News")

def scrape_welivesecurity():
    return scrape_rss_feed("https://www.welivesecurity.com/la-es/feed/", "WeLiveSecurity")

def scrape_dragonjar():
    return scrape_rss_feed("https://www.dragonjar.org/feed", "DragonJAR")

def scrape_el_lado_del_mal():
    return scrape_rss_feed("http://feeds.feedburner.com/ElLadoDelMal", "El Lado Del Mal")

def scrape_unaaldia():
    return scrape_rss_feed("https://unaaldia.hispasec.com/feed/", "Una al Día (Hispasec)")

def scrape_bleeping_computer():
    return scrape_rss_feed("https://www.bleepingcomputer.com/feed/", "Bleeping Computer")

def scrape_the_hacker_news():
    return scrape_rss_feed("https://feeds.feedburner.com/TheHackersNews", "The Hacker News")

def scrape_krebsonsecurity():
    return scrape_rss_feed("https://krebsonsecurity.com/feed/", "Krebs on Security")

def scrape_darkreading():
    return scrape_rss_feed("https://www.darkreading.com/rss.xml", "Dark Reading")

def scrape_schneier():
    return scrape_rss_feed("https://www.schneier.com/feed/atom/", "Schneier on Security")

def scrape_sans_isc():
    return scrape_rss_feed("https://isc.sans.edu/rssfeed.xml", "SANS ISC")

def scrape_therecord():
    return scrape_rss_feed("https://therecord.media/feed", "The Record")

def scrape_wired_security():
    return scrape_rss_feed("https://www.wired.com/feed/category/security/latest/rss", "Wired Security")

# ── AI Sources ────────────────────────────────────────────────────────────────

def scrape_ia_en_espanol():
    return scrape_rss2json("https://iaenespanol.substack.com/feed", "IA en Español")

def scrape_xataka_ia():
    return scrape_rss_feed("https://www.xataka.com/tag/inteligencia-artificial/rss2.xml", "Xataka IA")


# ── Aggregated function ──────────────────────────────────────────────────────

ALL_RSS_SCRAPERS = [
    scrape_cybersecurity_news,
    scrape_welivesecurity,
    scrape_dragonjar,
    scrape_el_lado_del_mal,
    scrape_unaaldia,
    scrape_bleeping_computer,
    scrape_the_hacker_news,
    scrape_krebsonsecurity,
    scrape_darkreading,
    scrape_schneier,
    scrape_sans_isc,
    scrape_therecord,
    scrape_wired_security,
    scrape_ia_en_espanol,
    scrape_xataka_ia,
]
