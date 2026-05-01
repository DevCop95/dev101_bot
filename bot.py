import os
import time
import logging
import json
import base64
import requests
import threading
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from groq import Groq
import schedule
from flask import Flask, request

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = "DevCop95/cYHBernews"
GITHUB_FILE = "noticias.json"

groq_client = Groq(api_key=GROQ_API_KEY)
app = Flask(__name__)

# ─── Flask routes ────────────────────────────────────────────────────────────

@app.route('/webhook', methods=['GET', 'POST'])
def whatsapp_webhook():
    if request.method == 'GET':
        token_esperado = os.getenv("VERIFY_TOKEN")
        mode = request.args.get('hub.mode')
        token_recibido = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        if mode == 'subscribe' and token_recibido == token_esperado:
            return challenge, 200
        return "Forbidden", 403
    return "EVENT_RECEIVED", 200

@app.route('/')
def health_check():
    return "Bot is running!", 200

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

SENT_NEWS_FILE = "sent_news.json"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}

# ─── Sent news cache ─────────────────────────────────────────────────────────

def load_sent_news():
    if os.path.exists(SENT_NEWS_FILE):
        try:
            with open(SENT_NEWS_FILE, "r") as f:
                return [str(x) for x in json.load(f)]
        except:
            return []
    return []

def save_sent_news(sent_news):
    try:
        with open(SENT_NEWS_FILE, "w") as f:
            json.dump(sent_news, f)
    except Exception as e:
        logger.error(f"Error saving sent news: {e}")

# ─── GitHub integration ───────────────────────────────────────────────────────

def get_github_file():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        sha = data["sha"]
        raw = base64.b64decode(data["content"]).decode("utf-8").strip()  # ← strip()
        content = json.loads(raw) if raw else []                          # ← si vacío, usa []
        return content, sha
    except Exception as e:
        logger.error(f"Error leyendo noticias.json de GitHub: {e}")
        return None, None

def push_to_github(item, summary_text):
    """Añade una noticia al principio de noticias.json en GitHub."""
    if not GITHUB_TOKEN:
        logger.warning("GITHUB_TOKEN no configurado, saltando push.")
        return

    noticias, sha = get_github_file()
    if noticias is None:
        return

    # Anti-duplicados: revisa los últimos 20 por URL
    ultimas_urls = [n.get("enlace_original", "") for n in noticias[:20]]
    if item["link"] in ultimas_urls:
        logger.info(f"Noticia ya existe en GitHub, saltando: {item['title']}")
        return

    # Construir nueva entrada con la estructura exacta del JSON
    nuevo_id = (noticias[0]["id"] + 1) if noticias else 1
    nueva_noticia = {
        "id": nuevo_id,
        "fecha": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "categoria": detectar_categoria(item["source"]),
        "titulo": summary_text.split("\n")[0].strip(),
        "resumen": "\n".join(summary_text.split("\n")[1:]).strip(),
        "url_imagen": f"https://source.unsplash.com/800x450/?{get_image_keyword(item['source'])}",
        "enlace_original": item["link"],
        "fuente": item["source"]
    }

    noticias.insert(0, nueva_noticia)

    # Máximo 50 noticias en el JSON
    noticias = noticias[:50]

    # Push a GitHub
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    payload = {
        "message": f"feat: add news - {nueva_noticia['titulo'][:60]}",
        "content": base64.b64encode(json.dumps(noticias, ensure_ascii=False, indent=2).encode("utf-8")).decode("utf-8"),
        "sha": sha
    }
    try:
        r = requests.put(url, headers=headers, json=payload, timeout=10)
        r.raise_for_status()
        logger.info(f"✅ Noticia publicada en GitHub: {nueva_noticia['titulo']}")
    except Exception as e:
        logger.error(f"Error haciendo push a GitHub: {e}")

def detectar_categoria(source):
    categorias = {
        "CyberSecurity News": "Ciberseguridad",
        "WeLiveSecurity": "Ciberseguridad",
        "Impacto TIC": "IA",
        "WIRED en Español": "IA"
    }
    return categorias.get(source, "Tech")

