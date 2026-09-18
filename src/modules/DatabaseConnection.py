import configparser
import os
import sqlite3
import time
import logging
from sqlalchemy_utils import database_exists, create_database
from sqlalchemy import event
from sqlalchemy.engine import Engine

import threading
import shutil

logger = logging.getLogger("WGDashboard")

_heal_lock = threading.Lock()
_last_check_times = {}

@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    # Set connection-level pragmas for concurrency, performance, and cache
    if isinstance(dbapi_connection, sqlite3.Connection) or "sqlite" in getattr(dbapi_connection.__class__, "__module__", ""):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=60000")
        cursor.execute("PRAGMA cache_size=-64000")
        cursor.close()

def _self_heal_sqlite_if_corrupted(db_path: str, database_name: str, force_check: bool = False) -> bool:
    """
    Verifies SQLite database integrity on boot and runtime.
    If any database is malformed (due to abnormal shutdown, torn write, or crash):
    1. For secondary/log databases, archives corrupt file so clean instance is generated.
    2. For critical databases (e.g. wgdashboard.db, wgdashboard_job.db), automatically attempts lossless rebuild:
       - Preserves full timestamped safety backup.
       - Rebuilds all tables, rows, and schema objects.
       - Rebuilds all secondary indexes (ix_...) on clean data.
       - Resolves duplicate rowids caused by B-tree index corruptions using INSERT OR REPLACE.
       - Verifies PRAGMA integrity_check == 'ok' before atomic file swap.
    """
    if not os.path.exists(db_path):
        return True

    now = time.time()
    with _heal_lock:
        # Debounce: avoid running quick_check multiple times within 30 seconds for the same db file
        if not force_check and (now - _last_check_times.get(db_path, 0) < 30):
            return True

        is_corrupted = False
        try:
            conn = sqlite3.connect(db_path, timeout=10)
            cur = conn.cursor()
            cur.execute("PRAGMA quick_check(1)")
            res = cur.fetchone()
            conn.close()
            if not res or res[0] != "ok":
                is_corrupted = True
                logger.warning(f"[WGDashboard] PRAGMA quick_check returned non-ok for '{database_name}': {res}")
        except Exception as e:
            is_corrupted = True
            logger.warning(f"[WGDashboard] Exception checking integrity for '{database_name}': {e}")

        if not is_corrupted:
            _last_check_times[db_path] = now
            return True

        # Database is corrupted - begin self-healing procedure
        ts = int(time.time())
        backup_corrupt = f"{db_path}.corrupt_{ts}"

        # If it's a log database, archive it so a fresh one is generated
        if "log" in database_name.lower():
            try:
                shutil.copy2(db_path, backup_corrupt)
                for ext in ["-wal", "-shm"]:
                    if os.path.exists(db_path + ext):
                        shutil.move(db_path + ext, f"{db_path}{ext}.corrupt_{ts}")
                os.remove(db_path)
                logger.info(f"[WGDashboard] Successfully archived corrupted log database to {backup_corrupt}")
            except Exception as backup_err:
                logger.error(f"[WGDashboard] Failed to auto-archive corrupted log database: {backup_err}")
            _last_check_times[db_path] = time.time()
            return True

        # For critical databases (wgdashboard.db, wgdashboard_job.db): automated lossless data recovery
        repaired_tmp = f"{db_path}.repaired_{ts}"
        try:
            shutil.copy2(db_path, backup_corrupt)
            logger.info(f"[WGDashboard] Preserved safety copy of corrupted '{database_name}' to {backup_corrupt}")

            src = sqlite3.connect(db_path, timeout=30)
            dst = sqlite3.connect(repaired_tmp)

            cur_src = src.cursor()
            cur_dst = dst.cursor()

            # Disable foreign keys during reconstruction so insertion order does not fail
            cur_dst.execute("PRAGMA foreign_keys = OFF")

            # 1. Fetch all tables and their CREATE TABLE statements
            cur_src.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL"
            )
            tables = cur_src.fetchall()

            # 2. Fetch all secondary indices
            cur_src.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
            )
            indices = cur_src.fetchall()

            # 3. Fetch any views or triggers
            cur_src.execute(
                "SELECT name, sql FROM sqlite_master WHERE type IN ('view', 'trigger') AND sql IS NOT NULL"
            )
            other_objects = cur_src.fetchall()

            # Create tables and copy rows
            for table_name, create_sql in tables:
                try:
                    cur_dst.execute(create_sql)
                except Exception as tbl_err:
                    logger.warning(f"[WGDashboard] Error creating table '{table_name}': {tbl_err}")
                    continue

                # Recover rows: try bulk fetch first; if a corrupted page is hit, fallback to row-by-row
                rows = []
                try:
                    rows = cur_src.execute(f'SELECT * FROM "{table_name}"').fetchall()
                except Exception as bulk_err:
                    logger.warning(f"[WGDashboard] Bulk fetch failed for '{table_name}' ({bulk_err}), falling back to row-by-row recovery")
                    try:
                        row_cur = src.cursor()
                        row_cur.execute(f'SELECT * FROM "{table_name}"')
                        while True:
                            try:
                                r = row_cur.fetchone()
                                if r is None:
                                    break
                                rows.append(r)
                            except Exception:
                                break
                    except Exception as scan_err:
                        logger.warning(f"[WGDashboard] Row scan failed for '{table_name}': {scan_err}")

                if rows:
                    try:
                        placeholders = ','.join(['?'] * len(rows[0]))
                        cur_dst.executemany(f'INSERT OR REPLACE INTO "{table_name}" VALUES ({placeholders})', rows)
                    except Exception as insert_err:
                        logger.warning(f"[WGDashboard] Error inserting rows into '{table_name}': {insert_err}")

            dst.commit()

            # 4. Re-create all secondary indices (rebuilt on clean data, free from index B-tree corruption)
            for idx_name, idx_sql in indices:
                try:
                    cur_dst.execute(idx_sql)
                except Exception as idx_err:
                    logger.warning(f"[WGDashboard] Error recreating index '{idx_name}': {idx_err}")

            # 5. Re-create views and triggers
            for obj_name, obj_sql in other_objects:
                try:
                    cur_dst.execute(obj_sql)
                except Exception as obj_err:
                    logger.warning(f"[WGDashboard] Error recreating schema object '{obj_name}': {obj_err}")

            dst.commit()
            cur_dst.execute("PRAGMA journal_mode=WAL")
            cur_dst.execute("PRAGMA synchronous=NORMAL")
            dst.commit()

            check_res = cur_dst.execute("PRAGMA integrity_check").fetchall()
            src.close()
            dst.close()

            if check_res == [('ok',)]:
                for ext in ["-wal", "-shm"]:
                    if os.path.exists(db_path + ext):
                        try:
                            shutil.move(db_path + ext, f"{db_path}{ext}.corrupt_{ts}")
                        except Exception:
                            pass
                shutil.move(repaired_tmp, db_path)
                try:
                    os.chmod(db_path, 0o644)
                except Exception:
                    pass
                logger.info(f"[WGDashboard] Successfully self-healed corrupted database '{database_name}'. All data and indexes recovered!")
                _last_check_times[db_path] = time.time()
                return True
            else:
                logger.error(f"[WGDashboard] Automated repair of '{database_name}' could not verify integrity: {check_res}")
                if os.path.exists(repaired_tmp):
                    os.remove(repaired_tmp)
                return False
        except Exception as repair_err:
            logger.error(f"[WGDashboard] Self-healing exception for '{database_name}': {repair_err}")
            if os.path.exists(repaired_tmp):
                try:
                    os.remove(repaired_tmp)
                except Exception:
                    pass
            return False

