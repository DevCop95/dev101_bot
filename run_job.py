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

# Groq admite VARIAS API keys para rotar cuando una agota su cuota diaria (429 TPD).
# Prioridad: GROQ_API_KEY, luego GROQ_API_KEY_2/_3, luego GROQ_API_KEYS (lista CSV).
def _cargar_groq_keys():
    keys = []
    for name in ("GROQ_API_KEY", "GROQ_API_KEY_2", "GROQ_API_KEY_3"):
        v = os.getenv(name, "").strip()
        if v and v not in keys:
            keys.append(v)
    for v in os.getenv("GROQ_API_KEYS", "").split(","):
        v = v.strip()
        if v and v not in keys:
            keys.append(v)
    return keys

GROQ_API_KEYS = _cargar_groq_keys()
GROQ_API_KEY = GROQ_API_KEYS[0] if GROQ_API_KEYS else ""
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
logger.info(f"GROQ API keys: {len(GROQ_API_KEYS)} configurada(s)" if GROQ_API_KEYS else "GROQ API keys: FALTANTE")
logger.info(f"NVD_API_KEY: {'Configurado' if os.getenv('NVD_API_KEY') else 'No configurado (rate limited)'}")
logger.info(f"GREYNOISE: {'Configurado' if os.getenv('GREYNOISE_API_KEY') else 'No configurado'}")
logger.info("------------------------------------")

_groq_clients = [Groq(api_key=k) for k in GROQ_API_KEYS]
_groq_idx = 0            # índice de la key en uso
_groq_exhausted = set()  # índices de keys con cuota DIARIA agotada (este run)

# ── Import modules ────────────────────────────────────────────────────────────
from sources.rss_feeds import ALL_RSS_SCRAPERS
from sources.nvd_cve import scrape_nvd_cves
from sources.exploitdb import scrape_exploitdb  # scrape_vulners_recent desactivado (API anónima descontinuada: 403)
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
    """Lee noticias.json de GitHub con reintentos.

    Contrato (importante para la deduplicación):
      - Devuelve (list, sha)  → lectura correcta (la lista puede estar vacía).
      - Devuelve ([], None)   → el archivo no existe todavía (404, primer run legítimo).
      - Devuelve (None, None) → FALLO real (sin token / 401 / red / JSON corrupto).
                                El llamador DEBE abortar: tratarlo como "vacío"
                                republicaría todo el historial.
    """
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}"
    token = GIT_TOKEN.strip()

    if not token:
        logger.error("GIT_TOKEN no configurado: no se puede leer noticias.json")
        return None, None

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json"
    }

    GITHUB_MAX_RETRIES = 3
    last_error = None
    for attempt in range(1, GITHUB_MAX_RETRIES + 1):
        try:
            r = requests.get(url, headers=headers, timeout=15)

            if r.status_code == 401:
                logger.error(f"Error 401: El token no es válido o no tiene permisos para {GITHUB_REPO}")
                return None, None  # error de auth: no tiene sentido reintentar
            elif r.status_code == 404:
                logger.info(f"Archivo {GITHUB_FILE} no encontrado. Se creará uno nuevo.")
                return [], None

            r.raise_for_status()
            data = r.json()
            content = data.get("content", "")
            if not content:
                # Archivos >1MB: la contents API devuelve content vacío (encoding
                # "none") aunque el archivo exista. Pedir el raw aparte — tratarlo
                # como vacío republicaría todo y SOBREESCRIBIRÍA el historial.
                if data.get("size", 0) > 0:
                    raw_r = requests.get(
                        url,
                        headers={**headers, "Accept": "application/vnd.github.raw+json"},
                        timeout=30,
                    )
                    raw_r.raise_for_status()
                    raw = raw_r.content.decode("utf-8").strip()
                    return json.loads(raw) if raw else [], data.get("sha")
                return [], data.get("sha")

            raw = base64.b64decode(content).decode("utf-8").strip()
            return json.loads(raw) if raw else [], data["sha"]
        except Exception as e:
            last_error = e
            wait = 2 ** attempt  # 2s, 4s, 8s
            logger.warning(f"GitHub lectura intento {attempt}/{GITHUB_MAX_RETRIES} falló ({e}). Reintentando en {wait}s...")
            time.sleep(wait)

    logger.error(f"Error leyendo noticias.json en GitHub tras {GITHUB_MAX_RETRIES} intentos: {last_error}")
    return None, None

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

