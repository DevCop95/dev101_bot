# run_job.py — Entry point para GitHub Actions
import os, re, time, logging, json, base64, requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from groq import Groq
from dotenv import load_dotenv

# Cargar variables de entorno desde .env si existe (local)
load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
GIT_TOKEN = os.getenv("GIT_TOKEN") or os.getenv("GH_PAT") or ""

TELEGRAM_TOKEN       = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID     = os.getenv("TELEGRAM_CHAT_ID", "")
UNSPLASH_ACCESS_KEY  = os.getenv("UNSPLASH_ACCESS_KEY", "")
GROQ_API_KEY         = os.getenv("GROQ_API_KEY", "")
GITHUB_REPO          = "DevCop95/cYHBernews"
GITHUB_FILE          = "noticias.json"

# Logging setup early
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Diagnóstico de variables
logger.info("--- Diagnóstico de Configuración ---")
logger.info(f"GIT_TOKEN: {'Configurado' if GIT_TOKEN else 'FALTANTE'}")
logger.info("------------------------------------")

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def clean_markdown(text):
    return re.sub(r'\*+', '', text).strip()

def is_recent(date_str):
    if not date_str:
        return False
    
    # Intentar parseo de texto en español (ej: "30 de abril de 2026")
    meses = {
        "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
        "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
        "septiembre": "09", "octubre": "10", "noviembre": "11", "diciembre": "12"
    }
    
    date_clean = date_str.lower().strip()
    for mes_nombre, mes_num in meses.items():
        if mes_nombre in date_clean:
            try:
                # Extraer día y año si existen
                parts = re.findall(r'\d+', date_clean)
                if len(parts) >= 2:
                    dia = parts[0].zfill(2)
                    anio = parts[-1]
                    dt = datetime.strptime(f"{anio}-{mes_num}-{dia}", "%Y-%m-%d")
                    return datetime.now() - dt < timedelta(days=2)
            except:
                pass

    try:
        clean = date_str.split('T')[0].strip()
        # Intentar formatos comunes + formato RSS (RFC 822)
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%Y/%m/%d', '%a, %d %b %Y %H:%M:%S %z'):
            try:
                dt = datetime.strptime(clean if fmt != '%a, %d %b %Y %H:%M:%S %z' else date_str, fmt)
                # Normalizar a offset-naive para la comparación si es necesario
                if dt.tzinfo:
                    dt = dt.replace(tzinfo=None)
                return datetime.now() - dt < timedelta(days=2)
            except:
                continue
    except:
        pass
    return False

# ── GitHub ────────────────────────────────────────────────────────────────────

def get_github_file():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}"
    token = GIT_TOKEN.strip()
    
    if not token:
        return None, None

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    try:
        r = requests.get(url, headers=headers, timeout=10)
        
        if r.status_code == 401:
            logger.error(f"Error 401: El token no es válido o no tiene permisos para {GITHUB_REPO}")
            return None, None
        elif r.status_code == 404:
            logger.info(f"Archivo {GITHUB_FILE} no encontrado. Se creará uno nuevo.")
            return [], None
            
        r.raise_for_status()
        data = r.json()
        content = data.get("content", "")
        if not content:
            return [], data.get("sha")
            
        raw = base64.b64decode(content).decode("utf-8").strip()
        return json.loads(raw) if raw else [], data["sha"]
    except Exception as e:
        logger.error(f"Error leyendo noticias.json en GitHub: {e}")
        return None, None

def get_published_links():
    """Devuelve el set de URLs ya publicadas en noticias.json — sirve como deduplicación."""
    noticias, _ = get_github_file()
    if not noticias:
        return set()
    return {n.get("enlace_original", "") for n in noticias}

