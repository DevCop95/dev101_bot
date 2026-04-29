import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}

def is_recent(date_str):
    if not date_str: return True
    try:
        clean_date = date_str.split('T')[0]
        dt = datetime.strptime(clean_date, '%Y-%m-%d')
        if datetime.now() - dt < timedelta(days=30):
            return True
    except: pass
    return False

def test_cybersecurity_news():
    print("\n--- Testing CyberSecurity News ---")
    try:
        response = requests.get("https://cybersecuritynews.es/category/actualidad/inteligencia-artificial/", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        articles = soup.find_all(['article', 'div'], class_=lambda x: x and ('post' in x or 'article' in x), limit=15)
        if not articles: articles = soup.find_all('article', limit=15)
        found = 0
        for article in articles:
            title_tag = article.find(['h1', 'h2', 'h3'])
            link_tag = title_tag.find('a') if title_tag else article.find('a', href=True)
            if link_tag and len(link_tag.text.strip()) > 25:
                print(f"[OK] {link_tag.text.strip()}")
                found += 1
        print(f"Total found: {found}")
    except Exception as e: print(f"Error: {e}")

def test_welivesecurity():
    print("\n--- Testing WeLiveSecurity ---")
    try:
        response = requests.get("https://www.welivesecurity.com/la-es/", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        articles = soup.find_all('div', class_='article-list-card', limit=15)
        if not articles: articles = soup.find_all('div', class_='article', limit=15)
        found = 0
        for article in articles:
            link_tag = article.find('a', href=True)
            title_tag = article.find('p', class_='title') or article.find(['h2', 'h3'])
            title = title_tag.text.strip() if title_tag else (link_tag['title'] if link_tag and link_tag.has_attr('title') else "")
            if title and link_tag:
                print(f"[OK] {title}")
                found += 1
        print(f"Total found: {found}")
    except Exception as e: print(f"Error: {e}")

def test_impacto_tic():
    print("\n--- Testing Impacto TIC ---")
    try:
        response = requests.get("https://impactotic.co/categoria/tecnologia/ia/", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        cards = soup.find_all('div', class_='card-post', limit=15)
        found = 0
        for card in cards:
            title_tag = card.find(['h2', 'h3'], class_='card-post__title')
            link_tag = card.find('a', href=True)
            if title_tag and link_tag:
                print(f"[OK] {title_tag.text.strip()}")
                found += 1
        print(f"Total found: {found}")
    except Exception as e: print(f"Error: {e}")

def test_wired_espanol():
    print("\n--- Testing WIRED en Español ---")
    try:
        url = "https://es.wired.com/tag/inteligencia-artificial"
        response = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        articles = soup.find_all('div', class_=lambda x: x and 'SummaryItemContent' in x, limit=15)
        if not articles: articles = soup.find_all('h2', limit=15)
        found = 0
        for article in articles:
            link_tag = article.find('a') if article.name != 'a' else article
            if link_tag and len(link_tag.text.strip()) > 20:
                print(f"[OK] {link_tag.text.strip()}")
                found += 1
        print(f"Total found: {found}")
    except Exception as e: print(f"Error: {e}")

if __name__ == "__main__":
    test_cybersecurity_news()
    test_welivesecurity()
    test_impacto_tic()
    test_wired_espanol()