def heal_database(database_name: str) -> bool:
    """
    Public API to trigger an immediate, forced self-healing repair of a database
    if a caller encounters a DatabaseError during runtime.
    """
    config_path = os.getenv('CONFIGURATION_PATH', '.')
    db_file = os.path.join(config_path, "db", f"{database_name}.db")
    if not os.path.exists(db_file) and os.path.exists(os.path.join("db", f"{database_name}.db")):
        db_file = os.path.join("db", f"{database_name}.db")
    return _self_heal_sqlite_if_corrupted(db_file, database_name, force_check=True)

def ConnectionString(database) -> str:    
    parser = configparser.ConfigParser(strict=False)
    config_path = os.getenv('CONFIGURATION_PATH', '.')
    ini_path = os.path.join(config_path, 'wg-dashboard.ini')
    if not os.path.exists(ini_path) and os.path.exists('wg-dashboard.ini'):
        ini_path = 'wg-dashboard.ini'

    if os.path.exists(ini_path):
        try:
            with open(ini_path, "r", encoding="utf-8") as f:
                parser.read_file(f)
        except Exception as e:
            logger.warning(f"[WGDashboard] Could not parse config file '{ini_path}': {e}")

    sqlitePath = os.path.join(config_path, "db")
    if not os.path.isdir(sqlitePath):
        os.makedirs(sqlitePath, exist_ok=True)

    db_type = parser.get("Database", "type", fallback="sqlite") if parser.has_section("Database") else "sqlite"
    if db_type == "postgresql":
        cn = f'postgresql+psycopg://{parser.get("Database", "username")}:{parser.get("Database", "password")}@{parser.get("Database", "host")}/{database}'
    elif db_type == "mysql":
        cn = f'mysql+pymysql://{parser.get("Database", "username")}:{parser.get("Database", "password")}@{parser.get("Database", "host")}/{database}'
    else:
        db_file = os.path.join(sqlitePath, f"{database}.db")
        _self_heal_sqlite_if_corrupted(db_file, database)
        cn = f'sqlite:///{db_file}?timeout=60'
    try:
        if not database_exists(cn):
            create_database(cn)
    except Exception as e:
        logger.error(f"[WGDashboard] Database initialization error for '{database}': {e}")
        exit(1)

    return cn