def _extraer_entidades_tecnicas(texto):
    """Extrae CVE IDs y nombres de producto/tecnología relevantes para comparación exacta."""
    texto = texto.lower()
    cves = set(re.findall(r'cve-\d{4}-\d+', texto))
    # Palabras técnicas significativas de 4+ letras que no son stopwords
    stopwords_extra = {
        "para", "como", "este", "esta", "con", "por", "que", "los", "las",
        "una", "unos", "unas", "del", "desde", "hasta", "sobre", "entre",
        "vulnerabilidad", "critica", "critico", "ataque", "exploit", "sistema",
        "through", "allows", "remote", "local", "code", "execution", "arbitrary"
    }
    palabras = set(re.findall(r'\b[a-z][a-z0-9_\-]{3,}\b', texto))
    palabras -= stopwords_extra
    return cves, palabras

def clave_contenido(titulo_original, contenido=""):
    """Genera una clave de deduplicación estable a partir del contenido ORIGINAL
    (antes del resumen IA, que es no determinista).

    La clave combina:
      - los CVE-IDs presentes (señal exacta y fuerte), o
      - el título original normalizado (sin emojis, puntuación ni espacios extra).

    Así, la misma historia desde fuentes/URLs distintas colapsa a la misma clave.
    """
    texto = f"{titulo_original} {contenido}".lower()
    cves = sorted(set(re.findall(r'cve-\d{4}-\d+', texto)))
    if cves:
        return "cve:" + ",".join(cves)

    # Normalizar título: solo letras/números/espacios, colapsar espacios
    norm = re.sub(r'[^a-z0-9áéíóúñ ]', ' ', titulo_original.lower())
    norm = re.sub(r'\s+', ' ', norm).strip()
    if not norm:
        return ""
    return "txt:" + norm

# Agrupación por MEDIO (no por source): varios canales de Telegram cuentan como
# un solo medio "Telegram" para que no acaparen todos los slots del run.
MEDIO_CAPS = {
    "Telegram": 2,
    "Exploit-DB": 2,
    "NVD (NIST)": 2,
    "GreyNoise": 1,
    "Vulners": 2,
}
MEDIO_CAP_DEFAULT = 2  # cada outlet RSS individual

# ── Diversidad dinámica por ejecución (run) ─────────────────────────────────────
# El cap absoluto de arriba no basta: los runs reales publican ~5 noticias (no 10),
# así que 3 Telegram sobre 5 = 60% aunque el cap "parezca" razonable. Estos límites
# son PROPORCIONALES al tamaño real del run:
#   - Ningún medio supera MEDIO_MAX_SHARE de lo publicado en el run.
#   - El conjunto "underground" (Telegram + Exploit-DB) no supera UNDERGROUND_MAX_SHARE,
#     lo que reserva implícitamente el resto de slots para prensa/RSS mainstream
#     (cuota mínima mainstream).
# El FLOOR garantiza que en runs muy pequeños siempre entre al menos 1 (evita bloqueo).
UNDERGROUND_MEDIOS = {"Telegram", "Exploit-DB"}
MEDIO_MAX_SHARE = 0.40        # un medio individual <= 40% del run real
UNDERGROUND_MAX_SHARE = 0.50  # TG + Exploit-DB juntos <= 50% del run real
DIVERSIDAD_FLOOR = 1          # siempre permitir al menos 1 por (grupo de) medio

def medio_de_fuente(source):
    """Mapea un 'source' a su 'medio' para aplicar cuotas de diversidad."""
    if source.startswith("TG:"):
        return "Telegram"
    # Limpiar el sufijo de fallback "(Fallback)" que añade scrape_rss2json
    return source.replace(" (Fallback)", "").strip()

def cap_para_medio(medio):
    return MEDIO_CAPS.get(medio, MEDIO_CAP_DEFAULT)

def _cap_dinamico(share, count):
    """Máximo permitido para un (grupo de) medio si añadimos un item más.

    Se proyecta sobre (count+1) = tamaño del run tras incluir este item, de modo
    que el ratio resultante no supere `share`. El FLOOR evita bloquear runs pequeños.
    """
    return max(DIVERSIDAD_FLOOR, round(share * (count + 1)))

