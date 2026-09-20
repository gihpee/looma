# io.net — анализ фронтенда, архитектуры и дизайн-подходов

**Объекты:** публичный сайт `io.net`, кабинет `cloud.io.net` + `ai.io.net`
**Дата обхода:** 19 сентября 2026
**Метод:** авторизованная сессия в тестовом аккаунте, обход всех разделов, чтение DOM/computed CSS/CSS-переменных, сетевых запросов, консоли; ничего не деплоилось и не оплачивалось.
**Ограничение:** кабинет обходился в панели шириной ~680px (планшетная раскладка); десктопная структура снималась через DOM. Лендинг — при 1440px и 375px.

---

## 0. Резюме

io.net — это **два независимых фронтенда** с разными стеками и, судя по всему, разными командами:

| | Лендинг `io.net` | Кабинет `cloud.io.net` / `ai.io.net` |
|---|---|---|
| Фреймворк | Next.js (App Router), SSR | Vite + React 18, чистый SPA |
| Стили | Tailwind, кастомные токены | Tailwind + shadcn/ui-токены (HSL) |
| Шрифт заголовков | Geist | Inter (Geist загружен, используется мало) |
| Качество кода/дизайна | Высокое, дисциплинированное | Среднее; много накопленного долга |
| Главная проблема | 6.5 MB картинок, попап с лид-формой | 5.4 MB JS-бандла, сломанный Training, тёмный паттерн в визарде |

Единственное, что их связывает визуально — чёрная primary-кнопка, компонент карточки железа (`HardwareCard`) и логотип с сине-фиолетовым градиентом.

Самые ценные решения для заимствования: **Recipes** (модель → готовая vLLM-конфигурация), **политика частичной аллокации GPU** (три явных варианта), **живой inference-виджет на лендинге**, **контекстная ссылка на документацию** в топбаре.

---

## 1. Общая архитектура

```
                    ┌──────────────────────────────────────────────┐
                    │  io.net  (Next.js, SSR, маркетинг)           │
                    │  hero → цены → продукты → чат → CTA           │
                    │  HubSpot формы / попап Managed Service        │
                    └───────────────┬──────────────────────────────┘
                                    │ Get Started / Deploy → редирект
                                    ▼
   ┌────────────────────────────────────────────────────────────────┐
   │  Кабинет — один Vite-бандл, два деплоя                          │
   │                                                                │
   │  cloud.io.net                    ai.io.net                     │
   │   /cloud/dashboard                /ai/app        (Chat)        │
   │   /cloud/virtual-machines         /ai/models     (прайс)       │
   │   /cloud/container                /ai/agents                   │
   │   /cloud/bare-metal|kubernetes|   /ai/agentic-workflow-editor  │
   │          ray|confidential-compute /ai/training                 │
   │   /cloud/id/account/settings      /ai/api-keys                 │
   │   /cloud/id/funds                                              │
   │                                                                │
   │  Переход cloud↔ai = полная перезагрузка страницы,              │
   │  второй cookie-баннер, повторный WorkOS refresh                │
   └───────────────┬──────────────────────────┬─────────────────────┘
                   │                          │
                   ▼                          ▼
     api.io.solutions/v1/*          api.taas.io.solutions/v1/taas/*
     (io-cloud, io-user,            (jobs, usage, base-models,
      notifications, api-keys,       datasets) — на момент обхода
      auth/user-uuid)                падает на CORS с обоих доменов

     cloud.io.net/api/*  — тонкий BFF: workos/refresh, competitor-pricing
```

### 1.1 Аутентификация
- **WorkOS** (`POST /api/workos/refresh` при каждом монтировании), reCAPTCHA v3 на всех страницах.
- Идентификатор пользователя — UUID (`io_id`), по нему строятся все REST-пути: `/v1/io-cloud/users/{uuid}/resources`, `/balances`.

### 1.2 Слой данных (кабинет)
- Все запросы — XHR (axios-подобный клиент с `toFriendlyError`), без GraphQL/WebSocket. Статусы кластеров — polling.
- Пагинация в query: `?status=all&archived=false&page=1&page_size=20`.
- BFF `cloud.io.net/api/competitor-pricing` отдаёт цены конкурентов (AWS, Scaleway, Massed Compute, ori, LeaderGPU) для отрисовки «зачёркнутая цена, -76%».
- `localStorage` хранит `SAVED_DEPLOYMENT_CONFIGS` — черновики визарда переживают перезагрузку.

