-- Bloque anónimo para insertar o actualizar parámetros de configuración
DO $$
DECLARE
    v_expiration_date TIMESTAMP := '2026-02-22 00:00:00';
    v_enterprise_code TEXT := 'M22112861297726';
BEGIN
    IF EXISTS (SELECT 1 FROM ir_config_parameter WHERE key = 'database.expiration_date') THEN
        UPDATE ir_config_parameter
        SET value = v_expiration_date::TEXT
        WHERE key = 'database.expiration_date';
    ELSE
        INSERT INTO ir_config_parameter (key, value)
        VALUES ('database.expiration_date', v_expiration_date::TEXT);
    END IF;

    IF EXISTS (SELECT 1 FROM ir_config_parameter WHERE key = 'database.enterprise_code') THEN
        UPDATE ir_config_parameter
        SET value = v_enterprise_code
        WHERE key = 'database.enterprise_code';
    ELSE
        INSERT INTO ir_config_parameter (key, value)
        VALUES ('database.enterprise_code', v_enterprise_code);
    END IF;
END $$;

-- Eliminar la función y el trigger que previenen la eliminación o modificación
-- CREATE OR REPLACE FUNCTION prevent_delete_update() RETURNS trigger AS $$
-- BEGIN
--     IF OLD.key IN ('database.expiration_date', 'database.enterprise_code') THEN
--         RAISE EXCEPTION 'No se puede eliminar ni modificar la clave %', OLD.key;
--     END IF;
--     RETURN OLD;
-- END;
-- $$ LANGUAGE plpgsql;

-- CREATE TRIGGER prevent_delete_update_trigger
-- BEFORE DELETE OR UPDATE ON ir_config_parameter
-- FOR EACH ROW
-- EXECUTE FUNCTION prevent_delete_update();
