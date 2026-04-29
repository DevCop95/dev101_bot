import os
import time
import logging
import json
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from groq import Groq
import schedule
from telegram import Bot

# Load environment variables
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WHAPI_TOKEN = os.getenv("WHAPI_TOKEN")
WHAPI_URL = os.getenv("WHAPI_URL")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
WHATSAPP_RECIPIENT = os.getenv("WHATSAPP_RECIPIENT")

# Initialize clients
groq_client = Groq(api_key=GROQ_API_KEY)
telegram_bot = Bot(token=TELEGRAM_TOKEN)

# Logging configuration
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

SENT_NEWS_FILE = "sent_news.json"

def load_sent_news():
    if os.path.exists(SENT_NEWS_FILE):
        try:
            with open(SENT_NEWS_FILE, "r") as f:
                return json.load(f)
        except:
            return []
    return []

def save_sent_news(sent_news):
    with open(SENT_NEWS_FILE, "w") as f:
        json.dump(sent_news, f)

def summarize_news(title, content):
    try:
        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "Eres un experto en IA y Ciberseguridad. Resume la siguiente noticia en un titular impactante y un resumen de máximo 2 frases en español. Formato: Titular\nResumen"},
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
        logger.warning("WHATSAPP_RECIPIENT not set. Skipping WhatsApp send.")
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
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        logger.info("Message sent to WhatsApp successfully.")
    except Exception as e:
        logger.error(f"Error sending to WhatsApp: {e}")

def scrape_cybersecurity_news():
    news_items = []
    try:
        response = requests.get("https://cybersecuritynews.es/category/actualidad/inteligencia-artificial/", timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        articles = soup.find_all('article', limit=5)
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
    except Exception as e:
        logger.error(f"Error scraping CyberSecurity News: {e}")
    return news_items

def scrape_welivesecurity():
    news_items = []
    try:
        response = requests.get("https://www.welivesecurity.com/la-es/", timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        articles = soup.find_all('div', class_='text-wrapper', limit=5)
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
    except Exception as e:
        logger.error(f"Error scraping WeLiveSecurity: {e}")
    return news_items

def scrape_impacto_tic():
    news_items = []
    try:
        response = requests.get("https://impactotic.co/categoria/tecnologia/ia/", timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        articles = soup.find_all('article', limit=5)
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
    except Exception as e:
        logger.error(f"Error scraping Impacto TIC: {e}")
    return news_items

def scrape_wired_espanol():
    news_items = []
    try:
        response = requests.get("https://es.wired.com/tecnologia/inteligencia-artificial", timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        articles = soup.find_all('div', class_='SummaryItemContent-fshLpY', limit=5) or \
                   soup.find_all('div', class_='summary-item__content', limit=5)
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
    except Exception as e:
        logger.error(f"Error scraping WIRED en Español: {e}")
    return news_items

def job():
    logger.info("Starting news fetch job...")
    sent_news = load_sent_news()
    all_news = []
    all_news.extend(scrape_cybersecurity_news())
    all_news.extend(scrape_welivesecurity())
    all_news.extend(scrape_impacto_tic())
    all_news.extend(scrape_wired_espanol())

    for item in all_news:
        if item['link'] not in sent_news:
            logger.info(f"Processing new item: {item['title']}")
            summary = summarize_news(item['title'], item.get('content', item['title'])) 
            final_message = f"🚀 *{item['source']}*\n\n{summary}\n\n🔗 Leer más: {item['link']}"
            send_to_whatsapp(final_message)
            sent_news.append(item['link'])
            if len(sent_news) > 200:
                sent_news.pop(0)
            save_sent_news(sent_news)
            time.sleep(2)

def main():
    logger.info("Bot started.")
    # Run once at startup
    job()
    # Schedule to run every hour
    schedule.every().hour.do(job)
    
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    main()
