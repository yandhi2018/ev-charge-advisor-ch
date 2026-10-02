"""Конфигурация: config/config.yaml (параметры) + .env (пароли и пути)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


@lru_cache(maxsize=1)
def settings() -> dict[str, Any]:
    with open(ROOT / "config" / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def data_dir() -> Path:
    path = Path(os.getenv("EVADVISOR_DATA_DIR") or ROOT / "data")
    path.mkdir(parents=True, exist_ok=True)
    return path


def raw_dir(source: str) -> Path:
    path = data_dir() / "raw" / source
    path.mkdir(parents=True, exist_ok=True)
    return path


def lake_dir(*parts: str) -> Path:
    path = data_dir().joinpath("lake", *parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def artifacts_dir() -> Path:
    path = data_dir() / "artifacts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def source_cfg(name: str) -> dict[str, Any]:
    return settings()["sources"][name]


def timeout(name: str) -> tuple[float, float]:
    connect, read = source_cfg(name).get("timeout_s", [10, 60])
    return float(connect), float(read)