### 1.3 Телеметрия и маркетинг
Общая для обоих приложений: GTM/GA, Amplitude + Experiments (`EXP_*` в localStorage — фичефлаги/A-B), Sentry (`sentry.javascript.react 8.54`), Contentsquare, Pingdom RUM, пиксели Facebook / LinkedIn / Twitter / Reddit / AdRoll (он же поставщик cookie-баннера). Кабинет дополнительно пишет **rrweb session replay**. Лендинг — HubSpot и Ahrefs.
Итог: ~50 сторонних запросов на переход в кабинете, ~130 на загрузку лендинга.

---

## 2. Кабинет (`cloud.io.net` / `ai.io.net`)

### 2.1 Стек

| Слой | Детали |
|---|---|
| Сборка | Vite; имя чанка = `Компонент-hash-commit-env.js`; версия зашита в DOM (`Version - commit:de01952, env:…`) |
| UI-kit | Tailwind + shadcn/ui (Radix-примитивы, `data-state`, `cn()`-композиция классов), `data-testid` на ключевых элементах |
| Шрифты | Inter 400–700 (body 16px) с Google Fonts; Geist во всех 9 весах + variable с собственного CDN; Work Sans импортирован и не используется |
| Специальные библиотеки | `@xyflow/react` (React Flow) — workflow-редактор; `mapbox-gl` 2.6.1 — CSS грузится на каждой странице, карта не встречается; `ldrs` (web-component лоадеры) с jsdelivr |
| Роутинг | Клиентский; неизвестный роут = пустой экран (404-страницы нет); `/cloud/virtual-machines/create` перехватывается как `/:id` |

### 2.2 Code-splitting
Чанкинг настроен **по файлу компонента**, а не по роуту: при открытии дашборда грузится ~30 чанков вида `StatusPing`, `USDIcon`, `ClockIcon`, `Pill`, `Box`, `TabField`, `RenameModal`… Главный `index-*.js` при этом **5.4 MB** несжатого JS, CSS — 259 KB. Каждый переход между разделами — 20–30 мелких запросов.

### 2.3 Дизайн-система

#### Токены (`:root`, формат shadcn — HSL без `hsl()`)
```
--background        0 0% 100%        фон карточек/контента
--container         0 0% 97.6%       фон страницы (#F9F9F9)
--foreground        0 0% 3.9%        текст (#0A0A0A)
--muted-foreground  215 16% 47%      вторичный текст
--border            0 0% 90%         (#E6E6E6)
--radius            .5rem
--primary           222 47% 11%      (почти чёрный)
--destructive       0 84% 60%
--ring              222 84% 4.9%
```
Плюс группа `--button-*` (primary/default/outline/ghost × bg/foreground/border/accent) — кнопки токенизированы отдельно и полностью.

Бренд-цвета: `--blue #0096FF`, `--blue-light #0065E1`, `--blue-dark-600 #0070F3` (Vercel-синий). Синий — **только** для CTA внутри флоу (Deploy / Continue / Pay & Deploy); все прочие кнопки чёрные или белые с бордером.

Серые: **20 несемантических значений** `--gray-light-100…1900`, `--gray-dark-100…2200`. Есть аномалии вроде `--gray-dark-600: #fff` (в light-теме) — токены явно добавлялись по мере надобности.

#### Тёмная тема
Класс `.dark` переопределяет все токены (фон чистый `#000`, карточки `#0A0A0A`, бордер 12%); поддержка есть в каждом компоненте (`dark:` варианты). **Переключателя в UI нет**, `localStorage.theme` не читается — мёртвая функциональность, вероятно выключена флагом.

#### Типографика
- Шкала: 10 / 12 / 14 / 16 / 20 / 32px.
- Утилитарные классы-роли: `title-h4`, `title-h5`, `subtext-small`, `subtext-x-small`, `label-small`. Заголовок страницы — `<h2>` 32px/500 (единственный `h1` на странице отсутствует).
- Кнопки: 14px/500, высота 32px (compact) или 40px (CTA), `tracking-[-0.6%]`.

#### Геометрия и тени
- Радиусы: 4 / 6 / 8 / 16px; пилюли 100px. Карточки-контейнеры 16px, кнопки 8px, инпуты 6–8px.
- Одна тень на всю систему: `-8px 8px 8px rgba(0,0,0,.02)` — лево-вниз, почти невидима. Применяется к content-панели, кнопкам, карточкам.
- Content-панель: `md:rounded-xl md:border md:bg-background md:shadow` — на десктопе контент лежит «карточкой» поверх серого фона, на мобильном сливается с фоном.

#### Иконки
Собственный набор (компоненты `*Icon`), плюс SVG из Figma-экспорта (`/assets/images/figma/…`). Флаги стран — emoji/SVG рядом с локацией.

### 2.4 Каркас приложения (app shell)

