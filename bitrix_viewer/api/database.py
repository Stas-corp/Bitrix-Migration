from contextlib import contextmanager

import pymysql
from pymysql.cursors import DictCursor

from .config import settings


@contextmanager
def get_connection():
    conn = pymysql.connect(
        host=settings.bitrix_db_host,
        port=settings.bitrix_db_port,
        user=settings.bitrix_db_user,
        password=settings.bitrix_db_password,
        database=settings.bitrix_db_name,
        cursorclass=DictCursor,
        charset="utf8mb4",
        connect_timeout=10,
        read_timeout=60,
    )
    try:
        yield conn
    finally:
        conn.close()


def query(sql: str, params=None) -> list[dict]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()


def query_one(sql: str, params=None) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None
