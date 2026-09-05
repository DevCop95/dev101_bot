"""Offline contract fixtures for RSS 2.0, Atom 1.0, Telegram HTML and source APIs.

Fixtures preserve wire-format fields/nesting, with dates fixed for boundary tests.
No runner imports, dotenv, credentials, publication clients or live services.
Run: python -B -m unittest discover -s tests -p test_sources_intelligence.py -v
"""

import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)

# Isolate import-time configuration and never import the real LLM/key client.
with patch.dict(os.environ, {}, clear=True):
    from sources import rss_feeds as rss, telegram_monitor as tg, nvd_cve as nvd
    from sources import exploitdb, greynoise
    from intelligence import ioc_extractor as ioc, severity_classifier as severity

llm_stub = types.ModuleType("groq_rotation")
llm_stub.GROQ_API_KEYS = []
llm_stub.GROQ_PRIMARY_MODEL = "offline-model"
llm_stub.NVIDIA_API_KEY = ""
llm_stub.groq_chat = Mock(side_effect=AssertionError("Live LLM forbidden"))
spec = importlib.util.spec_from_file_location("audit_mitre_tagger", ROOT / "intelligence" / "mitre_tagger.py")
mitre = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {"groq_rotation": llm_stub}):
    spec.loader.exec_module(mitre)

spec = importlib.util.spec_from_file_location("audit_catalog_generator", ROOT / "tools" / "update_attack_catalog.py")
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


def response(payload=None, *, status=200, content=None, headers=None):
    result = requests.Response()
    result.status_code = status
    result.headers.update(headers or {})
    result._content = content if content is not None else json.dumps(payload).encode("utf-8")
    return result


