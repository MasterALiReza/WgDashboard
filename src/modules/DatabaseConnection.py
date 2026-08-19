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
    if dbapi_connection.__class__.__module__ == "sqlite3":
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=60000")
        cursor.execute("PRAGMA cache_size=-64000")
        cursor.close()

def _self_heal_sqlite_if_corrupted(db_path: str, database_name: str):
    """
    Verifies SQLite database integrity on boot.
    If a secondary/log database is malformed (due to crash or power outage),
    safely archives it and allows SQLAlchemy to recreate a clean instance.
    """
    if not os.path.exists(db_path):
        return

    try:
        conn = sqlite3.connect(db_path, timeout=5)
        cur = conn.cursor()
        cur.execute("PRAGMA quick_check(1)")
        res = cur.fetchone()
        conn.close()
        if not res or res[0] != "ok":
            raise sqlite3.DatabaseError(f"Integrity check failed: {res}")
    except Exception as e:
        logger.warning(f"[WGDashboard] Detected malformed database '{database_name}': {e}")
        # For non-critical/log databases, auto-archive to avoid crashing boot process
        if "log" in database_name.lower():
            backup_corrupt = f"{db_path}.corrupt_{int(time.time())}"
            try:
                os.rename(db_path, backup_corrupt)
                for ext in ["-wal", "-shm"]:
                    if os.path.exists(db_path + ext):
                        os.remove(db_path + ext)
                logger.info(f"[WGDashboard] Successfully archived corrupted log database to {backup_corrupt}")
            except Exception as backup_err:
                logger.error(f"[WGDashboard] Failed to auto-archive corrupted database: {backup_err}")

def ConnectionString(database) -> str:    
    parser = configparser.ConfigParser(strict=False)
    parser.read_file(open('wg-dashboard.ini', "r+"))

    sqlitePath = os.path.join("db")
    if not os.path.isdir(sqlitePath):
        os.mkdir(sqlitePath)

    if parser.get("Database", "type") == "postgresql":
        cn = f'postgresql+psycopg://{parser.get("Database", "username")}:{parser.get("Database", "password")}@{parser.get("Database", "host")}/{database}'
    elif parser.get("Database", "type") == "mysql":
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