def pasa_diversidad(medio, medio_counts, count):
    """Decide si un item de `medio` puede publicarse sin romper la diversidad.

    `count` es el nº de noticias ya publicadas en este run. Devuelve (ok, motivo).
    """
    actual = medio_counts.get(medio, 0)
    # 1) Cap absoluto por medio.
    if actual >= cap_para_medio(medio):
        return False, f"cap absoluto del medio ({cap_para_medio(medio)})"
    # 2) Cap dinámico proporcional por medio.
    if actual + 1 > _cap_dinamico(MEDIO_MAX_SHARE, count):
        return False, f"cuota dinámica del medio (<= {int(MEDIO_MAX_SHARE*100)}% del run)"
    # 3) Cuota del grupo "underground" (reserva slots para mainstream).
    if medio in UNDERGROUND_MEDIOS:
        ug = sum(v for m, v in medio_counts.items() if m in UNDERGROUND_MEDIOS)
        if ug + 1 > _cap_dinamico(UNDERGROUND_MAX_SHARE, count):
            return False, f"cuota underground (<= {int(UNDERGROUND_MAX_SHARE*100)}% del run)"
    return True, ""

def es_noticia_similar(titulo_nuevo, resumen_nuevo, noticias_existentes, umbral=0.35, source_nuevo=""):
    texto_nuevo = f"{titulo_nuevo} {resumen_nuevo}"
    cves_nuevo, entidades_nuevo = _extraer_entidades_tecnicas(texto_nuevo)
    candidata = {"titulo": titulo_nuevo, "resumen": resumen_nuevo, "fuente": source_nuevo}
    ventana = noticias_existentes[:100]
    # Frecuencia documental de nombres propios sobre la ventana (para medir rareza).
    df_propios = _df_nombres_propios(ventana)
    for noticia in ventana:
        texto_existente = f"{noticia.get('titulo', '')} {noticia.get('resumen', '')}"
        cves_existente = set(re.findall(r'cve-\d{4}-\d+', texto_existente.lower()))
        # VETO de CVE: si ambas tienen CVE(s) y son DISJUNTOS, son vulnerabilidades
        # DISTINTAS → nunca es la misma noticia (aunque el resumen IA sea calcado,
        # p.ej. "Vulnerabilidad SQL en X permite..."). Evita matar CVEs nuevos.
        if cves_nuevo and cves_existente and not (cves_nuevo & cves_existente):
            continue
        # Chequeo 1: similitud por palabras
        similitud = calcular_similitud(texto_nuevo, texto_existente)
        if similitud >= umbral:
            return True, noticia.get('titulo', '')
        # Chequeo 2: mismo CVE = siempre duplicado
        if cves_nuevo:
            _, entidades_existente = _extraer_entidades_tecnicas(texto_existente)
            if cves_nuevo & cves_existente:
                return True, noticia.get('titulo', '')
            # Chequeo 3: misma entidad técnica + alta superposición de contexto
            entidades_comunes = entidades_nuevo & entidades_existente
            if len(entidades_comunes) >= 3 and similitud >= 0.25:
                return True, noticia.get('titulo', '')
        # Chequeo 4: misma historia desde otro medio (nombre propio raro compartido).
        if _misma_historia_propios(candidata, noticia, df_propios):
            return True, noticia.get('titulo', '')
    return False, ""

# ── Deduplicación retroactiva de alta confianza ───────────────────────────────
# Limpia duplicados que ya se colaron en noticias.json (misma historia desde
# URLs/fuentes distintas con títulos reformulados por la IA, p.ej. SmartLoader).
# Conserva la noticia MÁS RECIENTE y elimina las más antiguas.

