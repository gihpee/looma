# HTTP-контракт оркестратора

Что и по каким адресам отдаёт оркестратор трём поверхностям: лендингу,
консоли клиента и админке. Подробности по смыслу — в тематических документах
(`BILLING.md`, `TRAINING.md`, `RAY.md`, `DEPLOYMENT.md`); здесь — карта.

## Хосты и права

| хост | что отдаёт | к оркестратору проксируется |
|---|---|---|
| `loomafloat.ru` | лендинг | `/api/demo`, `/api/public/*` |
| `console.loomafloat.ru` | консоль клиента | `/api/*`, `/v1/*` |
| `admin.loomafloat.ru` | админка | `/api/*`, `/admin/*`, `/v1/*`, `/connect/*` |
| `api.loomafloat.ru` | оркестратор целиком | всё (для SDK и curl) |

Права проверяются одним слоем по префиксу пути:

- `/admin/*` — только администратор (сессия с ролью `admin` или заголовок
  `X-Looma-Admin-Token` — аварийный вход, у него нет `account_id`);
- `/api/*`, `/v1/*` — любой представившийся: cookie `looma_session`
  (`Domain=.loomafloat.ru`) или `Authorization: Bearer <api-key>`;
- открыто без представления: `POST/DELETE /api/session`, `/api/demo`,
  `/api/public/pricing`, `/agent/release/*`.

Ошибка — всегда `{"error": {"message": "...", "type": "..."}}` с
осмысленным статусом: `400` — запрос испорчен, `401/403` — не представились /
не положено, `404` — не ваше или нет, `409` — нельзя сейчас (узлов не хватает,
группа уже есть), `503` — оркестратор поднят без базы или без узлов.

Деньги — целые копейки. Время — ISO-8601 или unix-секунды (как в источнике).

## Публичное (лендинг)

| метод и путь | что |
|---|---|
| `GET /api/public/pricing` | прайс: классы карт с ценой за GPU-час и ценами конкурентов, модели с ценой за 1M токенов (скрытые не отдаются), ставка обучения, `as_of` |
| `GET /api/demo` | готова ли демо-модель |
| `POST /api/demo` | один вопрос демо-модели, SSE-поток с `usage` и `timings` в последнем событии |

## Сессия и ключи

| метод и путь | что |
|---|---|
| `POST /api/session` `{email, password}` | вход; ставит cookie |
| `DELETE /api/session` | выход |
| `GET /api/me` | кто я: `account_id`, `email`, `role`, `display_name` |
| `GET /api/keys` · `POST /api/keys` `{name}` · `DELETE /api/keys/{id}` | API-ключи; секрет показывается один раз, в ответе `POST` |

## looma-compute: аренда кластера

| метод и путь | что |
|---|---|
| `GET /api/capacity` | узлы сети без имён: `state` (free / mine / rented / inference / busy), `gpus`, `gpu_class`, `gpu_name`, `vram_gb`, `rtt_ms` |
| `GET /api/rates` | ставки по ресурсам, классы карт из прайса, ставка обучения — для стоимости в визардах |
| `POST /api/compute` | арендовать: `size`, `hours` (≤ `LOOMA_MAX_RENT_HOURS`), `label`, `requirements`, `ray_version`, `script` (base64), `resources.gpus`, `policy` (`displace` / `partial` / `wait`). Ответ — группа с `requested`, `granted`, `path`, `warning`; при `wait` и нехватке — `202` с заявкой |
| `GET /api/compute` | `clusters` — свои идущие аренды с `alive`; `pending` — свои заявки в очереди |
| `GET /api/compute/pending` · `DELETE /api/compute/pending/{ticket}` | очередь ожидания: посмотреть, отменить |
| `DELETE /api/compute/{group}` | снять свою аренду (владение — по журналу) |
| `WS /connect/{group}` | туннель `looma-connect` к ранг-0 кластера |

## looma-intelligence: инференс

| метод и путь | что |
|---|---|
| `GET /v1/models` | что отвечает сейчас, OpenAI-форма плюс `owned_by`, `context`, `price_in`, `price_out`, `logo_url`, `mine` |
| `POST /v1/chat/completions` | OpenAI-совместимо, `stream: true` — SSE; `usage` в последнем событии; токены записываются в счёт по цене модели |

### Свои модели клиента

