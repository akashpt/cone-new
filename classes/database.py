

import sqlite3
import time
from typing import Optional, Dict, Any, List
from functools import wraps
from datetime import datetime
from paths import SETTINGS_JSON, DB_PATH
import json


# Allowed tables for table-name based helpers (prevents injection)
_ALLOWED_TABLES = {
    "cone_entry",
    "defect_table",
    "cone_details_table",
    "idle_table",
    "lot_table",
    #modified by Gokul
    "shift_report_log"
}


# -----------------------------
# Connection helper
# -----------------------------
# def get_conn(path: str = DB_PATH):
#     conn = sqlite3.connect(
#         path,
#         timeout=30,
#         detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
#     )
#     conn.row_factory = sqlite3.Row
#     conn.execute("PRAGMA foreign_keys = ON;")
#     return conn
def get_conn():
    return sqlite3.connect(DB_PATH)


def load_settings(settings_path=SETTINGS_JSON):
    # settings_path = settings_path

    if not settings_path.exists():
        dummy_settings = {
            "values": {
                "cone_color": "",
                "cone_count": "",
                "tip_confidence": "5",
                "top_confidence": "45",
                "bottom_confidence": "45",
                "tip_images_selected": []
            },
            "locked": {
                "cone_color": False,
                "cone_count": False,
                "tip_confidence": False,
                "top_confidence": False,
                "bottom_confidence": False,
                "tip_images_selected": False
            }
        }

        try:
            with open(settings_path, "w", encoding="utf-8") as f:
                json.dump(dummy_settings, f, indent=2)
            print(f"✅ Dummy settings.json created: {settings_path}")
        except Exception as e:
            raise RuntimeError(f"Failed to create settings.json: {e}")

    # ---------- Load settings ----------
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise RuntimeError(f"Failed to read settings.json: {e}")


# -----------------------------
# Retry decorator for transient locks
# -----------------------------
# def _retry_on_lock(max_wait: float = 6.0, initial_delay: float = 0.02, max_delay: float = 0.5):
#     def decorator(fn):
#         @wraps(fn)
#         def wrapper(*args, **kwargs):
#             deadline = time.monotonic() + max_wait
#             delay = initial_delay
#             while True:
#                 try:
#                     return fn(*args, **kwargs)
#                 except sqlite3.OperationalError as e:
#                     msg = str(e).lower()
#                     if ("locked" in msg or "busy" in msg) and time.monotonic() < deadline:
#                         time.sleep(delay)
#                         delay = min(max_delay, delay * 2)
#                         continue
#                     raise
#         return wrapper
#     return decorator


# -----------------------------
# Initialize / create schema (5 tables)
# -----------------------------
def init_db() -> None:
    try:
        conn = get_conn()
        cur = conn.cursor()

        cur.execute("PRAGMA journal_mode = WAL;")
        cur.execute("PRAGMA synchronous = NORMAL;")

        # 1) cone_details_table
        cur.execute("""
        CREATE TABLE IF NOT EXISTS shift_table (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shift_name TEXT,
            start_time TIMESTAMP DEFAULT (datetime('now','localtime')),
            end_time TIMESTAMP DEFAULT (datetime('now','localtime')),
            status INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT (datetime('now','localtime')),
            updated_at TIMESTAMP DEFAULT (datetime('now','localtime'))
        );
        """)

        # # 1) cone_details_table
        cur.execute("""
        CREATE TABLE IF NOT EXISTS cone_color (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cone_color TEXT UNIQUE DEFAULT NULL,
            status INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT (datetime('now','localtime')),
            updated_at TIMESTAMP DEFAULT (datetime('now','localtime'))
        );
        """)

        #  # 1) cone_details_table
        cur.execute("""
        CREATE TABLE IF NOT EXISTS cone_count (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cone_count TEXT UNIQUE DEFAULT NULL,
            status INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT (datetime('now','localtime')),
            updated_at TIMESTAMP DEFAULT (datetime('now','localtime'))
        );
        """)

        # 2) cone_entry
        cur.execute("""
        CREATE TABLE IF NOT EXISTS cone_entry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shift_id INTEGER DEFAULT 0,
            shift_count INTEGER DEFAULT 0,
            cone_color TEXT DEFAULT NULL,
            cone_count TEXT DEFAULT NULL,
            tip_result TEXT DEFAULT NULL,
            top_result TEXT DEFAULT NULL,
            top_uv_result TEXT DEFAULT NULL,
            bottom_result TEXT DEFAULT NULL,
            bottom_uv_result TEXT DEFAULT NULL,
            defect_img_path TEXT DEFAULT NULL,
            created_at TIMESTAMP DEFAULT (datetime('now','localtime')),
            updated_at TIMESTAMP DEFAULT (datetime('now','localtime'))
        );
        """)

        # 3) idle_table
        cur.execute("""
        CREATE TABLE IF NOT EXISTS idle_table (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time TIMESTAMP,
            end_time TIMESTAMP,
            cone_color TEXT DEFAULT NULL,
            cone_count TEXT DEFAULT NULL,
            created_at TIMESTAMP DEFAULT (datetime('now','localtime')),
            updated_at TIMESTAMP DEFAULT (datetime('now','localtime'))
        );
        """)
        #modified by Gokul
        cur.execute("""
                        CREATE TABLE IF NOT EXISTS shift_report_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        shift_id INTEGER,
                        shift_end TEXT,
                        sent_at TEXT
                    );
        """)


        conn.commit()
        conn.close()
        print("DB Create or Checking Done")

    except Exception as e:
        print("DB Error", e)



