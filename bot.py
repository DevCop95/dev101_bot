import os
import time
import logging
import json
import requests
import threading
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from groq import Groq
import schedule
from telegram import Bot
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

def load_sent_news():
    if os.path.exists(SENT_NEWS_FILE):
        try:
            with open(SENT_NEWS_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading sent news: {e}")
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
        logger.info(f"Summarizing news: {title[:50]}...")
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
        logger.error(f"Error summarizing with Groq: {e}")
        return f"{title}\n(Resumen no disponible)"

def send_to_whatsapp(message):
    if not WHATSAPP_RECIPIENT:
        logger.warning("WHATSAPP_RECIPIENT not set.")
        return
    
    url = f"{WHAPI_URL.rstrip('/')}/messages/text"
    headers = {
        "Authorization": f"Bearer {WHAPI_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "to": WHATSAPP_RECIPIENT,
        "body": message
    }
    try:
        logger.info(f"Sending message to WhatsApp ({WHATSAPP_RECIPIENT})...")
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        response.raise_for_status()
        logger.info("Message sent to WhatsApp successfully.")
    except Exception as e:
        logger.error(f"Error sending to WhatsApp: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Response details: {e.response.text}")

def scrape_cybersecurity_news():
    news_items = []
    try:
        logger.info("Scraping CyberSecurity News...")
        response = requests.get("https://cybersecuritynews.es/category/actualidad/inteligencia-artificial/", timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        articles = soup.find_all('article', limit=3)
        for article in articles:
            title_tag = article.find('h3')
            link_tag = article.find('a')
            desc_tag = article.find('div', class_='entry-content') or article.find('p')
            if title_tag and link_tag:
                news_items.append({
                    'title': title_tag.text.strip(),
                    'link': link_tag['href'],
                    'content': desc_tag.text.strip() if desc_tag else title_tag.text.strip(),
                    'source': 'CyberSecurity News'
                })
        logger.info(f"Found {len(news_items)} items in CyberSecurity News")
    except Exception as e:
        logger.error(f"Error scraping CyberSecurity News: {e}")
    return news_items

def scrape_welivesecurity():
    news_items = []
    try:
        logger.info("Scraping WeLiveSecurity...")
        response = requests.get("https://www.welivesecurity.com/la-es/", timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        articles = soup.find_all('div', class_='text-wrapper', limit=3)
        for article in articles:
            title_tag = article.find('h2')
            link_tag = article.find('a')
            desc_tag = article.find('p')
            if title_tag and link_tag:
                news_items.append({
                    'title': title_tag.text.strip(),
                    'link': link_tag['href'] if link_tag['href'].startswith('http') else f"https://www.welivesecurity.com{link_tag['href']}",
                    'content': desc_tag.text.strip() if desc_tag else title_tag.text.strip(),
                    'source': 'WeLiveSecurity'
                })
        logger.info(f"Found {len(news_items)} items in WeLiveSecurity")
    except Exception as e:
        logger.error(f"Error scraping WeLiveSecurity: {e}")
    return news_items

def scrape_impacto_tic():
    news_items = []
    try:
        logger.info("Scraping Impacto TIC...")
        response = requests.get("https://impactotic.co/categoria/tecnologia/ia/", timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        articles = soup.find_all('article', limit=3)
        for article in articles:
            title_tag = article.find('h3')
            link_tag = article.find('a')
            desc_tag = article.find('div', class_='entry-summary') or article.find('p')
            if title_tag and link_tag:
                news_items.append({
                    'title': title_tag.text.strip(),
                    'link': link_tag['href'],
                    'content': desc_tag.text.strip() if desc_tag else title_tag.text.strip(),
                    'source': 'Impacto TIC'
                })
        logger.info(f"Found {len(news_items)} items in Impacto TIC")
    except Exception as e:
        logger.error(f"Error scraping Impacto TIC: {e}")
    return news_items

def scrape_wired_espanol():
    news_items = []
    try:
        logger.info("Scraping WIRED en Español...")
        response = requests.get("https://es.wired.com/tecnologia/inteligencia-artificial", timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        articles = soup.find_all('div', class_='SummaryItemContent-fshLpY', limit=3) or \
                   soup.find_all('div', class_='summary-item__content', limit=3)
        for article in articles:
            title_tag = article.find('h2') or article.find('h3')
            link_tag = article.find('a')
            desc_tag = article.find('div', class_='SummaryItemDek-fWfHte') or article.find('p')
            if title_tag and link_tag:
                news_items.append({
                    'title': title_tag.text.strip(),
                    'link': link_tag['href'] if link_tag['href'].startswith('http') else f"https://es.wired.com{link_tag['href']}",
                    'content': desc_tag.text.strip() if desc_tag else title_tag.text.strip(),
                    'source': 'WIRED en Español'
                })
        logger.info(f"Found {len(news_items)} items in WIRED en Español")
    except Exception as e:
        logger.error(f"Error scraping WIRED en Español: {e}")
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
            summary = summarize_news(item['title'], item.get('content', item['title'])) 
            final_message = f"🚀 *{item['source']}*\n\n{summary}\n\n🔗 Leer más: {item['link']}"
            send_to_whatsapp(final_message)
            sent_news.append(item['link'])
            new_count += 1
            if len(sent_news) > 200:
                sent_news.pop(0)
            save_sent_news(sent_news)
            time.sleep(3) # Small delay between messages
    
    if new_count == 0:
        logger.info("No new news found in this cycle.")
    else:
        logger.info(f"Job finished. Sent {new_count} new items.")

def run_scheduler():
    logger.info("Scheduler started.")
    # Send a startup confirmation to WhatsApp
    send_to_whatsapp("✅ *Bot de Noticias IA activado*\nEl bot está en línea y buscando noticias.")
    
    job()
    schedule.every().hour.do(job)
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    # Start scheduler in a separate thread
    scheduler_thread = threading.Thread(target=run_scheduler)
    scheduler_thread.daemon = True
    scheduler_thread.start()
    
    # Run Flask app
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting web server on port {port}")
    app.run(host='0.0.0.0', port=port)
