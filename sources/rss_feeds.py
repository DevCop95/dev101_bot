# sources/rss_feeds.py — Fuentes RSS migradas del run_job.py original
# Cada función retorna lista de dicts con keys: title, link, source, content

import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
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


def parse_date(date_str):
    """Parse full feed timestamps as UTC; date-only/naive dates assume UTC."""
    if not isinstance(date_str, str) or not date_str.strip():
        return None
    date_str = date_str.strip()
    dt = None
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(date_str)
        except (ValueError, TypeError, OverflowError):
            pass
    if dt is not None:
        try:
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            return None

    meses = {
        "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
        "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
        "septiembre": "09", "octubre": "10", "noviembre": "11", "diciembre": "12"
    }
    
    spanish = re.fullmatch(r'(\d{1,2})\s+(?:de\s+)?([a-z]+)\s+(?:de\s+)?(\d{4})', date_str.lower())
    if spanish and spanish[2] in meses:
        date_str = f"{spanish[3]}-{meses[spanish[2]]}-{spanish[1].zfill(2)}"
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%Y/%m/%d'):
        try:
            return datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def is_recent(date_str, max_age_days=5, *, now=None):
    """Inclusive UTC window. Missing/invalid dates return False in this helper."""
    dt = parse_date(date_str)
    now = now or datetime.now(timezone.utc)
    now = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
    return dt is not None and now - timedelta(days=max_age_days) <= dt <= now


def scrape_rss_feed(url, source_name, limit=8, max_age_days=5, *, now=None):
    """RSS/Atom: retain undated entries, skip invalid or out-of-window dates."""
    if limit <= 0:
        return []
    now = now or datetime.now(timezone.utc)
    try:
        r = scraper.get(url, headers=HEADERS, timeout=15)
        logger.info(f"FETCH {source_name}: Status {r.status_code}")

        if r.status_code != 200:
            logger.warning(f"RSS Status {r.status_code} ({source_name}). Intentando fallback RSS2JSON...")
            return scrape_rss2json(url, f"{source_name} (Fallback)", max_age_days=max_age_days, now=now)[:limit]



        # Pasar bytes crudos (r.content) en vez de r.text: deja que el parser XML
        # detecte el encoding declarado en el propio feed y evita el mojibake
        # (UTF-8 decodificado como latin-1 → "â€™") cuando el feed no envía charset.
        soup = BeautifulSoup(r.content, 'xml')
        if not soup.find(re.compile(r'^(rss|feed|RDF)$', re.IGNORECASE)):
            logger.error("RSS Error (%s): response is not RSS/Atom", source_name)
            return []
        
        items = []
        entries = soup.find_all('entry')
        if not entries:
            entries = soup.find_all('item')

        # Some feeds use different capitalization or namespaces
        if not entries:
            entries = soup.find_all(re.compile('^entry$', re.IGNORECASE))
        if not entries:
            entries = soup.find_all(re.compile('^item$', re.IGNORECASE))

        for entry in entries:
            if len(items) >= limit:
                break
            title = entry.title.text.strip() if entry.title else ""
            link = ""
            link_tag = next((tag for tag in entry.find_all('link')
                             if tag.get('rel', 'alternate') == 'alternate'), None)
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
            elif entry.find('date'):
                pub_date = entry.find('date').text.strip()

            description = entry.description.text.strip() if entry.description else ""
            if not description and entry.content:
                description = entry.content.text.strip()
            if not description and entry.summary:
                description = entry.summary.text.strip()
            
            if not title or not link:
                logger.warning("RSS (%s): invalid record, missing title/link", source_name)
                continue
                
            if pub_date and not is_recent(pub_date, max_age_days=max_age_days, now=now):
                if parse_date(pub_date) is None:
                    logger.warning("RSS (%s): invalid record date", source_name)
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


def scrape_rss2json(rss_url, source_name, max_age_days=3, *, now=None):
    """Scraper que usa RSS2JSON como puente para feeds bloqueados."""
    api_url = "https://api.rss2json.com/v1/api.json"
    now = now or datetime.now(timezone.utc)
    try:
        r = requests.get(api_url, params={"rss_url": rss_url}, timeout=15)
        logger.info(f"FETCH {source_name} (RSS2JSON): Status {r.status_code}")
        
        if r.status_code != 200:
            logger.error("RSS2JSON Error (%s): Status %s", source_name, r.status_code)
            return []
            
        data = r.json()
        if not isinstance(data, dict) or data.get("status") != "ok" or not isinstance(data.get("items"), list):
            logger.error("RSS2JSON Error (%s): invalid API response", source_name)
            return []
            
        items = []
        for entry in data.get("items", []):
            if not isinstance(entry, dict):
                logger.warning("RSS2JSON (%s): invalid record", source_name)
                continue
            title = entry.get("title", "")
            link = entry.get("link", "")
            pub_date = entry.get("pubDate", "")
            description = entry.get("description", "")
            
            if (not isinstance(title, str) or not title.strip() or
                    not isinstance(link, str) or not link.strip() or
                    not isinstance(description, str)):
                logger.warning("RSS2JSON (%s): invalid record fields", source_name)
                continue
                
            if pub_date and not is_recent(pub_date, max_age_days=max_age_days, now=now):
                if parse_date(pub_date) is None:
                    logger.warning("RSS2JSON (%s): invalid record date", source_name)
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

def scrape_incibe_cert():
    return scrape_rss_feed("https://www.incibe-cert.es/alerta-temprana/avisos-seguridad/feed", "INCIBE-CERT")

def scrape_cisa_advisories():
    return scrape_rss_feed("https://www.cisa.gov/cybersecurity-advisories/all.xml", "CISA Advisories")

def scrape_unit42():
    return scrape_rss_feed("https://unit42.paloaltonetworks.com/feed/", "Unit 42")

def scrape_cisco_talos():
    return scrape_rss_feed("https://blog.talosintelligence.com/rss/", "Cisco Talos")

def scrape_microsoft_security():
    return scrape_rss_feed("https://www.microsoft.com/en-us/security/blog/feed/", "Microsoft Security")

# ── AI Sources ────────────────────────────────────────────────────────────────

def scrape_ia_en_espanol():
    return scrape_rss2json("https://iaenespanol.substack.com/feed", "IA en Español", max_age_days=7)

def scrape_xataka_ia():
    return scrape_rss_feed("https://www.xataka.com/tag/inteligencia-artificial/rss2.xml", "Xataka IA")

def scrape_huggingface():
    return scrape_rss_feed("https://huggingface.co/blog/feed.xml", "Hugging Face")


# ── Aggregated function ──────────────────────────────────────────────────────

ALL_RSS_SCRAPERS = [
    scrape_cybersecurity_news,
    scrape_welivesecurity,
    scrape_incibe_cert,
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
    scrape_cisa_advisories,
    scrape_unit42,
    scrape_cisco_talos,
    scrape_microsoft_security,
    scrape_ia_en_espanol,
    scrape_xataka_ia,
    scrape_huggingface,
]
