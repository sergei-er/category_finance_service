#!/bin/sh
# Запускается официальным entrypoint только на новом томе PostgreSQL.
set -eu
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set=ON_ERROR_STOP=1 \
    --set=app_user="$APP_DB_USER" --set=app_password="$APP_DB_PASSWORD" \
    --set=app_db="$APP_DB_NAME" <<'SQL'
CREATE ROLE :"app_user" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'app_password';
CREATE DATABASE :"app_db" OWNER :"app_user";
SQL