```
┌ aside 271px ──────────┬─ топбар ───────────────────────────────────────┐
│ логотип  [collapse]   │ [Credits $0.00 ▾][+]  [Deploy Cluster ▾]        │
│                       │                    [Notifications] [Docs ↗]    │
│ Dashboard             ├────────────────────────────────────────────────┤
│ ▾ Cloud               │                                                │
│    Virtual Machine    │   content-панель (белая, 16px radius)          │
│    Container          │                                                │
│    ▸ Managed Services │                                                │
│ ▸ Intelligence        │                                                │
│                       │                                                │
│ ┌ Get Started 1/6 ─┐  │                                                │
│ │ ☐ Buy credits    │  │                                                │
│ │ ☐ … ☑ Explore   │  │                                                │
│ └──────────────────┘  │                                                │
│ Credits Balance $0 [+]│                                                │
│ [avatar] Имя      ▾   │                                                │
└───────────────────────┴────────────────────────────────────────────────┘
```
- `<768px`: сайдбар → бургер, открывается как полноэкранный `dialog`.
- Меню аккаунта (снизу сайдбара): Billing History / Settings / User Acknowledgment / Support / Log Out.
- **Docs в топбаре контекстная** — ссылка меняется в зависимости от раздела (`/docs/guides/clouds/start-using-io-cloud`, `/docs/guides/intelligence/api-keys-and-secrets`).
- Notifications — правый drawer на всю высоту, пустое состояние текстом.

### 2.5 Экраны

#### Dashboard
Приветствие по имени → «Featured Products» (горизонтальная карусель из 6 карточек: VM, Container — «Deploy now»; Bare Metal, Confidential, K8s, Ray — «Request access») → «Your Resources» (таблица Name / Chip-GPUs / Time Remaining / Status, toggle «Show completed», кнопка Deploy Cluster) → нижний ряд: Credits Usage (прогресс 0%), «Your Io Intelligence» (план Standard), Launch AI Studio (Models / Agents / API Keys).
Иллюстрация в пустой таблице — полупрозрачный рендер «шестерёнки».

#### Virtual Machine — список
Hero-заголовок + подзаголовок + компактная синяя CTA «Deploy Virtual Machine» → «Featured VMs 🔥» — карусель `HardwareCard` (первая карточка чёткая, остальные заблюрены под кнопкой «Explore More») → «Your Virtual Machines».

`HardwareCard`:
```
┌ ● Recommended ────────────────────────────────┐   (бейдж на рамке, рамка оранжевая)
│ [nvidia] H100  [x8]                        ○  │   (radio для выбора)
├───────────────────────────────────────────────┤
│ Price                       $63.30/hr $15.16/hr│  (зачёркнутая цена конкурента, -76%)
│ VRAM Per Card                          141 GB │
│ Storage                                 2.9 TB│
│ Supplier                              Partner │
│ Location                     🇺🇸 United States │
│                              [More details]   │  → модалка: vCPU, RAM, NVLink,
└───────────────────────────────────────────────┘     interconnect, storage
```
Бейджи: «Recommended» (оранжевый), «Most Deployed» (чёрный).

#### Virtual Machine — визард (2 шага)
1. **Select VM Processor**: поиск по модели, фильтры Regions / GPU Family, сортировка (Recommended / Cheaper First / Expensive First / Min VRAM / Max VRAM), toggle «Show unavailable», кнопка расширенных фильтров. Список из ~60 карточек без виртуализации. Sticky-футер «Back to VMs / Continue».
2. **Configure & Deploy**: SSH-ключи (пусто → «Add key», ссылка в Settings), имя VM (автогенерация `vmaas - 93c1122c`), Duration (stepper `− 1 +` + select Hours/Days/Weeks/Months), Total Cost, «Paying with IO Credits / Balance», inline-предупреждение о нехватке баланса (не блокирует). Футер «Back / Pay & Deploy».

Степпер: номер в круге + подпись + полоска прогресса под шагом; пройденные шаги — светло-синие.

