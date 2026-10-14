"""
tests/test_web_server.py: Integration test suite for Radar Web Server & REST API endpoints.
Tests /api/stats, /api/opportunities, /api/config, and static HTML serving.
"""

import json
import threading
import time
import unittest
import urllib.request
from pathlib import Path

from web_server import RadarAPIHandler, RadarController, ThreadingHTTPServer


class TestWebServer(unittest.TestCase):
    server: ThreadingHTTPServer
    server_thread: threading.Thread
    port: int = 8999

    @classmethod
    def setUpClass(cls):
        base_dir = Path(__file__).parent.parent
        db_path = base_dir / "radar.db"
        config_path = base_dir / "config.json"
        presets_path = base_dir / "presets.json"
        web_dir = base_dir / "web"

        controller = RadarController(db_path, config_path, presets_path)
        RadarAPIHandler.controller = controller
        RadarAPIHandler.web_dir = web_dir

        cls.server = ThreadingHTTPServer(("127.0.0.1", cls.port), RadarAPIHandler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.2)  # Give server brief time to bind

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path: str):
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()

    def _post_json(self, path: str, payload: dict):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def test_serve_index_html(self):
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"ARBITRAGE RADAR", body)

    def test_api_stats(self):
        status, body = self._get("/api/stats")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("total_listings", data)
        self.assertIn("total_opportunities", data)
        self.assertIn("is_scanning", data)

    def test_api_opportunities(self):
        status, body = self._get("/api/opportunities?limit=5")
        self.assertEqual(status, 200)
        opps = json.loads(body.decode("utf-8"))
        self.assertIsInstance(opps, list)
        if len(opps) > 0:
            self.assertIn("title", opps[0])
            self.assertIn("profit_margin", opps[0])

    def test_api_config_get_and_post(self):
        status, body = self._get("/api/config")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("config", data)
        self.assertIn("presets", data)

        # Test POST update config
        orig_min = data["config"].get("min_profit_tl", 3000)
        update_status, update_res = self._post_json("/api/config", {"min_profit_tl": orig_min})
        self.assertEqual(update_status, 200)
        self.assertEqual(update_res["status"], "saved")


if __name__ == "__main__":
    unittest.main()
