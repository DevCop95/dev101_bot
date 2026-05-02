# run_job.py — Entry point para GitHub Actions
import os, re, time, logging, json, base64, requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from groq import Groq

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN       = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID     = os.environ["TELEGRAM_CHAT_ID"]
UNSPLASH_ACCESS_KEY  = os.environ["UNSPLASH_ACCESS_KEY"]
GROQ_API_KEY         = os.environ["GROQ_API_KEY"]
GIT_TOKEN         = os.environ["GIT_TOKEN"]
GITHUB_REPO          = "DevCop95/cYHBernews"
GITHUB_FILE          = "noticias.json"

groq_client = Groq(api_key=GROQ_API_KEY)

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def clean_markdown(text):
    return re.sub(r'\*+', '', text).strip()

def is_recent(date_str):
    if not date_str:
        return False
    try:
        clean = date_str.split('T')[0].strip()
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%Y/%m/%d'):
            try:
                dt = datetime.strptime(clean, fmt)
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
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 401:
            logger.error(f"Error 401: El token GIT_TOKEN no es válido o no tiene permisos para {GITHUB_REPO}")
            return None, None
        if r.status_code == 404:
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
        logger.error(f"Error leyendo noticias.json: {e}")
        if hasattr(e, 'response') and e.response is not None:
             logger.error(f"Response: {e.response.text}")
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
            "Authorization": f"token {token}",
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

# ── Scrapers ──────────────────────────────────────────────────────────────────

BLOCKED_URLS = {
    "https://cybersecuritynews.es/ciber-insurance-day-22-el-evento-del-ciberseguro-ya-esta-aqui/",
    "https://cybersecuritynews.es/cyber-insurance-day-22-objetivo-concienciar-informar-sobre-ciberseguros/",
    "https://cybersecuritynews.es/la-necesidad-de-contar-con-un-ciberseguro/",
}

def scrape_cybersecurity_news():
    try:
        r = requests.get(
            "https://cybersecuritynews.es/category/actualidad/inteligencia-artificial/",
            headers=HEADERS, timeout=15
        )
        soup = BeautifulSoup(r.text, 'html.parser')
        for article in soup.find_all('article', limit=15):
            title_tag = article.find(['h1', 'h2', 'h3'])
            link_tag = title_tag.find('a') if title_tag else article.find('a', href=True)
            if not link_tag:
                continue
            href, title = link_tag['href'], link_tag.text.strip()
            title = title.replace("AntAnterior", "").replace("Siguiente", "").strip()
            if href in BLOCKED_URLS or len(title) <= 25:
                continue
            return [{'title': title, 'link': href, 'source': 'CyberSecurity News'}]
    except Exception as e:
        logger.error(f"CSN error: {e}")
    return []

def scrape_welivesecurity():
    try:
        r = requests.get("https://www.welivesecurity.com/la-es/", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, 'html.parser')
        for article in soup.find_all('div', class_=['article-list-card', 'article'], limit=5):
            time_tag = article.find('time') or article.find('span', class_='date')
            date_text = time_tag.text.strip() if time_tag else ""
            if date_text and "202" in date_text and "2026" not in date_text:
                continue
            link_tag = article.find('a', href=True)
            title_tag = article.find('p', class_='title') or article.find(['h2', 'h3'])
            title = title_tag.text.strip() if title_tag else ""
            if title and link_tag:
                href = link_tag['href']
                return [{'title': title,
                         'link': href if href.startswith('http') else f"https://www.welivesecurity.com{href}",
                         'source': 'WeLiveSecurity'}]
    except Exception as e:
        logger.error(f"WLS error: {e}")
    return []

def scrape_impacto_tic():
    try:
        r = requests.get("https://impactotic.co/categoria/tecnologia/ia/", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, 'html.parser')
        for card in soup.find_all('div', class_='card-post', limit=10):
            date_tag = card.find('p', class_='card-post__data')
            date_text = date_tag.text.strip() if date_tag else ""
            if any(y in date_text for y in ["2020","2021","2022","2023","2024","2025"]):
                continue
            title_tag = card.find(['h2', 'h3'], class_='card-post__title')
            link_tag = card.find('a', href=True)
            if title_tag and link_tag:
                return [{'title': title_tag.text.strip(), 'link': link_tag['href'], 'source': 'Impacto TIC'}]
    except Exception as e:
        logger.error(f"TIC error: {e}")
    return []

def scrape_wired_espanol():
    try:
        r = requests.get("https://es.wired.com/tag/inteligencia-artificial", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, 'html.parser')
        for article in soup.find_all('div', class_=lambda x: x and 'SummaryItemContent' in x, limit=5):
            time_tag = article.find('time')
            date_val = time_tag['datetime'] if time_tag and time_tag.has_attr('datetime') else None
            if date_val and not is_recent(date_val):
                continue
            link_tag = article.find('a')
            if link_tag:
                title = link_tag.text.strip()
                if len(title) > 20:
                    href = link_tag['href']
                    return [{'title': title,
                             'link': href if href.startswith('http') else f"https://es.wired.com{href}",
                             'source': 'WIRED en Español'}]
    except Exception as e:
        logger.error(f"WIRED error: {e}")
    return []

# ── Main ──────────────────────────────────────────────────────────────────────

def job():
    logger.info("=== Iniciando job ===")

    # Deduplicación usando noticias.json en GitHub (no sent_news.json local)
    published_links = get_published_links()
    logger.info(f"URLs ya publicadas: {len(published_links)}")

    all_news = []
    for scraper in [scrape_cybersecurity_news, scrape_welivesecurity,
                    scrape_impacto_tic, scrape_wired_espanol]:
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
