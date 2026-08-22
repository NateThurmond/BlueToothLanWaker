"""
SQLite database layer for WoL Waker.
Uses WAL mode for safe concurrent reads from background threads.
"""

import sqlite3
import os
from contextlib import contextmanager
from typing import Optional

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "wol_waker.db"))

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS pcs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    ip_address  TEXT,
    mac_address TEXT    NOT NULL,
    created_at  TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS bt_devices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mac_address     TEXT UNIQUE NOT NULL,
    discovered_name TEXT,
    custom_name     TEXT,
    device_type     TEXT,
    first_seen      TEXT DEFAULT (datetime('now')),
    last_seen       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS mappings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pc_id       INTEGER NOT NULL,
    bt_device_id INTEGER NOT NULL,
    created_at  TEXT    DEFAULT (datetime('now')),
    FOREIGN KEY (pc_id)        REFERENCES pcs(id)        ON DELETE CASCADE,
    FOREIGN KEY (bt_device_id) REFERENCES bt_devices(id) ON DELETE CASCADE,
    UNIQUE (pc_id, bt_device_id)
);
"""


@contextmanager
def _conn():
    con = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    # Wait up to 5 s for a held write lock instead of erroring with
    # "database is locked" (SQLITE_BUSY).
    con.execute("PRAGMA busy_timeout=5000")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db() -> None:
    with _conn() as con:
        con.executescript(_SCHEMA)
        # Migration: add device_type column to existing databases
        try:
            con.execute("ALTER TABLE bt_devices ADD COLUMN device_type TEXT")
        except Exception:
            pass  # Column already exists


# ---------------------------------------------------------------------------
# PCs
# ---------------------------------------------------------------------------

def get_all_pcs() -> list[dict]:
    with _conn() as con:
        rows = con.execute("SELECT * FROM pcs ORDER BY name COLLATE NOCASE").fetchall()
    return [dict(r) for r in rows]


def add_pc(name: str, mac_address: str, ip_address: Optional[str] = None) -> int:
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO pcs (name, mac_address, ip_address) VALUES (?, ?, ?)",
            (name, mac_address, ip_address),
        )
        return cur.lastrowid


def update_pc(pc_id: int, name: str, mac_address: str, ip_address: Optional[str] = None) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE pcs SET name=?, mac_address=?, ip_address=? WHERE id=?",
            (name, mac_address, ip_address, pc_id),
        )


def delete_pc(pc_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM pcs WHERE id=?", (pc_id,))


# ---------------------------------------------------------------------------
# BT Devices
# ---------------------------------------------------------------------------

def get_all_bt_devices() -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM bt_devices ORDER BY last_seen DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_bt_device(
    mac_address: str,
    discovered_name: Optional[str] = None,
    device_type: Optional[str] = None,
) -> int:
    with _conn() as con:
        existing = con.execute(
            "SELECT id FROM bt_devices WHERE mac_address=?", (mac_address,)
        ).fetchone()
        if existing:
            # Update name and type if we have new info; always bump last_seen
            con.execute(
                """
                UPDATE bt_devices
                SET last_seen    = datetime('now'),
                    discovered_name = COALESCE(?, discovered_name),
                    device_type     = COALESCE(?, device_type)
                WHERE mac_address = ?
                """,
                (discovered_name or None, device_type or None, mac_address),
            )
            return existing["id"]
        cur = con.execute(
            "INSERT INTO bt_devices (mac_address, discovered_name, device_type) VALUES (?, ?, ?)",
            (mac_address, discovered_name, device_type),
        )
        return cur.lastrowid


def update_bt_device_custom_name(device_id: int, custom_name: Optional[str]) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE bt_devices SET custom_name=? WHERE id=?",
            (custom_name, device_id),
        )


def delete_bt_device(device_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM bt_devices WHERE id=?", (device_id,))


# ---------------------------------------------------------------------------
# Mappings
# ---------------------------------------------------------------------------

def get_mappings_for_pc(pc_id: int) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            """
            SELECT m.id as mapping_id,
                   b.id as bt_id,
                   b.mac_address,
                   b.discovered_name,
                   b.custom_name
            FROM   mappings m
            JOIN   bt_devices b ON b.id = m.bt_device_id
            WHERE  m.pc_id = ?
            ORDER  BY b.custom_name, b.discovered_name
            """,
            (pc_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_mapping(pc_id: int, bt_device_id: int) -> None:
    with _conn() as con:
        con.execute(
            "INSERT OR IGNORE INTO mappings (pc_id, bt_device_id) VALUES (?, ?)",
            (pc_id, bt_device_id),
        )


def remove_mapping(pc_id: int, bt_device_id: int) -> None:
    with _conn() as con:
        con.execute(
            "DELETE FROM mappings WHERE pc_id=? AND bt_device_id=?",
            (pc_id, bt_device_id),
        )


def get_pcs_for_bt_device(bt_mac: str) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            """
            SELECT p.*
            FROM   pcs p
            JOIN   mappings m   ON m.pc_id = p.id
            JOIN   bt_devices b ON b.id = m.bt_device_id
            WHERE  b.mac_address = ?
            """,
            (bt_mac,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_mapped_bt_macs() -> list[str]:
    """Every BT MAC that currently has at least one PC mapping."""
    with _conn() as con:
        rows = con.execute(
            """
            SELECT DISTINCT b.mac_address
            FROM   bt_devices b
            JOIN   mappings   m ON m.bt_device_id = b.id
            """
        ).fetchall()
    return [r["mac_address"] for r in rows]
