# NVD CVE API 2.0: recent High/Critical CVEs, ranked after bounded pagination.

import os
import re
import time
import logging
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests
from intelligence.severity_classifier import normalize_cvss_score

logger = logging.getLogger(__name__)

NVD_API_KEY = os.getenv("NVD_API_KEY", "")
NVD_BASE_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
NVD_TIMEOUT = 45
NVD_MAX_RETRIES = 3  # Total attempts per page, including the initial request.
NVD_MAX_RETRY_DELAY = 30
NVD_PAGE_SIZE = 2000
NVD_MAX_PAGES = 10  # At most 20,000 records per run; warn when truncated.


def _nvd_get(params, headers):
    """Retry transport errors, 429 and 5xx, with bounded Retry-After/backoff."""
    for attempt in range(1, NVD_MAX_RETRIES + 1):
        response = None
        try:
            response = requests.get(NVD_BASE_URL, params=params, headers=headers, timeout=NVD_TIMEOUT)
            if response.status_code != 429 and not 500 <= response.status_code <= 599:
                return response
            reason = f"HTTP {response.status_code}"
        except requests.exceptions.RequestException as exc:
            reason = type(exc).__name__
        if attempt == NVD_MAX_RETRIES:
            logger.error("NVD: exhausted %s attempts (%s)", NVD_MAX_RETRIES, reason)
            return None

        wait = min(2 ** attempt, NVD_MAX_RETRY_DELAY)
        retry_after = response.headers.get("Retry-After") if response is not None else None
        if retry_after:
            try:
                if re.fullmatch(r'\d+', retry_after.strip()):
                    delay = int(retry_after)
                else:
                    retry_date = parsedate_to_datetime(retry_after)
                    if retry_date.tzinfo is None:
                        retry_date = retry_date.replace(tzinfo=timezone.utc)
                    delay = (retry_date - datetime.now(timezone.utc)).total_seconds()
                wait = max(0, min(delay, NVD_MAX_RETRY_DELAY))
            except (ValueError, TypeError, OverflowError):
                pass
        logger.warning("NVD attempt %s/%s failed (%s); retry in %ss", attempt, NVD_MAX_RETRIES, reason, wait)
        time.sleep(wait)
    return None


def _nvd_item(vuln, min_cvss):
    """Parse one record; prefer valid primary v4, then v3.1, then v3.0."""
    cve = vuln["cve"]
    cve_id = cve["id"]
    if not isinstance(cve_id, str) or not re.fullmatch(r'CVE-\d{4}-\d{4,}', cve_id):
        raise ValueError("invalid CVE ID")
    if cve.get("vulnStatus") == "Rejected":
        return None
    descriptions = {d["lang"]: d["value"] for d in cve.get("descriptions", [])
                    if isinstance(d, dict) and isinstance(d.get("value"), str) and isinstance(d.get("lang"), str)}
    description = descriptions.get("es") or descriptions.get("en", "")
    if not description or description.startswith("** REJECT"):
        return None

    metrics = cve.get("metrics", {})
    cvss_score = None
    for version in ("cvssMetricV40", "cvssMetricV31", "cvssMetricV30"):
        candidates = []
        metric_list = metrics.get(version, [])
        if not isinstance(metric_list, list):
            logger.warning("NVD %s: invalid CVSS metric list", cve_id)
            continue
        for metric in metric_list:
            if not isinstance(metric, dict) or not isinstance(metric.get("cvssData"), dict):
                logger.warning("NVD %s: invalid CVSS metric", cve_id)
                continue
            score = normalize_cvss_score(metric["cvssData"].get("baseScore"))
            if score is not None:
                candidates.append((metric.get("type") == "Primary", score))
            else:
                logger.warning("NVD %s: invalid CVSS score", cve_id)
        if candidates:
            cvss_score = max(candidates)[1]
            break
    if cvss_score is None or cvss_score < min_cvss:
        return None
    cvss_severity = ("CRITICAL" if cvss_score >= 9 else "HIGH" if cvss_score >= 7 else
                     "MEDIUM" if cvss_score >= 4 else "LOW" if cvss_score > 0 else "NONE")
    content = f"CVE: {cve_id}\nCVSS Score: {cvss_score} ({cvss_severity})\n\n{description}"
    affected_products = set()
    for config in cve.get("configurations", [])[:3]:
        for node in config.get("nodes", []):
            for match in node.get("cpeMatch", [])[:5]:
                if match.get("vulnerable"):
                    parts = match.get("criteria", "").split(":")
                    if len(parts) >= 5:
                        affected_products.add(f"{parts[3]}:{parts[4]}")
    if affected_products:
        content += f"\n\nProductos afectados: {', '.join(sorted(affected_products)[:5])}"
    return {
        "title": f"\U0001f534 {cve_id} (CVSS {cvss_score}) \u2014 {cvss_severity}",
        "link": f"https://nvd.nist.gov/vuln/detail/{cve_id}",
        "source": "NVD (NIST)",
        "content": content,
        "cve_id": cve_id,
        "cvss_score": cvss_score,
        "cvss_severity": cvss_severity,
    }


