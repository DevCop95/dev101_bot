# run_job.py — Entry point para GitHub Actions
import os, re, time, logging, json, base64, requests
from datetime import datetime, timedelta, timezone
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

def push_to_github(item, summary_text, categoria):
    token = GIT_TOKEN.strip()
    if not token:
        return
    noticias, sha = get_github_file()
    if noticias is None:
        return

    # Evitar duplicados recientes
    ultimas_urls = {n.get("enlace_original", "") for n in noticias[:30]}
    if item["link"] in ultimas_urls:
        logger.info(f"Ya existe en GitHub: {item['title']}")
        return

    nuevo_id = (noticias[0]["id"] + 1) if noticias else 1
    lines = summary_text.split("\n")
    # Usar timezone-aware datetime
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    nueva = {
        "id": nuevo_id,
        "fecha": ahora,
        "categoria": categoria,
        "titulo": clean_markdown(lines[0].strip()),
        "resumen": clean_markdown("\n".join(lines[1:]).strip()),
        "url_imagen": get_image_url(categoria),
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

# ── Logic ─────────────────────────────────────────────────────────────────────

def detectar_categoria(title, source):
    text = title.lower()
    ia_keywords = ["ia", "ai", "inteligencia artificial", "llm", "openai", "gpt", "gemini", "nvidia", "machine learning", "deep learning", "robotica", "asml"]
    security_keywords = ["seguridad", "hacker", "hacking", "malware", "ransomware", "vulnerabilidad", "ciberataque", "ciberseguridad", "brecha", "deepfake", "privacidad", "phishing"]
    
    if any(k in text for k in ia_keywords):
        return "IA"
    if any(k in text for k in security_keywords):
        return "Ciberseguridad"
    
    return {
        "CyberSecurity News": "Ciberseguridad",
        "WeLiveSecurity": "Ciberseguridad",
        "DragonJAR": "Ciberseguridad",
        "El Lado Del Mal": "Ciberseguridad",
        "IA en Español": "IA"
    }.get(source, "IA" if "IA" in source else "Ciberseguridad" if "Security" in source else "Tech")

def get_image_url(categoria):
    keyword = {
        "Ciberseguridad": "cybersecurity hacker",
        "IA": "artificial intelligence technology",
        "Tech": "technology digital"
    }.get(categoria, "technology")
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
        "Ciberseguridad": "cybersec99",
        "IA": "aitech77",
        "Tech": "tech01"
    }
    return f"https://picsum.photos/seed/{seeds.get(categoria, 'tech01')}/800/450"

# ── Groq ──────────────────────────────────────────────────────────────────────

def summarize_news(title, content):
    if not GROQ_API_KEY:
        logger.error("GROQ_API_KEY no configurada")
        return f"{title}\n(Resumen no disponible)"
    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "Eres un experto en IA y Ciberseguridad. Resume la noticia en un titular impactante y un resumen de máximo 2 frases en español. IMPORTANTE: Si la noticia NO trata sobre IA o Ciberseguridad de forma clara, responde ÚNICAMENTE con la palabra 'RECHAZAR'."},
                {"role": "user", "content": f"Título: {title}\nContenido: {content}"}
            ],
            temperature=0.3,
            max_tokens=150,
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Groq error: {e}")
        return f"{title}\n(Resumen no disponible)"

# ── Telegram ──────────────────────────────────────────────────────────────────

def send_to_telegram(message):
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

def scrape_rss_feed(url, source_name, limit=5):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'xml')
        
        items = []
        for entry in soup.find_all('item', limit=limit):
            title = entry.title.text.strip() if entry.title else ""
            link = entry.link.text.strip() if entry.link else ""
            pub_date = entry.pubDate.text.strip() if entry.pubDate else ""
            description = entry.description.text.strip() if entry.description else ""
            
            if not title or not link:
                continue
                
            if pub_date and not is_recent(pub_date):
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

# ── Scrapers ──────────────────────────────────────────────────────────────────

def scrape_cybersecurity_news():
    return scrape_rss_feed("https://cybersecuritynews.es/feed/", "CyberSecurity News")

def scrape_welivesecurity():
    return scrape_rss_feed("https://www.welivesecurity.com/la-es/feed/", "WeLiveSecurity")

def scrape_dragonjar():
    return scrape_rss_feed("https://www.dragonjar.org/feed", "DragonJAR")

def scrape_el_lado_del_mal():
    return scrape_rss_feed("http://feeds.feedburner.com/ElLadoDelMal", "El Lado Del Mal")

def scrape_ia_en_espanol():
    return scrape_rss_feed("https://iaenespanol.substack.com/feed", "IA en Español")

def scrape_xataka_ia():
    return scrape_rss_feed("https://www.xataka.com/tag/inteligencia-artificial/rss2.xml", "Xataka IA")

def scrape_wired_ia():
    # Usamos el feed general ya que los de etiquetas son inestables (404/400).
    # El filtro de Groq se encargará de seleccionar solo lo relevante a IA/Ciber.
    return scrape_rss_feed("https://es.wired.com/feed", "WIRED en Español")

# ── Main ──────────────────────────────────────────────────────────────────────

def job():
    logger.info("=== Iniciando job ===")

    published_links = get_published_links()
    logger.info(f"URLs ya publicadas: {len(published_links)}")

    scrapers = [
        scrape_cybersecurity_news, 
        scrape_welivesecurity,
        scrape_dragonjar,
        scrape_el_lado_del_mal,
        scrape_ia_en_espanol,
        scrape_xataka_ia,
        scrape_wired_ia
    ]

    all_news = []
    for scraper_func in scrapers:
        results = scraper_func()
        all_news.extend(results)

    # Filtrar por URLs no publicadas
    new_items = [i for i in all_news if i['link'] not in published_links]
    logger.info(f"Items candidatos nuevos: {len(new_items)}")

    count = 0
    for item in new_items:
        if count >= 3: # Limitar a 3 noticias por run
            break
            
        logger.info(f"Procesando: {item['title']}")
        
        # Resumen y filtro de relevancia con Groq
        summary = summarize_news(item['title'], item.get('content', item['title']))
        
        if "RECHAZAR" in summary.upper():
            logger.info(f"Noticia rechazada por irrelevante: {item['title']}")
            continue

        categoria = detectar_categoria(item["title"], item["source"])
        
        final_message = f"🚀 *{item['source']}*\n\n{summary}\n\n🔗 Leer más: {item['link']}"
        send_to_telegram(final_message)
        push_to_github(item, summary, categoria)
        
        count += 1
        time.sleep(3)

    if count == 0:
        logger.info("Sin noticias relevantes nuevas en este run.")

if __name__ == "__main__":
    job()
