import os
import unittest
from email.utils import formatdate
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
from groq import APIConnectionError, APIStatusError, APITimeoutError

import groq_rotation as providers


def status_error(status, message="provider error", headers=None, code=None):
    response = httpx.Response(
        status, request=httpx.Request("POST", "https://provider.invalid/chat"),
        headers=headers,
    )
    return APIStatusError(message, response=response, body={
        "error": {"message": message, "code": code},
    })


class ProviderTests(unittest.TestCase):
    def setUp(self):
        for name, value in (
            ("_clients", []), ("_idx", 0), ("_exhausted", set()),
            ("_cooldowns", {}), ("_failures", {}), ("NVIDIA_API_KEY", ""),
        ):
            patcher = patch.object(providers, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        clock = patch.object(providers.time, "monotonic", return_value=100.0)
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        self.response = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="complete"), finish_reason="stop",
        )])

    def client(self, *results):
        client = MagicMock()
        client.chat.completions.create.side_effect = results
        providers._clients.append(client)
        return client.chat.completions.create

    def test_sdk_transients_recover_after_cooldown(self):
        request = httpx.Request("POST", "https://provider.invalid/chat")
        errors = [
            APITimeoutError(request=request), APIConnectionError(request=request),
            TimeoutError("timeout"), status_error(408), status_error(409),
            status_error(429), status_error(500), status_error(502), status_error(503),
        ]
        for error in errors:
            with self.subTest(error=type(error).__name__, status=getattr(error, "status_code", None)):
                providers._clients.clear()
                self.clock.return_value = 100.0
                create = self.client(error, self.response)
                self.assertIsNone(providers.groq_chat(model="m", messages=[]))
                self.assertFalse(providers._exhausted)
                self.assertEqual(providers._cooldowns[0], 105.0)
                self.clock.return_value = 104.0
                self.assertIsNone(providers.groq_chat(model="m", messages=[]))
                self.assertEqual(create.call_count, 1)
                self.clock.return_value = 105.0
                self.assertIs(providers.groq_chat(model="m", messages=[]), self.response)
                self.assertFalse(providers._cooldowns)
                self.assertFalse(providers._failures)

    def test_transient_rotates_without_exhausting_key(self):
        first = self.client(status_error(503), self.response)
        second = self.client(self.response, status_error(503))
        self.assertIs(providers.groq_chat(model="m"), self.response)
        self.assertEqual(providers._idx, 1)
        self.assertFalse(providers._exhausted)
        self.clock.return_value = 105.0
        self.assertIs(providers.groq_chat(model="m"), self.response)
        self.assertEqual(first.call_count, 2)
        self.assertEqual(second.call_count, 2)

    def test_backoff_increases_is_bounded_and_resets_on_success(self):
        self.client(*([status_error(503)] * 9), self.response, status_error(503))
        for delay in (5, 10, 20, 40, 80, 160, 300, 300, 300):
            self.assertIsNone(providers.groq_chat(model="m"))
            self.assertEqual(providers._cooldowns[0] - self.clock.return_value, delay)
            self.clock.return_value += delay
        self.assertIs(providers.groq_chat(model="m"), self.response)
        self.assertIsNone(providers.groq_chat(model="m"))
        self.assertEqual(providers._cooldowns[0] - self.clock.return_value, 5)

    def test_retry_after_seconds_and_http_date(self):
        for header in ("30", formatdate(1030, usegmt=True)):
            with self.subTest(header=header), patch.object(providers.time, "time", return_value=1000):
                providers._clients.clear()
                providers._failures.clear()
                providers._cooldowns.clear()
                self.client(status_error(429, headers={"retry-after": header}))
                self.assertIsNone(providers.groq_chat(model="m"))
                self.assertEqual(providers._cooldowns[0], 130)

    def test_invalid_retry_after_uses_local_backoff(self):
        for header in ("invalid", "NaN", "inf", "-1"):
            with self.subTest(header=header):
                providers._clients.clear()
                providers._failures.clear()
                providers._cooldowns.clear()
                self.client(status_error(503, headers={"retry-after": header}))
                self.assertIsNone(providers.groq_chat(model="m"))
                self.assertEqual(providers._cooldowns[0], 105)

    def test_auth_and_daily_quota_exclude_for_run(self):
        errors = [
            status_error(401), status_error(403),
            status_error(429, "Tokens per day (TPD) exceeded"),
            status_error(429, code="insufficient_quota"),
            Exception("Error code: 429 - rate_limit_exceeded: tokens per day (TPD)"),
            Exception("401 Invalid API Key"),
        ]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                providers._clients.clear()
                providers._exhausted.clear()
                create = self.client(error)
                self.assertIsNone(providers.groq_chat(model="m"))
                self.assertEqual(providers._exhausted, {0})
                self.clock.return_value += 100000
                self.assertIsNone(providers.groq_chat(model="m"))
                self.assertEqual(create.call_count, 1)

    def test_status_takes_precedence_over_error_text(self):
        self.client(status_error(503, "401 invalid key; 429 tokens per day; model_not_found"))
        self.assertIsNone(providers.groq_chat(model="m"))
        self.assertFalse(providers._exhausted)
        self.assertEqual(providers._cooldowns[0], 105)

    def test_bad_request_does_not_disable_key_for_next_request(self):
        self.client(status_error(400, "invalid parameters"), self.response)
        self.assertIsNone(providers.groq_chat(model="m"))
        self.assertFalse(providers._exhausted)
        self.assertFalse(providers._cooldowns)
        self.assertIs(providers.groq_chat(model="m"), self.response)

    def test_model_fallback_and_its_transient_error_recover(self):
        create = self.client(status_error(404), status_error(503), self.response)
        self.assertIsNone(providers.groq_chat(model="primary"))
        self.assertEqual(create.call_args.kwargs["model"], providers.GROQ_FALLBACK_MODEL)
        self.assertFalse(providers._exhausted)
        self.clock.return_value = 105
        self.assertIs(providers.groq_chat(model="primary"), self.response)

    def test_model_errors_do_not_exhaust_key(self):
        create = self.client(status_error(404), status_error(404), self.response)
        self.assertIsNone(providers.groq_chat(model="primary"))
        self.assertEqual(create.call_count, 2)
        self.assertFalse(providers._exhausted)
        self.assertIs(providers.groq_chat(model="another-model"), self.response)

    def test_cooldown_uses_nvidia_then_returns_to_groq(self):
        create = self.client(status_error(503), self.response)
        with patch.object(providers, "NVIDIA_API_KEY", "fake-key"), \
             patch.object(providers, "nvidia_chat", return_value=self.response) as nvidia:
            self.assertIs(providers.groq_chat(model="m"), self.response)
            self.assertIs(providers.groq_chat(model="m"), self.response)
            self.assertEqual(create.call_count, 1)
            self.assertEqual(nvidia.call_count, 2)
            self.clock.return_value = 105
            self.assertIs(providers.groq_chat(model="m"), self.response)
            self.assertEqual(nvidia.call_count, 2)

    def test_groq_logs_do_not_expose_error_body_or_model(self):
        secret = "private-sentinel-do-not-log"
        self.client(status_error(404, secret), status_error(401, secret))
        with self.assertLogs(providers.logger, level="WARNING") as logs:
            self.assertIsNone(providers.groq_chat(model=secret))
        self.assertNotIn(secret, " ".join(logs.output))

    def test_unknown_errors_are_temporary_and_not_logged_verbatim(self):
        secret = "private-sentinel-do-not-log"
        self.client(RuntimeError(secret), self.response)
        with self.assertLogs(providers.logger, level="WARNING") as logs:
            self.assertIsNone(providers.groq_chat(model="m"))
        self.assertNotIn(secret, " ".join(logs.output))
        self.assertFalse(providers._exhausted)
        self.clock.return_value = 105
        self.assertIs(providers.groq_chat(model="m"), self.response)

    def test_nvidia_strips_reasoning_and_preserves_finish_reason(self):
        for finish in ("stop", "length"):
            response = MagicMock(status_code=200)
            response.json.return_value = {"choices": [{
                "message": {"content": "partial"}, "finish_reason": finish,
            }]}
            with self.subTest(finish=finish), \
                 patch.object(providers, "NVIDIA_API_KEY", "fake-key"), \
                 patch.dict(os.environ, {"NVIDIA_MODEL": "meta/test-model"}), \
                 patch.object(providers.requests, "post", return_value=response) as post:
                request = dict(model="groq-model", reasoning_effort="low", max_tokens=500, messages=[])
                result = providers.nvidia_chat(**request)
                payload = post.call_args.kwargs["json"]
                self.assertNotIn("reasoning_effort", payload)
                self.assertEqual(payload["model"], "meta/test-model")
                self.assertEqual(payload["max_tokens"], 500)
                self.assertEqual(request["reasoning_effort"], "low")
                self.assertEqual(result.choices[0].finish_reason, finish)

    def test_nvidia_truncation_rejected_by_summary_consumer(self):
        # Imported only inside the isolated test runner, never to load .env.
        import run_job

        response = MagicMock(status_code=200)
        response.json.return_value = {"choices": [{
            "message": {"content": "partial"}, "finish_reason": "length",
        }]}
        with patch.object(providers, "NVIDIA_API_KEY", "fake-key"), \
             patch.object(run_job, "NVIDIA_API_KEY", "fake-key"), \
             patch.object(run_job, "groq_chat", providers.groq_chat), \
             patch.object(providers.requests, "post", return_value=response), \
             patch.object(run_job, "parse_news_response") as parse:
            self.assertEqual(run_job.summarize_news("title", "body"), (None, None))
            parse.assert_not_called()

    def test_nvidia_errors_do_not_log_bodies_or_exceptions(self):
        secret = "private-sentinel-do-not-log"
        response = MagicMock(status_code=403, text=secret)
        for result in (response, providers.requests.Timeout(secret)):
            with self.subTest(result=type(result).__name__), \
                 patch.object(providers, "NVIDIA_API_KEY", "fake-key"), \
                 patch.object(providers.requests, "post", side_effect=[result]), \
                 self.assertLogs(providers.logger, level="ERROR") as logs:
                self.assertIsNone(providers.nvidia_chat(model="m"))
            self.assertNotIn(secret, " ".join(logs.output))

    def test_nvidia_malformed_response_returns_none(self):
        for body in ({}, {"choices": []}, {"choices": [{"message": {}}]}):
            response = MagicMock(status_code=200)
            response.json.return_value = body
            with self.subTest(body=body), \
                 patch.object(providers, "NVIDIA_API_KEY", "fake-key"), \
                 patch.object(providers.requests, "post", return_value=response):
                self.assertIsNone(providers.nvidia_chat(model="m"))