#### Container — визард (4 шага)
1. **Recipes** — сетка карточек-моделей: лого, название, 3 тега (`MoE`, `8× H200`, `Tool calling`), статус «Compatible hardware sold out» (карточка задизейблена), описание с указанием VRAM. Последняя карточка — «Custom Deployment». Рецепты: DeepSeek V4 Flash, Kimi K2.6/K2.7 Code, Qwen 3.6 27B FP8 (vLLM), Qwen/Gemma/Ministral в GGUF (llama.cpp для консумерских GPU), GLM 5.2 2-bit, MiniMax H3 (ComfyUI, видео).
2. **Select Processor** — тот же список карточек с автофильтром `VRAM ≥ 40 GB`. **Шаг пропускается автоматически** при выборе рецепта — см. баги.
3. **Configure** — Public/Private image; Container Image (`vllm/vllm-openai:v0.21.0`); Start Command (JSON-массив с `--tool-call-parser qwen3_coder --reasoning-parser qwen3 --speculative-config.method mtp …`); Port `8000`; Health Check (`/health`, «recommended for this recipe», свёрнутый редактор; текст: публичный URL публикуется после прохождения проверки); Environment Variables (Normal Variables, `VLLM_RPC_TIMEOUT=60000`, кнопка «Add», иконка удаления).
4. **Deploy** — имя; «Number of GPUs per Container» и «Replicas» (степперы); блок **«When we can't get all your GPUs»** — три radio-карточки с иконками: *Keep running, refund the rest* / *Keep running, extend the rest* (Recommended, преселект) / *Fail cluster completely*; Duration; Total Cost; оплата.

#### Managed Services (Bare Metal / Confidential / K8s / Ray)
Все четыре — один экран «Request Managed Service»: select типа кластера, First/Last Name (предзаполнены), Organization*, Email*, «Submit Request». Продукта за ними нет.

#### Intelligence → Models
Центрированный заголовок «Model Pricing — Transparent Pay As You Go pricing — no hidden markup», поиск, вертикальный список карточек: лого + имя, три метрики (Context / Input per M / Output per M), у части — «Try Model». ~35 моделей (DeepSeek, GLM, Qwen, Kimi, MiniMax, gemma, gpt-oss, Llama, Mistral).

#### Intelligence → Chat
Левая панель History (сворачиваемая), селектор модели (gpt-oss-120b Recommended), крупный слоган по центру, инпут «Ask me anything» с «+» (вложения) и стрелкой отправки.

#### Intelligence → Agents
Каталог готовых агентов-интеграций (Linear, GitHub, YouTube, Confluence, Web Search, News, Calendar/Zoom, ClickUp, Jira…): тег-иконка сервиса, название, описание. Полноширинные карточки, разделённые линиями.

#### Intelligence → Agentic Workflow Editor
Левая панель «Workflows» (поиск, «Add New Flow», список), канва React Flow с точечной сеткой, хлебные крошки `Workflows / Untitled`, «Auto Saved», кнопка Run; пустое состояние — карточка «Start New Flow: Components / Import from YAML»; контролы zoom/fit/lock; нижняя панель «Run Flow Outcome». На ширине <900 канва не адаптируется, обрезается.

#### Intelligence → Training (Beta)
Страница-список (`/ai/training`): hero-карточка «Train New Model — Beta — Start Training 0/0 left», список Training Jobs с поиском — на момент обхода **ERROR**.
Форма (`ai.io.net/ai/training/new`), одна длинная страница из radio-карточек:
1. Метод: SFT / Reward Modeling / PPO / DPO / «Controlled Tuning Optimization (KL-regularized, experimental)».
2. Base Model: Choose Model / Link HuggingFace; select (пуст).
3. Dataset: Base / Custom; select (пуст).
4. Стиль: Simple / Advanced. Advanced раскрывает аккордеоны: **Basic** (LR 1e-4, Epochs 3, Max Grad Norm 1, Max Samples 1000, Compute Type bf16/fp16, Cutoff 1024 [слайдер 1–131072], Batch 2 [1–1024], Grad Accum 8, Val size 0, LR Scheduler — 11 вариантов), **Extra**, **Freeze Tuning**, **LoRA** (rank 8, alpha 16, dropout 0, LoRA+ ratio 0.1, чекбоксы Create new adapter / rsLoRA / DoRA / PiSSA — все `on`, LoRA modules, Additional modules), **GaLore**, **Apollo**, **BAdam**.
Набор полей и терминология 1:1 повторяют WebUI LLaMA-Factory. Каждое поле — слайдер + числовой инпут + тултип-«i».

#### API Keys and Secrets
Hero-карточка «How It Works? → Read API Docs», секции «Your API Keys» и «Your Secrets» с иллюстрированными пустыми состояниями и дублирующей CTA внизу.

#### Settings (`/cloud/id/account/settings`)
Аватар + имя, «New Picture»; Account Details (Full Name*, Email — readonly, Timezone — select из ~120 зон), disabled «Save Changes» до изменения; SSH Keys (список, «Add Key»); «Delete your account» — только через письмо в поддержку.

#### Billing History (`/cloud/id/funds`)
«Withdraw IO Credits» / «Buy IO Credits»; Transactions с поиском по Cluster ID, date-picker, Filters; пустой список — **без empty-state**.
Модалка **Buy IO Credits**: Amount, «1 IO Credit = 1 USD», Payment Method — две radio-карточки: Credit Card (Visa/MC/Amex, «5% Fee») vs USD-stablecoin («0% Fee», преселект), три буллита выгод, «Cancel / Click to pay».