# ---------------------------
# INSERT / UPDATE / DELETE
# ---------------------------
def execute(query, values=None):
    try:
        conn = get_conn()
        cur = conn.cursor()

        if values is not None:
            cur.execute(query, values)
        else:
            cur.execute(query)

        conn.commit()

        q = query.strip().lower()

        if q.startswith("insert"):
            print(cur.lastrowid)
            return cur.lastrowid          # ✅ inserted row id

        if q.startswith("update") or q.startswith("delete"):
            return cur.rowcount           # ✅ affected rows count

        return True

    except Exception as e:
        print("DB execute error:", e)
        return False
    finally:
        if conn:
            conn.close()

# ---------------------------
# FETCH ONE
# ---------------------------
def fetch_one(query, values=None):
    try:
        """
        Returns a single row.
        """
        conn = get_conn()
        cur = conn.cursor()

        if values:
            cur.execute(query, values)
        else:
            cur.execute(query)

        row = cur.fetchone()
        conn.close()
        return row
    except Exception as e:
        print(e)
        return False

# ---------------------------
# FETCH ALL
# ---------------------------
def fetch_all(query, values=None):
    try:
        """
        Returns all rows.
        """
        conn = get_conn()
        cur = conn.cursor()

        if values:
            cur.execute(query, values)
        else:
            cur.execute(query)

        rows = cur.fetchall()
        conn.close()
        return rows
    except Exception as e:
        print(e)
        return False

# ---------------------------
# FETCH MANY
# ---------------------------
def fetch_many(query, size=10, values=None):
    try:
        """
        Returns N rows.
        """
        conn = get_conn()
        cur = conn.cursor()

        if values:
            cur.execute(query, values)
        else:
            cur.execute(query)

        rows = cur.fetchmany(size)
        conn.close()
        return rows
    except Exception as e:
        print(e)
        return False



def get_current_shift_id():
    """
    Returns active shift_id based on current time.
    Handles overnight shifts.
    """
    now = datetime.now().time()

    rows = fetch_all("""
        SELECT id, start_time, end_time
        FROM shift_table
        WHERE status = 1
    """) or []

    for sid, start_t, end_t in rows:
        # convert to time
        st = datetime.strptime(start_t, "%H:%M").time() if isinstance(start_t, str) else start_t
        et = datetime.strptime(end_t, "%H:%M").time() if isinstance(end_t, str) else end_t

        if st <= et:
            # normal shift (e.g. 06:00 - 14:00)
            if st <= now < et:
                return sid
        else:
            # overnight shift (e.g. 22:00 - 06:00)
            if now >= st or now < et:
                return sid

    return 0   # no shift matched


def get_current_shift_count(shift_id=None):
    """
    Returns shift_count for the current shift.
    """
    if shift_id is None:
        shift_id = get_current_shift_id()

    if not shift_id:
        return 0

    row = fetch_one(
        "SELECT COUNT(*) FROM cone_entry WHERE shift_id = ?",
        (shift_id,)
    )

    return int(row[0]) if row else 0
