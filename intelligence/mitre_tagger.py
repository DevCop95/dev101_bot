# intelligence/mitre_tagger.py — Clasificación MITRE ATT&CK automática
# Usa Groq LLaMA 3.3 con prompt especializado para mapear TTPs
# Diccionario local de técnicas para validación

import os
import re
import logging
from groq import Groq

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# ── Diccionario de las técnicas MITRE ATT&CK más comunes ─────────────────────
# Solo incluimos las más frecuentes en noticias para validación
KNOWN_TECHNIQUES = {
    # Initial Access
    "T1566": "Phishing",
    "T1566.001": "Spearphishing Attachment",
    "T1566.002": "Spearphishing Link",
    "T1190": "Exploit Public-Facing Application",
    "T1133": "External Remote Services",
    "T1078": "Valid Accounts",
    "T1195": "Supply Chain Compromise",
    "T1195.002": "Compromise Software Supply Chain",
    "T1199": "Trusted Relationship",
    # Execution
    "T1059": "Command and Scripting Interpreter",
    "T1059.001": "PowerShell",
    "T1059.003": "Windows Command Shell",
    "T1059.005": "Visual Basic",
    "T1059.007": "JavaScript",
    "T1204": "User Execution",
    "T1204.001": "Malicious Link",
    "T1204.002": "Malicious File",
    "T1203": "Exploitation for Client Execution",
    # Persistence
    "T1547": "Boot or Logon Autostart Execution",
    "T1547.001": "Registry Run Keys / Startup Folder",
    "T1053": "Scheduled Task/Job",
    "T1136": "Create Account",
    "T1098": "Account Manipulation",
    # Privilege Escalation
    "T1068": "Exploitation for Privilege Escalation",
    "T1548": "Abuse Elevation Control Mechanism",
    "T1548.002": "Bypass UAC",
    # Defense Evasion
    "T1562": "Impair Defenses",
    "T1562.001": "Disable or Modify Tools",
    "T1070": "Indicator Removal",
    "T1027": "Obfuscated Files or Information",
    "T1036": "Masquerading",
    "T1140": "Deobfuscate/Decode Files",
    "T1112": "Modify Registry",
    # Credential Access
    "T1003": "OS Credential Dumping",
    "T1110": "Brute Force",
    "T1555": "Credentials from Password Stores",
    "T1528": "Steal Application Access Token",
    "T1557": "Adversary-in-the-Middle",
    # Discovery
    "T1082": "System Information Discovery",
    "T1083": "File and Directory Discovery",
    "T1046": "Network Service Discovery",
    "T1018": "Remote System Discovery",
    # Lateral Movement
    "T1021": "Remote Services",
    "T1021.001": "Remote Desktop Protocol",
    "T1021.002": "SMB/Windows Admin Shares",
    "T1534": "Internal Spearphishing",
    # Collection
    "T1005": "Data from Local System",
    "T1114": "Email Collection",
    "T1113": "Screen Capture",
    "T1560": "Archive Collected Data",
    # Command and Control
    "T1071": "Application Layer Protocol",
    "T1071.001": "Web Protocols",
    "T1105": "Ingress Tool Transfer",
    "T1572": "Protocol Tunneling",
    "T1090": "Proxy",
    "T1573": "Encrypted Channel",
    # Exfiltration
    "T1041": "Exfiltration Over C2 Channel",
    "T1567": "Exfiltration Over Web Service",
    "T1048": "Exfiltration Over Alternative Protocol",
    # Impact
    "T1486": "Data Encrypted for Impact",  # Ransomware
    "T1489": "Service Stop",
    "T1490": "Inhibit System Recovery",
    "T1498": "Network Denial of Service",
    "T1499": "Endpoint Denial of Service",
    "T1529": "System Shutdown/Reboot",
    "T1485": "Data Destruction",
    "T1491": "Defacement",
    "T1565": "Data Manipulation",
}

# Mapeo de tácticas
TACTICS = {
    "TA0001": "Initial Access",
    "TA0002": "Execution",
    "TA0003": "Persistence",
    "TA0004": "Privilege Escalation",
    "TA0005": "Defense Evasion",
    "TA0006": "Credential Access",
    "TA0007": "Discovery",
    "TA0008": "Lateral Movement",
    "TA0009": "Collection",
    "TA0010": "Exfiltration",
    "TA0011": "Command and Control",
    "TA0040": "Impact",
    "TA0042": "Resource Development",
    "TA0043": "Reconnaissance",
}

MITRE_SYSTEM_PROMPT = """Eres un analista de inteligencia de amenazas (CTI) especializado en el framework MITRE ATT&CK.

Tu tarea es analizar noticias de ciberseguridad y extraer las Tácticas, Técnicas y Procedimientos (TTPs) relevantes.

REGLAS:
1. Solo incluye TTPs que se mencionen EXPLÍCITAMENTE o se INFIERAN CLARAMENTE del contenido
2. Usa el formato exacto: TXXXX o TXXXX.XXX (con subtécnica si aplica)
3. Incluye el nombre de la técnica junto al ID
4. Máximo 5 TTPs por noticia
5. Si la noticia NO es sobre un ataque/amenaza específica, responde SOLO: NONE

FORMATO DE RESPUESTA (una línea por TTP):
TXXXX - Nombre de la Técnica
TXXXX.XXX - Nombre de la Subtécnica

Ejemplo:
T1566.001 - Spearphishing Attachment
T1486 - Data Encrypted for Impact
T1078 - Valid Accounts"""


def tag_ttps(title, content=""):
    """
    Clasifica TTPs MITRE ATT&CK de una noticia usando Groq.
    Retorna lista de dicts con id y nombre, o lista vacía.
    """
    if not GROQ_API_KEY or not groq_client:
        return []
    
    text = f"Título: {title}\nContenido: {content[:3000]}"
    
    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": MITRE_SYSTEM_PROMPT},
                {"role": "user", "content": text}
            ],
            temperature=0.1,
            max_tokens=200,
        )
        
        response = r.choices[0].message.content.strip()
        
        if "NONE" in response.upper():
            return []
        
        ttps = []
        for line in response.split("\n"):
            line = line.strip()
            if not line:
                continue
            
            # Extraer ID de técnica (TXXXX o TXXXX.XXX)
            match = re.match(r'(T\d{4}(?:\.\d{3})?)\s*[-–—:]\s*(.+)', line)
            if match:
                tech_id = match.group(1)
                tech_name = match.group(2).strip()
                
                # Validar contra diccionario local
                if tech_id in KNOWN_TECHNIQUES:
                    tech_name = KNOWN_TECHNIQUES[tech_id]  # Usar nombre oficial
                
                ttps.append({
                    "id": tech_id,
                    "name": tech_name,
                })
        
        if ttps:
            logger.info(f"MITRE TTPs detectados: {[t['id'] for t in ttps]}")
        
        return ttps[:5]  # Máximo 5
        
    except Exception as e:
        logger.error(f"MITRE Tagger Error: {e}")
    return []


def format_ttps_telegram(ttps):
    """
    Formatea TTPs para mensaje de Telegram.
    Retorna string o None si no hay TTPs.
    """
    if not ttps:
        return None
    
    lines = []
    for ttp in ttps:
        lines.append(f"⚔️ `{ttp['id']}` — {ttp['name']}")
    
    return "\n".join(lines)


def format_ttps_twitter(ttps):
    """
    Formatea TTPs para tweet (versión corta).
    """
    if not ttps:
        return ""
    
    ids = [ttp['id'] for ttp in ttps[:3]]
    return " ".join(f"#{tid.replace('.', '_')}" for tid in ids)
