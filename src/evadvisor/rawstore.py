"""Слой raw: неизменяемые копии ответов источников + реестр ops.raw_manifest."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evadvisor.config import data_dir, raw_dir
from evadvisor.db import connect
from evadvisor.runlog import RunContext


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    """Запись через временный файл и os.replace: файл либо целый, либо отсутствует."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def relpath(path: Path) -> str:
    return path.resolve().relative_to(data_dir().resolve()).as_posix()


def register(ctx: RunContext, path: Path, sha: str | None = None) -> str:
    sha = sha or sha256_file(path)
    rel = relpath(path)
    with connect("engineer", autocommit=True) as conn:
        conn.execute(
            """INSERT INTO ops.raw_manifest (raw_path, run_id, source, sha256, size_bytes)
               VALUES (%s, %s, %s, %s, %s) ON CONFLICT (raw_path) DO NOTHING""",
            (rel, ctx.run_id, ctx.source, sha, path.stat().st_size),
        )
    ctx.raw_path, ctx.content_hash = rel, sha
    return sha


def save(ctx: RunContext, data: bytes, name: str, subdir: str = "") -> Path:
    """Сохранить ответ источника в data/raw/<source>/<subdir>/<name> и зарегистрировать."""
    path = raw_dir(ctx.source) / subdir / name if subdir else raw_dir(ctx.source) / name
    atomic_write(path, data)
    register(ctx, path, sha256_bytes(data))
    return path


def save_json(ctx: RunContext, payload: Any, stem: str, subdir: str = "") -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return save(ctx, data, f"{stem}_{stamp}.json", subdir)


def already_loaded(source: str, sha: str) -> bool:
    """Был ли файл с таким содержимым уже успешно обработан (идемпотентность по хэшу)."""
    with connect("engineer") as conn:
        row = conn.execute(
            """SELECT 1 FROM ops.raw_manifest m JOIN ops.load_run r USING (run_id)
                WHERE m.source = %s AND m.sha256 = %s AND r.status IN ('success', 'partial') LIMIT 1""",
            (source, sha),
        ).fetchone()
    return row is not None
