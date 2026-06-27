# sources/nvd_cve.py — NVD CVE API 2.0 (NIST)
# Obtiene CVEs recientes con CVSS >= 7.0 (High/Critical)
# API gratuita — API key opcional para mejor rate limit

import os
import logging
import requests
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

NVD_API_KEY = os.getenv("NVD_API_KEY", "")
NVD_BASE_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"


def scrape_nvd_cves(hours_back=48, min_cvss=7.0, limit=10):
    """
    Obtiene CVEs recientes de la NVD con CVSS >= min_cvss.
    Retorna lista de dicts compatibles con el pipeline del bot.
    """
    try:
        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=hours_back)
        
        params = {
            "lastModStartDate": start.strftime("%Y-%m-%dT%H:%M:%S.000"),
            "lastModEndDate": now.strftime("%Y-%m-%dT%H:%M:%S.000"),
            "cvssV3Severity": "HIGH",  # HIGH + CRITICAL
            "resultsPerPage": limit,
        }
        
        headers = {"Accept": "application/json"}
        if NVD_API_KEY:
            headers["apiKey"] = NVD_API_KEY
        
        logger.info("FETCH NVD CVE API...")
        r = requests.get(NVD_BASE_URL, params=params, headers=headers, timeout=30)
        
        if r.status_code == 403:
            logger.warning("NVD API rate limited. Intentando sin filtro de severidad...")
            # Fallback: buscar sin filtro de severidad
            params.pop("cvssV3Severity", None)
            r = requests.get(NVD_BASE_URL, params=params, headers=headers, timeout=30)
        
        if r.status_code != 200:
            logger.error(f"NVD API Error: Status {r.status_code}")
            return []
        
        data = r.json()
        vulnerabilities = data.get("vulnerabilities", [])
        logger.info(f"NVD: {len(vulnerabilities)} CVEs encontrados")
        
        items = []
        for vuln in vulnerabilities:
            cve = vuln.get("cve", {})
            cve_id = cve.get("id", "")
            
            # Extraer descripción (preferir español, fallback inglés)
            descriptions = cve.get("descriptions", [])
            desc_es = ""
            desc_en = ""
            for d in descriptions:
                if d.get("lang") == "es":
                    desc_es = d.get("value", "")
                elif d.get("lang") == "en":
                    desc_en = d.get("value", "")
            description = desc_es or desc_en
            
            if not description or description.startswith("** REJECT"):
                continue
            
            # Extraer CVSS score
            metrics = cve.get("metrics", {})
            cvss_score = 0.0
            cvss_severity = ""
            
            # Intentar CVSS v3.1 primero, luego v3.0
            for version_key in ["cvssMetricV31", "cvssMetricV30"]:
                metric_list = metrics.get(version_key, [])
                if metric_list:
                    cvss_data = metric_list[0].get("cvssData", {})
                    cvss_score = cvss_data.get("baseScore", 0.0)
                    cvss_severity = cvss_data.get("baseSeverity", "")
                    break
            
            if cvss_score < min_cvss:
                continue
            
            # Construir título informativo
            title = f"🔴 {cve_id} (CVSS {cvss_score}) — {cvss_severity}"
            
            # Link a NVD
            link = f"https://nvd.nist.gov/vuln/detail/{cve_id}"
            
            # Contenido enriquecido para el resumen IA
            content = f"CVE: {cve_id}\nCVSS Score: {cvss_score} ({cvss_severity})\n\n{description}"
            
            # Extraer CPEs afectados (si existen)
            configurations = cve.get("configurations", [])
            affected_products = []
            for config in configurations[:3]:  # Limitar
                for node in config.get("nodes", []):
                    for cpe_match in node.get("cpeMatch", [])[:5]:
                        if cpe_match.get("vulnerable"):
                            cpe = cpe_match.get("criteria", "")
                            # Extraer vendor:product de CPE URI
                            parts = cpe.split(":")
                            if len(parts) >= 5:
                                affected_products.append(f"{parts[3]}:{parts[4]}")
            
            if affected_products:
                content += f"\n\nProductos afectados: {', '.join(set(affected_products[:5]))}"
            
            items.append({
                'title': title,
                'link': link,
                'source': 'NVD (NIST)',
                'content': content,
                'cve_id': cve_id,
                'cvss_score': cvss_score,
                'cvss_severity': cvss_severity,
            })
        
        # Ordenar por CVSS score (más críticos primero)
        items.sort(key=lambda x: x.get('cvss_score', 0), reverse=True)
        return items[:limit]
        
    except Exception as e:
        logger.error(f"NVD CVE Error: {e}")
    return []