| метод и путь | что |
|---|---|
| `POST /api/models/describe` `{repo}` | параметры модели с HF: слои, размер, архитектура — для раскладки по узлам |
| `GET /api/deployments` | свои развёртывания со здоровьем стадий |
| `POST /api/deployments` | развернуть: `repo`, `label`, `engine`, `dtype`, `device`, `stages` (≤ `CLIENT_MAX_STAGES`), `by_vram`; узлы по именам выбирать нельзя. Открывает аренду `looma-inference` |
| `DELETE /api/deployments/{group}` | снять свою |

### Обучение

| метод и путь | что |
|---|---|
| `GET /api/train` · `GET /api/train/{id}` | свои задания; в карточке — прогресс, loss, адаптеры |
| `POST /api/train` | запустить LoRA: `repo`, `dataset` (base64 JSONL), `precision`, `schedule`, `lora`, `max_len`, `force`. Аренда `looma-training` |
| `POST /api/train/{id}/stop` | остановить |
| `GET /api/train/{id}/adapter/{name}` | скачать файл адаптера |

## Биллинг и кредиты

| метод и путь | что |
|---|---|
| `GET /api/usage` | расход: `leases` по ресурсам (GPU-часы, стоимость, идущие), `tokens` по моделям (со стоимостью), `total`. Фильтры `since`, `until`, `resource` |
| `GET /api/usage/export.csv` | то же строками (`;`, рубли) |
| `GET /api/balance` | `kopecks` = начислено − израсходовано, `credited`, `spent`, `grants` (последние 20) |

## Админка

### Сеть

| метод и путь | что |
|---|---|
| `GET /admin/agents` | узлы с железом, кэшами, p2p-каналом, версией агента |
| `GET /admin/agents/{node}/logs` · `POST …/rescan` · `POST …/restart` | лог, перечитать железо, перезапустить агента |
| `GET /admin/connect` | адрес, на который звонят агенты, и предупреждения |
| `GET/POST /admin/keys` · `DELETE /admin/keys/{id}` | ключи подключения (адрес оркестратора внутри ключа) |
| `GET/POST /admin/release` · `POST /admin/release/wave` `{percent}` · `POST /admin/release/withdraw` | релиз агента и выкатка по процентам |

### Нагрузка

| метод и путь | что |
|---|---|
| `GET /admin/groups` · `GET /admin/groups/health` (батч) · `GET /admin/groups/{id}/health` | группы задач и здоровье стадий |
| `POST /admin/groups/{id}/stop` · `DELETE /admin/groups/{id}` | снять; удалить запись |
| `POST /admin/deploy` · `GET /admin/deployments` · `POST /admin/deployments/{id}/protected` | развернуть модель (с выбором узлов), список, защита от вытеснения |
| `POST /admin/models/describe` | как `/api/models/describe` |
| `GET/POST /admin/train` · `GET /admin/train/{id}` · `POST …/stop` · `GET …/adapter/{name}` | обучение всех клиентов |
| `GET/POST /admin/tasks` · `GET /admin/tasks/{id}` · `…/logs` · `…/results/{name}` · `POST …/stop` · `DELETE` | одиночные задачи |
| `POST /admin/ray` | кластер администратора (узлы по именам, без потолка) |

### Клиенты и деньги

| метод и путь | что |
|---|---|
| `GET/POST /admin/accounts` · `POST /admin/accounts/{id}/password` · `POST …/disabled` | учётные записи (регистрация по приглашению) |
| `GET/POST /admin/accounts/{id}/credits` | баланс и журнал; начислить `{kopecks, note}` (минус — корректировка) |
| `GET /admin/leases` · `POST /admin/leases/{group}/close` | все идущие аренды с `alive`/`known`; закрыть зависшую |
| `GET /admin/usage` · `GET /admin/usage/export.csv` | расход по всем или `?account_id=` |
| `GET/POST /admin/rates` | ставки биллинга по ресурсам (копейки за GPU-час) |
| `GET/PUT /admin/pricing` | публичный прайс целиком; модели без `logo_url` при публикации получают аватар владельца `repo` с HuggingFace |
| `GET /admin/logos/lookup?repo=` | найти логотип по репозиторию HF |

## Чего в контракте нет намеренно

- **Истории чата на сервере.** Консоль хранит переписку в браузере через
  `chatStore`; серверный `/api/chat/threads` подключится за той же абстракцией.
- **Платежей.** Кредиты начисляет администратор.
- **Живых чисел сети на лендинге.** Доступные карты клиент видит на шаге
  «Узлы» визарда аренды, после входа.