### 2.6 Применённые UX/UI-паттерны (кабинет)

| Паттерн | Где | Оценка |
|---|---|---|
| Hero-блок страницы (H2 + серый подзаголовок + одна CTA) | все разделы | ✅ единообразно |
| Карточка железа с key-value таблицей и «More details» → модалка | VM, Container, лендинг | ✅ переиспользуется везде |
| Бейдж на рамке карточки (Recommended / Most Deployed) | списки GPU | ✅ хорошо читается |
| Зачёркнутая цена конкурента + «-N%» | карточки GPU | ⚠️ убедительно, но источник цены не показан |
| Заблюренная вторая карточка + «Explore More» | Featured VMs | ⚠️ дизайн-приём, скрывающий контент |
| Степпер с прогресс-полосой, sticky-футер Back/Continue | визарды | ✅ |
| Radio-карточки с иконкой, заголовком, описанием и «Recommended» | политика аллокации, метод обучения, оплата | ✅ лучший компонент системы |
| Number-stepper + select единиц | Duration, GPUs, Replicas | ✅ |
| Inline-предупреждение вместо блокировки | нехватка баланса | ✅ не мешает дойти до оплаты |
| Автогенерированные имена (`vmaas - 93c1122c`) | VM/Container | ✅ |
| Рецепты (модель → готовая конфигурация) | Container | ✅ главная ценность |
| Health-check как first-class поле с объяснением | Container | ✅ |
| Чеклист онбординга Get Started x/6 в сайдбаре | глобально | ⚠️ счётчик расходится (1/6 vs 0/6), битая ссылка |
| Контекстная ссылка Docs в топбаре | глобально | ✅ |
| Drawer уведомлений | глобально | ✅ |
| Иллюстрированные пустые состояния | API Keys, Dashboard | ✅ но не везде (Billing — нет) |
| Слайдер + числовой инпут + тултип на каждом гиперпараметре | Training | ⚠️ верно для 5 полей, шум для 40 |
| Simple / Advanced режим формы | Training | ✅ идея; ❌ Advanced — свалка |
| Лид-форма вместо продукта | Managed Services | ⚠️ 4 из 6 «Featured Products» — не продукты |
| Тёмная тема | CSS | ❌ реализована, не включена |

### 2.7 Найденные дефекты

| # | Где | Что |
|---|---|---|
| 1 | Training (оба домена) | `api.taas.io.solutions/v1/taas/{jobs,usage,base-models,datasets}` — CORS-preflight без `Access-Control-Allow-Origin`; список задач, моделей и датасетов пуст, экран «ERROR» |
| 2 | Container wizard | При выборе рецепта шаг «Select Processor» пропускается; преселектится первая Recommended-карточка H100 ×8 ($28.69/ч), хотя рецепт «fits a single H100». Пользователь попадает на «Pay & Deploy» с 8 GPU |
| 3 | `/cloud/virtual-machines/create` | Роут перехватывается как `/:id`; пользователю показан сырой Pydantic-текст «Input should be a valid UUID… found `r` at 2» |
| 4 | Любой неизвестный роут | Пустой экран, 404-страницы нет |
| 5 | Get Started | Счётчик 1/6 в сайдбаре и 0/6 в мобильном меню; «Connect your cluster» → `/cloud/clusters` → редирект на дашборд |
| 6 | Settings | В URL утекает `?fileName=Untitled` из workflow-редактора |
| 7 | Dashboard | `resources` запрашивается дважды (page_size 20 и 100) |
| 8 | Billing History | Пустой список без empty-state |
| 9 | Container list | Подзаголовок «Featured Containers» — «virtual machines» (copy-paste); CTA на VM компактная, на Container — full-width |
| 10 | Workflow Editor | Console: `Cannot read properties of null (reading 'style')` |
| 11 | Глобально | mapbox-gl.css, Geist ×9 весов, Work Sans грузятся везде; тёмная тема без переключателя |

### 2.8 Производительность и доступность

- Главный бандл 5.4 MB JS + 259 KB CSS + ~30 микрочанков на переход; DOMContentLoaded ≈ 0.9 с, load ≈ 1.6 с (тёплый кэш); ~3100 DOM-нод на списке VM (60 карточек без виртуализации).
- ~50 сторонних запросов и rrweb-запись на каждой странице.
- A11y: единственный landmark `<main>`, skip-link нет, 20 `<img>` без `alt`, серый текст `#9B9B9B` на белом — контраст **2.6:1** (AA — 4.5). Focus-ring через `focus-visible:ring-2` есть на кнопках. `lang="en"`.

