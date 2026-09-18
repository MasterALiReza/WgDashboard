import configparser
import os
import sqlite3
import time
import logging
from sqlalchemy_utils import database_exists, create_database
from sqlalchemy import event
from sqlalchemy.engine import Engine

logger = logging.getLogger("WGDashboard")

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

def _self_heal_sqlite_if_corrupted(db_path: str, database_name: str):
    """
    Verifies SQLite database integrity on boot.
    If any database is malformed (due to abnormal shutdown, torn write, or crash):
    1. For secondary/log databases, archives corrupt file so clean instance is generated.
    2. For critical databases (e.g. wgdashboard.db), automatically attempts lossless rebuild:
       reads all existing tables/records, deduplicates B-tree rowids via INSERT OR REPLACE into a fresh database,
       verifies integrity, and atomically swaps it in with full safety backups.
    """
    if not os.path.exists(db_path):
        return

    try:
        conn = sqlite3.connect(db_path, timeout=10)
        cur = conn.cursor()
        cur.execute("PRAGMA quick_check(1)")
        res = cur.fetchone()
        conn.close()
        if not res or res[0] != "ok":
            raise sqlite3.DatabaseError(f"Integrity check failed: {res}")
    except Exception as e:
        logger.warning(f"[WGDashboard] Detected malformed database '{database_name}': {e}")
        ts = int(time.time())
        backup_corrupt = f"{db_path}.corrupt_{ts}"
        
        # If it's a log database, archive it so a fresh one is generated
        if "log" in database_name.lower():
            try:
                import shutil
                shutil.copy2(db_path, backup_corrupt)
                for ext in ["-wal", "-shm"]:
                    if os.path.exists(db_path + ext):
                        shutil.move(db_path + ext, f"{db_path}{ext}.corrupt_{ts}")
                os.remove(db_path)
                logger.info(f"[WGDashboard] Successfully archived corrupted log database to {backup_corrupt}")
            except Exception as backup_err:
                logger.error(f"[WGDashboard] Failed to auto-archive corrupted log database: {backup_err}")
            return

        # For main/job database: attempt automated lossless data recovery
        repaired_tmp = f"{db_path}.repaired_{ts}"
        try:
            import shutil
            shutil.copy2(db_path, backup_corrupt)
            logger.info(f"[WGDashboard] Preserved safety copy of corrupted '{database_name}' to {backup_corrupt}")
            
            src = sqlite3.connect(db_path, timeout=30)
            dst = sqlite3.connect(repaired_tmp)
            
            cur_src = src.cursor()
            tables = [row[0] for row in cur_src.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()]
            
            for table in tables:
                create_sql = cur_src.execute(
                    f"SELECT sql FROM sqlite_master WHERE name='{table}'"
                ).fetchone()[0]
                dst.execute(create_sql)
                try:
                    rows = cur_src.execute(f'SELECT * FROM "{table}"').fetchall()
                    if rows:
                        placeholders = ','.join(['?'] * len(rows[0]))
                        dst.executemany(f'INSERT OR REPLACE INTO "{table}" VALUES ({placeholders})', rows)
                except Exception as row_err:
                    logger.warning(f"[WGDashboard] Error recovering rows from {table}: {row_err}")
            
            dst.commit()
            dst.execute("PRAGMA journal_mode=WAL")
            dst.execute("PRAGMA synchronous=NORMAL")
            dst.commit()
            
            check_res = dst.execute("PRAGMA integrity_check").fetchall()
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
                os.chmod(db_path, 0o644)
                logger.info(f"[WGDashboard] Successfully self-healed corrupted database '{database_name}'. All data recovered!")
            else:
                logger.error(f"[WGDashboard] Automated repair of '{database_name}' could not verify integrity: {check_res}")
                if os.path.exists(repaired_tmp):
                    os.remove(repaired_tmp)
        except Exception as repair_err:
            logger.error(f"[WGDashboard] Self-healing exception for '{database_name}': {repair_err}")
            if os.path.exists(repaired_tmp):
                try:
                    os.remove(repaired_tmp)
                except Exception:
                    pass

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