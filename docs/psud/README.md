# Материалы курсовой работы по ПСУД

Сквозной проект ПР № 1–9 реализован в этом репозитории. Таблица показывает, где лежит результат каждой работы и как его продемонстрировать.

| ПР | Тема | Результат | Демонстрация |
|---|---|---|---|
| 1 | Анализ предметной области | [pr1_analysis.md](pr1_analysis.md): 4 роли, 12 функций, 10 бизнес-правил, группы данных, границы | — |
| 2 | Требования и ТЗ | [technical_task.md](technical_task.md): 20 FR, 10 NFR, 9 DR, ограничения, критерии приёмки, прослеживаемость | Критерии приёмки 1–7 |
| 3 | Моделирование бизнес-процессов | [bpmn_recommendation.bpmn](bpmn_recommendation.bpmn): BPMN 2.0, процесс «Подбор станции для зарядки» (3 дорожки, 15 действий, 5 шлюзов, объекты данных). Генератор — [bpmn_generate.py](bpmn_generate.py) | Открыть в https://demo.bpmn.io (File → Open) и экспортировать PNG для отчёта |
| 4 | UML | [uml_use_case.puml](uml_use_case.puml) (5 акторов, 15 вариантов, include/extend), [uml_class.puml](uml_class.puml) (13 классов, 15 связей с кратностями) | Расширение PlantUML для VS Code или plantuml.com |
| 5 | Логическая модель БД | [er_logical.md](er_logical.md): ER-диаграмма, M:N через ассоциативные сущности, 3НФ и обоснованные отступления | Предпросмотр Markdown на GitHub (Mermaid) |
| 6 | Физическая БД | [db/00_schemas.sql](../../db/00_schemas.sql), [db/01_create_tables.sql](../../db/01_create_tables.sql): 38 таблиц в схемах stg/core/mart/ops, PK, FK, CHECK, UNIQUE, правила ON DELETE | `evadvisor db migrate`; словарь [data_dictionary.md](../data_dictionary.md); тесты `test_check_*`, `test_unique_*`, `test_fk_*` |
| 7 | Наполнение и SQL-запросы | Наполнение из открытых источников (5 800 станций, 17 000 точек, 1 284 автомобиля, 1,2 млн часов погоды); [db/02_insert_data.sql](../../db/02_insert_data.sql) — справочники; [db/03_queries.sql](../../db/03_queries.sql) — 14 прикладных запросов всех требуемых категорий | `psql -f db/03_queries.sql` |
| 8 | Функции, процедуры, триггеры | [db/04_functions.sql](../../db/04_functions.sql) (6 функций), [db/05_procedures.sql](../../db/05_procedures.sql) (6 процедур), [db/06_triggers.sql](../../db/06_triggers.sql) (4 триггера: SCD2, защита журнала, проверка совместимости рекомендации, счётчик выдачи) | `pytest tests/test_db.py -k "history or reopen or append or incompatible or idempotent"` |
| 9 | Роли и разграничение доступа | [db/07_roles.sql](../../db/07_roles.sql): групповые роли водителя, аналитика, инженера, матрица доступа, GRANT/REVOKE, отзыв прав PUBLIC; учётные записи создаёт мигратор | `pytest tests/test_db.py -k role_access` — 10 сценариев (разрешённые и запрещённые) |

Сценарий демонстрации прототипа:
1. `docker compose up -d db`.
2. `evadvisor serve` → http://127.0.0.1:8000: подбор станции, затем дашборды «Аналитика» и «Данные», затем «Происхождение».
3. `pytest -v`.