_STOPWORDS_DEDUP = {
    "el", "la", "los", "las", "un", "una", "unos", "unas", "y", "o", "de", "en",
    "a", "que", "por", "para", "con", "del", "al", "se", "es", "su", "como", "sobre", "le",
}
# Palabras NO distintivas: comunes en titulares de seguridad/IA. Una entidad
# distintiva es un token de 4+ letras que NO está aquí (nombre de producto,
# malware, vendor, etc.). Sirve para no fusionar historias distintas que solo
# comparten una palabra genérica.
_PALABRAS_GENERICAS = _STOPWORDS_DEDUP | {
    "vulnerabilidad", "vulnerabilidades", "ataque", "ataques", "malware", "ransomware",
    "campana", "codigo", "exploit", "exploits", "nuevo", "nueva", "nuevos", "nuevas",
    "alerta", "alertas", "amenaza", "amenazas", "seguridad", "ciberseguridad", "ciberataque",
    "hacker", "hackers", "grupo", "critico", "critica", "critical", "brecha", "brechas",
    "datos", "fuga", "filtracion", "leak", "robo", "robos", "zero", "day", "parche",
    "parches", "actualizacion", "error", "fallo", "fallos", "riesgo", "urgente",
    "detectado", "descubierto", "analisis", "informe", "transforma", "evita", "usan",
    "usa", "utiliza", "utilizan", "despliega", "lanza", "lanzan", "sufre", "expone",
    "exposicion", "phishing", "spyware", "troyano", "backdoor", "botnet", "firewall",
    "firewalls", "server", "servidor", "software", "hardware", "sistema", "sistemas",
    "aplicacion", "app", "apps", "millones", "miles", "pese", "contra", "mediante",
    "traves", "cadena", "suministro", "operacion",
}

def _norm_dedup(texto):
    """minúsculas + sin acentos (para comparar entidades de forma estable)."""
    mapa = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n"}
    return re.sub(r"[áéíóúñ]", lambda m: mapa[m.group()], texto.lower())

def _tokens_dedup(texto):
    return set(re.findall(r'\b\w+\b', _norm_dedup(texto))) - _STOPWORDS_DEDUP

def _entidades_distintivas(titulo):
    """Tokens de 4+ letras que NO son palabras genéricas (nombres propios/productos)."""
    return {w for w in _tokens_dedup(titulo) if len(w) >= 4 and w not in _PALABRAS_GENERICAS}

def _jaccard_titulos(t1, t2):
    a, b = _tokens_dedup(t1), _tokens_dedup(t2)
    return len(a & b) / len(a | b) if a and b else 0.0

def _cves_de(texto):
    return set(re.findall(r'cve-\d{4}-\d+', texto.lower()))

# ── Dedup por NOMBRES PROPIOS raros (misma historia desde medios distintos) ─────
# El caso que ni la clave de contenido (títulos distintos), ni el CVE (campañas
# sin CVE), ni el Jaccard de títulos atrapaban: la MISMA historia cubierta por
# varios medios con titulares reformulados (FortiBleed, Mistic, WhatsApp/VBScript,
# Operación Endgame, Huione, npm-PostCSS...). La señal limpia es un NOMBRE PROPIO
# (mayúscula/CamelCase/ALLCAPS) RARO en el corpus: 'fortibleed' aparece en pocos
# artículos; 'cisa'/'vulnerabilidad' en decenas. Calibrado sobre noticias.json:
# 14/14 parejas marcadas eran duplicados reales (0 falsos +).
_NOMBRES_PROPIOS_STOP = {
    "una", "este", "esta", "estos", "estas", "para", "como", "desde", "tras", "segun",
    "con", "por", "sin", "cuando", "aunque", "ademas", "varios", "varias", "nueva",
    "nuevo", "nuevos", "agencia", "ahora", "estados", "unidos", "research",
    # genéricos de seguridad que suelen ir capitalizados pero no distinguen historias
    "seguridad", "vulnerabilidad", "vulnerabilidades", "ataque", "ataques", "alerta",
    "urgente", "campana", "campa", "infraestructura", "cibernetica", "ciberseguridad",
    "exploit", "malware",
}
# Un nombre propio se considera "raro" (distintivo) si aparece en <= este nº de
# noticias del corpus. Por encima es un término recurrente (vendor común, CISA...).
DF_PROPIO_RARO = 6
DF_PROPIO_ULTRARARO = 2  # casi único de la historia → señal muy fuerte

def _nombres_propios(texto):
    """Tokens en mayúscula/CamelCase/ALLCAPS (nombres propios) normalizados."""
    out = set()
    for m in re.finditer(r'[A-Za-z][A-Za-z0-9+]{3,}', texto):
        w = m.group()
        if re.search(r'[a-z][A-Z]|[A-Z]{2}', w) or w[0].isupper():
            nw = _norm_dedup(w)
            if nw not in _NOMBRES_PROPIOS_STOP and nw not in _PALABRAS_GENERICAS:
                out.add(nw)
    return out