def get_image_keyword(source):
    keywords = {
        "CyberSecurity News": "cybersecurity,hacker",
        "WeLiveSecurity": "cybersecurity,security",
        "Impacto TIC": "artificial,intelligence",
        "WIRED en Español": "technology,future"
    }
    return keywords.get(source, "technology")

# ─── Groq summarizer ─────────────────────────────────────────────────────────

def summarize_news(title, content):
    try:
        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "Eres un experto en IA y Ciberseguridad. Resume la noticia en un titular impactante y un resumen de máximo 2 frases en español. Formato: Titular\nResumen"},
                {"role": "user", "content": f"Título: {title}\nContenido: {content}"}
            ],
            temperature=0.5,
            max_tokens=150,
        )
        return completion.choices[0].message.content
    except Exception as e:
        return f"{title}\n(Resumen no disponible)"

# ─── Telegram sender ──────────────────────────────────────────────────────────

def send_to_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    try:
        r = requests.post(url, json=payload)
        logger.info(f"Respuesta de Telegram: {r.json()}")
    except Exception as e:
        logger.error(f"Error enviando a Telegram: {e}")

# ─── Scrapers ─────────────────────────────────────────────────────────────────

def is_recent(date_str):
    if not date_str: return False
    try:
        clean_date = date_str.split('T')[0].strip()
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%Y/%m/%d'):
            try:
                dt = datetime.strptime(clean_date, fmt)
                if datetime.now() - dt < timedelta(days=2):
                    return True
            except:
                continue
    except:
        pass
    return False

