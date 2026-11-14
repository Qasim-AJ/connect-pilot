import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from modules.connector import ConnectionManager
from modules.rate_limiter import RateLimiter


class FakeDriver:
    """Controllable fake WebDriver for testing multi-layered invitation verification."""

    def __init__(self, perf_logs=None, ui_state=None, resource_entries=None, modal_open=False):
        self._perf_logs = list(perf_logs or [])
        self._ui_state = ui_state or {
            "weekly_limit_reached": False,
            "failed": False,
            "error_text": "",
            "is_pending": False,
            "still_connect": True,
        }
        self._resource_entries = list(resource_entries or [])
        self._modal_open = modal_open
        self._cdp_bodies = {}
        self.current_url = "https://www.linkedin.com/search/results/people/?page=12"

    def set_cdp_body(self, request_id: str, body_str: str, base64_encoded: bool = True):
        if base64_encoded:
            encoded = base64.b64encode(body_str.encode("utf-8")).decode("ascii")
            self._cdp_bodies[request_id] = {"body": encoded, "base64Encoded": True}
        else:
            self._cdp_bodies[request_id] = {"body": body_str, "base64Encoded": False}

    def get_log(self, log_type: str):
        if log_type == "performance":
            out = list(self._perf_logs)
            self._perf_logs.clear()
            return out
        return []

    def execute_cdp_cmd(self, cmd: str, params: dict):
        if cmd == "Network.getResponseBody":
            rid = params.get("requestId")
            if rid in self._cdp_bodies:
                return self._cdp_bodies[rid]
            raise RuntimeError("No body")
        return {}

    def execute_script(self, script: str, *args):
        if "performance.now" in script:
            return 100.0
        if "getEntriesByType('resource')" in script:
            return self._resource_entries
        if "send without a note" in script.lower() and "clicked" not in script.lower():
            return self._modal_open
        if "weeklyLimitText" in script:
            return self._ui_state
        return None


def make_perf_entry(request_id: str, url: str, status: int) -> dict:
    return {
        "message": json.dumps({
            "message": {
                "method": "Network.responseReceived",
                "params": {
                    "requestId": request_id,
                    "response": {
                        "url": url,
                        "status": status,
                        "mimeType": "application/json",
                    },
                },
            }
        })
    }


