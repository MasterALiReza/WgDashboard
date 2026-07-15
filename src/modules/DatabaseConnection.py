import configparser
import os
from sqlalchemy_utils import database_exists, create_database
from sqlalchemy import event
from sqlalchemy.engine import Engine

@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    # Enable WAL mode for SQLite to dramatically improve concurrency and prevent 'database is locked' errors.
    # Check if the connection is a sqlite connection by inspecting its class name or type
    if dbapi_connection.__class__.__module__ == "sqlite3":
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()
        dbapi_connection.isolation_level = None

@event.listens_for(Engine, "begin")
def do_begin(conn):
    opts = conn._execution_options if hasattr(conn, '_execution_options') else {}
    if conn.engine.dialect.name == "sqlite" and opts.get("isolation_level") != "AUTOCOMMIT":
        conn.exec_driver_sql("BEGIN IMMEDIATE")

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
        cn = f'sqlite:///{os.path.join(sqlitePath, f"{database}.db")}?timeout=60'
    try:
        if not database_exists(cn):
            create_database(cn)
    except Exception as e:
        exit(1)

    return cn