def scrape_nvd_cves(hours_back=48, min_cvss=7.0, limit=10, *, now=None):
    """Return top scores across fetched pages; log errors/truncation, keep partial results."""
    items = {}
    try:
        min_cvss = normalize_cvss_score(min_cvss)
        if min_cvss is None or not 0 < hours_back <= 120 * 24:
            raise ValueError("invalid NVD score or date window (maximum 120 days)")
        if limit <= 0:
            return []
        now = now or datetime.now(timezone.utc)
        now = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
        start = now - timedelta(hours=hours_back)
        params = {
            "lastModStartDate": start.isoformat(timespec="milliseconds"),
            "lastModEndDate": now.isoformat(timespec="milliseconds"),
            # HIGH alone excludes CRITICAL, and a v3 filter also excludes v4-only CVEs.
            "resultsPerPage": NVD_PAGE_SIZE,
            "startIndex": 0,
        }
        headers = {"Accept": "application/json"}
        if NVD_API_KEY:
            headers["apiKey"] = NVD_API_KEY

        for page in range(NVD_MAX_PAGES):
            if page:
                time.sleep(0.6 if "apiKey" in headers else 6)
            response = _nvd_get(dict(params), headers)
            if response is not None and response.status_code == 404 and "apiKey" in headers:
                logger.warning("NVD 404 with API key; retrying anonymously")
                headers.pop("apiKey")
                response = _nvd_get(dict(params), headers)
            if response is None:
                logger.error("NVD: incomplete fetch at startIndex=%s", params["startIndex"])
                break
            if response.status_code != 200:
                logger.error("NVD API Error: Status %s", response.status_code)
                break
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("vulnerabilities"), list):
                raise ValueError("invalid NVD response")
            total, index, size = (data.get(key) for key in ("totalResults", "startIndex", "resultsPerPage"))
            if any(type(value) is not int or value < 0 for value in (total, index, size)):
                raise ValueError("invalid NVD pagination metadata")
            records = data["vulnerabilities"]
            if index != params["startIndex"] or len(records) > size or size > NVD_PAGE_SIZE:
                raise ValueError("inconsistent NVD pagination")
            logger.info("NVD: %s records at startIndex=%s (total=%s)", len(records), index, total)
            for record in records:
                try:
                    item = _nvd_item(record, min_cvss)
                    if item and (item["cve_id"] not in items or item["cvss_score"] > items[item["cve_id"]]["cvss_score"]):
                        items[item["cve_id"]] = item
                except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
                    logger.warning("NVD: skipping invalid record at startIndex=%s", index)
            if index + len(records) >= total:
                break
            if not records or size == 0 or len(records) != size:
                logger.error("NVD: incomplete page; stopping pagination")
                break
            params["startIndex"] = index + size
        else:
            logger.warning("NVD: pagination capped at %s pages; ranking partial results", NVD_MAX_PAGES)
    except Exception as exc:
        logger.error("NVD CVE Error: %s", type(exc).__name__)
    return sorted(items.values(), key=lambda item: (-item["cvss_score"], item["cve_id"]))[:limit]
