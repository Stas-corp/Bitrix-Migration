from contextlib import contextmanager

import pymysql
from fastapi import HTTPException
from pymysql.cursors import DictCursor
from pymysql.err import OperationalError

from .config import settings


@contextmanager
def get_connection():
    try:
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
    except OperationalError as e:
        errno, msg = e.args
        if errno == 1045:
            detail = (
                f"Access denied for user '{settings.bitrix_db_user}' "
                f"— проверь BITRIX_DB_USER / BITRIX_DB_PASSWORD в .env"
            )
        elif errno == 2003:
            detail = (
                f"Cannot connect to MySQL at {settings.bitrix_db_host}:{settings.bitrix_db_port} "
                f"— проверь BITRIX_DB_HOST / BITRIX_DB_PORT в .env"
            )
        elif errno == 1049:
            detail = (
                f"Unknown database '{settings.bitrix_db_name}' "
                f"— проверь BITRIX_DB_NAME в .env"
            )
        else:
            detail = f"MySQL error {errno}: {msg}"
        raise HTTPException(status_code=503, detail=detail) from e
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


def check_connection() -> dict:
    """Проверяет коннект к БД. Возвращает dict со статусом."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
        return {
            "status": "ok",
            "host": settings.bitrix_db_host,
            "port": settings.bitrix_db_port,
            "database": settings.bitrix_db_name,
            "user": settings.bitrix_db_user,
        }
    except HTTPException as e:
        return {"status": "error", "detail": e.detail}