---

## 3. Лендинг (`io.net`)

### 3.1 Стек

| Слой | Детали |
|---|---|
| Фреймворк | Next.js App Router, SSR, `next/font` (Inter), `next/image` — только для 23 из 484 картинок |
| Стили | Tailwind; кастомные переменные-размеры (`--width-container: 1200px`, `--height-hero: 507px`, `--leading-p: 28px`) — токены раскладки, не цвета |
| Шрифты | **Geist** 300–700 для h1/h2/h3/p; Inter — body-дефолт; «Controller» (5 весов) — display-шрифт для лого-надписей |
| Анимации | Ни GSAP, ни Framer Motion, ни Lottie, ни three.js; CSS-transition + 4 `<canvas>` для фоновых линий; «3D»-объекты — статичные PNG |
| Маркетинг | HubSpot (формы, баннер, трекинг), Amplitude Experiments, Ahrefs, GTM, пиксели FB/LinkedIn/Twitter/Reddit/AdRoll — 32 сторонних хоста |

### 3.2 Структура страницы

```
[ Announcement-бар: «Access +1,000 GPUs from $0.30/hr»  [Try Now →] (градиент) ]
[ Хедер (sticky): io.net | Product ▾  Company ▾  Docs ↗ | [Get Started] ]

 1. Hero      логотип-куб с градиентной рамкой, «провода» к иконкам OpenAI/Meta/Ray/K8s
              H1 40px «70% cheaper than AWS. Zero waitlists.»  p 18px  [Get Started]
 2. Trusted by   marquee логотипов (Solana, Aptos, Leonardo.Ai, Filecoin, baseten…)
 3. Focus on building, not your runway   4 бенефита в bento-сетке с иллюстрациями
 4. Instant access at 70% lower costs    карусель HardwareCard (RTXPRO6000, H200, A100, H100)
 5. GPUs everywhere, on demand           таблица цен 4 GPU × (VM / CaaS / Bare Metal)
                                         «Prices correct as of 30th July 2025»
 6. Deploy how you want                  3 изометрических блока: Containers / VMs / Ray
 7. Open source AI platform              ЖИВОЙ чат-инпут с селектором модели (GLM-4.5-Air)
                                         marquee провайдеров моделей
 8. Everything you need                  Workflow Editor / Marketplace / TaaS с превью
 9. Финальный CTA                        ряд плиток-логотипов, «Get off the waitlist…»
10. Футер                                SOC2/GDPR, «All services are online», IO Ventures,
                                         Products / Company / Resources
```

Мега-меню **Product**: три колонки Cloud (Containers, VM, Request GPUs) / Intelligence (Marketplace, Agentic Workflow, TaaS) / Ecosystem (Explorer) с описанием под каждым пунктом. **Company**: About / Resources + промо-карточка «Tokenomics → Read the Litepaper».

Крипто-слой (IO Coin, Staking, Worker, Explorer) вынесен в футер и Ecosystem — сознательный репозиционинг в «GPU-cloud для AI-стартапов».

### 3.3 Дизайн-язык

- **Палитра почти монохромная**: белый фон, `#0A0A0A` заголовки, `#404040` абзацы, `#5B5B5D` вторичный (4.6:1 — AA проходит), `#F5F5F5` подложки, `#DADBE3` бордеры. Цвет только в градиенте синий→фиолетовый: логотип, announcement-кнопка, один диагональный фон. Всего **7 градиентных элементов** на страницу.
- **Кнопки**: чёрные, 8px radius, 40px, 14/500 — тот же primary, что в кабинете.
- **Типографика**: h1/h2 Geist 40px/500 (28px на мобильном), h3 20px, p 18/28. Заголовки центрированы, две строки, вторая — «панч» (`Zero waitlists.`).
- **Иллюстративный стиль**: белые скруглённые «плитки» с мягкой тенью, соединённые тонкими линиями-«проводами» (метафора кластера); изометрические 3D-рендеры серверов; всё — PNG @2x.
- **Ритм**: контейнер 1200px, секции по ~120px вертикального отступа, чередование «текст слева + картинка справа» и «центр».
- **Копирайт**: короткий, с цифрами (70%, $2.19 vs $12.29, «$10k+ monthly»), повторяющий «no waitlists / no lock-in / no hidden fees».

### 3.4 UX/UI-паттерны (лендинг)