def push_to_github(item, summary_text):
    token = GIT_TOKEN.strip()
    if not token:
        return
    noticias, sha = get_github_file()
    if noticias is None:
        return

    ultimas_urls = {n.get("enlace_original", "") for n in noticias[:30]}
    if item["link"] in ultimas_urls:
        logger.info(f"Ya existe en GitHub: {item['title']}")
        return

    nuevo_id = (noticias[0]["id"] + 1) if noticias else 1
    lines = summary_text.split("\n")
    nueva = {
        "id": nuevo_id,
        "fecha": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "categoria": detectar_categoria(item["source"]),
        "titulo": clean_markdown(lines[0].strip()),
        "resumen": clean_markdown("\n".join(lines[1:]).strip()),
        "url_imagen": get_image_url(item["source"]),
        "enlace_original": item["link"],
        "fuente": item["source"]
    }
    noticias.insert(0, nueva)
    noticias = noticias[:50]

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}"
    payload = {
        "message": f"feat: add news - {nueva['titulo'][:60]}",
        "content": base64.b64encode(
            json.dumps(noticias, ensure_ascii=False, indent=2).encode()
        ).decode(),
        "sha": sha
    }
    try:
        r = requests.put(url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json"
        }, json=payload, timeout=10)

        r.raise_for_status()
        logger.info(f"✅ Publicado en GitHub: {nueva['titulo']}")
    except Exception as e:
        logger.error(f"Error push GitHub: {e}")
        if hasattr(e, 'response') and e.response is not None:
             logger.error(f"Response: {e.response.text}")

def detectar_categoria(source):
    return {
        "CyberSecurity News": "Ciberseguridad",
        "WeLiveSecurity": "Ciberseguridad",
        "Impacto TIC": "IA",
        "WIRED en Español": "IA"
    }.get(source, "Tech")

def get_image_url(source):
    keyword = {
        "CyberSecurity News": "cybersecurity hacker",
        "WeLiveSecurity": "cybersecurity malware",
        "Impacto TIC": "artificial intelligence technology",
        "WIRED en Español": "future technology digital"
    }.get(source, "technology")
    try:
        r = requests.get(
            "https://api.unsplash.com/photos/random",
            params={"query": keyword, "orientation": "landscape"},
            headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"},
            timeout=5
        )
        if r.ok:
            return r.json()["urls"]["regular"]
    except:
        pass
    seeds = {
        "CyberSecurity News": "cybersec99",
        "WeLiveSecurity": "security42",
        "Impacto TIC": "aitech77",
        "WIRED en Español": "futuretech11"
    }
    return f"https://picsum.photos/seed/{seeds.get(source, 'tech01')}/800/450"

# ── Groq ──────────────────────────────────────────────────────────────────────

def summarize_news(title, content):
    if not GROQ_API_KEY:
        logger.error("GROQ_API_KEY no configurada")
        return f"{title}\n(Resumen no disponible)"
    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "Eres un experto en IA y Ciberseguridad. Resume la noticia en un titular impactante y un resumen de máximo 2 frases en español. Formato: Titular\nResumen"},
                {"role": "user", "content": f"Título: {title}\nContenido: {content}"}
            ],
            temperature=0.5,
            max_tokens=150,
        )
        return r.choices[0].message.content
    except Exception as e:
        logger.error(f"Groq error: {e}")
        return f"{title}\n(Resumen no disponible)"

# ── Telegram ──────────────────────────────────────────────────────────────────

def send_to_telegram(message):
    # Limpiamos el token por si acaso viene con prefijo 'bot' o espacios
    token = TELEGRAM_TOKEN.strip()
    if token.lower().startswith("bot"):
        token = token[3:]
    
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message,
                  "parse_mode": "Markdown", "disable_web_page_preview": False},
            timeout=10
        )
        if r.status_code != 200:
            logger.error(f"Telegram Error {r.status_code}: {r.text}")
        else:
            logger.info(f"Telegram: {r.status_code}")
    except Exception as e:
        logger.error(f"Error Telegram: {e}")

# ── RSS Scraper ───────────────────────────────────────────────────────────────

