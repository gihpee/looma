# Пограничный nginx. Отдельным образом от панели: у панели своя сборка с node,
# а этому нужен только конфиг.
FROM nginx:1.27-alpine

COPY docker/edge/default.conf.template /etc/nginx/templates/default.conf.template
# Общий TLS-блок — тоже шаблон: в нём путь к сертификату с именем домена.
COPY docker/edge/ssl.conf.template /etc/nginx/templates/ssl.conf.template
# В conf.d, а не в templates: подставлять тут нечего, а nginx подхватит сам.
COPY docker/edge/upgrade.conf /etc/nginx/conf.d/upgrade.conf

ENV LOOMA_WEB_PORT=8080 \
    LOOMA_HTTP_PORT=8000

# Перезагрузка по расписанию — сценарием, который образ выполняет сам перед
# стартом. Команда остаётся нетронутой: подмена её на `sh -c` отключает
# подстановку переменных в шаблоны, и конфиг просто не собирается (см. шапку
# reload.sh).
COPY docker/edge/reload.sh /docker-entrypoint.d/90-reload.sh