| Паттерн | Оценка |
|---|---|
| Announcement-бар с ценой «от» | ⚠️ «$0.30/hr» — это RTX 4090, не сказано |
| Живой inference-виджет на лендинге | ✅ лучший proof-of-product на странице |
| Общий `HardwareCard` с продуктом | ✅ преемственность лендинг → кабинет |
| Таблица цен по продуктам + дата актуальности | ✅ честно |
| Мега-меню с описанием под пунктом | ✅ объясняет CaaS/TaaS без клика |
| Marquee логотипов (клонирование DOM ×6–8) | ⚠️ 392 дубля `<img>` |
| Bento-сетка бенефитов с «скриншот-кнопкой» внутри | ✅ |
| Контекстный Docs в хедере, статус «All services are online» в футере | ✅ |
| **Автопопап «Request Managed Service»** через несколько секунд | ❌ прерывающий лид-ген на весь экран |
| Число «70%» ×6 на странице; первая видимая цена — $28.69/ч | ⚠️ обещание и первая цена конфликтуют |
| CLS 0, sticky-хедер с blur | ✅ |

### 3.5 Производительность и доступность

| Метрика | Значение |
|---|---|
| Собственный JS | 863 KB (39 чанков) |
| CSS | 26 KB |
| Картинки | **6.5 MB**, PNG — 5.3 MB |
| Самый тяжёлый файл | `/articles/tokenomics.png` — **4.5 MB** (промо в мега-меню Company, грузится на главной всегда) |
| `divider-3.png` | 485 KB декоративный разделитель |
| `<img>` в DOM | 484, из них 392 дубликата (marquee), 194 без `alt` |
| DOM | 5 655 нод |
| Сторонние | 130 запросов / 285 KB |
| TTFB / DCL / Load / CLS | 364 мс / 1.1 с / 2.7 с / 0 |
| Мобильный | без горизонтального скролла; H1 28px |

---

## 4. Сквозные дизайн-подходы

1. **Чёрный primary, синий только для «денежного» действия.** На лендинге все CTA чёрные; в кабинете чёрный — default, синий — Deploy/Continue/Pay. Цвет = «здесь начинается оплата».
2. **Карточка как базовая единица**: и железо, и рецепты, и методы обучения, и способы оплаты — radio-карточки с иконкой, заголовком, описанием, бейджем. Одна ментальная модель на всё.
3. **Key-value таблица внутри карточки** (label слева серым, value справа чёрным) — для спецификаций GPU и цен моделей.
4. **Hero-блок на каждой странице кабинета** повторяет структуру секции лендинга: заголовок → серый подзаголовок → одна CTA.
5. **Токены shadcn как контракт** — кабинет полностью на HSL-переменных, поэтому тёмная тема существует «бесплатно» (пусть и выключена).
6. **Прогрессивное раскрытие**: «More details» → модалка; «Expand to edit» для health-check; Simple/Advanced в Training; свёрнутые аккордеоны гиперпараметров.
7. **Честность в пустых состояниях** — иллюстрация + текст + повторная CTA (кроме Billing).
8. **Онбординг-чеклист** в сайдбаре как постоянный элемент.
9. **Документация рядом** — контекстная ссылка в топбаре, «Learn more in the training documentation» inline-баннером.
10. **Маркетинг проникает в продукт**: сравнение с конкурентами в карточке GPU, «Request access» на продукты, которых нет, Featured-карусели с 🔥.

---

## 5. Рекомендации для Looma

Опираются на текущие экраны `web/src/screens/{Landing,Models,Training,Nodes,Ray,Topology}.tsx`.

### Взять
1. **Recipes в Models.** Каталог «модель → готовая vLLM-конфигурация» (image, команда с parser/reasoning/spec-decode, ENV, health-check, VRAM-требование) поверх `looma_stage`. Показывать требуемое железо честно и **не** преселектить лишние карты.
2. **Training: Simple / Advanced с ограниченным Advanced.** Метод — radio-карточки с пояснением *для чего* (SFT → DPO по плану); Advanced — только LoRA rank/alpha/dropout, LR, epochs, cutoff, batch/grad-accum, precision (= «precision as client choice»). Остальные PEFT-методы не выносить.
3. **Политика частичной аллокации** — три явных варианта с Recommended, если в Ray/Nodes есть сценарий «не все узлы поднялись».
4. **Стоимость/ресурсы пересчитываются inline** в sticky-футере визарда.
5. **Контекстная ссылка на доки** в топбаре; **статус сервисов** в футере.
6. **Health-check как обязательное поле деплоя** с текстом «эндпоинт публикуется после прохождения».
7. **Один компонент карточки ресурса** на лендинге и в кабинете.
8. **Живой inference-виджет** на лендинге поверх своего vLLM.
9. **Монохром + один градиентный акцент**, ≤7 градиентов на страницу.
10. **Дата актуальности** у любой таблицы цен.

