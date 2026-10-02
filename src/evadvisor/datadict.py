"""Словарь данных из каталога PostgreSQL: таблицы, колонки, типы, ключи, ограничения, комментарии.

Генерируется командой `evadvisor docs` в docs/data_dictionary.md — описание всегда соответствует
фактической схеме (метаданные берутся из БД, а не пишутся вручную).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from evadvisor.config import ROOT
from evadvisor.db import connect

SCHEMAS = ["core", "mart", "ops", "stg"]


def build() -> str:
    with connect("admin") as conn:
        tables = conn.execute(
            """SELECT n.nspname AS schema, c.relname AS name, obj_description(c.oid) AS comment,
                      c.reltuples::bigint AS approx_rows
                 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = ANY(%s) AND c.relkind IN ('r', 'p')
                ORDER BY array_position(%s, n.nspname::text), c.relname""", (SCHEMAS, SCHEMAS)).fetchall()
        for t in tables:
            t["rows"] = conn.execute(f'SELECT count(*) AS n FROM "{t["schema"]}"."{t["name"]}"').fetchone()["n"]
        cols = conn.execute(
            """SELECT table_schema AS schema, table_name AS name, column_name, data_type, is_nullable,
                      column_default, col_description((table_schema || '.' || table_name)::regclass, ordinal_position)
                      AS comment, ordinal_position
                 FROM information_schema.columns WHERE table_schema = ANY(%s)
                ORDER BY table_schema, table_name, ordinal_position""", (SCHEMAS,)).fetchall()
        cons = conn.execute(
            """SELECT n.nspname AS schema, t.relname AS name, c.contype, pg_get_constraintdef(c.oid) AS def
                 FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid
                 JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE n.nspname = ANY(%s) ORDER BY 1, 2, 3""", (SCHEMAS,)).fetchall()
        routines = conn.execute(
            """SELECT n.nspname AS schema, p.proname AS name, p.prokind,
                      pg_get_function_identity_arguments(p.oid) AS args, obj_description(p.oid) AS comment
                 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = ANY(%s) ORDER BY 1, 3, 2""", (SCHEMAS,)).fetchall()
        triggers = conn.execute(
            """SELECT event_object_schema AS schema, event_object_table AS tbl, trigger_name,
                      string_agg(event_manipulation, ', ') AS events, action_timing
                 FROM information_schema.triggers WHERE event_object_schema = ANY(%s)
                GROUP BY 1, 2, 3, 5 ORDER BY 1, 2""", (SCHEMAS,)).fetchall()
        versions = conn.execute("SELECT file_name, left(checksum, 12) AS checksum, applied_at FROM ops.schema_version "
                                "ORDER BY applied_at DESC LIMIT 15").fetchall()

    by_table = defaultdict(list)
    for c in cols:
        by_table[(c["schema"], c["name"])].append(c)
    by_cons = defaultdict(list)
    for c in cons:
        by_cons[(c["schema"], c["name"])].append(c)
    kind = {"p": "PK", "f": "FK", "u": "UNIQUE", "c": "CHECK"}

    out = ["# Словарь данных\n\nСгенерировано командой `evadvisor docs` из каталога PostgreSQL "
           f"{date.today():%d.%m.%Y}. "
           "Не редактируйте вручную: описания берутся из `COMMENT ON` в `db/*.sql`.\n"]
    out.append("## Таблицы\n\n| Таблица | Назначение | Строк |\n|---|---|---|")
    for t in tables:
        rows = f"{t['rows']:,}".replace(",", " ")
        out.append(f"| `{t['schema']}.{t['name']}` | {t['comment'] or ''} | {rows} |")
    for t in tables:
        key = (t["schema"], t["name"])
        out.append(f"\n### `{t['schema']}.{t['name']}`\n\n{t['comment'] or ''}\n")
        out.append("| Колонка | Тип | NULL | По умолчанию | Описание |\n|---|---|---|---|---|")
        for c in by_table[key]:
            default = (c["column_default"] or "").replace("|", "\\|")
            out.append(f"| `{c['column_name']}` | {c['data_type']} | {'да' if c['is_nullable'] == 'YES' else 'нет'} "
                       f"| {default} | {c['comment'] or ''} |")
        if by_cons[key]:
            out.append("\nОграничения:\n")
            for c in by_cons[key]:
                out.append(f"- {kind.get(c['contype'], c['contype'])}: `{c['def']}`")
    out.append("\n## Функции и процедуры\n\n| Объект | Вид | Аргументы | Назначение |\n|---|---|---|---|")
    for r in routines:
        k = {"f": "функция", "p": "процедура"}.get(r["prokind"], r["prokind"])
        out.append(f"| `{r['schema']}.{r['name']}` | {k} | `{r['args']}` | {r['comment'] or ''} |")
    out.append("\n## Триггеры\n\n| Таблица | Триггер | Момент | События |\n|---|---|---|---|")
    for t in triggers:
        out.append(f"| `{t['schema']}.{t['tbl']}` | `{t['trigger_name']}` | {t['action_timing']} | {t['events']} |")
    out.append("\n## Версии схемы (последние)\n\n| Файл | Контрольная сумма | Применён |\n|---|---|---|")
    for v in versions:
        out.append(f"| {v['file_name']} | `{v['checksum']}` | {v['applied_at']:%d.%m.%Y %H:%M} |")
    return "\n".join(out) + "\n"


def write() -> str:
    path = ROOT / "docs" / "data_dictionary.md"
    path.write_text(build(), encoding="utf-8")
    return str(path)