class InvitationVerificationTests(unittest.TestCase):
    def test_detects_search_page_silent_429_fuse_limit_exceeded(self):
        url = (
            "https://www.linkedin.com/voyager/api/voyagerRelationshipsDashMemberRelationships"
            "?action=verifyQuotaAndCreateV2&decorationId=com.linkedin.voyager.dash.deco.relat"
        )
        driver = FakeDriver(perf_logs=[make_perf_entry("req-1", url, 429)])
        driver.set_cdp_body(
            "req-1",
            '{"data":{"code":"FUSE_LIMIT_EXCEEDED","status":429},"included":[]}',
            base64_encoded=True,
        )
        cm = ConnectionManager(driver)
        res = cm._verify_invitation_sent(
            100.0,
            profile_info={"name": "Mahmoud Yasser", "profile_url": "https://www.linkedin.com/in/mahmoud-yasser-39a589398/"},
            timeout=1.0,
        )
        self.assertFalse(res["verified"])
        self.assertTrue(res["weekly_limit_reached"])
        self.assertIn("FUSE_LIMIT_EXCEEDED", res["error"])

    def test_detects_visible_weekly_limit_toast_from_screenshot(self):
        toast_msg = (
            "Your invitation to Mairaj was not sent because you have reached the weekly limit "
            "for connection invitations. Please try again next week"
        )
        driver = FakeDriver(
            ui_state={
                "weekly_limit_reached": True,
                "failed": True,
                "error_text": toast_msg,
                "is_pending": False,
                "still_connect": True,
            }
        )
        cm = ConnectionManager(driver)
        res = cm._verify_invitation_sent(
            100.0,
            profile_info={"name": "Mairaj", "profile_url": "https://www.linkedin.com/in/mairaj/"},
            timeout=1.0,
        )
        self.assertFalse(res["verified"])
        self.assertTrue(res["weekly_limit_reached"])
        self.assertIn("reached the weekly limit", res["error"].lower())

    def test_rejects_silent_failure_when_button_stays_connect(self):
        driver = FakeDriver(
            ui_state={
                "weekly_limit_reached": False,
                "failed": False,
                "error_text": "",
                "is_pending": False,
                "still_connect": True,
            }
        )
        cm = ConnectionManager(driver)
        res = cm._verify_invitation_sent(
            100.0,
            profile_info={"name": "Subash S", "profile_url": "https://www.linkedin.com/in/subash-gtm-pm/"},
            timeout=0.5,
        )
        self.assertFalse(res["verified"])
        self.assertFalse(res["weekly_limit_reached"])
        self.assertIn("not sent", res["error"].lower())

    def test_verifies_success_on_pending_button_state(self):
        driver = FakeDriver(
            ui_state={
                "weekly_limit_reached": False,
                "failed": False,
                "error_text": "",
                "is_pending": True,
                "still_connect": False,
            }
        )
        cm = ConnectionManager(driver)
        res = cm._verify_invitation_sent(
            100.0,
            profile_info={"name": "Muzzamil Ali", "profile_url": "https://www.linkedin.com/in/muzzamil-ali/"},
            timeout=1.0,
        )
        self.assertTrue(res["verified"])
        self.assertFalse(res["weekly_limit_reached"])

    def test_verifies_success_on_200_invite_api_without_errors(self):
        url = (
            "https://www.linkedin.com/voyager/api/voyagerRelationshipsDashMemberRelationships"
            "?action=verifyQuotaAndCreateV2"
        )
        driver = FakeDriver(
            perf_logs=[make_perf_entry("req-ok", url, 200)],
            ui_state={
                "weekly_limit_reached": False,
                "failed": False,
                "error_text": "",
                "is_pending": False,
                "still_connect": False,
            },
        )
        driver.set_cdp_body("req-ok", '{"data":{"status":200}}', base64_encoded=False)
        cm = ConnectionManager(driver)
        res = cm._verify_invitation_sent(100.0, timeout=1.2)
        self.assertTrue(res["verified"])
        self.assertFalse(res["weekly_limit_reached"])

    def test_rate_limiter_tracks_failures_and_weekly_limit_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test_bot.db"
            rl = RateLimiter(database_path=db_path)

            # Log 1 verified sent request
            rl.log_request("https://www.linkedin.com/in/alice/", "sent", "Alice")
            stats1 = rl.get_statistics()
            self.assertEqual(stats1["today_count"], 1)
            self.assertEqual(stats1["today_failed"], 0)
            self.assertFalse(stats1["linkedin_weekly_limit_active"])

            # Log 1 failed request due to LinkedIn weekly limit
            rl.log_request(
                "https://www.linkedin.com/in/bob/",
                "failed",
                "Bob",
                error_message="LinkedIn weekly connection invitation limit reached [HTTP 429 (FUSE_LIMIT_EXCEEDED)]",
                metadata={"weekly_limit_reached": True},
            )
            stats2 = rl.get_statistics()
            # Sent count MUST remain 1 (failed requests never inflate sent count!)
            self.assertEqual(stats2["today_count"], 1)
            self.assertEqual(stats2["today_failed"], 1)
            self.assertTrue(stats2["linkedin_weekly_limit_active"])
            self.assertEqual(stats2["daily_remaining"], 0)
            self.assertEqual(stats2["weekly_remaining"], 0)

            # Once LinkedIn resets next week and a connection succeeds, active limit flag clears automatically
            rl.log_request("https://www.linkedin.com/in/charlie/", "sent", "Charlie")
            stats3 = rl.get_statistics()
            self.assertEqual(stats3["today_count"], 2)
            self.assertFalse(stats3["linkedin_weekly_limit_active"])
            self.assertGreater(stats3["weekly_remaining"], 0)


if __name__ == "__main__":
    unittest.main()
