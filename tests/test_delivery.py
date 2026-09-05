import base64
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import requests
import run_job


def response(status, data):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(data).encode()
    return result


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.stored = []
        self.sha = "revision-0"
        self.revision = 0
        self.telegram_calls = []
        self.fail_put = False
        self.fail_ack = False
        self.accept_then_timeout = False
        self.telegram_response = response(200, {"ok": True, "result": {"message_id": 42}})
        self.item = {"title": "Zeta ransomware", "content": "Zeta encrypts server backups.",
                     "source": "Audit News", "link": "https://example.test/report"}
        replacements = {
            "GIT_TOKEN": "fake-git", "TELEGRAM_TOKEN": "fake-telegram", "TELEGRAM_CHAT_ID": "123",
            "ALL_RSS_SCRAPERS": [lambda: [self.item]],
            "scrape_nvd_cves": lambda **kwargs: [], "scrape_exploitdb": lambda: [],
            "scrape_greynoise_trends": lambda: [], "scrape_telegram_channels": lambda: [],
            "summarize_news": lambda *args: ("Zeta ransomware", "Zeta cifra copias de seguridad."),
            "tag_ttps": lambda *args: [], "get_image_url": lambda *args: "https://example.test/image",
        }
        for name, value in replacements.items():
            self.enterContext(patch.object(run_job, name, value))
        self.enterContext(patch.object(run_job.requests, "get", side_effect=self.get))
        self.enterContext(patch.object(run_job.requests, "put", side_effect=self.put))
        self.enterContext(patch.object(run_job.requests, "post", side_effect=self.post))
        self.enterContext(patch.object(run_job.time, "sleep"))

    def get(self, url, **kwargs):
        encoded = base64.b64encode(json.dumps(self.stored).encode()).decode()
        wrapped = "\n".join(encoded[i:i + 60] for i in range(0, len(encoded), 60)) + "\n"
        return response(200, {"sha": self.sha, "content": wrapped})

    def put(self, url, **kwargs):
        payload = kwargs["json"]
        intended = json.loads(base64.b64decode(payload["content"]))
        if self.fail_put or (self.fail_ack and any(n.get("telegram", {}).get("status") == "sent" for n in intended)):
            return response(503, {})
        if payload.get("sha") != self.sha:
            return response(409, {})
        self.stored = intended
        self.revision += 1
        self.sha = f"revision-{self.revision}"
        if self.accept_then_timeout:
            raise requests.ReadTimeout("simulated")
        return response(200, {"content": {"sha": self.sha}})

    def post(self, url, **kwargs):
        # Every external send must already have a durable sending marker.
        if self.stored:
            self.assertEqual(self.stored[0]["telegram"]["status"], "sending")
        self.telegram_calls.append(deepcopy(kwargs["json"]))
        if isinstance(self.telegram_response, Exception):
            raise self.telegram_response
        return self.telegram_response

    def test_success_is_not_republished_on_next_run(self):
        run_job.job()
        run_job.job()
        self.assertEqual(len(self.telegram_calls), 1)
        self.assertEqual(self.stored[0]["telegram"]["status"], "sent")
        self.assertEqual(self.stored[0]["telegram"]["message_id"], 42)
        self.assertNotIn("text", self.stored[0]["telegram"])

    def test_confirmed_rejection_retries_without_losing_news(self):
        self.telegram_response = response(500, {"ok": False, "description": "Rejected"})
        with self.assertRaises(RuntimeError):
            run_job.job()
        self.assertEqual(self.stored[0]["telegram"]["status"], "failed")
        self.telegram_response = response(200, {"ok": True, "result": {"message_id": 43}})
        run_job.job()
        self.assertEqual(len(self.telegram_calls), 2)
        self.assertEqual(len(self.stored), 1)
        self.assertEqual(self.stored[0]["telegram"]["attempts"], 2)

    def test_cannot_send_before_queue_is_persisted(self):
        self.fail_put = True
        with self.assertRaises(RuntimeError):
            run_job.job()
        self.assertEqual(self.telegram_calls, [])
        self.assertEqual(self.stored, [])

    def test_cannot_send_before_sending_marker_is_persisted(self):
        self.stored = [{"id": 1, "telegram": {"status": "pending", "text": "test", "attempts": 0}}]
        self.fail_put = True
        with self.assertRaises(RuntimeError):
            run_job.deliver_pending(deepcopy(self.stored), self.sha)
        self.assertEqual(self.telegram_calls, [])
        self.assertEqual(self.stored[0]["telegram"]["status"], "pending")

    def test_failed_ack_never_automatically_repeats_accepted_send(self):
        self.fail_ack = True
        with self.assertRaises(RuntimeError):
            run_job.job()
        self.assertEqual(self.stored[0]["telegram"]["status"], "sending")
        self.fail_ack = False
        with self.assertRaisesRegex(RuntimeError, "incierta"):
            run_job.job()
        self.assertEqual(len(self.telegram_calls), 1)
        self.assertEqual(self.stored[0]["telegram"]["status"], "uncertain")

    def test_timeout_is_uncertain_and_not_retried(self):
        self.telegram_response = requests.ReadTimeout("simulated token-bearing URL")
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                run_job.job()
        self.assertEqual(len(self.telegram_calls), 1)
        self.assertEqual(self.stored[0]["telegram"]["status"], "uncertain")

    def test_accepted_github_write_with_lost_response_is_reconciled(self):
        self.accept_then_timeout = True
        run_job.job()
        self.assertEqual(len(self.telegram_calls), 1)
        self.assertEqual(self.stored[0]["telegram"]["status"], "sent")

    def test_conflict_aborts_without_overwriting_remote(self):
        self.stored = [{"id": 99, "titulo": "Other writer"}]
        with self.assertRaises(RuntimeError):
            run_job.commit_noticias([], "stale-sha")
        self.assertEqual(self.stored[0]["id"], 99)
        self.assertEqual(self.telegram_calls, [])

    def test_competing_sender_cannot_reconcile_another_writers_claim(self):
        self.stored = [{"id": 1, "telegram": {"status": "pending", "text": "test", "attempts": 0}}]
        first = deepcopy(self.stored)
        rival = deepcopy(self.stored)
        original_sha = self.sha

        def race(url, **kwargs):
            with self.assertRaises(RuntimeError):
                run_job.deliver_pending(rival, original_sha)
            return self.post(url, **kwargs)

        with patch.object(run_job.requests, "post", side_effect=race):
            run_job.deliver_pending(first, original_sha)
        self.assertEqual(len(self.telegram_calls), 1)
        self.assertEqual(self.stored[0]["telegram"]["status"], "sent")

    def test_failed_history_load_aborts_before_collection(self):
        with patch.object(run_job, "get_github_file", return_value=(None, None)), \
             patch.object(run_job, "scrape_nvd_cves") as source:
            with self.assertRaises(RuntimeError):
                run_job.job()
        source.assert_not_called()
        self.assertEqual(self.telegram_calls, [])

    def test_legacy_records_are_not_resent(self):
        legacy = [{"id": 1, "titulo": "Previous news"}]
        self.assertEqual(run_job.deliver_pending(legacy, self.sha), self.sha)
        self.assertEqual(self.telegram_calls, [])

    def test_dedup_never_drops_unresolved_delivery(self):
        items = [{"id": 2, "titulo": "Same news"}, {"id": 1, "titulo": "Same news",
                  "telegram": {"status": "failed", "text": "test", "attempts": 1}}]
        kept, removed = run_job.deduplicar_noticias(items)
        self.assertEqual(len(kept), 2)
        self.assertEqual(removed, [])

    def test_retry_after_is_persisted_and_respected(self):
        self.telegram_response = response(429, {"ok": False, "parameters": {"retry_after": 3600}})
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                run_job.job()
        self.assertEqual(len(self.telegram_calls), 1)
        self.assertGreater(self.stored[0]["telegram"]["retry_at"], run_job.time.time())

    def test_retry_budget_blocks_further_attempts(self):
        self.stored = [{"id": 1, "telegram": {"status": "failed", "text": "test", "attempts": 5}}]
        with self.assertRaisesRegex(RuntimeError, "agotada"):
            run_job.job()
        self.assertEqual(self.telegram_calls, [])

    def test_malformed_success_is_uncertain(self):
        self.telegram_response = response(200, {"ok": True, "result": {}})
        self.assertEqual(run_job.send_to_telegram("test")["status"], "uncertain")

    def test_markdown_rejection_falls_back_to_plain_text(self):
        with patch.object(run_job.requests, "post", side_effect=[
            response(400, {"ok": False, "description": "Bad Request: can't parse entities"}),
            response(200, {"ok": True, "result": {"message_id": 1}}),
        ]) as post:
            self.assertEqual(run_job.send_to_telegram("bad * text")["status"], "sent")
        self.assertEqual(post.call_count, 2)
        self.assertNotIn("parse_mode", post.call_args.kwargs["json"])

    def test_long_message_is_bounded_in_utf16_units(self):
        run_job.send_to_telegram("\U0001f600" * 5000)
        self.assertLessEqual(len(self.telegram_calls[0]["text"].encode("utf-16-le")), 8192)
        self.assertNotIn("parse_mode", self.telegram_calls[0])

    def test_history_wrong_shape_is_rejected(self):
        with patch.object(run_job.requests, "get", return_value=response(200, {
            "sha": "abc", "content": base64.b64encode(b"{}").decode(),
        })):
            self.assertEqual(run_job.get_github_file(), (None, None))

    def test_missing_file_requires_accessible_repository(self):
        with patch.object(run_job.requests, "get", side_effect=[response(404, {}), response(200, {})]):
            self.assertEqual(run_job.get_github_file(), ([], None))
        with patch.object(run_job.requests, "get", return_value=response(404, {})):
            self.assertEqual(run_job.get_github_file(), (None, None))


if __name__ == "__main__":
    unittest.main()