RSS_FIXTURE = b'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel><title>Security News</title><link>https://news.example.org/</link>
<description>Security updates</description>
<item><title>Future</title><link>https://news.example.org/future</link>
<pubDate>Sat, 05 Sep 2026 12:00:01 GMT</pubDate></item>
<item><title>Invalid date</title><link>https://news.example.org/invalid</link><pubDate>not-a-date</pubDate></item>
<item><title>Missing link</title></item>
<item><title>OpenSSH patch</title><link>https://news.example.org/openssh</link>
<pubDate>Sat, 05 Sep 2026 14:00:00 +0200</pubDate>
<description><![CDATA[<p>CVE-2024-6387 patch available</p>]]></description></item>
<item><title>No date</title><link>https://news.example.org/undated</link></item>
</channel></rss>'''

ATOM_FIXTURE = b'''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Security Atom</title>
<id>https://news.example.org/atom</id><updated>2026-09-05T12:00:00Z</updated>
<entry><id>tag:news.example.org,2026:1</id><title>Atom advisory</title>
<link rel="self" type="application/atom+xml" href="https://news.example.org/api/1"/>
<link rel="alternate" type="text/html" href="https://news.example.org/article/1"/>
<published>2026-08-31T07:00:00-05:00</published>
<summary type="html">&lt;p&gt;Security summary&lt;/p&gt;</summary></entry>
<entry><id>tag:news.example.org,2026:2</id><title>Too old by one second</title>
<link href="https://news.example.org/old"/><updated>2026-08-31T11:59:59Z</updated></entry>
</feed>'''

TELEGRAM_FIXTURE = b'''<!doctype html><html><body><section class="tgme_channel_history">
<div class="tgme_widget_message_wrap"><div class="tgme_widget_message" data-post="cveNotify/1">
<div class="tgme_widget_message_text js-message_text">Security patch CVE-2024-6387</div>
<a class="tgme_widget_message_date" href="https://t.me/cveNotify/1"><time datetime="2026-09-02T14:00:00+02:00">Sep 2</time></a></div></div>
<div class="tgme_widget_message" data-post="cveNotify/2"><div class="tgme_widget_message_text">Security undated</div>
<a class="tgme_widget_message_date" href="https://t.me/cveNotify/2"></a></div>
<div class="tgme_widget_message" data-post="cveNotify/3"><div class="tgme_widget_message_text">Security bad date</div>
<a class="tgme_widget_message_date" href="https://t.me/cveNotify/3"><time datetime="invalid">?</time></a></div>
<div class="tgme_widget_message" data-post="cveNotify/4"><div class="tgme_widget_message_text">Security future</div>
<a class="tgme_widget_message_date" href="https://t.me/cveNotify/4"><time datetime="2026-09-05T12:00:01Z">Later</time></a></div>
<div class="tgme_widget_message"><div class="tgme_widget_message_text">Security missing permalink</div></div>
<div class="tgme_widget_message"><div class="tgme_widget_message_text">hello</div></div>
</section></body></html>'''

NVD_RECORD = {
    "cve": {
        "id": "CVE-2024-6387", "sourceIdentifier": "secalert@redhat.com",
        "published": "2024-07-01T12:15:02.507", "lastModified": "2026-09-05T10:00:00.000",
        "vulnStatus": "Analyzed",
        "descriptions": [{"lang": "en", "value": "A signal handler race condition was found in OpenSSH's server."}],
        "metrics": {"cvssMetricV31": [{"source": "nvd@nist.gov", "type": "Primary", "cvssData": {
            "version": "3.1", "vectorString": "CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:H/A:H",
            "baseScore": 8.1, "baseSeverity": "HIGH",
        }}]},
        "configurations": [{"nodes": [{"operator": "OR", "negate": False, "cpeMatch": [{
            "vulnerable": True, "criteria": "cpe:2.3:a:openbsd:openssh:*:*:*:*:*:*:*:*",
            "matchCriteriaId": "5d09ab2c-6b66-4cbf-8d3f-17c88f399653",
        }]}]}],
    }
}


def nvd_record(identifier, score, version="cvssMetricV31"):
    record = copy.deepcopy(NVD_RECORD)
    record["cve"]["id"] = identifier
    metric = record["cve"]["metrics"].pop("cvssMetricV31")
    metric[0]["cvssData"]["baseScore"] = score
    metric[0]["cvssData"]["version"] = {"cvssMetricV40": "4.0", "cvssMetricV31": "3.1", "cvssMetricV30": "3.0"}[version]
    if version == "cvssMetricV40":
        metric[0]["cvssData"]["vectorString"] = "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"
    elif version == "cvssMetricV30":
        metric[0]["cvssData"]["vectorString"] = "CVSS:3.0/AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:H/A:H"
    record["cve"]["metrics"][version] = metric
    return record


def nvd_page(records, index=0, total=None):
    return response({"resultsPerPage": len(records), "startIndex": index,
                     "totalResults": len(records) if total is None else total,
                     "format": "NVD_CVE", "version": "2.0", "timestamp": "2026-09-05T12:00:00.000",
                     "vulnerabilities": records})


class OfflineTest(unittest.TestCase):
    def setUp(self):
        for target in ("requests.sessions.Session.request", "socket.create_connection", "socket.socket.connect"):
            blocker = patch(target, side_effect=AssertionError("Network forbidden in offline tests"))
            blocker.start()
            self.addCleanup(blocker.stop)
        for module, name in ((nvd, "NVD_API_KEY"), (greynoise, "GREYNOISE_API_KEY")):
            guard = patch.object(module, name, "")
            guard.start()
            self.addCleanup(guard.stop)

    def assert_item_contract(self, item, source):
        for key in ("title", "link", "source", "content"):
            self.assertIsInstance(item[key], str)
        self.assertEqual(item["source"], source)


class FeedTests(OfflineTest):
    def test_dates_full_timestamp_offsets_and_inclusive_bounds(self):
        cases = {
            "2026-09-05T12:00:00Z": True,
            "2026-09-05T14:00:00+02:00": True,
            "2026-09-05T07:00:00-05:00": True,
            "Sat, 05 Sep 2026 14:00:00 +0200": True,
            "Sat, 05 Sep 2026 12:00:00 GMT": True,
            "2026-08-31T12:00:00Z": True,
            "2026-08-31T11:59:59.999999Z": False,
            "2026-09-05T12:00:00.000001Z": False,
            "2026-09-05T07:00:01-05:00": False,
            "2026-09-05 12:00:00": True,
            "5 de septiembre de 2026": True,
            "05/09/2026": True,
            "2026/09/05": True,
            "2026-09-06": False,
            "2026-09-31": False,
            "0001-01-01T00:00:00+14:00": False,
            "9999-12-31T23:59:59-14:00": False,
            "garbage": False,
            "": False,
            None: False,
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(rss.is_recent(value, now=NOW), expected)
        self.assertEqual(rss.parse_date("2026-09-05T14:00:00+02:00"), NOW)

    def test_rss_bytes_invalid_entries_do_not_consume_limit(self):
        with patch.object(rss.scraper, "get", return_value=response(content=RSS_FIXTURE)):
            with self.assertLogs(rss.logger, level="WARNING"):
                items = rss.scrape_rss_feed("https://news.example.org/rss", "RSS", limit=2, now=NOW)
        self.assertEqual([i["title"] for i in items], ["OpenSSH patch", "No date"])
        self.assertIn("CVE-2024-6387", items[0]["content"])
        self.assert_item_contract(items[0], "RSS")

    def test_atom_namespace_alternate_link_summary_and_lower_bound(self):
        with patch.object(rss.scraper, "get", return_value=response(content=ATOM_FIXTURE)):
            items = rss.scrape_rss_feed("https://news.example.org/atom", "Atom", now=NOW)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["link"], "https://news.example.org/article/1")
        self.assertIn("Security summary", items[0]["content"])
        self.assert_item_contract(items[0], "Atom")

    def test_rss_rejects_html_but_empty_feed_is_normal(self):
        with patch.object(rss.scraper, "get", return_value=response(content=b"<html><body>Access denied</body></html>")):
            with self.assertLogs(rss.logger, level="ERROR"):
                self.assertEqual(rss.scrape_rss_feed("https://news.example.org", "RSS", now=NOW), [])
        with patch.object(rss.scraper, "get", return_value=response(content=b'<rss version="2.0"><channel/></rss>')):
            with self.assertNoLogs(rss.logger, level="WARNING"):
                self.assertEqual(rss.scrape_rss_feed("https://news.example.org", "RSS", now=NOW), [])

    def test_rss2json_wire_contract_and_per_record_validation(self):
        payload = {"status": "ok", "feed": {"url": "https://news.example.org/feed"}, "items": [
            None, {"title": [], "link": "https://news.example.org/bad"},
            {"title": "Future", "link": "https://news.example.org/future", "pubDate": "2026-09-05 12:00:01"},
            {"title": "API item", "link": "https://news.example.org/1", "pubDate": "2026-09-05 12:00:00", "description": "Patch"},
            {"title": "Undated", "link": "https://news.example.org/2", "description": "No timestamp"},
        ]}
        with patch.object(rss.requests, "get", return_value=response(payload)) as get:
            with self.assertLogs(rss.logger, level="WARNING"):
                items = rss.scrape_rss2json("https://news.example.org/feed?a=1&b=2", "Bridge", now=NOW)
        self.assertEqual([item["title"] for item in items], ["API item", "Undated"])
        self.assertEqual(get.call_args.kwargs["params"]["rss_url"], "https://news.example.org/feed?a=1&b=2")
        self.assert_item_contract(items[0], "Bridge")

    def test_rss_fallback_keeps_window_clock_and_limit(self):
        with patch.object(rss.scraper, "get", return_value=response(status=403)):
            with patch.object(rss, "scrape_rss2json", return_value=[{"title": "a"}, {"title": "b"}]) as fallback:
                with self.assertLogs(rss.logger, level="WARNING"):
                    items = rss.scrape_rss_feed("https://news.example.org", "RSS", limit=1, max_age_days=5, now=NOW)
        self.assertEqual(len(items), 1)
        self.assertEqual(fallback.call_args.kwargs, {"max_age_days": 5, "now": NOW})

    def test_bridge_error_is_logged_but_empty_success_is_not(self):
        for payload, status in (({"status": "error"}, 200), ({"status": "ok", "items": {}}, 200), ({}, 503)):
            with self.subTest(payload=payload, status=status), patch.object(rss.requests, "get", return_value=response(payload, status=status)):
                with self.assertLogs(rss.logger, level="ERROR"):
                    self.assertEqual(rss.scrape_rss2json("https://news.example.org", "RSS", now=NOW), [])
        with patch.object(rss.requests, "get", return_value=response({"status": "ok", "items": []})):
            with self.assertNoLogs(rss.logger, level="WARNING"):
                self.assertEqual(rss.scrape_rss2json("https://news.example.org", "RSS", now=NOW), [])


class TelegramTests(OfflineTest):
    def test_real_preview_html_short_messages_invalid_date_and_future(self):
        with patch.object(tg.scraper, "get", return_value=response(content=TELEGRAM_FIXTURE)):
            with self.assertLogs(tg.logger, level="WARNING"):
                items = tg._scrape_channel("cveNotify", "TG", now=NOW)
        self.assertEqual([item["link"] for item in items], ["https://t.me/cveNotify/2", "https://t.me/cveNotify/1"])
        self.assert_item_contract(items[1], "TG")

    def test_one_parser_failure_does_not_discard_channel(self):
        soup = BeautifulSoup(TELEGRAM_FIXTURE, "html.parser")
        valid = soup.select("div.tgme_widget_message")[0]
        broken = Mock()
        broken.select_one.side_effect = ValueError("malformed message")
        parsed = Mock()
        parsed.select.return_value = [valid, broken]
        with patch.object(tg.scraper, "get", return_value=response(content=TELEGRAM_FIXTURE)), patch.object(tg, "BeautifulSoup", return_value=parsed):
            with self.assertLogs(tg.logger, level="WARNING"):
                items = tg._scrape_channel("cveNotify", "TG", now=NOW)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["link"], "https://t.me/cveNotify/1")

    def test_telegram_bounds_and_missing_date_policy(self):
        for dt, expected in ((None, True), (NOW, True), (NOW - timedelta(days=3), True),
                             (NOW + timedelta(microseconds=1), False), (NOW - timedelta(days=3, seconds=1), False)):
            with self.subTest(dt=dt):
                self.assertEqual(tg._is_recent(dt, now=NOW), expected)
        self.assertTrue(tg._is_recent(NOW.replace(tzinfo=None), now=NOW))


class NvdTests(OfflineTest):
    def test_paginate_before_top_score_includes_critical_98(self):
        pages = [nvd_page([nvd_record("CVE-2024-6387", 8.1)], total=2),
                 nvd_page([nvd_record("CVE-2024-3400", 9.8)], index=1, total=2)]
        with patch.object(nvd.requests, "get", side_effect=pages) as get, patch.object(nvd.time, "sleep"):
            items = nvd.scrape_nvd_cves(limit=1, now=NOW)
        self.assertEqual([(item["cve_id"], item["cvss_score"]) for item in items], [("CVE-2024-3400", 9.8)])
        self.assertEqual(items[0]["cvss_severity"], "CRITICAL")
        self.assertIn("openbsd:openssh", items[0]["content"])
        self.assertEqual([call.kwargs["params"]["startIndex"] for call in get.call_args_list], [0, 1])
        for call in get.call_args_list:
            self.assertNotIn("cvssV3Severity", call.kwargs["params"])
            self.assertEqual(call.kwargs["params"]["resultsPerPage"], nvd.NVD_PAGE_SIZE)
            self.assertEqual(call.kwargs["params"]["lastModEndDate"], "2026-09-05T12:00:00.000+00:00")
        self.assert_item_contract(items[0], "NVD (NIST)")

    def test_v4_v31_v30_invalid_scores_and_bad_record_isolation(self):
        v4 = nvd_record("CVE-2026-1000", 9.9, "cvssMetricV40")
        v4["cve"]["metrics"]["cvssMetricV31"] = NVD_RECORD["cve"]["metrics"]["cvssMetricV31"]
        records = [None, {"cve": None}, {"cve": {"id": "bad-id"}}, v4,
                   nvd_record("CVE-2026-1001", "9.8", "cvssMetricV30"), NVD_RECORD]
        for index, bad in enumerate((float("nan"), float("inf"), -1, 10.1, True, None, "invalid")):
            records.append(nvd_record(f"CVE-2026-{2000 + index}", bad))
        with patch.object(nvd.requests, "get", return_value=nvd_page(records)):
            with self.assertLogs(nvd.logger, level="WARNING"):
                items = nvd.scrape_nvd_cves(now=NOW)
        self.assertEqual([i["cvss_score"] for i in items], [9.9, 9.8, 8.1])

    def test_invalid_v4_falls_back_to_valid_primary_v3(self):
        record = nvd_record("CVE-2026-1000", "NaN", "cvssMetricV40")
        metrics = copy.deepcopy(NVD_RECORD["cve"]["metrics"]["cvssMetricV31"])
        secondary = copy.deepcopy(metrics[0])
        secondary["type"] = "Secondary"
        secondary["cvssData"]["baseScore"] = 9.9
        record["cve"]["metrics"]["cvssMetricV31"] = [secondary, {"cvssData": {"baseScore": -1}}, *metrics]
        with patch.object(nvd.requests, "get", return_value=nvd_page([record])):
            with self.assertLogs(nvd.logger, level="WARNING"):
                self.assertEqual(nvd.scrape_nvd_cves(now=NOW)[0]["cvss_score"], 8.1)

    def test_rejected_duplicate_ids_and_score_zero(self):
        rejected = nvd_record("CVE-2026-1000", 10)
        rejected["cve"]["vulnStatus"] = "Rejected"
        records = [rejected, nvd_record("CVE-2026-1001", 0), nvd_record("CVE-2026-1002", 7), nvd_record("CVE-2026-1002", 8)]
        with patch.object(nvd.requests, "get", return_value=nvd_page(records)):
            items = nvd.scrape_nvd_cves(min_cvss=0, now=NOW)
        self.assertEqual([i["cvss_score"] for i in items], [8.0, 0.0])
        self.assertEqual(items[-1]["cvss_severity"], "NONE")

    def test_retry_429_and_5xx_retry_after_capped(self):
        replies = [response(status=429, headers={"Retry-After": "999999"}),
                   response(status=503, headers={"Retry-After": "2"}), response({})]
        with patch.object(nvd.requests, "get", side_effect=replies) as get, patch.object(nvd.time, "sleep") as sleep:
            with self.assertLogs(nvd.logger, level="WARNING"):
                self.assertEqual(nvd._nvd_get({}, {}).status_code, 200)
        self.assertEqual(get.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [nvd.NVD_MAX_RETRY_DELAY, 2])

    def test_retry_after_http_date_past_future_and_malformed(self):
        for header, expected in (("Sat, 05 Sep 2026 12:00:07 GMT", 7),
                                 ("Sat, 05 Sep 2026 12:10:00 GMT", nvd.NVD_MAX_RETRY_DELAY),
                                 ("Sat, 05 Sep 2026 11:59:00 GMT", 0), ("bad-date", 2)):
            with self.subTest(header=header), patch.object(nvd, "datetime", wraps=datetime) as clock:
                clock.now.return_value = NOW
                with patch.object(nvd.requests, "get", side_effect=[response(status=429, headers={"Retry-After": header}), response({})]), patch.object(nvd.time, "sleep") as sleep:
                    with self.assertLogs(nvd.logger, level="WARNING"):
                        self.assertEqual(nvd._nvd_get({}, {}).status_code, 200)
                sleep.assert_called_once_with(expected)

    def test_retry_exhaustion_transport_and_no_sleep_after_final_attempt(self):
        for failure in (requests.Timeout("offline timeout"), response(status=500)):
            with self.subTest(failure=type(failure).__name__):
                with patch.object(nvd.requests, "get", side_effect=[failure] * nvd.NVD_MAX_RETRIES) as get, patch.object(nvd.time, "sleep") as sleep:
                    with self.assertLogs(nvd.logger, level="ERROR"):
                        self.assertIsNone(nvd._nvd_get({}, {}))
                self.assertEqual(get.call_count, nvd.NVD_MAX_RETRIES)
                self.assertEqual(sleep.call_count, nvd.NVD_MAX_RETRIES - 1)

    def test_nonretryable_http_error_and_normal_empty_page(self):
        with patch.object(nvd.requests, "get", return_value=response(status=400)) as get, patch.object(nvd.time, "sleep") as sleep:
            with self.assertLogs(nvd.logger, level="ERROR"):
                self.assertEqual(nvd.scrape_nvd_cves(now=NOW), [])
        get.assert_called_once()
        sleep.assert_not_called()
        with patch.object(nvd.requests, "get", return_value=nvd_page([])):
            with self.assertNoLogs(nvd.logger, level="WARNING"):
                self.assertEqual(nvd.scrape_nvd_cves(now=NOW), [])

    def test_explicit_page_cap_and_repeated_page_stop(self):
        pages = [nvd_page([NVD_RECORD], total=100), nvd_page([NVD_RECORD], index=1, total=100)]
        with patch.object(nvd, "NVD_MAX_PAGES", 2), patch.object(nvd.requests, "get", side_effect=pages) as get, patch.object(nvd.time, "sleep"):
            with self.assertLogs(nvd.logger, level="WARNING") as logs:
                items = nvd.scrape_nvd_cves(now=NOW)
        self.assertEqual(len(items), 1)
        self.assertEqual(get.call_count, 2)
        self.assertIn("capped", " ".join(logs.output))
        with patch.object(nvd.requests, "get", return_value=nvd_page([NVD_RECORD], total=100)) as get, patch.object(nvd.time, "sleep"):
            with self.assertLogs(nvd.logger, level="ERROR"):
                items = nvd.scrape_nvd_cves(now=NOW)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(len(items), 1)

    def test_api_schema_failure_is_not_silent_empty(self):
        for payload in ({}, {"vulnerabilities": {}}, {"vulnerabilities": [], "totalResults": "0"}):
            with self.subTest(payload=payload), patch.object(nvd.requests, "get", return_value=response(payload)):
                with self.assertLogs(nvd.logger, level="ERROR"):
                    self.assertEqual(nvd.scrape_nvd_cves(now=NOW), [])

    def test_partial_fetch_preserves_valid_pages_and_stops_on_empty_page(self):
        for second in (response(status=400), nvd_page([], index=1, total=100), response(content=b"bad-json")):
            with self.subTest(status=second.status_code):
                with patch.object(nvd.requests, "get", side_effect=[nvd_page([NVD_RECORD], total=100), second]) as get, patch.object(nvd.time, "sleep"):
                    with self.assertLogs(nvd.logger, level="ERROR"):
                        items = nvd.scrape_nvd_cves(now=NOW)
                self.assertEqual(len(items), 1)
                self.assertEqual(get.call_count, 2)

    def test_invalid_window_score_and_zero_limit_make_no_requests(self):
        with patch.object(nvd.requests, "get") as get:
            for kwargs in ({"hours_back": -1}, {"hours_back": 2881}, {"min_cvss": "nan"}):
                with self.subTest(kwargs=kwargs), self.assertLogs(nvd.logger, level="ERROR"):
                    self.assertEqual(nvd.scrape_nvd_cves(now=NOW, **kwargs), [])
            self.assertEqual(nvd.scrape_nvd_cves(limit=0, now=NOW), [])
            get.assert_not_called()


class ApiContractsTests(OfflineTest):
    def test_vulners_wire_records_and_invalid_record_isolation(self):
        payload = {"result": "OK", "data": {"total": 3, "search": [
            None, {"_source": {"title": "Bad", "href": "https://vulners.com/bad", "cvss": {"score": "NaN"}}},
            {"_id": "CVE-2024-6387", "_source": {"id": "CVE-2024-6387", "title": "OpenSSH race condition",
                "href": "https://vulners.com/cve/CVE-2024-6387", "cvss": {"score": 8.1},
                "description": "A signal handler race condition", "published": "2026-09-05T10:00:00"}},
        ]}}
        with patch.object(exploitdb.requests, "post", return_value=response(payload)):
            with self.assertLogs(exploitdb.logger, level="WARNING"):
                items = exploitdb.scrape_vulners_recent()
        self.assertEqual(len(items), 1)
        self.assert_item_contract(items[0], "Vulners")

    def test_vulners_error_vs_empty(self):
        with patch.object(exploitdb.requests, "post", return_value=response(status=503)):
            with self.assertLogs(exploitdb.logger, level="ERROR"):
                self.assertEqual(exploitdb.scrape_vulners_recent(), [])
        with patch.object(exploitdb.requests, "post", return_value=response({"result": "OK", "data": {"search": []}})):
            with self.assertNoLogs(exploitdb.logger, level="WARNING"):
                self.assertEqual(exploitdb.scrape_vulners_recent(), [])

    def test_greynoise_gnql_wire_contract_and_malformed_record(self):
        payload = {"complete": True, "count": 1, "query": "classification:malicious", "data": [None,
            {"ip": "8.8.4.4", "classification": "malicious", "last_seen": "2026-09-05",
             "tags": ["CVE-2024-6387", {"name": "SSH Bruteforcer"}, None]}]}
        with patch.object(greynoise, "GREYNOISE_API_KEY", "offline-fixture"), patch.object(greynoise.requests, "get", return_value=response(payload)):
            with self.assertLogs(greynoise.logger, level="WARNING"):
                items = greynoise.scrape_greynoise_trends()
        self.assertEqual(len(items), 2)
        self.assertIn("CVE-2024-6387", items[0]["content"])
        self.assertIn("SSH Bruteforcer", items[0]["content"])
        self.assert_item_contract(items[0], "GreyNoise")

    def test_greynoise_optional_disabled_empty_and_error(self):
        self.assertEqual(greynoise.scrape_greynoise_trends(), [])
        with patch.object(greynoise, "GREYNOISE_API_KEY", "offline-fixture"):
            with patch.object(greynoise.requests, "get", return_value=response({"count": 0, "data": []})):
                with self.assertNoLogs(greynoise.logger, level="WARNING"):
                    self.assertEqual(greynoise.scrape_greynoise_trends(), [])
            with patch.object(greynoise.requests, "get", return_value=response({"message": "bad API schema"})):
                with self.assertLogs(greynoise.logger, level="ERROR"):
                    self.assertEqual(greynoise.scrape_greynoise_trends(), [])

    def test_greynoise_community_wire_error_and_not_observed(self):
        payload = {"ip": "71.6.135.131", "noise": True, "riot": False, "classification": "malicious",
                   "name": "unknown", "link": "https://viz.greynoise.io/ip/71.6.135.131", "last_seen": "2026-09-05", "message": "Success"}
        with patch.object(greynoise.requests, "get", side_effect=[response(content=b"not-json"), response(payload)]):
            with self.assertLogs(greynoise.logger, level="ERROR"):
                self.assertEqual(len(greynoise._fallback_community_lookup()), 1)
        with patch.object(greynoise.requests, "get", return_value=response(status=404)):
            with self.assertNoLogs(greynoise.logger, level="WARNING"):
                self.assertEqual(greynoise._fallback_community_lookup(), [])


class SeverityTests(OfflineTest):
    def test_keywords_match_whole_words_not_source_or_laptop(self):
        self.assertEqual(severity.classify_severity("Open source laptop"), "BAJA")
        self.assertEqual(severity.classify_severity("source laptop scarf"), "BAJA")
        self.assertEqual(severity.classify_severity("RCE and APT"), "CRITICA")
        self.assertEqual(severity.classify_severity("Remote code execution"), "ALTA")

    def test_unique_cves_across_title_content_and_iocs(self):
        baseline = severity.classify_severity("CVE-2024-6387")
        repeated = severity.classify_severity("CVE-2024-6387", "cve-2024-6387 CVE-2024-6387",
                                              iocs={"cve": ["CVE-2024-6387", "cve-2024-6387", "not-a-cve"]})
        self.assertEqual(baseline, repeated)
        self.assertEqual(baseline, severity.classify_severity("", iocs={"cve": ["CVE-2024-6387"]}))
        self.assertEqual(severity.classify_severity("CVE-2024-6387 CVE-2024-3400"), "ALTA")

    def test_cvss_boundaries_and_zero_override_keywords(self):
        for score, expected in ((0, "INFO"), (0.1, "BAJA"), (3.9, "BAJA"), (4, "MEDIA"), (6.9, "MEDIA"),
                                (7, "ALTA"), (8.9, "ALTA"), (9, "CRITICA"), (10, "CRITICA"), ("0", "INFO")):
            with self.subTest(score=score):
                self.assertEqual(severity.classify_severity("rce apt", cvss_score=score), expected)

    def test_nonfinite_invalid_and_out_of_range_scores_abstain(self):
        for bad in (float("nan"), float("inf"), -float("inf"), -1, 10.01, True, "NaN", "invalid", {}, None):
            with self.subTest(bad=bad):
                self.assertIsNone(severity.normalize_cvss_score(bad))
                self.assertEqual(severity.classify_severity("Open source laptop", cvss_score=bad), "BAJA")

    def test_existing_labels_preserved(self):
        self.assertEqual(list(severity.SEVERITY_CONFIG), ["CRITICA", "ALTA", "MEDIA", "BAJA", "INFO"])
        self.assertEqual(severity.SEVERITY_CONFIG["CRITICA"]["label"], "CR\u00cdTICA")
        self.assertEqual(severity.get_severity_label("ALTA"), "\U0001f7e0 ALTA")


class IocTests(OfflineTest):
    def test_whitelist_uses_actual_hostname_not_query_path_userinfo_or_prefix(self):
        malicious = ["https://evil.example.net/?ref=github.com", "https://evil.example.net/github.com",
                     "https://github.com.evil.net/a", "https://notgithub.com/a", "https://github.com@evil.net/a"]
        excluded = ["https://github.com/a", "https://raw.github.com/a", "https://GITHUB.COM.:443/a"]
        results = ioc.extract_iocs(" ".join(malicious + excluded))
        self.assertEqual(results["url"], sorted(malicious))
        self.assertIn("github.com.evil.net", results["domain"])
        self.assertIn("notgithub.com", results["domain"])
        self.assertNotIn("raw.github.com", results["domain"])

    def test_defanged_urls_domains_ipv4_email_and_hashes(self):
        text = "hxxps[:]//evil[.]net/a 8[.]8[.]8[.]8 hxxp://evil(.)org/b user[@]evil{.}net " + "A" * 64
        result = ioc.extract_iocs(text)
        self.assertEqual(result["url"], ["http://evil.org/b", "https://evil.net/a"])
        self.assertEqual(result["ipv4"], ["8.8.8.8"])
        self.assertEqual(result["email"], ["user@evil.net"])
        self.assertEqual(result["sha256"], ["a" * 64])
        self.assertIn("hxxps://evil[.]net/a", ioc.format_iocs_telegram(result))

    def test_ipaddress_normalizes_ipv6_and_excludes_nonpublic_addresses(self):
        text = """8.8.8.8 8.8.4.4 999.8.8.8 010.1.1.1 10.0.0.1 172.31.1.1 192.168.1.1
        127.0.0.1 169.254.1.1 192.0.2.1 198.51.100.1 203.0.113.1 100.64.0.1 224.0.0.1 240.0.0.1
        2606:4700:4700:0000:0000:0000:0000:1111 2606:4700:4700::1111 2606:4700::
        2001:db8::1 fc00::1 fe80::1 ff02::1 ::1 :: ::ffff:192.168.1.1"""
        result = ioc.extract_iocs(text)
        self.assertEqual(result["ipv4"], ["8.8.4.4", "8.8.8.8"])
        self.assertEqual(result["ipv6"], ["2606:4700:4700::1111", "2606:4700::"])

    def test_ipv6_url_and_bad_urls(self):
        text = "https://[2606:4700:4700::1111]/payload https://[::1]/local https://evil.net:invalid/a https://[broken]/a"
        self.assertEqual(ioc.extract_iocs(text)["url"], ["https://[2606:4700:4700::1111]/payload"])

    def test_cves_deduplicated_and_case_normalized(self):
        self.assertEqual(ioc.extract_iocs("cve-2024-6387 CVE-2024-6387 CVE-2026-12345678")["cve"],
                         ["CVE-2024-6387", "CVE-2026-12345678"])

    def test_sentence_punctuation_does_not_drop_iocs_or_match_partial_hosts(self):
        result = ioc.extract_iocs("Contact evil.net. Seen at 8.8.8.8. Also 2606:4700::1111. Ignore evil.net.invalidsuffix.")
        self.assertEqual(result["domain"], ["evil.net"])
        self.assertEqual(result["ipv4"], ["8.8.8.8"])
        self.assertEqual(result["ipv6"], ["2606:4700::1111"])

    def test_stix_uuid_ids_safe_patterns_and_no_automatic_malicious_claim(self):
        value = "https://evil.net/a' OR url:value = 'x\\y"
        bundle = ioc.iocs_to_stix({"url": [value], "cve": ["CVE-2024-6387"], "sha256": ["a" * 64]}, title="Report")
        identifiers = []
        for obj in [bundle, *bundle["objects"]]:
            prefix, identifier = obj["id"].split("--")
            self.assertEqual(prefix, obj["type"])
            self.assertEqual(UUID(identifier).version, 4)
            identifiers.append(obj["id"])
        self.assertEqual(len(identifiers), len(set(identifiers)))
        indicator = bundle["objects"][0]
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        self.assertEqual(indicator["pattern"], f"[url:value = '{escaped}']")
        self.assertEqual(indicator["indicator_types"], ["unknown"])
        self.assertNotIn("malicious-activity", json.dumps(bundle))
        self.assertEqual(bundle["objects"][1]["external_references"][0]["external_id"], "CVE-2024-6387")
        self.assertIsNone(ioc.iocs_to_stix({}))


class MitreTests(OfflineTest):
    def test_complete_pinned_catalog_names_and_deterministic_artifact(self):
        raw = (ROOT / "intelligence" / "attack_catalog.json").read_bytes()
        catalog = json.loads(raw)
        self.assertEqual(catalog["attack_version"], "17.1")
        self.assertEqual(catalog["source_url"], generator.SOURCE_URL)
        self.assertRegex(catalog["source_sha256"], r'^[0-9a-f]{64}$')
        self.assertEqual(catalog["technique_count"], 823)
        self.assertEqual(len(catalog["techniques"]), 823)
        self.assertEqual(len(catalog["tactics"]), 14)
        self.assertEqual(catalog["techniques"]["T1059.009"], "Cloud API")
        self.assertEqual(catalog["techniques"]["T1548.002"], "Bypass User Account Control")
        self.assertEqual(raw, (json.dumps(catalog, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode())

    def test_generator_preserves_all_catalog_ids_and_ignores_other_domains(self):
        catalog = mitre.ATTACK_CATALOG
        objects = [{"type": "attack-pattern", "name": name, "x_mitre_domains": ["enterprise-attack"],
                    "revoked": identifier == "T1004", "external_references": [{"source_name": "mitre-attack", "external_id": identifier}]}
                   for identifier, name in catalog["techniques"].items()]
        objects += [{"type": "x-mitre-tactic", "name": name, "x_mitre_domains": ["enterprise-attack"],
                     "external_references": [{"source_name": "mitre-attack", "external_id": identifier}]}
                    for identifier, name in catalog["tactics"].items()]
        objects.append({"type": "attack-pattern", "name": "Not Enterprise", "x_mitre_domains": ["mobile-attack"],
                        "external_references": [{"source_name": "mitre-attack", "external_id": "T9999"}]})
        result = generator.build_catalog(json.dumps({"type": "bundle", "objects": objects}).encode())
        self.assertEqual(result["techniques"], catalog["techniques"])
        self.assertEqual(result["tactics"], catalog["tactics"])
        with self.assertRaises(ValueError):
            generator.build_catalog(b'{"type":"bundle","objects":[]}')

    def test_llm_unknown_ids_abstain_names_canonical_and_duplicates_removed(self):
        text = "T9999 - Fabricated\nT1059.009 - Incorrect name\nT1059.009 - Duplicate\nT1651 - Also incorrect\nT1548.002 - Bypass UAC\nT1059.999 - Unknown subtechnique"
        result = types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=text))])
        with patch.object(mitre, "GROQ_API_KEYS", ["offline-fixture"]), patch.object(mitre, "groq_chat", return_value=result):
            techniques = mitre.tag_ttps("Cloud attack", "A cloud administration command ran")
        self.assertEqual(techniques, [{"id": "T1059.009", "name": "Cloud API"},
                                     {"id": "T1651", "name": "Cloud Administration Command"},
                                     {"id": "T1548.002", "name": "Bypass User Account Control"}])

    def test_none_unknown_and_no_credentials_abstain(self):
        self.assertEqual(mitre.tag_ttps("News"), [])
        for text in ("NONE", "T9999 - Unknown", "T1059.999 - Unknown", "T1059.0099 - Malformed"):
            with self.subTest(text=text):
                result = types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=text))])
                with patch.object(mitre, "GROQ_API_KEYS", ["offline-fixture"]), patch.object(mitre, "groq_chat", return_value=result):
                    self.assertEqual(mitre.tag_ttps("News"), [])

    def test_max_five_unique_ids(self):
        ids = ["T1001", "T1002", "T1003", "T1005", "T1006", "T1007"]
        text = "\n".join(f"{identifier} - model name" for identifier in [ids[0], *ids])
        result = types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=text))])
        with patch.object(mitre, "GROQ_API_KEYS", ["offline-fixture"]), patch.object(mitre, "groq_chat", return_value=result):
            self.assertEqual([item["id"] for item in mitre.tag_ttps("News")], ids[:5])

    def test_missing_catalog_fails_closed_without_calling_llm(self):
        spec = importlib.util.spec_from_file_location("audit_mitre_missing_catalog", ROOT / "intelligence" / "mitre_tagger.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"groq_rotation": llm_stub}), patch.object(Path, "read_text", side_effect=FileNotFoundError):
            with self.assertLogs("audit_mitre_missing_catalog", level="ERROR"):
                spec.loader.exec_module(module)
        with patch.object(module, "GROQ_API_KEYS", ["offline-fixture"]), patch.object(module, "groq_chat") as chat:
            self.assertEqual(module.tag_ttps("Attack"), [])
            chat.assert_not_called()


if __name__ == "__main__":
    unittest.main()
