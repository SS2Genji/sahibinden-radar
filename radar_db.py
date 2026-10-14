"""
radar_db.py: Lightweight SQLite state storage for listing deduplication,
price history tracking, and market median analysis.
Uses Python standard library sqlite3 only.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


from contextlib import contextmanager

@contextmanager
def db_session(db_path: str | Path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str | Path = "radar.db") -> None:
    """Initializes tables and indexes for efficient delta queries."""
    with db_session(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS listings (
                id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                price INTEGER NOT NULL,
                url TEXT NOT NULL,
                location TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                notified INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                listing_id TEXT NOT NULL,
                price INTEGER NOT NULL,
                recorded_at TEXT NOT NULL,
                FOREIGN KEY (listing_id) REFERENCES listings(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS opportunities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                listing_id TEXT NOT NULL,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                price INTEGER NOT NULL,
                fair_price INTEGER NOT NULL,
                profit_margin INTEGER NOT NULL,
                discount_pct REAL NOT NULL,
                variant TEXT,
                condition_score INTEGER,
                cosmetic_score INTEGER,
                detected_flaws TEXT,
                positive_traits TEXT,
                expert_verdict TEXT,
                liquidity_rating TEXT,
                turnover_days TEXT,
                quick_flip_price INTEGER,
                location TEXT,
                url TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                dismissed INTEGER DEFAULT 0,
                FOREIGN KEY (listing_id) REFERENCES listings(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS custom_targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                min_price INTEGER,
                max_price INTEGER,
                min_profit_tl INTEGER DEFAULT 3000,
                negative_keywords TEXT DEFAULT '[]',
                target_cities TEXT DEFAULT '[]',
                enabled INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_category_price ON listings(category, price)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_last_seen ON listings(last_seen)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_opp_detected_at ON opportunities(detected_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_opp_profit ON opportunities(profit_margin DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_targets_enabled ON custom_targets(enabled)")


def process_listing_delta(
    db_path: str | Path,
    item: Dict[str, Any]
) -> Tuple[bool, bool, Optional[int]]:
    """
    Evaluates a listing against existing records.
    Returns: (is_new, is_price_drop, previous_price)
    """
    now = datetime.now(timezone.utc).isoformat()
    listing_id = str(item["id"])
    new_price = int(item["price"])
    category = str(item.get("category", "default"))
    title = str(item.get("title", ""))
    url = str(item.get("url", ""))
    location = str(item.get("location", ""))

    with db_session(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT price, notified FROM listings WHERE id = ?", (listing_id,))
        row = cursor.fetchone()

        if row is None:
            # Brand new listing
            cursor.execute("""
                INSERT INTO listings (id, category, title, price, url, location, first_seen, last_seen, notified)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """, (listing_id, category, title, new_price, url, location, now, now))
            cursor.execute("""
                INSERT INTO price_history (listing_id, price, recorded_at)
                VALUES (?, ?, ?)
            """, (listing_id, new_price, now))
            return True, False, None
        else:
            old_price = int(row["price"])
            is_price_drop = new_price < old_price

            cursor.execute("""
                UPDATE listings
                SET price = ?, last_seen = ?, title = ?, location = ?
                WHERE id = ?
            """, (new_price, now, title, location, listing_id))

            if is_price_drop:
                cursor.execute("""
                    INSERT INTO price_history (listing_id, price, recorded_at)
                    VALUES (?, ?, ?)
                """, (listing_id, new_price, now))

            return False, is_price_drop, old_price


def mark_as_notified(db_path: str | Path, listing_id: str) -> None:
    """Marks a listing as already alerted to prevent repeated notifications."""
    with db_session(db_path) as conn:
        conn.execute("UPDATE listings SET notified = 1 WHERE id = ?", (str(listing_id),))


def is_notified(db_path: str | Path, listing_id: str) -> bool:
    """Checks if a listing was already alerted."""
    with db_session(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT notified FROM listings WHERE id = ?", (str(listing_id),))
        row = cursor.fetchone()
        return bool(row["notified"]) if row else False


def get_category_prices(
    db_path: str | Path,
    category: str,
    limit: int = 100
) -> List[int]:
    """Retrieves recent prices in a category to feed the anomaly detector."""
    with db_session(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT price FROM listings
            WHERE category = ?
            ORDER BY last_seen DESC
            LIMIT ?
        """, (str(category), limit))
        return [int(row["price"]) for row in cursor.fetchall()]