def _df_nombres_propios(noticias):
    """Frecuencia documental de cada nombre propio sobre el corpus dado."""
    df = {}
    for n in noticias:
        for w in _nombres_propios(f"{n.get('titulo','')}. {n.get('resumen','')}"):
            df[w] = df.get(w, 0) + 1
    return df

def _jaccard_contenido(n1, n2):
    a = _tokens_dedup(f"{n1.get('titulo','')} {n1.get('resumen','')}")
    b = _tokens_dedup(f"{n2.get('titulo','')} {n2.get('resumen','')}")
    return len(a & b) / len(a | b) if a and b else 0.0

def _misma_historia_propios(n1, n2, df):
    """True si comparten nombre(s) propio(s) raro(s) + solape de contenido.

    Requiere `df` (frecuencia documental del corpus) para medir rareza.
    Solo aplica a pares de MEDIOS DISTINTOS: la misma historia republicada por el
    mismo medio (p.ej. digests diarios "Stormcast", o familias de CVEs de un mismo
    producto) la cubren otras capas, y aquí daría falsos positivos.
    """
    m1 = medio_de_fuente(n1["fuente"]) if n1.get("fuente") else None
    m2 = medio_de_fuente(n2["fuente"]) if n2.get("fuente") else None
    if m1 and m2 and m1 == m2:
        return False
    p1 = _nombres_propios(f"{n1.get('titulo','')}. {n1.get('resumen','')}")
    p2 = _nombres_propios(f"{n2.get('titulo','')}. {n2.get('resumen','')}")
    comunes = {w for w in (p1 & p2) if df.get(w, 0) <= DF_PROPIO_RARO}
    if not comunes:
        return False
    ultra = any(df.get(w, 0) <= DF_PROPIO_ULTRARARO for w in comunes)
    jc = _jaccard_contenido(n1, n2)
    # Nombre casi único compartido + algo de solape; o varios raros; o uno + solape alto.
    return (ultra and jc >= 0.15) or (len(comunes) >= 2 and jc >= 0.18) or jc >= 0.30

def son_duplicadas(n1, n2, df=None):
    """True si dos noticias son la MISMA historia (alta confianza, pocos falsos +).

    Señales (con veto de CVE para no fusionar vulnerabilidades distintas):
      - VETO: ambas tienen CVEs y son disjuntos → NUNCA es duplicado.
      - mismo CVE → duplicado.
      - títulos casi idénticos (Jaccard ≥ 0.85) → duplicado.
      - Jaccard ≥ 0.6 Y mismas entidades distintivas → duplicado.
      - (si se pasa `df`) comparten un nombre propio raro + solape de contenido.
    """
    t1, t2 = n1.get("titulo", ""), n2.get("titulo", "")
    c1 = _cves_de(f"{t1} {n1.get('resumen','')}")
    c2 = _cves_de(f"{t2} {n2.get('resumen','')}")
    if c1 and c2:
        return bool(c1 & c2)  # mismo CVE = sí; CVEs distintos = no (veto)
    j = _jaccard_titulos(t1, t2)
    if j >= 0.85:
        return True
    e1, e2 = _entidades_distintivas(t1), _entidades_distintivas(t2)
    if j >= 0.6 and e1 and e1 == e2:
        return True
    # Capa de nombres propios raros (solo con contexto de corpus para medir rareza).
    if df is not None and _misma_historia_propios(n1, n2, df):
        return True
    return False

def deduplicar_noticias(noticias):
    """Recorre la lista (orden newest-first) y elimina las noticias MÁS ANTIGUAS
    que sean duplicado de una más reciente ya conservada.

    Devuelve (lista_limpia, eliminadas).
    """
    df = _df_nombres_propios(noticias)
    kept, eliminadas = [], []
    for n in noticias:
        if any(son_duplicadas(n, k, df=df) for k in kept):
            eliminadas.append(n)
        else:
            kept.append(n)
    return kept, eliminadas

