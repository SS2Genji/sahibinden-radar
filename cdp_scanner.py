"""
cdp_scanner.py: Python wrapper invoking the zero-dependency Node.js CDP worker.
Extracts live category listings via the running Chromium session.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List


class CDPScanner:
    """Invokes the local Node.js CDP worker to harvest live listings."""

    def __init__(self, cdp_port: int = 9222) -> None:
        self.cdp_port = cdp_port
        self.worker_path = Path(__file__).parent / "scanner.js"
        # Resolve node binary dynamically (supports PATH, nvm, mise, volta)
        self.node_bin = shutil.which("node") or "node"

    def scan_url(self, target_url: str) -> List[Dict[str, Any]]:
        """
        Runs the CDP worker on the specified URL and returns parsed listing objects.
        """
        try:
            env = os.environ.copy()
            env["CDP_PORT"] = str(self.cdp_port)
            res = subprocess.run(
                [self.node_bin, str(self.worker_path), target_url],
                capture_output=True,
                text=True,
                timeout=45,
                env=env
            )

            if res.returncode != 0:
                print(f"[CDPScanner Warning] Worker exited with code {res.returncode}: {res.stderr.strip()}")
                return []

            output = res.stdout.strip()
            if not output:
                return []

            return json.loads(output)
        except subprocess.TimeoutExpired:
            print("[CDPScanner Warning] Scan timed out after 45s")
            return []
        except Exception as e:
            print(f"[CDPScanner Error] Failed to scan {target_url}: {e}")
            return []
