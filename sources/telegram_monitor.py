# sources/telegram_monitor.py — Monitoreo de canales de Telegram via RSS bridge
# Usa RSSHub como proxy para convertir canales públicos de Telegram a RSS
# No requiere Telethon ni cuenta de Telegram API

import logging
from sources.rss_feeds import scrape_rss_feed, scrape_rss2json

logger = logging.getLogger(__name__)

# Canales de threat intelligence conocidos
TELEGRAM_CHANNELS = [
    {
        "channel": "vaboronkova",          # Oleg Shakirov — Threat Intel
        "name": "Threat Intel (Shakirov)",
    },
    {
        "channel": "RansomwareNews",        # Ransomware News
        "name": "Ransomware News",
    },
    {
        "channel": "DailyDarkWeb",          # Daily Dark Web
        "name": "Daily Dark Web",
    },
    {
        "channel": "exploitin",             # Exploit.in Feed
        "name": "Exploit.in",
    },
    {
        "channel": "caborangecyberwar",     # Cyber Warfare / APT tracking
        "name": "CyberWarfare Feed",
    },
]

# Bridges RSS alternativos (en caso de que uno falle)
RSS_BRIDGES = [
    "https://rsshub.app/telegram/channel/{channel}",
    "https://tg.i-c-a.su/rss/{channel}",
]


def scrape_telegram_channels():
    """
    Monitorea canales públicos de Telegram via RSS bridges.
    Intenta múltiples bridges por cada canal en caso de fallo.
    """
    all_items = []
    
    for ch_info in TELEGRAM_CHANNELS:
        channel = ch_info["channel"]
        source_name = f"TG: {ch_info['name']}"
        found = False
        
        for bridge_template in RSS_BRIDGES:
            if found:
                break
                
            url = bridge_template.format(channel=channel)
            try:
                items = scrape_rss_feed(url, source_name, limit=3)
                if items:
                    all_items.extend(items)
                    found = True
                    logger.info(f"Telegram {channel}: {len(items)} items via bridge")
            except Exception as e:
                logger.debug(f"Bridge fallido para {channel}: {e}")
                continue
        
        if not found:
            # Último intento via RSS2JSON
            try:
                primary_url = RSS_BRIDGES[0].format(channel=channel)
                items = scrape_rss2json(primary_url, source_name)
                if items:
                    all_items.extend(items)
            except:
                logger.debug(f"Telegram {channel}: todos los bridges fallaron")
    
    logger.info(f"Telegram Monitor: {len(all_items)} items totales de {len(TELEGRAM_CHANNELS)} canales")
    return all_items
