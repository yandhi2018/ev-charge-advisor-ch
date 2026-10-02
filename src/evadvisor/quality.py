"""Контроль качества данных: проверки из config/dq_checks.yaml → ops.dq_check, ops.dq_result.

Поведение при обнаружении некорректных данных:
- на этапе загрузки записи-нарушители не попадают в core, а уходят в ops.quarantine (процедуры sp_*);
- на этапе проверок нарушение severity=error останавливает обновление витрин и обучение моделей
  (DataQualityError), нарушение severity=warn фиксируется и показывается на операционном дашборде.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import yaml

from evadvisor.config import ROOT
from evadvisor.db import connect, session
from evadvisor.runlog import start_run

log = logging.getLogger(__name__)


class DataQualityError(RuntimeError):
    pass


@dataclass
class CheckResult:
    check_id: str
    severity: str
    passed: bool
    failed_rows: int


def load_catalog() -> list[dict]:
    with open(ROOT / "config" / "dq_checks.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["checks"]


def sync_catalog(checks: list[dict]) -> None:
    with session("engineer") as conn:
        ids = [c["id"] for c in checks]
        conn.execute("DELETE FROM ops.dq_check WHERE NOT (check_id = ANY(%s))", (ids,))
        for c in checks:
            conn.execute(
                """INSERT INTO ops.dq_check (check_id, layer, table_name, check_type, severity, description, sql_text)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (check_id) DO UPDATE SET layer = EXCLUDED.layer, table_name = EXCLUDED.table_name,
                     check_type = EXCLUDED.check_type, severity = EXCLUDED.severity,
                     description = EXCLUDED.description, sql_text = EXCLUDED.sql_text""",
                (c["id"], c["layer"], c["table"], c["type"], c["severity"], c["description"], c["sql"]),
            )


def run_checks(raise_on_error: bool = True) -> list[CheckResult]:
    checks = load_catalog()
    sync_catalog(checks)
    results: list[CheckResult] = []
    with start_run("quality", {"checks": len(checks)}) as ctx:
        for c in checks:
            sql = c["sql"].strip().rstrip(";")
            with connect("engineer") as conn:
                try:
                    n = conn.execute(f"SELECT count(*) AS n FROM ({sql}\n) v").fetchone()["n"]
                    sample = conn.execute(f"SELECT * FROM ({sql}\n) v LIMIT 5").fetchall() if n else []
                except Exception as exc:  # некорректная проверка не должна ронять остальные
                    conn.rollback()
                    log.error("DQ %s: %s", c["id"], exc)
                    ctx.errors.append(f"{c['id']}: {exc}")
                    n, sample = 1, [{"error": str(exc)}]
                conn.execute(
                    """INSERT INTO ops.dq_result (check_id, run_id, passed, failed_rows, sample)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (c["id"], ctx.run_id, n == 0, n, json.dumps(sample, default=str, ensure_ascii=False)),
                )
                conn.commit()
            results.append(CheckResult(c["id"], c["severity"], n == 0, n))
            log.info("DQ %-34s %-5s %s", c["id"], c["severity"], "OK" if n == 0 else f"нарушений: {n}")
        ctx.rows_received = len(results)
        ctx.rows_rejected = sum(1 for r in results if not r.passed)
        failed_errors = [r for r in results if not r.passed and r.severity == "error"]
        if failed_errors:
            ctx.status = "partial"
            ctx.errors.append("blocking checks failed: " + ", ".join(r.check_id for r in failed_errors))
    if raise_on_error and any(not r.passed and r.severity == "error" for r in results):
        raise DataQualityError("Не пройдены блокирующие проверки качества: "
                               + ", ".join(r.check_id for r in results if not r.passed and r.severity == "error"))
    return results