### Не повторять
- 20 несемантических серых токенов — держать ≤6 с ролями (`bg`, `surface`, `border`, `text`, `text-muted`, `text-disabled`).
- 5 MB главного бандла и чанкинг по компоненту — split по роутам.
- Тёмная тема без переключателя; неизвестный роут без 404; пустые списки без empty-state.
- Серый текст с контрастом 2.6:1.
- Сырой текст ошибок бэкенда в UI.
- Автопопап с лид-формой; 4.5 MB PNG в меню; клонирование логотипов в DOM для marquee (делать CSS-анимацией одного набора).
- Одно и то же обещание («70%») шесть раз, расходящееся с первой видимой ценой.

---

## Приложение A. Роуты кабинета

| Путь | Экран | Домен |
|---|---|---|
| `/cloud/home` | выбор продукта (VM / Container / Managed) | cloud |
| `/cloud/dashboard` | Dashboard | cloud |
| `/cloud/virtual-machines` | список VM | cloud |
| `/cloud/virtual-machines/:id` | VM (роут перехватывает `/create`) | cloud |
| `/cloud/container` | список контейнеров | cloud |
| `/cloud/bare-metal`, `/kubernetes`, `/ray`, `/confidential-compute` | лид-форма Managed Service | cloud |
| `/cloud/clusters` | → редирект на dashboard | cloud |
| `/cloud/id/account/settings` | Settings | cloud |
| `/cloud/id/funds` | Billing History | cloud |
| `/ai/app` | Chat | ai |
| `/ai/models` | Model Pricing | ai |
| `/ai/agents` | каталог агентов | ai |
| `/ai/agentic-workflow-editor` | React Flow редактор | ai |
| `/ai/training`, `/ai/training/new` | Training (Beta) | ai |
| `/ai/api-keys` | API Keys and Secrets | оба |

## Приложение B. API-эндпоинты, замеченные в сессии

```
POST cloud.io.net/api/workos/refresh
GET  cloud.io.net/api/competitor-pricing
GET  api.io.solutions/v1/auth/user-uuid?io_id=
GET  api.io.solutions/v1/io-user/wallets
GET  api.io.solutions/v1/io-user/payments/subscription?price_model=INTELLIGENCE_MONTHLY
GET  api.io.solutions/v1/io-cloud/users/{uuid}/balances
GET  api.io.solutions/v1/io-cloud/users/{uuid}/resources?status=all&archived=false&page=1&page_size=20|100
GET  api.io.solutions/v1/api-keys/
GET  api.io.solutions/v1/notifications/?seen=false&page=1&page_size=5
GET  api.taas.io.solutions/v1/taas/usage            (CORS fail)
GET  api.taas.io.solutions/v1/taas/jobs?offset=0&sort_by=created_at&sort_order=desc&limit=20  (CORS fail)
GET  api.taas.io.solutions/v1/taas/base-models      (CORS fail)
GET  api.taas.io.solutions/v1/taas/datasets         (CORS fail)
```

## Приложение C. Токены кабинета (полный `:root`, light)

```
--background 0 0% 100%          --foreground 0 0% 3.9%
--card 0 0% 100%                --card-foreground 222.2 84% 4.9%
--popover 0 0% 100%             --popover-foreground 222.2 84% 4.9%
--popover-accent 0 0% 98%       --popover-accent-border 0 0% 98%
--primary 222.2 47.4% 11.2%     --primary-foreground 210 40% 98%
--secondary 0 0% 98%            --secondary-foreground 0 0% 45.1%
--muted 210 40% 96.1%           --muted-foreground 215.4 16.3% 46.9%
--accent 0 0% 97.6%             --accent-foreground 222.2 47.4% 11.2%
--destructive 0 84.2% 60.2%     --destructive-foreground 210 40% 98%
--border 0 0% 90%               --input 0 0% 100%   --input-border 0 0% 90%
--ring 222.2 84% 4.9%           --radius .5rem
--separator 0 0% 85%            --container 0 0% 97.6%   --highlight #FDFDFD
--button-primary 0 0% 0%        --button-primary-foreground 0 0% 100%  --button-primary-border 0 0% 20%
--button-default 0 0% 100%      --button-default-foreground 0 0% 0%    --button-default-border 0 0% 90%
--button-outline 0 0% 98%       --button-outline-border 0 0% 90%
--button-ghost-accent 0 0% 97.6%
--blue #0096ff  --blue-light #0065e1  --blue-light-300 #3291ff  --blue-dark-600 #0070f3
--gray #9b9b9b  --gray-light #f7f7f7  --gray-light-200 #e8e8e8 … --gray-light-1900 #262626
--gray-dark-100 #2b2c39 … --gray-dark-2200 #737373
--scrollbar-color #ccc transparent
--status-animation-duration 2s  --status-animation-delay 1s
```
