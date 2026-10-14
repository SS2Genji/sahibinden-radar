"""
web_server.py: Lightweight, zero-dependency HTTP server and REST API for
the Local Arbitrage & Price Drop Radar Web Dashboard.
Built entirely on Python standard library (http.server, threading, json).
"""

from __future__ import annotations

import json
import mimetypes
import os
import random
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from cdp_scanner import CDPScanner
from filter_engine import FilterEngine
from main import load_json, resolve_active_targets, run_cycle, save_json
from notifier import Notifier
from radar_db import (
    add_custom_target,
    db_session,
    delete_custom_target,
    get_custom_targets,
    get_dashboard_stats,
    get_opportunities,
    get_recent_listings,
    init_db,
    toggle_custom_target,
)


class RadarController:
    """Manages background scanning cycles and daemon loop with thread safety."""

    def __init__(self, db_path: Path, config_path: Path, presets_path: Path):
        self.db_path = db_path
        self.config_path = config_path
        self.presets_path = presets_path

        self.lock = threading.Lock()
        self.is_scanning = False
        self.last_scan_result: Dict[str, Any] = {"status": "idle", "alerts_fired": 0, "timestamp": None}

        self.daemon_active = False
        self.daemon_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def execute_scan(self) -> Dict[str, Any]:
        """Runs a single scanning cycle across active presets and custom targets."""
        if not self.lock.acquire(blocking=False):
            return {"status": "busy", "message": "A scan is already in progress"}

        try:
            self.is_scanning = True
            config = load_json(self.config_path)
            presets = load_json(self.presets_path)

            targets, preset_keywords = resolve_active_targets(config, presets)

            # Include user-defined custom targets from SQLite
            custom_targets = get_custom_targets(self.db_path, enabled_only=True)
            for ct in custom_targets:
                targets.append({
                    "name": ct["name"],
                    "url": ct["url"],
                    "min_price": ct.get("min_price"),
                    "max_price": ct.get("max_price"),
                    "category": ct["name"],
                })
                if ct.get("negative_keywords"):
                    preset_keywords[ct["name"]] = ct["negative_keywords"]

            if not targets:
                return {"status": "error", "message": "No active targets configured"}

            telegram_cfg = config.get("telegram", {})
            notifier = Notifier(
                bot_token=telegram_cfg.get("bot_token"),
                chat_id=telegram_cfg.get("chat_id"),
                enabled=telegram_cfg.get("enabled", False),
            )

            filter_engine = FilterEngine(
                db_path=self.db_path,
                negative_keywords=config.get("negative_keywords", []),
                discount_threshold_pct=config.get("min_discount_pct", 12.0),
                min_profit_tl=config.get("min_profit_tl", 0),
                target_cities=config.get("target_cities", []),
            )

            scanner = CDPScanner(cdp_port=config.get("cdp_port", 9222))
            alerts = run_cycle(scanner, filter_engine, notifier, targets, preset_keywords, self.db_path)

            result = {
                "status": "success",
                "alerts_fired": alerts,
                "timestamp": time.time(),
            }
            self.last_scan_result = result
            return result
        except Exception as e:
            err_result = {"status": "error", "message": str(e), "timestamp": time.time()}
            self.last_scan_result = err_result
            return err_result
        finally:
            self.is_scanning = False
            self.lock.release()

    def start_daemon(self) -> bool:
        """Starts continuous background scanning with humanized jitter."""
        if self.daemon_active:
            return False

        self._stop_event.clear()
        self.daemon_active = True

        def _worker():
            while not self._stop_event.is_set():
                config = load_json(self.config_path)
                min_int = config.get("scan_interval_min_sec", 45)
                max_int = config.get("scan_interval_max_sec", 75)

                self.execute_scan()

                jitter = random.uniform(min_int, max_int)
                if self._stop_event.wait(timeout=jitter):
                    break
            self.daemon_active = False

        self.daemon_thread = threading.Thread(target=_worker, daemon=True)
        self.daemon_thread.start()
        return True

    def stop_daemon(self) -> bool:
        """Stops the continuous daemon loop."""
        if not self.daemon_active:
            return False
        self._stop_event.set()
        self.daemon_active = False
        return True


class RadarAPIHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler providing REST endpoints and static file serving."""

    controller: RadarController
    web_dir: Path

    def _send_json(self, data: Any, status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # ---------------- REST API ----------------
        if path == "/api/stats":
            stats = get_dashboard_stats(self.controller.db_path)
            stats["is_scanning"] = self.controller.is_scanning
            stats["is_daemon_active"] = self.controller.daemon_active
            stats["last_scan_result"] = self.controller.last_scan_result
            self._send_json(stats)
            return

        elif path == "/api/opportunities":
            limit = int(query.get("limit", [50])[0])
            category = query.get("category", [None])[0]
            min_profit = int(query.get("min_profit", [0])[0])
            opps = get_opportunities(
                self.controller.db_path,
                limit=limit,
                category=category,
                min_profit=min_profit,
            )
            self._send_json(opps)
            return

        elif path == "/api/listings":
            limit = int(query.get("limit", [500])[0])
            search = query.get("search", [None])[0]
            items = get_recent_listings(self.controller.db_path, limit=limit, search=search)
            self._send_json(items)
            return

        elif path == "/api/config":
            cfg = load_json(self.controller.config_path)
            presets = load_json(self.controller.presets_path)
            self._send_json({"config": cfg, "presets": presets})
            return

        elif path == "/api/scanner/status":
            self._send_json({
                "is_scanning": self.controller.is_scanning,
                "daemon_active": self.controller.daemon_active,
                "last_result": self.controller.last_scan_result,
            })
            return

        elif path == "/api/targets":
            targets = get_custom_targets(self.controller.db_path)
            self._send_json(targets)
            return

        # ---------------- Static Files ----------------
        if path == "/" or path == "/index.html":
            file_path = self.web_dir / "index.html"
        else:
            clean_rel = path.lstrip("/")
            file_path = (self.web_dir / clean_rel).resolve()
            # Prevent path traversal
            if not str(file_path).startswith(str(self.web_dir.resolve())):
                self.send_error(HTTPStatus.FORBIDDEN, "Forbidden")
                return

        if not file_path.exists() or file_path.is_dir():
            self.send_error(HTTPStatus.NOT_FOUND, "File Not Found")
            return

        content_type, _ = mimetypes.guess_type(str(file_path))
        content_type = content_type or "application/octet-stream"

        try:
            with open(file_path, "rb") as f:
                content = f.read()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
        try:
            data = json.loads(body)
        except Exception:
            data = {}

        if path == "/api/scan":
            # Run scan in thread so response returns quickly or synchronously if requested
            async_scan = data.get("async", True)
            if async_scan:
                threading.Thread(target=self.controller.execute_scan, daemon=True).start()
                self._send_json({"status": "initiated", "message": "Scan cycle started in background"})
            else:
                res = self.controller.execute_scan()
                self._send_json(res)
            return

        elif path == "/api/scanner/toggle":
            action = data.get("action", "toggle")
            if action == "start" or (action == "toggle" and not self.controller.daemon_active):
                ok = self.controller.start_daemon()
                self._send_json({"status": "ok", "daemon_active": self.controller.daemon_active, "started": ok})
            else:
                ok = self.controller.stop_daemon()
                self._send_json({"status": "ok", "daemon_active": self.controller.daemon_active, "stopped": ok})
            return

        elif path == "/api/config":
            current_cfg = load_json(self.controller.config_path)
            for k in ["active_presets", "min_profit_tl", "min_discount_pct", "target_cities"]:
                if k in data:
                    current_cfg[k] = data[k]
            save_json(self.controller.config_path, current_cfg)
            self._send_json({"status": "saved", "config": current_cfg})
            return

        elif path == "/api/opportunities/dismiss":
            opp_id = data.get("id")
            if opp_id:
                with db_session(self.controller.db_path) as conn:
                    conn.execute("UPDATE opportunities SET dismissed = 1 WHERE id = ?", (int(opp_id),))
                self._send_json({"status": "dismissed", "id": opp_id})
            else:
                self._send_json({"status": "error", "message": "Missing id"}, status=400)
            return

        elif path == "/api/targets":
            name = data.get("name", "").strip()
            url = data.get("url", "").strip()
            if not name or not url:
                self._send_json({"status": "error", "message": "name and url are required"}, status=400)
                return
            min_p = data.get("min_price")
            max_p = data.get("max_price")
            min_profit = int(data.get("min_profit_tl", 3000))
            kws = data.get("negative_keywords", [])
            cities = data.get("target_cities", [])
            new_id = add_custom_target(
                self.controller.db_path,
                name=name,
                url=url,
                min_price=min_p,
                max_price=max_p,
                min_profit_tl=min_profit,
                negative_keywords=kws,
                target_cities=cities,
            )
            self._send_json({"status": "created", "id": new_id})
            return

        elif path == "/api/targets/toggle":
            target_id = data.get("id")
            if target_id:
                ok = toggle_custom_target(self.controller.db_path, int(target_id))
                self._send_json({"status": "toggled", "success": ok})
            else:
                self._send_json({"status": "error", "message": "Missing id"}, status=400)
            return

        elif path == "/api/targets/delete":
            target_id = data.get("id")
            if target_id:
                ok = delete_custom_target(self.controller.db_path, int(target_id))
                self._send_json({"status": "deleted", "success": ok})
            else:
                self._send_json({"status": "error", "message": "Missing id"}, status=400)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Endpoint not found")

    def log_message(self, format: str, *args: Any) -> None:
        # Clean, quiet console logging
        sys_time = time.strftime("%H:%M:%S")
        print(f"[{sys_time}] [HTTP] {args[0]} - {args[1]}")


def find_available_port(preferred: int = 8888) -> int:
    """Finds an open port starting from preferred."""
    import socket
    for p in [preferred, 8888, 5000, 9090, 8085]:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("0.0.0.0", p))
                return p
        except OSError:
            continue
    return preferred


def run_server(
    port: int = 8888,
    db_path: Optional[Path] = None,
    config_path: Optional[Path] = None,
    presets_path: Optional[Path] = None,
) -> None:
    """Launches the Radar Web Dashboard HTTP server."""
    base_dir = Path(__file__).parent
    db_path = db_path or base_dir / "radar.db"
    config_path = config_path or base_dir / "config.json"
    presets_path = presets_path or base_dir / "presets.json"
    web_dir = base_dir / "web"

    init_db(db_path)
    controller = RadarController(db_path, config_path, presets_path)

    # Attach shared dependencies to handler class
    RadarAPIHandler.controller = controller
    RadarAPIHandler.web_dir = web_dir

    ThreadingHTTPServer.allow_reuse_address = True
    actual_port = find_available_port(port)
    server_address = ("0.0.0.0", actual_port)
    httpd = ThreadingHTTPServer(server_address, RadarAPIHandler)

    print("\n" + "=" * 60)
    print("  🌐 RADAR WEB DASHBOARD ACTIVE")
    print(f"  Local URL : http://localhost:{actual_port}")
    print(f"  Database  : {db_path.name}")
    print("  Zero-dependency Python HTTP engine running.")
    print("=" * 60 + "\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[Web Server Stopped] Shutting down...")
        controller.stop_daemon()
        httpd.server_close()


if __name__ == "__main__":
    run_server(port=8888)
