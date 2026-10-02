"""Командная строка системы: `evadvisor <группа> <команда>`.

Примеры:
    evadvisor db migrate            применить схему БД
    evadvisor ingest all            догрузить все источники (инкрементально)
    evadvisor transform             витрины и проверки качества
    evadvisor run-all               полный цикл: загрузка → преобразования → качество → модели
    evadvisor serve                 веб-приложение на http://127.0.0.1:8000
"""

from __future__ import annotations

import logging
import sys

import typer

app = typer.Typer(help="EV Charge Advisor CH — ИС управления данными зарядной инфраструктуры", no_args_is_help=True)
db_app = typer.Typer(help="Схема БД", no_args_is_help=True)
ingest_app = typer.Typer(help="Загрузка источников (инкрементально, идемпотентно)", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(ingest_app, name="ingest")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


@app.callback()
def main(ctx: typer.Context,
         verbose: bool = typer.Option(False, "--verbose", "-v", help="Подробный журнал")) -> None:
    _setup_logging(verbose)
    if ctx.invoked_subcommand not in (None, "db", "serve"):
        try:
            from evadvisor.runlog import recover_stale

            n = recover_stale(max_hours=0.5)
            if n:
                logging.getLogger("evadvisor").warning("Помечено прерванных запусков: %s", n)
        except Exception:  # БД может быть ещё не создана
            pass


# ---------------------------------------------------------------- db
@db_app.command("migrate")
def db_migrate() -> None:
    """Применить db/*.sql и создать учётные записи приложения."""
    from evadvisor.migrate import migrate

    changed = migrate()
    typer.echo("Схема применена. Новые версии: " + (", ".join(changed) if changed else "нет"))


@db_app.command("reset")
def db_reset(yes: bool = typer.Option(False, "--yes", help="Подтверждение удаления")) -> None:
    """Удалить схемы проекта и создать заново (данные в data/ сохраняются)."""
    if not yes:
        typer.echo("Добавьте --yes: команда удаляет все таблицы проекта.")
        raise typer.Exit(1)
    from evadvisor.migrate import migrate, reset

    reset()
    migrate()
    typer.echo("БД пересоздана.")


# ---------------------------------------------------------------- ingest
def _run_loader(name: str, fn, **kwargs) -> bool:
    try:
        fn(**kwargs)
        return True
    except Exception as exc:  # сбой одного источника не останавливает остальные
        logging.getLogger("evadvisor").error("Источник %s: %s", name, exc)
        return False


@ingest_app.command("postal")
def ingest_postal() -> None:
    """Справочник почтовых индексов swisstopo (привязка станций к кантонам)."""
    from evadvisor.ingestion import postal

    postal.run()


@ingest_app.command("evse-data")
def ingest_evse_data() -> None:
    """Справочник зарядных точек EVSEData (SCD2)."""
    from evadvisor.ingestion import evse_data

    evse_data.run()


@ingest_app.command("status")
def ingest_status() -> None:
    """Снимок текущих статусов точек (не чаще раза в 5 минут)."""
    from evadvisor.ingestion import evse_status

    evse_status.run()


@ingest_app.command("weather")
def ingest_weather(kind: str = typer.Option("all", help="actual | hist_forecast | forecast | all")) -> None:
    """Погода Open-Meteo по 26 кантонам."""
    from evadvisor.ingestion import weather

    weather.run(kind)


@ingest_app.command("vehicles")
def ingest_vehicles() -> None:
    """Каталог электромобилей (Open EV Data + собственный CSV)."""
    from evadvisor.ingestion import vehicles

    vehicles.run()


@ingest_app.command("archive")
def ingest_archive(month: str = typer.Option(None, help="Один месяц YYYY-MM; по умолчанию все из конфига")) -> None:
    """Архив статусов 2024–2025: скачивание, проверка, Parquet, эпизоды, агрегаты."""
    from evadvisor.ingestion import archive

    archive.run(month)


@ingest_app.command("all")
def ingest_all(with_archive: bool = typer.Option(False, help="Включить архив (долго при первом запуске)")) -> None:
    """Все источники по очереди; ошибка одного не останавливает остальные."""
    from evadvisor.ingestion import archive, evse_data, evse_status, postal, vehicles, weather

    ok = [
        _run_loader("postal", postal.run),
        _run_loader("evse_data", evse_data.run),
        _run_loader("vehicles", vehicles.run),
        _run_loader("weather", weather.run, kind="all"),
        _run_loader("evse_status", evse_status.run),
    ]
    if with_archive:
        ok.append(_run_loader("archive", archive.run, month=None))
    if not all(ok):
        raise typer.Exit(2)


# ---------------------------------------------------------------- transform / quality
@app.command("quality")
def quality_cmd(strict: bool = typer.Option(True, help="Ошибка при непройденных блокирующих проверках")) -> None:
    """Проверки качества данных (config/dq_checks.yaml) → ops.dq_result."""
    from evadvisor.quality import run_checks

    results = run_checks(raise_on_error=strict)
    failed = [r for r in results if not r.passed]
    typer.echo(f"Проверок: {len(results)}, не пройдено: {len(failed)}")


@app.command("transform")
def transform_cmd() -> None:
    """Витрины по живым снимкам, метаданные lineage, проверки качества."""
    from evadvisor import lineage
    from evadvisor.db import session
    from evadvisor.quality import run_checks

    with session("engineer") as conn:
        conn.execute("CALL mart.sp_refresh_live_occupancy(now() - interval '14 days')")
    typer.echo(f"Lineage: рёбер {lineage.sync()}")
    results = run_checks(raise_on_error=True)
    typer.echo(f"Проверки качества: {sum(r.passed for r in results)}/{len(results)} пройдено")


@app.command("docs")
def docs_cmd() -> None:
    """Словарь данных из каталога БД → docs/data_dictionary.md."""
    from evadvisor.datadict import write

    typer.echo(write())


# ---------------------------------------------------------------- модели и приложение
@app.command("train")
def train_cmd(task: str = typer.Option("all", help="canton | availability | all"),
              resample: bool = typer.Option(False, help="Пересобрать обучающую выборку модели B")) -> None:
    """Обучение и временная валидация моделей; метрики → mart.model_metric, модели → ops.model_registry."""
    if task in ("canton", "all"):
        from evadvisor.models import canton

        for name, m in canton.evaluate().items():
            typer.echo(f"A {name}: MAE {m['mae']:.4f}, RMSE {m['rmse']:.4f}, MASE {m['mase']:.3f}")
    if task in ("availability", "all"):
        from evadvisor.models import availability

        for name, m in availability.evaluate(force_samples=resample).items():
            typer.echo(f"B {name}: Brier {m['brier']:.4f}, AUC {m['auc']:.3f}, ECE {m['ece']:.4f}")


@app.command("backtest")
def backtest_cmd(n: int = typer.Option(1500, help="Число смоделированных запросов")) -> None:
    """Бэктест рекомендателя: Hit@1/Hit@3 против «ближайшей» и «ближайшей свободной сейчас»."""
    from evadvisor.models import backtest

    for name, m in backtest.run(n_requests=n).items():
        typer.echo(f"{name:13s} Hit@1 {m['hit_at_1']:.3f}  Hit@3 {m['hit_at_3']:.3f}  (n={m['n']:.0f})")


@app.command("serve")
def serve_cmd(host: str = typer.Option(None), port: int = typer.Option(None)) -> None:
    """Веб-приложение (водитель, аналитика, состояние данных, происхождение)."""
    import uvicorn

    from evadvisor.config import settings

    web = settings()["web"]
    uvicorn.run("evadvisor.web.app:app", host=host or web["host"], port=port or web["port"])


@app.command("run-all")
def run_all(with_archive: bool = typer.Option(False, help="Обработать архив (первый запуск ~1,5 ч)"),
            with_train: bool = typer.Option(False, help="Переобучить модели")) -> None:
    """Полный цикл: загрузка → преобразования и lineage → проверки качества → (модели)."""
    from evadvisor.ingestion import archive, evse_data, evse_status, postal, vehicles, weather

    results = [_run_loader("postal", postal.run), _run_loader("evse_data", evse_data.run),
               _run_loader("vehicles", vehicles.run), _run_loader("weather", weather.run, kind="all"),
               _run_loader("evse_status", evse_status.run)]
    if with_archive:
        results.append(_run_loader("archive", archive.run, month=None))
    transform_cmd()
    if with_train:
        train_cmd(task="all", resample=False)
    if not all(results):
        typer.echo("Часть источников не загрузилась — см. ops.load_run и страницу «Данные».")
        raise typer.Exit(2)


if __name__ == "__main__":
    app()
