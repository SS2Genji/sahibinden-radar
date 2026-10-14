"""
main.py: Main Orchestrator for the Local Arbitrage & Price Drop Radar.
Executes multi-target category scanning with preset packs, randomized human jitter,
and coordinates the 3-tier noise filter with Telegram notifications.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from cdp_scanner import CDPScanner
from filter_engine import FilterEngine
from notifier import Notifier
from radar_db import init_db, mark_as_notified, record_opportunity


def load_json(file_path: Path) -> dict:
    if not file_path.exists():
        return {}
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(file_path: Path, data: dict) -> None:
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def resolve_active_targets(config: dict, presets: dict) -> Tuple[List[Dict[str, Any]], Dict[str, List[str]]]:
    """Combines targets from active preset packs and custom config targets."""
    active_presets = config.get("active_presets", [])
    all_targets: List[Dict[str, Any]] = []
    preset_keywords: Dict[str, List[str]] = {}

    for preset_key in active_presets:
        if preset_key in presets:
            p_data = presets[preset_key]
            p_targets = p_data.get("targets", [])
            p_kws = p_data.get("negative_keywords", [])
            for t in p_targets:
                all_targets.append(t)
                preset_keywords[t.get("name", "")] = p_kws

    # Append any custom standalone targets
    for t in config.get("custom_targets", []):
        all_targets.append(t)

    return all_targets, preset_keywords


def interactive_setup(config_path: Path, presets_path: Path) -> None:
    """Zero-friction interactive terminal setup wizard for end users."""
    config = load_json(config_path)
    presets = load_json(presets_path)

    print("\n" + "=" * 60)
    print("  ⚙️  RADAR KURULUM SİHİRBAZI (ZERO-FRICTION SETUP)")
    print("=" * 60)
    print("İzlemek istediğin hazır paketleri seçebilirsin:\n")

    preset_keys = list(presets.keys())
    active_now = set(config.get("active_presets", ["smartphones"]))

    for i, key in enumerate(preset_keys, 1):
        status = "[✓] AKTİF" if key in active_now else "[ ] PASİF"
        p = presets[key]
        print(f"  [{i}] {status:10} {p.get('name')} ({p.get('description')})")

    def safe_input(prompt: str, default: str = "") -> str:
        try:
            val = input(prompt).strip()
            return val if val else default
        except (EOFError, KeyboardInterrupt):
            return default

    print("\nPaket seçimini değiştirmek için numaraları virgülle gir (örn: 1,2)")
    print("Mevcut seçimi korumak için ENTER'a bas.")
    selection = safe_input("Seçiminiz: ")

    if selection:
        new_active = []
        for part in selection.split(","):
            part = part.strip()
            if part.isdigit():
                idx = int(part) - 1
                if 0 <= idx < len(preset_keys):
                    new_active.append(preset_keys[idx])
        if new_active:
            config["active_presets"] = new_active

    # Minimum Profit in TL
    curr_profit = config.get("min_profit_tl", 3000)
    profit_in = safe_input(f"\nMinimum hedeflenen kâr tutarı (TL) [Varsayılan: {curr_profit:,} TL]: ")
    if profit_in.isdigit():
        config["min_profit_tl"] = int(profit_in)

    # City filter
    curr_cities = ", ".join(config.get("target_cities", [])) or "Tüm Türkiye"
    cities_in = safe_input(f"Hedef Şehirler (Virgülle ayırın veya boş bırakın) [Mevcut: {curr_cities}]: ")
    if cities_in:
        config["target_cities"] = [c.strip() for c in cities_in.split(",") if c.strip()]

    save_json(config_path, config)
    print("\n✅ Ayarlar kaydedildi! Radarı başlatmak için: python3 main.py\n")


def run_cycle(
    scanner: CDPScanner,
    filter_engine: FilterEngine,
    notifier: Notifier,
    targets: List[Dict[str, Any]],
    preset_keywords: Dict[str, List[str]],
    db_path: Path
) -> int:
    """Executes a single scanning pass over all active targets."""
    alerts_fired = 0

    for target in targets:
        name = target.get("name", "Unknown Target")
        url = target.get("url", "")
        if not url:
            continue

        category = target.get("category", name)
        extra_kws = preset_keywords.get(name, [])

        print(f"[{datetime.now().strftime('%H:%M:%S')}] Scanning target: '{name}'...")
        listings = scanner.scan_url(url)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Harvested {len(listings)} listings from page.")

        for item in listings:
            item["category"] = category
            decision = filter_engine.evaluate_listing(item, extra_negative_keywords=extra_kws)

            if decision["should_notify"]:
                record_opportunity(db_path, item, decision)
                print(f"  -> MATCH: [{decision['alert_type']}] {item['title']} - {item['price']:,} TL ({decision['reason']})")
                sent = notifier.send_alert(item, decision)
                if sent:
                    mark_as_notified(db_path, str(item["id"]))
                    alerts_fired += 1

    return alerts_fired


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Arbitrage & Price Drop Radar")
    parser.add_argument("--config", default="config.json", help="Path to config.json")
    parser.add_argument("--presets", default="presets.json", help="Path to presets.json")
    parser.add_argument("--once", action="store_true", help="Run a single scan pass and exit")
    parser.add_argument("--setup", action="store_true", help="Launch interactive setup wizard")
    parser.add_argument("--web", nargs="?", const=8888, type=int, help="Launch modern Web Dashboard (default port 8888)")
    parser.add_argument("--db", default="radar.db", help="Path to SQLite database")
    args = parser.parse_args()

    project_dir = Path(__file__).parent
    config_path = project_dir / args.config
    presets_path = project_dir / args.presets
    db_path = project_dir / args.db

    if args.web:
        from web_server import run_server
        run_server(port=args.web, db_path=db_path, config_path=config_path, presets_path=presets_path)
        return

    if args.setup:
        interactive_setup(config_path, presets_path)
        return

    config = load_json(config_path)
    presets = load_json(presets_path)
    init_db(db_path)

    targets, preset_keywords = resolve_active_targets(config, presets)

    telegram_cfg = config.get("telegram", {})
    notifier = Notifier(
        bot_token=telegram_cfg.get("bot_token"),
        chat_id=telegram_cfg.get("chat_id"),
        enabled=telegram_cfg.get("enabled", True)
    )

    filter_engine = FilterEngine(
        db_path=db_path,
        negative_keywords=config.get("negative_keywords", []),
        discount_threshold_pct=config.get("min_discount_pct", 12.0),
        min_profit_tl=config.get("min_profit_tl", 0),
        target_cities=config.get("target_cities", []),
    )

    scanner = CDPScanner(cdp_port=config.get("cdp_port", 9222))

    active_pack_names = [presets[k]["name"] for k in config.get("active_presets", []) if k in presets]

    print("\n" + "=" * 60)
    print("  🚀 LOCAL ARBITRAGE & PRICE DROP RADAR ACTIVE")
    print(f"  Database       : {db_path.name}")
    print(f"  Active Packs   : {', '.join(active_pack_names) or 'None'}")
    print(f"  Target Feeds   : {len(targets)} live category streams")
    print(f"  Profit Filter  : Min {config.get('min_profit_tl', 0):,} TL profit & %{config.get('min_discount_pct', 12.0)} discount")
    cities_str = ', '.join(config.get('target_cities', [])) or 'Tüm Türkiye'
    print(f"  Target Cities  : {cities_str}")
    print(f"  Telegram Alert : {'ENABLED' if notifier.enabled else 'CONSOLE SIMULATION'}")
    print("=" * 60 + "\n")

    if not targets:
        print("⚠️ No active targets found. Run 'python3 main.py --setup' to enable preset packs.")
        return

    if args.once:
        run_cycle(scanner, filter_engine, notifier, targets, preset_keywords, db_path)
        print("\nSingle pass completed. Exiting.")
        return

    min_interval = config.get("scan_interval_min_sec", 45)
    max_interval = config.get("scan_interval_max_sec", 75)

    try:
        while True:
            run_cycle(scanner, filter_engine, notifier, targets, preset_keywords, db_path)
            sleep_duration = random.uniform(min_interval, max_interval)
            print(f"\nSleeping for {sleep_duration:.1f}s (human jitter)...")
            time.sleep(sleep_duration)
    except KeyboardInterrupt:
        print("\n[Radar Stopped] Graceful shutdown.")
        sys.exit(0)


if __name__ == "__main__":
    main()
