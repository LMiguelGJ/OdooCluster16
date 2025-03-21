#!/bin/bash

# Modificar el archivo SQL antes de ejecutarlo usando awk
echo "Modificando el archivo SQL..."
awk -v exp_date="$EXPIRATION_DATE" -v ent_code="$ENTERPRISE_CODE" '
{
  gsub(/v_expiration_date TIMESTAMP := '\''[^'\'']*'\'';/, "v_expiration_date TIMESTAMP := '\''" exp_date "'\'';");
  gsub(/v_enterprise_code TEXT := '\''[^'\'']*'\'';/, "v_enterprise_code TEXT := '\''" ent_code "'\'';");
  print
}' init-db.sql > temp.sql && cp temp.sql init-db.sql && rm temp.sql


# Ruta de configuración de Odoo
ODOO_CONF="/etc/odoo/odoo.conf"
TEMP_CONF="/etc/odoo/temp_odoo.conf"

# Crear un nuevo archivo de configuración temporal con la sección [options]
echo "[options]" > "$TEMP_CONF"

# Leer todas las variables de entorno excluyendo las críticas
env | grep -vE '^(PATH|HOSTNAME|TERM|SHELL|USER|PWD|HOME|SHLVL|LOGNAME|_|WEB_PORT|WEB_HOST|WEB_BUILD|EXPIRATION_DATE|ENTERPRISE_CODE)' | while IFS= read -r line; do
    # Validar que la línea no esté vacía
    if [ -n "$line" ]; then
        # Convertir clave a minúscula, agregar espacios alrededor del '=', y escribir en el archivo temporal
        key=$(echo "$line" | cut -d= -f1 | tr '[:upper:]' '[:lower:]')
        value=$(echo "$line" | cut -d= -f2-)
        echo "$key = $value" >> "$TEMP_CONF"
    fi
done

# Reemplazar el archivo de configuración original con el temporal
mv "$TEMP_CONF" "$ODOO_CONF"


# Ejecutar Odoo
echo "Iniciando Odoo..."
sleep 5
# Iniciar Odoo en modo "stop-after-init"
echo "Iniciando Odoo y esperando que cargue todos los módulos..."
/usr/bin/odoo -c /etc/odoo/odoo.conf -u all --stop-after-init &  
ODOO_PID=$!

# Esperar a que Odoo termine
echo "Esperando que Odoo finalice su carga y se detenga..."
wait $ODOO_PID

if [ $? -eq 0 ]; then
    echo "Odoo se ha detenido correctamente."
else
    echo "Error: Odoo no se detuvo correctamente." 
    exit 1
fi

# Función para ejecutar comandos SQL con reintentos
execute_sql_with_retries() {
  local max_retries=5
  local retry_interval=10
  local attempt=1

  while [ $attempt -le $max_retries ]
  do
    echo "Intento $attempt de $max_retries para ejecutar comandos SQL en la base de datos..."

    export PGPASSWORD=${DB_PASSWORD}
    if psql -h ${DB_HOST} -U ${DB_USER} -d ${DB_NAME} -f init-db.sql; then
      echo "Comandos SQL ejecutados con éxito."
      return 0
    else
      echo "Error al ejecutar comandos SQL. Intentando de nuevo en $retry_interval segundos..."
      sleep $retry_interval
      attempt=$((attempt + 1))
    fi
  done

  echo "Falló la ejecución de comandos SQL después de $max_retries intentos."
  return 1
}

# Ejecutar comandos SQL en la base de datos con reintentos
echo "Ejecutando comandos SQL en la base de datos..."
execute_sql_with_retries

sleep 5

# Reiniciar Odoo con módulos adicionales
echo "Reiniciando Odoo con web_studio..."
/usr/bin/odoo -c /etc/odoo/odoo.conf -i web_studio -u web
