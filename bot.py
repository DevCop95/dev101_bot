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
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
}

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
        response = requests.get("https://cybersecuritynews.es/category/actualidad/inteligencia-artificial/", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        # Based on research, titles are often in h2.entry-title
        articles = soup.find_all(['h2', 'h1'], class_='entry-title', limit=3)
        for title_tag in articles:
            link_tag = title_tag.find('a')
            if title_tag and link_tag:
                news_items.append({
                    'title': title_tag.text.strip(),
                    'link': link_tag['href'],
                    'content': title_tag.text.strip(),
                    'source': 'CyberSecurity News'
                })
        
        # Fallback if no specific class found
        if not news_items:
            articles = soup.find_all('article', limit=3)
            for article in articles:
                title_tag = article.find(['h2', 'h3', 'h1'])
                link_tag = article.find('a')
                if title_tag and link_tag:
                    news_items.append({
                        'title': title_tag.text.strip(),
                        'link': link_tag['href'],
                        'content': title_tag.text.strip(),
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
        response = requests.get("https://www.welivesecurity.com/la-es/", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        # WeLiveSecurity structure often uses articles with specific classes
        articles = soup.find_all(['div', 'article'], class_=['c-card', 'text-wrapper'], limit=3)
        if not articles:
             articles = soup.find_all('h2', limit=3) # Fallback to search for titles directly
        
        for article in articles:
            title_tag = article if article.name == 'h2' else article.find(['h2', 'h3'])
            link_tag = article.find('a') if article.name != 'a' else article
            if not link_tag and title_tag:
                link_tag = title_tag.find('a')
            
            if title_tag and link_tag:
                href = link_tag.get('href', '')
                news_items.append({
                    'title': title_tag.text.strip(),
                    'link': href if href.startswith('http') else f"https://www.welivesecurity.com{href}",
                    'content': title_tag.text.strip(),
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
        response = requests.get("https://impactotic.co/categoria/tecnologia/ia/", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        articles = soup.find_all(['h2', 'h3'], class_='entry-title', limit=3)
        if not articles:
            articles = soup.find_all('article', limit=3)
            
        for article in articles:
            title_tag = article if article.name in ['h2', 'h3'] else article.find(['h2', 'h3'])
            link_tag = article.find('a') if article.name != 'a' else article
            if title_tag and link_tag:
                news_items.append({
                    'title': title_tag.text.strip(),
                    'link': link_tag['href'],
                    'content': title_tag.text.strip(),
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
        response = requests.get("https://es.wired.com/tecnologia/inteligencia-artificial", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        # Wired usually has articles in SummaryItem containers
        articles = soup.select('div[class*="SummaryItemContent"]', limit=3) or \
                   soup.select('div[class*="summary-item__content"]', limit=3) or \
                   soup.find_all('h2', limit=3)
        
        for article in articles:
            title_tag = article if article.name == 'h2' else article.find(['h2', 'h3'])
            link_tag = article.find('a') if article.name != 'a' else article
            if title_tag and link_tag:
                href = link_tag['href']
                news_items.append({
                    'title': title_tag.text.strip(),
                    'link': href if href.startswith('http') else f"https://es.wired.com{href}",
                    'content': title_tag.text.strip(),
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
    
    # Run scrapers
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
            time.sleep(3) 
    
    if new_count == 0:
        logger.info("No new news found in this cycle.")
    else:
        logger.info(f"Job finished. Sent {new_count} new items.")

def run_scheduler():
    logger.info("Scheduler started.")
    # Startup check
    send_to_whatsapp("🔄 *Bot reiniciado*\nBuscando nuevas noticias...")
    
    job()
    schedule.every().hour.do(job)
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    # Start scheduler
    scheduler_thread = threading.Thread(target=run_scheduler)
    scheduler_thread.daemon = True
    scheduler_thread.start()
    
    # Run Flask
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