def build_noticia(item, titulo, resumen, categoria, noticias_actuales,
                  severity="", ttps=None, iocs=None, dedup_key=""):
    """Construye el dict de una noticia nueva en memoria (sin tocar la red).

    `noticias_actuales` se usa para asignar id incremental y evitar reusar imágenes.
    """
    nuevo_id = (max((n.get("id", 0) for n in noticias_actuales), default=0) + 1)
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    used_images = [n.get("url_imagen", "") for n in noticias_actuales[:20]]
    used_images = [img for img in used_images if img]

    return {
        "id": nuevo_id,
        "fecha": ahora,
        "categoria": categoria,
        "severidad": severity,
        "titulo": titulo,
        "resumen": resumen,
        "url_imagen": get_image_url(categoria, used_images),
        "enlace_original": item["link"],
        "fuente": item["source"],
        "dedup_key": dedup_key,
        "ttps": [{"id": t["id"], "name": t["name"]} for t in (ttps or [])],
        "iocs": iocs or {},
    }

def commit_noticias(noticias, sha, nuevas=0):
    """Escribe noticias.json en GitHub en un ÚNICO commit por run."""
    token = GIT_TOKEN.strip()
    if not token:
        logger.error("GIT_TOKEN no configurado: no se puede commitear noticias.json")
        return False

    noticias = noticias[:1000]
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}"
    payload = {
        "message": f"feat: add {nuevas} news items ({datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC)",
        "content": base64.b64encode(
            json.dumps(noticias, ensure_ascii=False, indent=2).encode()
        ).decode(),
    }
    if sha:  # omitir sha solo si el archivo no existía (404)
        payload["sha"] = sha

    try:
        r = requests.put(url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json"
        }, json=payload, timeout=15)
        r.raise_for_status()
        logger.info(f"✅ noticias.json actualizado en GitHub ({len(noticias)} items totales)")
        return True
    except Exception as e:
        logger.error(f"Error commit GitHub: {e}")
        return False

# ── Logic ─────────────────────────────────────────────────────────────────────

# Keywords CORTOS: matching por palabra completa (\b). Como substring dan falsos
# positivos masivos: "ia" en "historia/social", "ai" en "email", "apt" en "laptop",
# "soc" en "social", "nist" en "ministerio", "rag" en "dragon".
_IA_CORTOS_RE = re.compile(r'\b(ia|ai|llm|gpt|rag|gpu|tpu|amd|chips?)\b')
_SECURITY_CORTOS_RE = re.compile(r'\b(apt|soc|siem|xdr|edr|vpn|nist|ddos)\b')

def detectar_categoria(title, source):
    text = title.lower()
    # Expanded AI keywords: agents, models, AI companies, frameworks, research orgs
    ia_keywords = [
        "inteligencia artificial", "claude", "gemini", "openai",
        "anthropic", "chatgpt", "copilot", "midjourney", "stable diffusion", "dall-e",
        "machine learning", "deep learning", "neural", "transformer", "modelo de lenguaje",
        "nvidia", "acelerador", "computacion",
        "robotica", "robot", "autonomo", "asml", "agente", "embeddings",
        "hugging face", "langchain", "pytorch", "tensorflow"
    ]
    # Expanded cybersecurity keywords: threats, tools, compliance, incidents
    security_keywords = [
        "seguridad", "ciberseguridad", "hacker", "hacking", "malware", "ransomware",
        "vulnerabilidad", "exploit", "cve-", "zero-day", "0-day", "ciberataque",
        "brecha", "filtracion", "data breach", "deepfake", "privacidad", "phishing",
        "spyware", "trojan", "botnet", "firewall", "cifrado",
        "encriptacion", "autenticacion", "credential", "password", "contraseña",
        "backdoor", "rootkit", "threat", "amenaza", "incidente", "parche",
        "compliance", "gdpr", "iso 27001"
    ]

    if _IA_CORTOS_RE.search(text) or any(k in text for k in ia_keywords):
        return "IA"
    if _SECURITY_CORTOS_RE.search(text) or any(k in text for k in security_keywords):
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

    # Sin key no tiene sentido intentar Unsplash (5 requests fallidas por noticia).
    for _ in range(5 if UNSPLASH_ACCESS_KEY else 0):
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

def _es_rate_limit(e):
    s = str(e).lower()
    return "rate_limit" in s or "429" in s or "too many requests" in s

def _es_limite_diario(e):
    s = str(e).lower()
    return "per day" in s or "tpd" in s or "tokens per day" in s

