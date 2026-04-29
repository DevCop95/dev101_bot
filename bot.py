import os
import time
import logging
import json
import requests
import threading
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from groq import Groq
import schedule
from flask import Flask

# Load environment variables
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WHAPI_TOKEN = os.getenv("WHAPI_TOKEN")
WHAPI_URL = os.getenv("WHAPI_URL")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
WHATSAPP_RECIPIENT = os.getenv("WHATSAPP_RECIPIENT")

# Initialize clients
groq_client = Groq(api_key=GROQ_API_KEY)

# Flask for Render Free Tier
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is running!", 200

# Logging configuration
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

SENT_NEWS_FILE = "sent_news.json"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}

def load_sent_news():
    if os.path.exists(SENT_NEWS_FILE):
        try:
            with open(SENT_NEWS_FILE, "r") as f:
                return json.load(f)
        except:
            return []
    return []

def save_sent_news(sent_news):
    try:
        with open(SENT_NEWS_FILE, "w") as f:
            json.dump(sent_news, f)
    except Exception as e:
        logger.error(f"Error saving sent news: {e}")

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

def send_to_whatsapp(message):
    if not WHATSAPP_RECIPIENT: return
    url = f"{WHAPI_URL.rstrip('/')}/messages/text"
    headers = {"Authorization": f"Bearer {WHAPI_TOKEN}", "Content-Type": "application/json"}
    payload = {"to": WHATSAPP_RECIPIENT, "body": message}
    try:
        requests.post(url, headers=headers, json=payload, timeout=15)
    except Exception as e:
        logger.error(f"Error sending to WhatsApp: {e}")

def is_recent(date_str):
    """Checks if a date is within the last 48 hours."""
    if not date_str: return True # If no date found, we assume it's new but filter by title later
    try:
        # Try to parse common formats
        for fmt in ('%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%d', '%d/%m/%Y'):
            try:
                dt = datetime.strptime(date_str.split('T')[0], fmt.split('T')[0])
                if datetime.now() - dt < timedelta(days=2):
                    return True
            except: continue
    except: pass
    return False

def scrape_cybersecurity_news():
    news_items = []
    try:
        response = requests.get("https://cybersecuritynews.es/category/actualidad/inteligencia-artificial/", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        articles = soup.find_all('article', limit=10)
        for article in articles:
            time_tag = article.find('time')
            date_val = time_tag['datetime'] if time_tag and time_tag.has_attr('datetime') else None
            
            if date_val and not is_recent(date_val): continue
            
            title_tag = article.find(['h2', 'h1', 'h3'], class_='entry-title') or article.find(['h2', 'h3'])
            link_tag = article.find('a')
            if title_tag and link_tag:
                news_items.append({
                    'title': title_tag.text.strip(),
                    'link': link_tag['href'],
                    'source': 'CyberSecurity News'
                })
    except Exception as e:
        logger.error(f"Error in CyberSecurity News: {e}")
    return news_items

def scrape_welivesecurity():
    news_items = []
    try:
        response = requests.get("https://www.welivesecurity.com/la-es/", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        articles = soup.find_all(['div', 'article'], class_=['c-card', 'text-wrapper'], limit=10)
        for article in articles:
            # WeLiveSecurity usually has dates in a span or time tag
            time_tag = article.find('time') or article.find('span', class_='date')
            date_val = time_tag.text.strip() if time_tag else None
            
            # Simple check for year 2026 or 2025 in text if parsing fails
            if date_val and not any(year in date_val for year in ['2026', '2025']): continue

            title_tag = article.find(['h2', 'h3'])
            link_tag = article.find('a')
            if title_tag and link_tag:
                href = link_tag['href']
                news_items.append({
                    'title': title_tag.text.strip(),
                    'link': href if href.startswith('http') else f"https://www.welivesecurity.com{href}",
                    'source': 'WeLiveSecurity'
                })
    except Exception as e:
        logger.error(f"Error in WeLiveSecurity: {e}")
    return news_items

def scrape_impacto_tic():
    news_items = []
    try:
        response = requests.get("https://impactotic.co/categoria/tecnologia/ia/", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        articles = soup.find_all('article', limit=10)
        for article in articles:
            time_tag = article.find('time')
            date_val = time_tag['datetime'] if time_tag and time_tag.has_attr('datetime') else None
            if date_val and not is_recent(date_val): continue

            title_tag = article.find(['h2', 'h3'])
            link_tag = article.find('a')
            if title_tag and link_tag:
                news_items.append({
                    'title': title_tag.text.strip(),
                    'link': link_tag['href'],
                    'source': 'Impacto TIC'
                })
    except Exception as e:
        logger.error(f"Error in Impacto TIC: {e}")
    return news_items

def scrape_wired_espanol():
    news_items = []
    try:
        response = requests.get("https://es.wired.com/tecnologia/inteligencia-artificial", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        articles = soup.select('div[class*="SummaryItemContent"]', limit=10)
        for article in articles:
            time_tag = article.find('time')
            date_val = time_tag['datetime'] if time_tag and time_tag.has_attr('datetime') else None
            if date_val and not is_recent(date_val): continue

            title_tag = article.find(['h2', 'h3'])
            link_tag = article.find('a')
            if title_tag and link_tag:
                href = link_tag['href']
                news_items.append({
                    'title': title_tag.text.strip(),
                    'link': href if href.startswith('http') else f"https://es.wired.com{href}",
                    'source': 'WIRED en Español'
                })
    except Exception as e:
        logger.error(f"Error in WIRED: {e}")
    return news_items

def job():
    logger.info("--- Starting news fetch job ---")
    sent_news = load_sent_news()
    all_news = []
    all_news.extend(scrape_cybersecurity_news())
    all_news.extend(scrape_welivesecurity())
    all_news.extend(scrape_impacto_tic())
    all_news.extend(scrape_wired_espanol())

    new_count = 0
    for item in all_news:
        if item['link'] not in sent_news:
            logger.info(f"Processing NEW item: {item['title']}")
            summary = summarize_news(item['title'], item['title']) 
            final_message = f"🚀 *{item['source']}*\n\n{summary}\n\n🔗 Leer más: {item['link']}"
            send_to_whatsapp(final_message)
            sent_news.append(item['link'])
            new_count += 1
            if len(sent_news) > 200: sent_news.pop(0)
            save_sent_news(sent_news)
            time.sleep(3) 
    
    if new_count == 0:
        logger.info("No recent news found.")

def run_scheduler():
    logger.info("Scheduler started.")
    # Startup check
    send_to_whatsapp("🔍 *Bot actualizado*\nBuscando noticias de las últimas 48 horas...")
    job()
    schedule.every().hour.do(job)
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    scheduler_thread = threading.Thread(target=run_scheduler)
    scheduler_thread.daemon = True
    scheduler_thread.start()
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