def scrape_rss_feed(url, source_name):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'xml')
        
        items = []
        for entry in soup.find_all('item', limit=10):
            title = entry.title.text.strip() if entry.title else ""
            link = entry.link.text.strip() if entry.link else ""
            pub_date = entry.pubDate.text.strip() if entry.pubDate else ""
            
            if not title or not link:
                continue
                
            if pub_date and not is_recent(pub_date):
                continue
                
            items.append({
                'title': title,
                'link': link,
                'source': source_name
            })
            # Solo retornamos la primera noticia válida para mantener consistencia con el flujo actual
            if items:
                return items
                
    except Exception as e:
        logger.error(f"RSS Error ({source_name}): {e}")
    return []

# ── Scrapers ──────────────────────────────────────────────────────────────────

BLOCKED_URLS = {
    "https://cybersecuritynews.es/ciber-insurance-day-22-el-evento-del-ciberseguro-ya-esta-aqui/",
    "https://cybersecuritynews.es/cyber-insurance-day-22-objetivo-concienciar-informar-sobre-ciberseguros/",
    "https://cybersecuritynews.es/la-necesidad-de-contar-con-un-ciberseguro/",
    "https://cybersecuritynews.es/resumen-de-la-jornada-de-puertas-abiertas-en-cybersecurity-news/",
    "https://cybersecuritynews.es/os-invitamos-a-la-jornada-de-puertas-abiertas-de-cybersecurity-news/",
    "https://cybersecuritynews.es/codigos-qr-o-sms-riesgos-de-la-vieja-tecnologia-que-la-pandemia-ha-puesto-de-moda-2/",
    "https://cybersecuritynews.es/cybercoffee-23-con-raquel-ballesteros-responsable-de-desarrollo-de-mercado-en-basque-cybersecurity-centre/",
    "https://cybersecuritynews.es/cyberwebinar-el-epm-antidoto-contra-sus-infecciones-del-malware/",
}

def scrape_cybersecurity_news():
    # RSS de CSN (generalmente incluye IA si es el feed principal o de categoría)
    return scrape_rss_feed("https://cybersecuritynews.es/feed/", "CyberSecurity News")

def scrape_welivesecurity():
    return scrape_rss_feed("https://www.welivesecurity.com/la-es/feed/", "WeLiveSecurity")

def scrape_xataka():
    # Feed verificado de Xataka (el index reenvía hacia acá)
    return scrape_rss_feed("https://www.xataka.com/feedburner.xml", "Xataka")

def scrape_wired_espanol():
    # Feed de Wired España (estándar, a veces requiere el trailing slash)
    return scrape_rss_feed("https://es.wired.com/feed/rss", "WIRED en Español")

# ── Main ──────────────────────────────────────────────────────────────────────

def job():
    logger.info("=== Iniciando job ===")

    # Deduplicación usando noticias.json en GitHub (no sent_news.json local)
    published_links = get_published_links()
    logger.info(f"URLs ya publicadas: {len(published_links)}")

    all_news = []
    # Hemos sustituido scrape_impacto_tic por scrape_xataka
    for scraper in [scrape_cybersecurity_news, scrape_welivesecurity,
                    scrape_xataka, scrape_wired_espanol]:
        results = scraper()
        if results:
            all_news.append(results[0])

    new_items = [i for i in all_news if i['link'] not in published_links]
    logger.info(f"Items nuevos: {len(new_items)}")

    for item in new_items[:3]:
        logger.info(f"Procesando: {item['title']}")
        summary = summarize_news(item['title'], item.get('content', item['title']))
        final_message = f"🚀 *{item['source']}*\n\n{summary}\n\n🔗 Leer más: {item['link']}"
        send_to_telegram(final_message)
        push_to_github(item, summary)
        time.sleep(3)

    if not new_items:
        logger.info("Sin noticias nuevas en este run.")

if __name__ == "__main__":
    job()
