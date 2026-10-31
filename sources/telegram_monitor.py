# sources/telegram_monitor.py — Monitoreo de canales públicos de Telegram
# Usa el preview web oficial de Telegram (https://t.me/s/{channel}) en lugar de
# bridges RSS de terceros (rsshub.app / tg.i-c-a.su), que devolvían 403/422.
# No requiere Telethon, cuenta de API ni API key.

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from sources.rss_feeds import scraper, HEADERS, parse_date

logger = logging.getLogger(__name__)

# Canales de threat intelligence conocidos.
# IMPORTANTE: solo sirven canales con "preview web" público habilitado
# (https://t.me/s/{channel} debe mostrar mensajes, no la página de contacto).
# Los 5 anteriores (vaboronkova, RansomwareNews, DailyDarkWeb, exploitin,
# caborangecyberwar) NO exponen preview público → devolvían 0 items.
TELEGRAM_CHANNELS = [
    {"channel": "vxunderground",          "name": "vx-underground"},
    {"channel": "cveNotify",              "name": "CVE Notify"},
    {"channel": "secharvester",           "name": "Security Harvester"},
    {"channel": "Cyber_Security_Channel", "name": "Cyber Security Channel"},
    {"channel": "androidMalware",         "name": "Android Malware"},
]

TME_PREVIEW_URL = "https://t.me/s/{channel}"
MAX_AGE_DAYS = 3          # los canales TI publican seguido; ventana corta
ITEMS_PER_CHANNEL = 3


def _is_recent(dt, max_age_days=MAX_AGE_DAYS, *, now=None):
    if dt is None:
        return True  # si no hay fecha, no descartamos
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return now - timedelta(days=max_age_days) <= dt.astimezone(timezone.utc) <= now


def _scrape_channel(channel, source_name, limit=ITEMS_PER_CHANNEL, *, now=None):
    """Retain undated messages; skip malformed dates/records independently."""
    if limit <= 0:
        return []
    now = now or datetime.now(timezone.utc)
    url = TME_PREVIEW_URL.format(channel=channel)
    try:
        r = scraper.get(url, headers=HEADERS, timeout=15)
        logger.info(f"FETCH {source_name} (t.me/s): Status {r.status_code}")
        if r.status_code != 200:
            logger.error(f"Telegram Error ({source_name}): Status {r.status_code}")
            return []

        soup = BeautifulSoup(r.content, "html.parser")
        messages = soup.select("div.tgme_widget_message")

        items = []
        # Recorrer del más reciente al más antiguo (el preview los lista en orden cronológico)
        for msg in reversed(messages):
            if len(items) >= limit:
                break

            try:
                text_el = msg.select_one("div.tgme_widget_message_text")
                text = text_el.get_text(separator="\n", strip=True) if text_el else ""
                if not text:
                    continue
                if len(text) < 70 and not re.search(r'cve-\d{4}-\d+|https?://|vulnerab|malware|exploit|breach|patch|zero-day|0-day|ransomware|security|\bia\b|\bai\b', text, re.I):
                    continue

                date_link = msg.select_one("a.tgme_widget_message_date")
                link = date_link.get("href", "").strip() if date_link else ""
                if not link:
                    logger.warning("Telegram (%s): invalid message link", source_name)
                    continue

                time_el = msg.select_one("time[datetime]")
                date_str = time_el.get("datetime", "") if time_el else ""
                pub_dt = parse_date(date_str)
                if date_str and pub_dt is None:
                    logger.warning("Telegram (%s): invalid message date", source_name)
                    continue
                if not _is_recent(pub_dt, now=now):
                    continue

                non_empty_lines = [line.strip() for line in text.splitlines() if line.strip()]
                first_line = non_empty_lines[0] if non_empty_lines else ""
                # Muchos canales encabezan con banners tipo "🚨 BREAKING 🚨" o "[ALERTA]":
                # si la primera línea casi no tiene texto alfanumérico, usar la siguiente
                # para no colapsar noticias distintas bajo la misma clave de título.
                if len(non_empty_lines) > 1 and len(re.sub(r'[^A-Za-z0-9ÁÉÍÓÚÑáéíóúñ]', '', first_line)) < 15:
                    for extra_line in non_empty_lines[1:]:
                        if len(re.sub(r'[^A-Za-z0-9ÁÉÍÓÚÑáéíóúñ]', '', extra_line)) >= 15:
                            first_line = extra_line
                            break
                if len(first_line) > 120:
                    candidate = first_line[:117].rsplit(" ", 1)[0].rstrip(" ,;:-—.")
                    title = f"{candidate}..." if candidate else (first_line[:117] + "...")
                else:
                    title = first_line
                items.append({
                    "title": title,
                    "link": link,
                    "source": source_name,
                    "content": text,
                })
            except Exception as e:
                logger.warning("Telegram (%s): invalid message (%s)", source_name, type(e).__name__)

        return items

    except Exception as e:
        logger.error(f"Telegram Error ({source_name}): {e}")
        return []


def scrape_telegram_channels(*, now=None):
    """Monitorea canales públicos de Telegram en paralelo conservando el orden."""
    all_items = []
    now = now or datetime.now(timezone.utc)
    if not TELEGRAM_CHANNELS:
        return []
    workers = min(5, len(TELEGRAM_CHANNELS))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_scrape_channel, ch_info["channel"], f"TG: {ch_info['name']}", now=now)
            for ch_info in TELEGRAM_CHANNELS
        ]
        for fut in futures:
            all_items.extend(fut.result())

    logger.info(f"Telegram Monitor: {len(all_items)} items totales de {len(TELEGRAM_CHANNELS)} canales")
    return all_items
