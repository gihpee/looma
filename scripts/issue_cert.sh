#!/usr/bin/env bash
# Выпуск сертификата на все четыре имени платформы: апекс, console., admin.,
# api. Один SAN-сертификат, один каталог в live/ — по апексу.
#
# Два режима:
#
#   scripts/issue_cert.sh            первый выпуск, до первого `compose up`:
#                                    порт 80 свободен, certbot слушает его сам
#   scripts/issue_cert.sh --webroot  добавить имена к уже работающему
#                                    сертификату без остановки edge: проверка
#                                    идёт через папку /var/www/acme, которую
#                                    edge отдаёт на всех четырёх именах
#
# Почему первый выпуск отдельно, а не в compose: проверка владения доменом
# ходит по http на порт 80, а его занимает пограничный nginx, который без
# сертификата не поднимется. Замкнутый круг разрывается тем, что первый выпуск
# делает отдельно стоящий certbot.
#
# --cert-name обязателен: без него certbot заведёт каталог live/<домен>-0001,
# а оркестратор и nginx продолжат читать старый файл и через 90 дней узлы
# отвалятся все разом. --expand разрешает добавить имена к существующему
# сертификату, не спрашивая подтверждения (скрипт неинтерактивный).
#
# Продление идёт само, службой certbot из compose, и порт 80 у неё уже
# обслуживает работающий nginx. Агентов перевыпуск не касается: оркестратор
# перечитывает файл сам, а имя апекса в сертификате остаётся.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] || { echo "нет .env — скопируйте .env.example и заполните"; exit 1; }
set -a; . ./.env; set +a

: "${LOOMA_DOMAIN:?LOOMA_DOMAIN не задан в .env}"
: "${LOOMA_ACME_EMAIL:?LOOMA_ACME_EMAIL не задан в .env}"

PROJECT="$(basename "$PWD")"
LETSENCRYPT="${PROJECT}_letsencrypt"
ACME="${PROJECT}_acme"

echo "домен:  $LOOMA_DOMAIN"
echo "почта:  $LOOMA_ACME_EMAIL"
echo

# Тома создаются заранее: certbot запускается вне compose и сам их не заведёт.
docker volume create "$LETSENCRYPT" >/dev/null
docker volume create "$ACME" >/dev/null

NAMES=(-d "$LOOMA_DOMAIN" -d "console.$LOOMA_DOMAIN" -d "admin.$LOOMA_DOMAIN" -d "api.$LOOMA_DOMAIN")
echo "имена:  $LOOMA_DOMAIN console. admin. api."
echo

# Уже есть сертификат ровно на эти четыре имени — выпускать нечего.
if docker run --rm -v "$LETSENCRYPT:/etc/letsencrypt" certbot/certbot \
     certificates --cert-name "$LOOMA_DOMAIN" 2>/dev/null \
   | grep -q "api.$LOOMA_DOMAIN"; then
  echo "сертификат на $LOOMA_DOMAIN и поддомены уже есть — выпускать нечего."
  echo "Продлевает его служба certbot из compose."
  exit 0
fi

if [ "${1:-}" = "--webroot" ]; then
  # Edge работает и отдаёт /.well-known/acme-challenge/ на всех именах —
  # порт 80 занимать не нужно, простоя нет.
  docker run --rm \
    -v "$LETSENCRYPT:/etc/letsencrypt" \
    -v "$ACME:/var/www/acme" \
    certbot/certbot certonly --webroot -w /var/www/acme \
      --cert-name "$LOOMA_DOMAIN" --expand "${NAMES[@]}" \
      --email "$LOOMA_ACME_EMAIL" \
      --agree-tos --no-eff-email --non-interactive
  echo
  echo "Готово. Nginx перечитает файл сам в течение 12 часов; чтобы сразу:"
  echo "  docker compose exec edge nginx -s reload"
  exit 0
fi

echo "Порт 80 должен быть свободен и доступен снаружи. Если compose поднят —"
echo "остановите пограничный слой: docker compose stop edge"
echo "(или используйте --webroot, чтобы не останавливать)"
echo

docker run --rm -p 80:80 \
  -v "$LETSENCRYPT:/etc/letsencrypt" \
  -v "$ACME:/var/www/acme" \
  certbot/certbot certonly --standalone \
    --cert-name "$LOOMA_DOMAIN" --expand "${NAMES[@]}" \
    --email "$LOOMA_ACME_EMAIL" \
    --agree-tos --no-eff-email --non-interactive

echo
echo "Готово. Дальше: docker compose up -d --build"
