# run_job.py — Entry point para GitHub Actions
# Orquestador principal: recolecta → analiza → enriquece → distribuye

import os, re, time, logging, json, base64, requests
from datetime import datetime, timedelta, timezone
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
logger.info(f"GROQ_API_KEY: {'Configurado' if GROQ_API_KEY else 'FALTANTE'}")
logger.info(f"NVD_API_KEY: {'Configurado' if os.getenv('NVD_API_KEY') else 'No configurado (rate limited)'}")
logger.info(f"GREYNOISE: {'Configurado' if os.getenv('GREYNOISE_API_KEY') else 'No configurado'}")
logger.info("------------------------------------")

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# ── Import modules ────────────────────────────────────────────────────────────
from sources.rss_feeds import ALL_RSS_SCRAPERS
from sources.nvd_cve import scrape_nvd_cves
from sources.exploitdb import scrape_exploitdb, scrape_vulners_recent
from sources.greynoise import scrape_greynoise_trends
from sources.telegram_monitor import scrape_telegram_channels

from intelligence.ioc_extractor import extract_iocs, format_iocs_telegram
from intelligence.mitre_tagger import tag_ttps, format_ttps_telegram
from intelligence.severity_classifier import (
    classify_severity, get_severity_emoji, format_severity_telegram
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def clean_markdown(text):
    return re.sub(r'\*+', '', text).strip()

def interleave_by_source(items):
    """
    Agrupa los items por su fuente ('source') e intercala sus resultados
    en formato Round-Robin para maximizar la diversidad de medios en cada ejecución.
    """
    from collections import defaultdict, deque
    by_source = defaultdict(deque)
    for item in items:
        by_source[item['source']].append(item)
    
    interleaved = []
    # Seguir intercalando hasta que todos los deques estén vacíos
    while by_source:
        to_remove = []
        for source, queue in list(by_source.items()):
            if queue:
                interleaved.append(queue.popleft())
            if not queue:
                to_remove.append(source)
        for source in to_remove:
            del by_source[source]
            
    return interleaved

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

def calcular_similitud(texto1, texto2):
    if not texto1 or not texto2:
        return 0.0

    palabras1 = set(re.findall(r'\b\w+\b', texto1.lower()))
    palabras2 = set(re.findall(r'\b\w+\b', texto2.lower()))

    stopwords = {"el", "la", "los", "las", "un", "una", "unos", "unas", "y", "o", "de", "en", "a", "que", "por", "para", "con", "del", "al", "se", "es", "su", "como", "sobre"}
    palabras1 -= stopwords
    palabras2 -= stopwords

    if not palabras1 or not palabras2:
        return 0.0

    interseccion = palabras1.intersection(palabras2)
    jaccard = len(interseccion) / len(palabras1.union(palabras2))
    overlap = len(interseccion) / min(len(palabras1), len(palabras2))

    return max(jaccard, overlap)

def es_noticia_similar(titulo_nuevo, resumen_nuevo, noticias_existentes, umbral=0.45):
    texto_nuevo = f"{titulo_nuevo} {resumen_nuevo}"
    # Revisar las ultimas 50 noticias para ser mas rigurosos con duplicados
    for noticia in noticias_existentes[:50]:
        texto_existente = f"{noticia.get('titulo', '')} {noticia.get('resumen', '')}"
        similitud = calcular_similitud(texto_nuevo, texto_existente)
        if similitud >= umbral:
            return True, noticia.get('titulo', '')
    return False, ""

def push_to_github(item, titulo, resumen, categoria, severity="", ttps=None, iocs=None):
    token = GIT_TOKEN.strip()
    if not token:
        return
    noticias, sha = get_github_file()
    if noticias is None:
        return

    # Evitar duplicados recientes
    ultimas_urls = {n.get("enlace_original", "") for n in noticias[:500]}
    if item["link"] in ultimas_urls:
        logger.info(f"Ya existe en GitHub: {item['title']}")
        return

    nuevo_id = (noticias[0]["id"] + 1) if noticias else 1
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    used_images = [n.get("url_imagen", "") for n in noticias[:20]]
    used_images = [img for img in used_images if img]

    nueva = {
        "id": nuevo_id,
        "fecha": ahora,
        "categoria": categoria,
        "severidad": severity,
        "titulo": titulo,
        "resumen": resumen,
        "url_imagen": get_image_url(categoria, used_images),
        "enlace_original": item["link"],
        "fuente": item["source"],
        "ttps": [{"id": t["id"], "name": t["name"]} for t in (ttps or [])],
        "iocs": iocs or {},
    }
    noticias.insert(0, nueva)
    noticias = noticias[:500]

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
    # Expanded AI keywords: agents, models, AI companies, frameworks, research orgs
    ia_keywords = [
        "ia", "ai", "inteligencia artificial", "llm", "gpt", "claude", "gemini", "openai",
        "anthropic", "chatgpt", "copilot", "midjourney", "stable diffusion", "dall-e",
        "machine learning", "deep learning", "neural", "transformer", "modelo de lenguaje",
        "nvidia", "amd", "chip", "gpu", "tpu", "acelerador", "computacion",
        "robotica", "robot", "autonomo", "asml", "agente", "rag", "embeddings",
        "hugging face", "langchain", "pytorch", "tensorflow"
    ]
    # Expanded cybersecurity keywords: threats, tools, compliance, incidents
    security_keywords = [
        "seguridad", "ciberseguridad", "hacker", "hacking", "malware", "ransomware",
        "vulnerabilidad", "exploit", "cve-", "zero-day", "0-day", "ciberataque",
        "brecha", "filtracion", "data breach", "deepfake", "privacidad", "phishing",
        "spyware", "trojan", "botnet", "ddos", "firewall", "vpn", "cifrado",
        "encriptacion", "autenticacion", "credential", "password", "contraseña",
        "backdoor", "rootkit", "apt", "threat", "amenaza", "incidente", "parche",
        "compliance", "gdpr", "iso 27001", "nist", "soc", "siem", "xdr", "edr"
    ]

    if any(k in text for k in ia_keywords):
        return "IA"
    if any(k in text for k in security_keywords):
        return "Ciberseguridad"

    return {
        "CyberSecurity News": "Ciberseguridad",
        "WeLiveSecurity": "Ciberseguridad",
        "DragonJAR": "Ciberseguridad",
        "El Lado Del Mal": "Ciberseguridad",
        "Una al Día (Hispasec)": "Ciberseguridad",
        "The Hacker News": "Ciberseguridad",
        "Bleeping Computer": "Ciberseguridad",
        "Krebs on Security": "Ciberseguridad",
        "Dark Reading": "Ciberseguridad",
        "Schneier on Security": "Ciberseguridad",
        "SANS ISC": "Ciberseguridad",
        "The Record": "Ciberseguridad",
        "Wired Security": "Ciberseguridad",
        "NVD (NIST)": "Ciberseguridad",
        "Exploit-DB": "Ciberseguridad",
        "Vulners": "Ciberseguridad",
        "GreyNoise": "Ciberseguridad",
        "IA en Español": "IA",
        "Xataka IA": "IA"
    }.get(source, "IA" if "IA" in source else "Ciberseguridad" if "Security" in source else "Tech")

def get_image_url(categoria, used_images=None):
    if used_images is None:
        used_images = []

    keyword = {
        "Ciberseguridad": "cybersecurity hacker",
        "IA": "artificial intelligence technology",
        "Tech": "technology digital"
    }.get(categoria, "technology")

    for _ in range(5):
        try:
            r = requests.get(
                "https://api.unsplash.com/photos/random",
                params={"query": keyword, "orientation": "landscape"},
                headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"},
                timeout=5
            )
            if r.ok:
                url = r.json()["urls"]["regular"]
                base_url = url.split("?")[0]
                if not any(base_url in used_url for used_url in used_images):
                    return url
        except:
            pass

    import random
    seeds = {
        "Ciberseguridad": ["cybersec99", "cybersec100", "cybersec101", "cybersec102", "cybersec103"],
        "IA": ["aitech77", "aitech78", "aitech79", "aitech80", "aitech81"],
        "Tech": ["tech01", "tech02", "tech03", "tech04", "tech05"]
    }
    random_seed = random.choice(seeds.get(categoria, seeds["Tech"]))
    return f"https://picsum.photos/seed/{random_seed}/800/450"

# ── Groq ──────────────────────────────────────────────────────────────────────

def summarize_news(title, content):
    if not GROQ_API_KEY:
        logger.error("GROQ_API_KEY no configurada")
        return None, None

    # Truncate content to avoid Groq rate limits (max ~10k tokens = ~8000 chars)
    max_content_length = 8000
    if len(content) > max_content_length:
        content = content[:max_content_length] + "..."

    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": """Eres un analista senior de inteligencia de ciberseguridad e IA con 15 años de experiencia en SOCs de nivel 3. Tu estilo es técnico, preciso y directo — como un briefing para un CISO.

CRITERIOS DE ACEPTACIÓN (noticia debe cumplir AL MENOS uno):
- IA: Modelos de lenguaje (GPT, Claude, Gemini, LLaMA), empresas AI (OpenAI, Anthropic, Google AI, Meta AI), herramientas AI (ChatGPT, Copilot, Midjourney), hardware AI (NVIDIA GPUs, TPUs, chips especializados), frameworks ML/DL, agentes autónomos, RAG, embeddings.
- Ciberseguridad: Vulnerabilidades (CVE, exploits, zero-days), malware/ransomware, ataques (phishing, DDoS, APT), brechas de datos, herramientas de seguridad (firewalls, VPN, EDR, SIEM), compliance (GDPR, ISO 27001), incidentes de seguridad, privacidad digital.

RECHAZAR si:
- Tech genérica (apps, e-commerce, social media sin relación IA/seguridad)
- Noticias corporativas/financieras sin aspecto técnico
- Hardware/software general sin enfoque IA o seguridad
- Tutoriales básicos de programación

Si cumple criterios: responde EN ESPAÑOL con este formato exacto:
TÍTULO: [Título impactante de máximo 80 caracteres, estilo briefing de inteligencia]
RESUMEN: [Resumen técnico de máximo 2 frases. Incluye impacto real, vectores de ataque si aplica, y contexto relevante. Habla como analista, no como periodista.]
SECTOR: [Sector afectado: Gobierno, Finanzas, Salud, Tecnología, Telecomunicaciones, Energía, Educación, Todos, N/A]

Si el contenido original está en inglés, tradúcelo al español manteniendo términos técnicos en inglés cuando sea estándar (e.g., zero-day, ransomware, phishing).

Si NO cumple criterios: responde ÚNICAMENTE 'RECHAZAR'."""},
                {"role": "user", "content": f"Título original: {title}\nContenido: {content}"}
            ],
            temperature=0.3,
            max_tokens=250,
        )
        response = r.choices[0].message.content.strip()
        
        if "RECHAZAR" in response.upper():
            return "RECHAZAR", None

        # Parse structured response
        titulo_ai = ""
        resumen_ai = ""
        sector = ""
        
        for line in response.split("\n"):
            line = line.strip()
            if line.upper().startswith("TÍTULO:") or line.upper().startswith("TITULO:"):
                titulo_ai = clean_markdown(line.split(":", 1)[1].strip())
            elif line.upper().startswith("RESUMEN:"):
                resumen_ai = clean_markdown(line.split(":", 1)[1].strip())
            elif line.upper().startswith("SECTOR:"):
                sector = line.split(":", 1)[1].strip()
        
        # Fallback: old format (2 lines)
        if not titulo_ai or not resumen_ai:
            lines = [l.strip() for l in response.split("\n") if l.strip()]
            if len(lines) >= 2:
                titulo_ai = titulo_ai or clean_markdown(lines[0])
                resumen_ai = resumen_ai or clean_markdown(" ".join(lines[1:]))
            elif lines:
                text = lines[0]
                match = re.search(r'[:.!?]\s', text)
                if match:
                    idx = match.start() + 1
                    titulo_ai = titulo_ai or clean_markdown(text[:idx])
                    resumen_ai = resumen_ai or clean_markdown(text[idx:])
                else:
                    titulo_ai = titulo_ai or clean_markdown(text)
                    resumen_ai = resumen_ai or ""
        
        return titulo_ai, resumen_ai

    except Exception as e:
        logger.error(f"Groq error: {e}")
        return None, None

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

# ── Main ──────────────────────────────────────────────────────────────────────

def job():
    logger.info("=== Iniciando job ===")

    # Obtener noticias actuales de GitHub para deduplicación
    noticias_existentes, _ = get_github_file()
    if not noticias_existentes:
        noticias_existentes = []

    published_links = {n.get("enlace_original", "") for n in noticias_existentes}
    logger.info(f"URLs ya publicadas: {len(published_links)}")

    # ── FASE 1: Recolección de todas las fuentes ──────────────────────────────
    logger.info("--- Fase 1: Recolección de fuentes ---")
    
    all_news = []
    
    # RSS feeds (fuentes originales)
    for scraper_func in ALL_RSS_SCRAPERS:
        try:
            results = scraper_func()
            all_news.extend(results)
        except Exception as e:
            logger.error(f"Scraper error: {e}")
    
    # NVD CVE API
    try:
        cve_items = scrape_nvd_cves(hours_back=48, min_cvss=7.0, limit=5)
        all_news.extend(cve_items)
        logger.info(f"NVD: {len(cve_items)} CVEs añadidos")
    except Exception as e:
        logger.error(f"NVD Error: {e}")
    
    # Exploit-DB RSS
    try:
        exploitdb_items = scrape_exploitdb()
        all_news.extend(exploitdb_items)
        logger.info(f"Exploit-DB: {len(exploitdb_items)} items añadidos")
    except Exception as e:
        logger.error(f"Exploit-DB Error: {e}")
    
    # Vulners API
    try:
        vulners_items = scrape_vulners_recent(limit=3)
        all_news.extend(vulners_items)
        logger.info(f"Vulners: {len(vulners_items)} items añadidos")
    except Exception as e:
        logger.error(f"Vulners Error: {e}")
    
    # GreyNoise
    try:
        greynoise_items = scrape_greynoise_trends()
        all_news.extend(greynoise_items)
        logger.info(f"GreyNoise: {len(greynoise_items)} items añadidos")
    except Exception as e:
        logger.error(f"GreyNoise Error: {e}")
    
    # Telegram Channels
    try:
        telegram_items = scrape_telegram_channels()
        all_news.extend(telegram_items)
        logger.info(f"Telegram Channels: {len(telegram_items)} items añadidos")
    except Exception as e:
        logger.error(f"Telegram Monitor Error: {e}")
    
    logger.info(f"Total items recolectados: {len(all_news)}")

    # Filtrar por URLs no publicadas
    new_items = [i for i in all_news if i['link'] not in published_links]
    logger.info(f"Items candidatos nuevos: {len(new_items)}")

    # Intercalar por fuente (Round-Robin) para maximizar la diversidad de medios
    new_items = interleave_by_source(new_items)

    # ── FASE 2 + 3: Procesamiento con IA + Distribución ──────────────────────
    logger.info("--- Fase 2+3: Análisis IA + Distribución ---")
    
    count = 0
    for item in new_items:
        if count >= 10:  # Aumentado de 3 a 5 a 10 (más fuentes = más candidatos)
            break
            
        logger.info(f"Procesando: {item['title']}")
        
        # ── Resumen y filtro de relevancia con Groq ───────────────────────────
        titulo_ai, resumen_ai = summarize_news(item['title'], item.get('content', item['title']))
        
        if titulo_ai == "RECHAZAR":
            logger.info(f"Noticia rechazada por irrelevante: {item['title']}")
            continue
            
        if not titulo_ai or not resumen_ai:
            logger.info(f"Noticia descartada por resumen incompleto: {item['title']}")
            continue

        # Filtrar similitud
        es_similar, titulo_similar = es_noticia_similar(titulo_ai, resumen_ai, noticias_existentes)
        if es_similar:
            logger.info(f"Noticia omitida por demasiada similitud con: {titulo_similar}")
            continue

        categoria = detectar_categoria(titulo_ai, item["source"])
        
        # ── Enriquecimiento de inteligencia ───────────────────────────────────
        
        # Extraer IoCs
        full_text = f"{item['title']} {item.get('content', '')} {titulo_ai} {resumen_ai}"
        iocs = extract_iocs(full_text)
        iocs_text = format_iocs_telegram(iocs)
        
        # Clasificar TTPs MITRE
        ttps = tag_ttps(titulo_ai, resumen_ai)
        ttps_text = format_ttps_telegram(ttps)
        
        # Clasificar severidad
        cvss_score = item.get('cvss_score')
        severity = classify_severity(titulo_ai, resumen_ai, cvss_score=cvss_score, iocs=iocs)
        severity_emoji = get_severity_emoji(severity)
        severity_text = format_severity_telegram(severity)
        
        # ── Construir mensaje de Telegram enriquecido ─────────────────────────
        msg_parts = [
            f"{severity_emoji} *{item['source']}*",
            f"\n*{titulo_ai}*",
            f"\n{resumen_ai}",
        ]
        
        # Añadir severidad
        msg_parts.append(f"\n{severity_text}")
        
        # Añadir TTPs si existen
        if ttps_text:
            msg_parts.append(f"\n📋 *MITRE ATT&CK:*\n{ttps_text}")
        
        # Añadir IoCs si existen
        if iocs_text:
            msg_parts.append(f"\n🔍 *IoCs:*\n{iocs_text}")
        
        msg_parts.append(f"\n🔗 [Leer más]({item['link']})")
        
        final_message = "\n".join(msg_parts)
        
        # ── Distribución ──────────────────────────────────────────────────────
        
        # Telegram
        send_to_telegram(final_message)
        
        
        # GitHub
        push_to_github(
            item, titulo_ai, resumen_ai, categoria,
            severity=severity,
            ttps=ttps,
            iocs=iocs,
        )
        
        # Añadir al listado en memoria para evitar duplicados en el mismo run
        noticias_existentes.insert(0, {
            "titulo": titulo_ai,
            "resumen": resumen_ai,
            "enlace_original": item['link']
        })

        count += 1
        time.sleep(3)

    if count == 0:
        logger.info("Sin noticias relevantes nuevas en este run.")
    else:
        logger.info(f"=== Job completado: {count} noticias publicadas ===")

if __name__ == "__main__":
    job()