def _groq_chat(**kwargs):
    """Llama a Groq rotando entre las API keys cuando una agota su cuota.

    - Límite DIARIO (TPD): marca la key como agotada para el resto del run.
    - 429 transitorio (por minuto): solo rota, sin descartarla.
    Devuelve la respuesta de la API o None si no queda ninguna key utilizable.
    """
    global _groq_idx
    n = len(_groq_clients)
    if n == 0:
        logger.error("No hay GROQ_API_KEY configurada")
        return None
    intentos = 0
    while intentos < n:
        if _groq_idx in _groq_exhausted:
            _groq_idx = (_groq_idx + 1) % n
            intentos += 1
            continue
        try:
            return _groq_clients[_groq_idx].chat.completions.create(**kwargs)
        except Exception as e:
            if _es_rate_limit(e):
                if _es_limite_diario(e):
                    _groq_exhausted.add(_groq_idx)
                    logger.warning(f"Groq key #{_groq_idx+1} agotó su cuota DIARIA. Rotando a la siguiente...")
                else:
                    logger.warning(f"Groq key #{_groq_idx+1} con rate limit transitorio. Rotando...")
                _groq_idx = (_groq_idx + 1) % n
                intentos += 1
                continue
            logger.error(f"Groq error (no rate-limit): {e}")
            return None
    logger.error("Groq: todas las API keys agotaron su cuota. No se puede resumir más en este run.")
    return None

def summarize_news(title, content):
    if not _groq_clients:
        logger.error("GROQ_API_KEY no configurada")
        return None, None

    # Truncate content to avoid Groq rate limits (max ~10k tokens = ~8000 chars)
    max_content_length = 8000
    if len(content) > max_content_length:
        content = content[:max_content_length] + "..."

    try:
        r = _groq_chat(
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
        if r is None:
            return None, None
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
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message,
               "parse_mode": "Markdown", "disable_web_page_preview": False}
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 400:
            # Markdown inválido (la IA puede generar '_', '*' o '[' sueltos que
            # rompen parse_mode): reintentar en texto plano para no perder el envío.
            logger.warning(f"Telegram 400 (Markdown inválido), reintentando sin formato: {r.text}")
            payload.pop("parse_mode")
            r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            logger.error(f"Telegram Error {r.status_code}: {r.text}")
        else:
            logger.info(f"Telegram: {r.status_code}")
    except Exception as e:
        logger.error(f"Error Telegram: {e}")

# ── Main ──────────────────────────────────────────────────────────────────────

