-- 06_triggers.sql — триггеры (ПР8 ПСУД): автоматическая обработка изменений данных.

------------------------------------------------------------------------
-- 1. История зарядных точек (SCD2).
-- Бизнес-правило: любое изменение характеристик точки сохраняется в истории;
-- в каждый момент у точки ровно одна открытая версия.
------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.trg_evse_history_fn()
RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_change text;
BEGIN
    IF TG_OP = 'INSERT' THEN
        v_change := 'insert';
    ELSIF NEW.row_hash IS DISTINCT FROM OLD.row_hash
       OR NEW.station_id IS DISTINCT FROM OLD.station_id THEN
        v_change := CASE WHEN NOT OLD.is_active AND NEW.is_active THEN 'reactivate' ELSE 'update' END;
    ELSIF OLD.is_active AND NOT NEW.is_active THEN
        v_change := 'deactivate';
    ELSIF NOT OLD.is_active AND NEW.is_active THEN
        v_change := 'reactivate';
    ELSE
        RETURN NEW;   -- служебные поля (last_seen) историю не порождают
    END IF;

    IF TG_OP = 'UPDATE' THEN
        UPDATE core.evse_history
           SET valid_to = NEW.valid_from
         WHERE evse_id = NEW.evse_id AND valid_to IS NULL;
    END IF;

    INSERT INTO core.evse_history (evse_id, station_id, power_kw, power_type, is_active, row_hash,
                                   valid_from, valid_to, change_type, run_id)
    VALUES (NEW.evse_id, NEW.station_id, NEW.power_kw, NEW.power_type, NEW.is_active, NEW.row_hash,
            NEW.valid_from, NULL, v_change, NEW.last_run_id);
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_evse_history ON core.evse;
CREATE TRIGGER trg_evse_history
    AFTER INSERT OR UPDATE ON core.evse
    FOR EACH ROW EXECUTE FUNCTION core.trg_evse_history_fn();

------------------------------------------------------------------------
-- 2. Защита журнала загрузок.
-- Бизнес-правила: завершённый запуск нельзя вернуть в состояние «running»;
-- источник и время начала запуска не изменяются; строки журнала не удаляются.
------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION ops.trg_load_run_guard_fn()
RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'load_run rows are append-only (run %)', OLD.run_id;
    END IF;
    IF OLD.status <> 'running' AND NEW.status = 'running' THEN
        RAISE EXCEPTION 'finished run % cannot be reopened', OLD.run_id;
    END IF;
    IF NEW.source <> OLD.source OR NEW.started_at <> OLD.started_at THEN
        RAISE EXCEPTION 'source and started_at of run % are immutable', OLD.run_id;
    END IF;
    IF NEW.status <> 'running' AND NEW.finished_at IS NULL THEN
        NEW.finished_at := now();
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_load_run_guard ON ops.load_run;
CREATE TRIGGER trg_load_run_guard
    BEFORE UPDATE OR DELETE ON ops.load_run
    FOR EACH ROW EXECUTE FUNCTION ops.trg_load_run_guard_fn();

------------------------------------------------------------------------
-- 3. Проверка рекомендации.
-- Бизнес-правило: система не может рекомендовать станцию, на которой нет ни одной
-- активной точки с разъёмом, совместимым с автомобилем из запроса.
------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.trg_recommendation_item_check_fn()
RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM core.recommendation_request r
          JOIN core.vehicle_plug vp ON vp.vehicle_id = r.vehicle_id
          JOIN core.evse e          ON e.station_id = NEW.station_id AND e.is_active
          JOIN core.evse_plug ep    ON ep.evse_id = e.evse_id AND ep.plug_code = vp.plug_code
         WHERE r.request_id = NEW.request_id) THEN
        RAISE EXCEPTION 'station % has no active EVSE compatible with the vehicle of request %',
            NEW.station_id, NEW.request_id;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_recommendation_item_check ON core.recommendation_item;
CREATE TRIGGER trg_recommendation_item_check
    BEFORE INSERT OR UPDATE OF station_id ON core.recommendation_item
    FOR EACH ROW EXECUTE FUNCTION core.trg_recommendation_item_check_fn();

------------------------------------------------------------------------
-- 4. Счётчик выдачи в запросе рекомендаций поддерживается автоматически.
------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.trg_recommendation_count_fn()
RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE core.recommendation_request r
       SET n_results = (SELECT count(*) FROM core.recommendation_item i WHERE i.request_id = r.request_id)
     WHERE r.request_id = coalesce(NEW.request_id, OLD.request_id);
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_recommendation_count ON core.recommendation_item;
CREATE TRIGGER trg_recommendation_count
    AFTER INSERT OR DELETE ON core.recommendation_item
    FOR EACH ROW EXECUTE FUNCTION core.trg_recommendation_count_fn();