def get_category_historical_items(
    db_path: str | Path,
    category: str,
    limit: int = 150
) -> List[Dict[str, Any]]:
    """Retrieves recent items in a category with title and price."""
    with db_session(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, title, price FROM listings
            WHERE category = ?
            ORDER BY last_seen DESC
            LIMIT ?
        """, (str(category), limit))
        return [
            {"id": row["id"], "title": row["title"], "price": int(row["price"])}
            for row in cursor.fetchall()
        ]


def record_opportunity(
    db_path: str | Path,
    item: Dict[str, Any],
    decision: Dict[str, Any]
) -> int:
    """Records an evaluated opportunity to the SQLite database."""
    now = datetime.now(timezone.utc).isoformat()
    listing_id = str(item["id"])
    category = str(item.get("category", "default"))
    title = str(item.get("title", ""))
    price = int(item.get("price", 0))
    url = str(item.get("url", ""))
    location = str(item.get("location", ""))

    fair_price = int(decision.get("clean_median", price))
    profit_margin = int(decision.get("price_advantage", max(0, fair_price - price)))
    discount_pct = float(decision.get("discount_pct", decision.get("drop_pct", 0.0)))
    variant = str(decision.get("detected_variant", "Standard"))
    cond_score = decision.get("hardware_score")
    cosm_score = decision.get("cosmetic_score")
    flaws = json.dumps(decision.get("detected_flaws", []), ensure_ascii=False)
    traits = json.dumps(decision.get("positive_traits", []), ensure_ascii=False)
    verdict = str(decision.get("expert_verdict", "UNKNOWN"))
    liquidity = str(decision.get("liquidity_rating", "MODERATE"))
    turnover = str(decision.get("turnover_days", "1_to_3_days"))
    quick_flip = int(decision.get("quick_flip_price", price))
    alert_type = str(decision.get("alert_type", "NEW_OPPORTUNITY"))

    with db_session(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO opportunities (
                listing_id, category, title, price, fair_price, profit_margin, discount_pct,
                variant, condition_score, cosmetic_score, detected_flaws, positive_traits,
                expert_verdict, liquidity_rating, turnover_days, quick_flip_price,
                location, url, alert_type, detected_at, dismissed
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """, (
            listing_id, category, title, price, fair_price, profit_margin, discount_pct,
            variant, cond_score, cosm_score, flaws, traits,
            verdict, liquidity, turnover, quick_flip,
            location, url, alert_type, now
        ))
        return cursor.lastrowid or 0