def scrape_cybersecurity_news():
    news_items = []
    try:
        response = requests.get("https://cybersecuritynews.es/category/actualidad/inteligencia-artificial/", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        ANCHORED_URLS = [
            "https://cybersecuritynews.es/ciber-insurance-day-22-el-evento-del-ciberseguro-ya-esta-aqui/",
            "https://cybersecuritynews.es/cyber-insurance-day-22-objetivo-concienciar-informar-sobre-ciberseguros/",
            "https://cybersecuritynews.es/la-necesidad-de-contar-con-un-ciberseguro/",
            "https://cybersecuritynews.es/resumen-de-la-jornada-de-puertas-abiertas-en-cybersecurity-news/",
            "https://cybersecuritynews.es/os-invitamos-a-la-jornada-de-puertas-abiertas-de-cybersecurity-news/",
            "https://cybersecuritynews.es/codigos-qr-o-sms-riesgos-de-la-vieja-tecnologia-que-la-pandemia-ha-puesto-de-moda-2/",
            "https://cybersecuritynews.es/cybercoffee-23-con-raquel-ballesteros-responsable-de-desarrollo-de-mercado-en-basque-cybersecurity-centre/",
            "https://cybersecuritynews.es/cyberwebinar-el-epm-antidoto-contra-sus-infecciones-del-malware/"
        ]
        articles = soup.find_all('article', limit=15)
        for article in articles:
            title_tag = article.find(['h1', 'h2', 'h3'])
            link_tag = title_tag.find('a') if title_tag else article.find('a', href=True)
            if link_tag:
                href = link_tag['href']
                title = link_tag.text.strip().replace("AntAnterior", "").replace("Siguiente", "").strip()
                if href in ANCHORED_URLS: continue
                if any(k in title for k in ["Insurance Day", "Puertas Abiertas", "CyberCoffee"]): continue
                if len(title) > 25:
                    news_items.append({'title': title, 'link': href, 'source': 'CyberSecurity News'})
                    break
    except Exception as e:
        logger.error(f"Error in CyberSecurity News: {e}")
    return news_items

def scrape_welivesecurity():
    news_items = []
    try:
        response = requests.get("https://www.welivesecurity.com/la-es/", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        articles = soup.find_all('div', class_=['article-list-card', 'article'], limit=5)
        for article in articles:
            time_tag = article.find('time') or article.find('span', class_='date')
            date_text = time_tag.text.strip() if time_tag else ""
            if date_text and "202" in date_text and "2026" not in date_text: continue
            link_tag = article.find('a', href=True)
            title_tag = article.find('p', class_='title') or article.find(['h2', 'h3'])
            title = title_tag.text.strip() if title_tag else ""
            if title and link_tag:
                href = link_tag['href']
                news_items.append({'title': title, 'link': href if href.startswith('http') else f"https://www.welivesecurity.com{href}", 'source': 'WeLiveSecurity'})
                break
    except Exception as e:
        logger.error(f"Error in WeLiveSecurity: {e}")
    return news_items

def scrape_impacto_tic():
    news_items = []
    try:
        response = requests.get("https://impactotic.co/categoria/tecnologia/ia/", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        cards = soup.find_all('div', class_='card-post', limit=5)
        for card in cards:
            date_tag = card.find('p', class_='card-post__data')
            date_text = date_tag.text.strip() if date_tag else ""
            if date_text and "202" in date_text and not ("2025" in date_text or "2026" in date_text): continue
            title_tag = card.find(['h2', 'h3'], class_='card-post__title')
            link_tag = card.find('a', href=True)
            if title_tag and link_tag:
                news_items.append({'title': title_tag.text.strip(), 'link': link_tag['href'], 'source': 'Impacto TIC'})
                break
    except Exception as e:
        logger.error(f"Error in Impacto TIC: {e}")
    return news_items

def scrape_wired_espanol():
    news_items = []
    try:
        response = requests.get("https://es.wired.com/tag/inteligencia-artificial", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        articles = soup.find_all('div', class_=lambda x: x and 'SummaryItemContent' in x, limit=5)
        for article in articles:
            time_tag = article.find('time')
            date_val = time_tag['datetime'] if time_tag and time_tag.has_attr('datetime') else None
            if date_val and not is_recent(date_val): continue
            link_tag = article.find('a')
            if link_tag:
                title = link_tag.text.strip()
                if len(title) > 20:
                    href = link_tag['href']
                    news_items.append({'title': title, 'link': href if href.startswith('http') else f"https://es.wired.com{href}", 'source': 'WIRED en Español'})
                    break
    except Exception as e:
        logger.error(f"Error in WIRED: {e}")
    return news_items

# ─── Main job ─────────────────────────────────────────────────────────────────

def job():
    logger.info("--- Starting news fetch job ---")
    sent_news = load_sent_news()
    sources = [scrape_cybersecurity_news(), scrape_welivesecurity(), scrape_impacto_tic(), scrape_wired_espanol()]
    all_news = [s[0] for s in sources if s]

    bad_years = ["2020", "2021", "2022", "2023", "2024", "2025"]
    filtered_news = [
        item for item in all_news
        if not any(year in str(item['title']) or year in str(item['link']) for year in bad_years)
        and item['link'] not in sent_news
        and item['title'] not in sent_news
    ]

    for item in filtered_news[:3]:
        logger.info(f"Sending: {item['title']}")
        summary = summarize_news(item['title'], item.get('content', item['title']))
        final_message = f"🚀 *{item['source']}*\n\n{summary}\n\n🔗 Leer más: {item['link']}"
        send_to_telegram(final_message)
        push_to_github(item, summary)  # ← nuevo
        sent_news.extend([item['link'], item['title']])
        if len(sent_news) > 400:
            sent_news = sent_news[-400:]
        save_sent_news(sent_news)
        time.sleep(3)

# ─── Scheduler ────────────────────────────────────────────────────────────────

def run_scheduler():
    logger.info("Scheduler started.")
    job()
    schedule.every(3).hours.do(job)
    while True:
        schedule.run_pending()
        time.sleep(1)

scheduler_thread = threading.Thread(target=run_scheduler)
scheduler_thread.daemon = True
scheduler_thread.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
