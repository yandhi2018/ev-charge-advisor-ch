"""Подключения к PostgreSQL под ролями проекта.

Каждый компонент работает под своей учётной записью (db/07_roles.sql):
admin — мигратор, engineer — загрузка и преобразования, analyst — предметный дашборд,
driver — приложение водителя.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

import psycopg
from psycopg.rows import dict_row

import evadvisor.config  # noqa: F401  — загружает .env

Role = Literal["admin", "engineer", "analyst", "driver"]


def conninfo(role: Role) -> str:
    key = role.upper()
    user = os.getenv(f"PG_{key}_USER")
    password = os.getenv(f"PG_{key}_PASSWORD")
    if not user or not password:
        raise RuntimeError(
            f"Нет PG_{key}_USER / PG_{key}_PASSWORD в .env. Запустите scripts\\init_db.ps1 и `evadvisor db migrate`."
        )
    return psycopg.conninfo.make_conninfo(
        host=os.getenv("PG_HOST", "127.0.0.1"),
        port=os.getenv("PG_PORT", "5432"),
        dbname=os.getenv("PG_DB", "evadvisor"),
        user=user,
        password=password,
        application_name=f"evadvisor-{role}",
    )


def connect(role: Role = "engineer", autocommit: bool = False) -> psycopg.Connection:
    return psycopg.connect(conninfo(role), autocommit=autocommit, row_factory=dict_row)


@contextmanager
def session(role: Role = "engineer") -> Iterator[psycopg.Connection]:
    """Соединение с транзакцией: commit при успехе, rollback при ошибке."""
    conn = connect(role)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def fetch_all(sql: str, params: dict | tuple | None = None, role: Role = "analyst") -> list[dict]:
    with connect(role) as conn:
        return conn.execute(sql, params).fetchall()


def fetch_one(sql: str, params: dict | tuple | None = None, role: Role = "analyst") -> dict | None:
    with connect(role) as conn:
        return conn.execute(sql, params).fetchone()