def get_opportunities(
    db_path: str | Path,
    limit: int = 50,
    category: Optional[str] = None,
    min_profit: int = 0
) -> List[Dict[str, Any]]:
    """Fetches opportunities ordered by detection date, with parsed metadata."""
    query = """
        SELECT * FROM opportunities
        WHERE profit_margin >= ? AND dismissed = 0
    """
    params: List[Any] = [min_profit]
    if category:
        query += " AND category = ?"
        params.append(category)

    query += " ORDER BY detected_at DESC LIMIT ?"
    params.append(limit)

    with db_session(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        results = []
        for r in rows:
            d = dict(r)
            try:
                d["detected_flaws"] = json.loads(d["detected_flaws"]) if d["detected_flaws"] else []
            except Exception:
                d["detected_flaws"] = []
            try:
                d["positive_traits"] = json.loads(d["positive_traits"]) if d["positive_traits"] else []
            except Exception:
                d["positive_traits"] = []
            results.append(d)
        return results


def get_dashboard_stats(db_path: str | Path) -> Dict[str, Any]:
    """Computes summary statistics for the dashboard cards."""
    with db_session(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*), MAX(last_seen) FROM listings")
        total_listings, last_scan_time = cursor.fetchone()

        cursor.execute("""
            SELECT 
                COUNT(*),
                COALESCE(SUM(CASE WHEN discount_pct >= 30.0 THEN 1 ELSE 0 END), 0),
                COALESCE(AVG(profit_margin), 0)
            FROM opportunities WHERE dismissed = 0
        """)
        opp_row = cursor.fetchone()
        total_opportunities = opp_row[0] or 0
        high_profit_count = opp_row[1] or 0
        avg_profit = int(opp_row[2]) if opp_row[2] else 0

        cursor.execute("SELECT COUNT(*) FROM custom_targets WHERE enabled = 1")
        active_targets_count = cursor.fetchone()[0]

        cursor.execute("SELECT DISTINCT category FROM listings")
        categories_tracked = [row[0] for row in cursor.fetchall() if row[0]]

        return {
            "total_listings": total_listings or 0,
            "total_opportunities": total_opportunities,
            "high_profit_count": high_profit_count,
            "active_targets_count": active_targets_count,
            "avg_profit": avg_profit,
            "last_scan_time": last_scan_time,
            "categories_tracked": categories_tracked,
        }


def get_recent_listings(
    db_path: str | Path,
    limit: int = 50,
    search: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Retrieves recent raw listings for database exploration."""
    query = "SELECT * FROM listings"
    params: List[Any] = []
    if search:
        query += " WHERE title LIKE ?"
        params.append(f"%{search}%")

    query += " ORDER BY last_seen DESC LIMIT ?"
    params.append(limit)

    with db_session(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]


def add_custom_target(
    db_path: str | Path,
    name: str,
    url: str,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    min_profit_tl: int = 3000,
    negative_keywords: Optional[List[str]] = None,
    target_cities: Optional[List[str]] = None,
) -> int:
    """Adds a new custom target feed to the database."""
    now = datetime.now(timezone.utc).isoformat()
    neg_json = json.dumps(negative_keywords or [], ensure_ascii=False)
    cities_json = json.dumps(target_cities or [], ensure_ascii=False)

    with db_session(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO custom_targets (
                name, url, min_price, max_price, min_profit_tl,
                negative_keywords, target_cities, enabled, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
        """, (name, url, min_price, max_price, min_profit_tl, neg_json, cities_json, now))
        return cursor.lastrowid or 0


def get_custom_targets(
    db_path: str | Path,
    enabled_only: bool = False
) -> List[Dict[str, Any]]:
    """Retrieves configured custom targets with parsed json fields."""
    query = "SELECT * FROM custom_targets"
    if enabled_only:
        query += " WHERE enabled = 1"
    query += " ORDER BY id DESC"

    with db_session(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(query)
        results = []
        for row in cursor.fetchall():
            d = dict(row)
            try:
                d["negative_keywords"] = json.loads(d["negative_keywords"]) if d["negative_keywords"] else []
            except Exception:
                d["negative_keywords"] = []
            try:
                d["target_cities"] = json.loads(d["target_cities"]) if d["target_cities"] else []
            except Exception:
                d["target_cities"] = []
            results.append(d)
        return results


def delete_custom_target(db_path: str | Path, target_id: int) -> bool:
    """Deletes a custom target by ID."""
    with db_session(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM custom_targets WHERE id = ?", (int(target_id),))
        return cursor.rowcount > 0


def toggle_custom_target(db_path: str | Path, target_id: int) -> bool:
    """Toggles enabled state (0 <-> 1) for a custom target."""
    with db_session(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE custom_targets
            SET enabled = CASE WHEN enabled = 1 THEN 0 ELSE 1 END
            WHERE id = ?
        """, (int(target_id),))
        return cursor.rowcount > 0