def job():
    logger.info("=== Iniciando job ===")

    # Obtener noticias actuales de GitHub para deduplicación.
    # IMPORTANTE: si la lectura FALLA (None), abortamos. Tratarlo como historial
    # vacío republicaría todo (causa del incidente de duplicados tipo YAMCS).
    noticias_existentes, sha = get_github_file()
    if noticias_existentes is None:
        logger.error("No se pudo leer noticias.json (fallo de red/auth). "
                     "Abortando el run para NO republicar duplicados.")
        return

    # Estructuras de deduplicación (3 capas)
    published_links = {n.get("enlace_original", "") for n in noticias_existentes}
    claves_publicadas = {n.get("dedup_key", "") for n in noticias_existentes if n.get("dedup_key")}
    logger.info(f"URLs ya publicadas: {len(published_links)} | claves de contenido: {len(claves_publicadas)}")

    # Copia de trabajo que se commiteará UNA sola vez al final
    noticias_actualizadas = list(noticias_existentes)

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
        cve_items = scrape_nvd_cves(hours_back=48, min_cvss=7.0, limit=8)
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
    
    # Vulners API — DESACTIVADO: la API anónima fue descontinuada (devuelve 403).
    # NVD + Exploit-DB ya cubren CVEs/exploits recientes. Reactivar requiere VULNERS_API_KEY.

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

    # ── Pre-filtro de duplicados (barato, antes de gastar llamadas a la IA) ────
    # Capa 1: URL ya publicada.  Capa 2: clave de contenido (mismo CVE / mismo
    # título original) — colapsa la misma historia llegada desde fuentes distintas,
    # tanto contra el historial como entre sí dentro de este mismo run.
    new_items = []
    claves_vistas_run = set()
    descartados_url = 0
    descartados_clave = 0
    for i in all_news:
        if i['link'] in published_links:
            descartados_url += 1
            continue
        clave = clave_contenido(i.get('title', ''), i.get('content', ''))
        if clave and (clave in claves_publicadas or clave in claves_vistas_run):
            descartados_clave += 1
            continue
        if clave:
            claves_vistas_run.add(clave)
        new_items.append(i)

    logger.info(f"Items candidatos nuevos: {len(new_items)} "
                f"(descartados: {descartados_url} por URL, {descartados_clave} por clave de contenido)")

    # Intercalar por fuente (Round-Robin) para maximizar la diversidad de medios
    new_items = interleave_by_source(new_items)

    # ── FASE 2 + 3: Procesamiento con IA + Distribución ──────────────────────
    logger.info("--- Fase 2+3: Análisis IA + Distribución ---")

    MAX_NOTICIAS = 10
    count = 0
    medio_counts = {}      # cuántas publicadas por medio (Telegram, Exploit-DB, outlet...)
    # Métricas de descarte para el resumen del run
    drop_stats = {"cap_medio": 0, "ia_rechazo": 0, "resumen_incompleto": 0, "similar": 0}

    for item in new_items:
        if count >= MAX_NOTICIAS:
            break

        source = item['source']
        medio = medio_de_fuente(source)
        ok_div, motivo_div = pasa_diversidad(medio, medio_counts, count)
        if not ok_div:
            drop_stats["cap_medio"] += 1
            logger.info(f"[Diversidad] Saltando '{item['title'][:50]}' — medio '{medio}': {motivo_div}")
            continue

        logger.info(f"Procesando [{medio}] {item['title']}")

        # ── Resumen y filtro de relevancia con Groq ───────────────────────────
        titulo_ai, resumen_ai = summarize_news(item['title'], item.get('content', item['title']))

        if titulo_ai == "RECHAZAR":
            drop_stats["ia_rechazo"] += 1
            logger.info(f"Noticia rechazada por irrelevante: {item['title']}")
            continue

        if not titulo_ai or not resumen_ai:
            drop_stats["resumen_incompleto"] += 1
            logger.info(f"Noticia descartada por resumen incompleto: {item['title']}")
            continue

        # Filtrar similitud (capa 3: semántica, sobre el título ya resumido)
        es_similar, titulo_similar = es_noticia_similar(
            titulo_ai, resumen_ai, noticias_actualizadas, source_nuevo=item["source"])
        if es_similar:
            drop_stats["similar"] += 1
            logger.info(f"Noticia omitida por demasiada similitud con: {titulo_similar}")
            continue

        categoria = detectar_categoria(titulo_ai, item["source"])
        dedup_key = clave_contenido(item.get('title', ''), item.get('content', ''))
        
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

        # GitHub: construir en memoria, commitear UNA vez al final del run
        nueva = build_noticia(
            item, titulo_ai, resumen_ai, categoria, noticias_actualizadas,
            severity=severity, ttps=ttps, iocs=iocs, dedup_key=dedup_key,
        )
        noticias_actualizadas.insert(0, nueva)

        # Actualizar estructuras de dedup en memoria para el resto del run
        published_links.add(item['link'])
        if dedup_key:
            claves_publicadas.add(dedup_key)

        medio_counts[medio] = medio_counts.get(medio, 0) + 1
        count += 1
        time.sleep(3)

    # ── Dedup retroactivo ─────────────────────────────────────────────────────
    # Limpia duplicados de alta confianza que se hayan colado en el historial
    # (misma historia con título reformulado por la IA, mismo CVE, etc.).
    noticias_actualizadas, dups = deduplicar_noticias(noticias_actualizadas)
    for dn in dups:
        logger.info(f"[Dedup retro] eliminada id{dn.get('id')} — {dn.get('titulo')!r} ({dn.get('fuente')})")

    # ── Commit único + resumen del run ────────────────────────────────────────
    if count == 0 and not dups:
        logger.info("Sin noticias nuevas ni duplicados que limpiar.")
        logger.info(f"=== Resumen descartes: {drop_stats} ===")
        return

    commit_noticias(noticias_actualizadas, sha, nuevas=count)
    logger.info("=== Job completado ===")
    logger.info(f"Publicadas: {count} | por medio: {dict(medio_counts)} | dups eliminados: {len(dups)}")
    logger.info(f"Descartes: {drop_stats}")

if __name__ == "__main__":
    job()
