"""Мигратор: применяет db/*.sql по порядку и ведёт журнал ops.schema_version.

Файлы 00–07 идемпотентны и применяются при каждом запуске (03_queries.sql — только
запросы, не применяется). Изменения существующих таблиц — файлы db/migrations/NNN_*.sql,
каждый применяется один раз. Учётные записи приложения создаются с паролями из .env.
"""

from __future__ import annotations

import hashlib
import logging
import os

import psycopg
from psycopg import sql

from evadvisor.config import ROOT
from evadvisor.db import connect

log = logging.getLogger(__name__)

DB_DIR = ROOT / "db"
BASE_FILES = [
    "00_schemas.sql",
    "01_create_tables.sql",
    "02_insert_data.sql",
    "04_functions.sql",
    "05_procedures.sql",
    "06_triggers.sql",
    "07_roles.sql",
]
LOGIN_USERS = {"engineer": "role_engineer", "analyst": "role_analyst", "driver": "role_driver"}


def _checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _record(conn: psycopg.Connection, name: str, checksum: str) -> bool:
    cur = conn.execute(
        "INSERT INTO ops.schema_version (file_name, checksum) VALUES (%s, %s) "
        "ON CONFLICT (file_name, checksum) DO NOTHING",
        (name, checksum),
    )
    return cur.rowcount == 1


def _ensure_login_users(conn: psycopg.Connection) -> None:
    db = os.getenv("PG_DB", "evadvisor")
    for key, group in LOGIN_USERS.items():
        user = os.environ[f"PG_{key.upper()}_USER"]
        password = os.environ[f"PG_{key.upper()}_PASSWORD"]
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (user,)).fetchone()
        verb = "ALTER" if exists else "CREATE"
        conn.execute(
            sql.SQL("{} ROLE {} LOGIN PASSWORD {}").format(
                sql.SQL(verb), sql.Identifier(user), sql.Literal(password)
            )
        )
        conn.execute(sql.SQL("GRANT {} TO {}").format(
            sql.Identifier(group), sql.Identifier(user)))
    for group in LOGIN_USERS.values():
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(db), sql.Identifier(group)))
    # временные таблицы нужны процедурам загрузки
    conn.execute(sql.SQL("GRANT TEMPORARY ON DATABASE {} TO role_engineer").format(
        sql.Identifier(db)))


def migrate() -> list[str]:
    """Применить схему. Возвращает список файлов, чья версия записана впервые."""
    changed: list[str] = []
    with connect("admin") as conn:
        for name in BASE_FILES:
            text = (DB_DIR / name).read_text(encoding="utf-8")
            log.info("apply %s", name)
            conn.execute(text)
        migrations = sorted((DB_DIR / "migrations").glob("*.sql")) if (DB_DIR / "migrations").exists() else []
        applied = {r["file_name"] for r in conn.execute("SELECT file_name FROM ops.schema_version").fetchall()}
        for path in migrations:
            if path.name in applied:
                continue
            log.info("apply migration %s", path.name)
            conn.execute(path.read_text(encoding="utf-8"))
        for name in BASE_FILES:
            if _record(conn, name, _checksum((DB_DIR / name).read_text(encoding="utf-8"))):
                changed.append(name)
        for path in migrations:
            if _record(conn, path.name, _checksum(path.read_text(encoding="utf-8"))):
                changed.append(path.name)
        _ensure_login_users(conn)
        # роли выданы по таблицам, созданным выше; повторяем 07 после миграций
        conn.execute((DB_DIR / "07_roles.sql").read_text(encoding="utf-8"))
        conn.commit()
    return changed


def reset() -> None:
    """Удалить все схемы проекта (для разработки). Данные в data/ не трогаются."""
    with connect("admin") as conn:
        conn.execute("DROP SCHEMA IF EXISTS mart, core, stg, ops CASCADE")
        conn.commit()
