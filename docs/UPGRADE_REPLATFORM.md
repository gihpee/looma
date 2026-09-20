# Переезд работающего сервера на четыре имени

Для сервера, на котором сейчас крутится старая версия: один хост
`loomafloat.ru`, за ним edge → web → оркестратор, Postgres в том же compose.
После переезда: `loomafloat.ru` (лендинг), `console.` (клиенты), `admin.`
(администратор), `api.` (SDK и curl) — на одном сертификате, с одной
сессией на все.

Что **не** меняется и что важно знать заранее:

- Postgres, том `looma-data` (ключи, релизы, состояние), том `letsencrypt` —
  те же. Учётные записи, ключи API, журнал аренд остаются.
- Агенты не переподключаются: адрес `loomafloat.ru:9000` и апекс в
  сертификате прежние; оркестратор перечитывает файл сертификата сам.
- Простой — только на время перезапуска оркестратора (секунды): стадии на
  узлах продолжают работать, агенты передозваниваются сами; запросы к
  инференсу в эти секунды получат ошибку соединения.
- Всем придётся войти заново: старая cookie была без `Domain`, новая — на
  `.loomafloat.ru`.

Порядок ниже рассчитан на то, чтобы каждый шаг можно было проверить до
следующего и откатить, если что-то не так.

---

## 0. Код на сервере

Новая версия — в `main` (коммит `feat(client:web+edge): full replatform`).
На сервер она попадает так же, как предыдущие:

```bash
cd /opt/looma            # каталог, где лежит docker-compose.yml
git pull origin main
```

Имя каталога важно: `scripts/issue_cert.sh` ищет тома compose по нему
(`<каталог>_letsencrypt`). Каталог не переименовывать.

## 1. DNS

В зоне `loomafloat.ru` добавить три A-записи на IP сервера (и AAAA, если
есть IPv6):

```
console  A  <ip сервера>
admin    A  <ip сервера>
api      A  <ip сервера>
```

Дождаться, пока имена видны снаружи — иначе Let's Encrypt не пройдёт проверку:

```bash
for h in console admin api; do echo -n "$h: "; dig +short @1.1.1.1 $h.loomafloat.ru; done
```

Все три должны печатать IP сервера. Пока не печатают — дальше не идти.

## 2. `.env`

Одна новая переменная и проверка трёх старых:

```bash
grep -E '^LOOMA_(DOMAIN|ACME_EMAIL|PUBLIC_ADDR|TLS_CERT|TLS_KEY|DATABASE_URL|COOKIE_DOMAIN)=' .env
```

Должно быть:

```
LOOMA_DOMAIN=loomafloat.ru
LOOMA_ACME_EMAIL=<живая почта>
LOOMA_PUBLIC_ADDR=loomafloat.ru:9000
LOOMA_TLS_CERT=/etc/letsencrypt/live/loomafloat.ru/fullchain.pem
LOOMA_TLS_KEY=/etc/letsencrypt/live/loomafloat.ru/privkey.pem
LOOMA_DATABASE_URL=postgresql://looma:<пароль>@127.0.0.1:5432/looma
LOOMA_COOKIE_DOMAIN=loomafloat.ru
```

Если `LOOMA_COOKIE_DOMAIN` нет — добавить строкой (без комментария на той же
строке). Необязательные, со значениями по умолчанию:

```
LOOMA_MAX_RENT_HOURS=24      потолок часов одной аренды
CLIENT_MAX_STAGES=4          сколько стадий клиент может занять своей моделью
LOOMA_WAIT_HOURS=6           сколько живёт заявка «ждать свободные»
```

## 3. Сертификат — добавить три имени, не останавливая ничего

Старый edge ещё работает и отдаёт `/.well-known/acme-challenge/` на любой
Host (его единственный блок на порту 80 — default). Поэтому имена добавляются
через webroot, без простоя:

```bash
scripts/issue_cert.sh --webroot
```

Скрипт расширяет **существующий** сертификат `loomafloat.ru` (`--cert-name`,
`--expand`): каталог `live/loomafloat.ru/` остаётся тем же, оркестратор и
агенты ничего не замечают. Проверить:

```bash
docker run --rm -v "$(basename $PWD)_letsencrypt:/etc/letsencrypt" certbot/certbot certificates
```

В `Domains:` должны быть все четыре имени. Если certbot ругается на
проверку — DNS ещё не разъехался (шаг 1) или порт 80 закрыт снаружи.

Старый edge на этом шаге ещё отдаёт сертификат из старого файла в памяти —
это нормально, он сменится на шаге 5.

## 4. Собрать образы до переключения

Сборка `web` (три Vite-сборки) и оркестратора занимает минуты; делать её
заранее, пока всё работает, чтобы перезапуск на шаге 5 был коротким:

```bash
docker compose build
```

Ошибка сборки здесь ничего не ломает — старые контейнеры продолжают работать.

## 5. Переключение

```bash
docker compose up -d
```

Compose пересоздаст `orchestrator`, `web`, `edge` (и `certbot`, если образ
обновился); `postgres` и `relay` не трогает. Оркестратор при старте накатит
миграцию `0005_credits` (журнал начислений, стоимость токенов) — вручную
ничего делать не нужно. Проверить по логу:

```bash
docker compose logs --tail=50 orchestrator | grep -E "миграции|TLS|HTTP on|ошибк|Traceback"
```

Ожидается `миграции накатаны: 0005_credits.sql`, `AgentGateway слушает :9000
по TLS`, `HTTP on :8000`.

```bash
docker compose ps                    # все Up, postgres — healthy
docker compose exec edge nginx -t    # syntax is ok
docker compose logs --tail=20 edge web
```

## 6. Проверка снаружи

```bash
curl -sI https://loomafloat.ru | head -1                      # 200, лендинг
curl -sI https://console.loomafloat.ru | head -1              # 200, консоль
curl -sI https://admin.loomafloat.ru | head -1                # 200, админка
curl -s  https://api.loomafloat.ru/api/public/pricing | head -c 200   # JSON
curl -sI http://console.loomafloat.ru | head -1               # 301 → https
curl -sI https://loomafloat.ru/app | grep -i location         # → console.
```

И сертификат — тот же, что видят агенты, на всех именах:

```bash
for h in loomafloat.ru console.loomafloat.ru admin.loomafloat.ru api.loomafloat.ru; do
  echo -n "$h: "; echo | openssl s_client -servername $h -connect $h:443 2>/dev/null | openssl x509 -noout -enddate
done
```

Агенты: в админке «Узлы» все на связи, `seconds_since_seen` маленький. Если
узел висит «не на связи» дольше минуты — смотреть его лог (`детали` → «лог
агента»), но по опыту передозваниваются сами.

## 7. Первичное наполнение (без этого платформа пустая)

Войти на `https://admin.loomafloat.ru` существующей учётной записью
администратора (та же база). Если администратора в базе нет — аварийный
токен из `.env` (`LOOMA_ADMIN_TOKEN`) по-прежнему работает: в консоли
браузера `localStorage.looma_token='<токен>'`, затем завести администратора в
«Учётные записи» и дальше входить им.

1. **Ставки и цены → Ставки биллинга**: `looma-compute` и `looma-inference`
   (копейки за GPU-час; `looma-training` можно оставить пустым — пойдёт по
   ставке кластера). Старые ставки из базы сохранились, проверить.
2. **Ставки и цены → Классы GPU**: класс на каждую карту в сети (подстроки
   `match` — по `gpu_name` узлов; столбец «В сети» покажет, сколько карт
   подошло), цена, цены Selectel/AWS, галочка «hero» на одном классе — он
   попадёт в шапку лендинга.
3. **Модели**: имя как в `/v1/models`, репозиторий HF (логотип подтянется
   сам), контекст, цена за 1M токенов на вход и выход.
4. **Опубликовать в прайс**. До этого лендинг показывает пустые цены, а
   визарды считают по нулю.
5. **Учётные записи → кредиты**: каждому клиенту стартовое начисление.
   Платёжного модуля нет — без начисления баланс ноль (действия при этом не
   блокируются, но клиент видит «0 ₽»).
6. Проверить как клиент: войти на `console.`, «Кластеры → Арендовать» —
   на шаге «Узлы» видны карты по классам; «Модели» — платформенные с ценами;
   «Биллинг» — баланс.

## 8. Что сказать клиентам

- Кабинет теперь на `console.loomafloat.ru`, войти заново (старые ссылки
  `/app`, `/signin` перенаправляются сами).
- API-ключи прежние; `base_url` для SDK — `https://api.loomafloat.ru/v1`
  (старый `https://loomafloat.ru/v1` больше не проксируется — лендинг
  отдаёт только `/api/demo` и `/api/public`).

## Откат

Сертификат с четырьмя именами и миграция обратной совместимости не ломают,
DNS-записи можно оставить. Откат — вернуть прежнюю ревизию и пересобрать:

```bash
git checkout 6b09887       # последний коммит до переезда
docker compose up -d --build
```

Старый edge читает тот же `live/loomafloat.ru/fullchain.pem`, ему всё равно,
сколько там имён.

## Если что-то не так

| симптом | где смотреть |
|---|---|
| `502` на любом хосте | `docker compose logs web` — nginx не видит оркестратор на 127.0.0.1:8000; `docker compose ps orchestrator` |
| `console.` отдаёт лендинг | `LOOMA_DOMAIN` в `.env` не `loomafloat.ru` → web-nginx не различает Host; `docker compose exec web cat /etc/nginx/conf.d/default.conf \| grep server_name` |
| вход проходит, но на соседнем хосте не залогинен | нет `LOOMA_COOKIE_DOMAIN` в `.env` (шаг 2), перезапустить оркестратор |
| cookie не ставится вовсе | оркестратор не видит `X-Forwarded-Proto https` — edge/web обязаны стоять на этой же машине (`forwarded_allow_ips=127.0.0.1`) |
| certbot: `Connection refused` на `console.` | DNS ещё не разъехался, либо порт 80 закрыт |
| оркестратор не стартует, в логе `миграция` и `Traceback` | `docker compose logs orchestrator`; база жива? `docker compose exec postgres pg_isready` |
| агент «не на связи» после переезда | лог агента; адрес в ключе — `loomafloat.ru:9000`, он не менялся; проверить `openssl s_client -connect loomafloat.ru:9000` отдаёт цепочку с апексом |
