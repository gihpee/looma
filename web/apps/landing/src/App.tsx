/** Публичная главная.
 *
 *  Что здесь НЕ делается и почему. Не выдумываются цифры: цены, конкуренты и
 *  модели приходят из прайса, который заполняет админ, а скорость демо
 *  измеряется прямо здесь. Нет отзывов — их не существует, а придуманные
 *  видны сразу. Нет живых чисел сети (узлы, свободные карты): доступные карты
 *  клиент видит в визарде аренды, когда они ему нужны. */
import { useState } from "react";
import { ArrowRight, Check, Cpu, Layers, Lock, Radio, Shuffle, Terminal } from "lucide-react";
import { usePublicPricing, type GpuClass } from "@looma/api";
import { Badge, Card, CardBody, CardFoot, CardHead, Chip, KeyValue, LinkButton, Logo, Mark, Segmented, rubles } from "@looma/ui";
import "./landing.css";
import { Announcement, CONSOLE, DOCS, Header } from "./Header";
import { Demo } from "./Demo";
import { cheapestRival, fmtTimes, heroClass, kop, savePercent, savingsRange, timesCheaper } from "./pricing";

export function App() {
  const pricing = usePublicPricing();
  const p = pricing.data ?? null;
  const rivals = p?.competitors ?? { selectel: "Selectel", aws: "AWS" };
  const range = savingsRange(p);

  return (
    <div className="lp">
      <Announcement pricing={p} />
      <Header />
      <main>
        <Hero p={p} rivals={rivals} range={range} />
        <Products />
        <How />
        <Prices p={p} rivals={rivals} range={range} />
        <section className="lp-section" id="demo">
          <div className="lp-wrap lp-demo">
            <div className="lp-section__head" style={{ marginBottom: 0, flexDirection: "column", alignItems: "flex-start" }}>
              <span className="lp-section__kicker">Попробуйте сами</span>
              <h2>Модель разрезана по домашним машинам. Оцените скорость.</h2>
              <p>Ответ приходит с той модели, которая сейчас отвечает в сети: несколько узлов держат по части слоёв. Скорость и время первого токена считаются здесь, в вашем браузере.</p>
            </div>
            <Demo />
          </div>
        </section>
        <Owners />
        <section className="lp-section">
          <div className="lp-wrap lp-final">
            <Mark size={40} className="lu-muted" />
            <h2>Полотно уже соткано</h2>
            <p>Сеть работает, счёт считается, модели отвечают. Осталось выдать вам ключ.</p>
            <LinkButton kind="primary" size="lg" glow href={CONSOLE}>Войти в консоль</LinkButton>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}

/* --------------------------------------------------------------------- hero */
function Hero({ p, rivals, range }: { p: ReturnType<typeof usePublicPricing>["data"] | null; rivals: Record<string, string>; range: [number, number] | null }) {
  const cls = heroClass(p);
  return (
    <section className="lp-hero">
      <Loom />
      <div className="lp-wrap lp-hero__grid">
        <div className="lp-hero__copy">
          <Badge tone="info" dot={false} className="lp-hero__tag"><i className="lu-badge__dot" style={{ color: "var(--accent)" }} />looma-compute · looma-intelligence</Badge>
          <h1 className="lu-display-1" style={{ margin: 0 }}>Мощность,<br />когда она нужна</h1>
          <p>Считаем на распределённой сети GPU. Берите кластер под свой код или инференс готовых и своих моделей — платите только за использованное.</p>
          <div className="lp-hero__cta">
            <LinkButton kind="primary" size="lg" glow href={CONSOLE}>Начать</LinkButton>
            <LinkButton size="lg" href="#demo" icon={undefined}>Попробовать инференс <ArrowRight size={16} /></LinkButton>
          </div>
          <div className="lp-hero__checks">
            <span><Check size={14} strokeWidth={2.5} />без минимума и договора</span>
            <span><Check size={14} strokeWidth={2.5} />OpenAI-совместимый API</span>
            <span><Check size={14} strokeWidth={2.5} />свои модели без заявок</span>
          </div>
        </div>
        {cls ? <Compare cls={cls} rivals={rivals} /> : (
          <Card panel raised className="lu-card--pad-lg">
            <div className="lu-label">Цены</div>
            <div className="lu-title" style={{ marginTop: 6 }}>{range ? `В ${fmtTimes(range[0])}–${fmtTimes(range[1])} раза дешевле облаков` : "Одна ставка за GPU-час"}</div>
            <p className="lu-dim" style={{ margin: "8px 0 0", fontSize: 14 }}>Прайс по классам карт и моделям — в секции «Цены». Списание по минутам, ставка фиксируется при аренде.</p>
          </Card>
        )}
      </div>
    </section>
  );
}

function Compare({ cls, rivals }: { cls: GpuClass; rivals: Record<string, string> }) {
  const rows = Object.entries(cls.competitors ?? {}).filter(([, v]) => v > 0).sort((a, b) => a[1] - b[1]);
  const best = cheapestRival(cls);
  const times = best ? timesCheaper(cls, best.key) : null;
  const share = best ? Math.round((cls.rate_kopecks / best.kopecks) * 100) : 100;
  return (
    <div className="lp-compare">
      <div className="lu-card__head">
        <div className="lu-row"><Logo src={cls.logo_url} name={cls.vendor} /><b style={{ fontSize: 16 }}>{cls.name}{cls.vram_gb ? ` ${cls.vram_gb} GB` : ""}</b><Chip>×1</Chip></div>
        <span className="lu-muted" style={{ fontSize: 12 }}>₽ за GPU-час</span>
      </div>
      <div style={{ padding: "4px 20px" }}>
        <div className="lp-compare__row">
          <span className="lp-compare__us"><Mark size={22} />Looma Float</span>
          <span className="lp-compare__price">{rubles(kop(cls.rate_kopecks))}</span>
        </div>
        {rows.map(([key, v]) => (
          <div key={key} className="lp-compare__row">
            <span className="lu-dim">{rivals[key] ?? key}</span>
            <span className="lp-compare__them"><span className="lp-compare__more">+{Math.round((v / cls.rate_kopecks - 1) * 100)}%</span><s>{rubles(kop(v))}</s></span>
          </div>
        ))}
      </div>
      <div className="lu-card__foot" style={{ flexDirection: "column", alignItems: "stretch", gap: 8 }}>
        <div className="lp-compare__bar"><i style={{ width: `${share}%`, background: "var(--accent)" }} /><i style={{ flexGrow: 1, background: "var(--border)" }} /></div>
        <div className="lu-row lu-row--between" style={{ fontSize: 12, color: "var(--text-3)" }}>
          <span>{times && best ? `В ${fmtTimes(times)} раза дешевле ${rivals[best.key] ?? best.key} на ${cls.name}` : "цена за GPU-час"}</span>
          <a href="#prices">все классы карт →</a>
        </div>
      </div>
    </div>
  );
}

/** Станок: приглушённые нити фоном. Одна нить от каждого узла — не орнамент,
 *  а факт архитектуры: у узла нет входящих портов, он сам тянет одну нить. */
function Loom() {
  return (
    <svg className="lp-hero__loom" viewBox="0 0 1440 700" preserveAspectRatio="none" fill="none" aria-hidden="true">
      <path d="M-20 300 C 300 280, 600 350, 900 310 S 1300 260, 1460 300" stroke="var(--accent-bright)" strokeOpacity=".18" strokeWidth="1.5" />
      <path d="M-20 400 C 320 420, 640 360, 960 400 S 1320 450, 1460 410" stroke="var(--accent-bright)" strokeOpacity=".12" strokeWidth="1.5" />
      <path d="M-20 520 C 300 490, 700 570, 1000 520 S 1300 480, 1460 530" stroke="var(--accent-bright)" strokeOpacity=".1" strokeWidth="1.5" />
      <path d="M-20 640 C 260 670, 620 610, 940 650 S 1300 680, 1460 640" stroke="var(--accent-bright)" strokeOpacity=".08" strokeWidth="1.5" />
      {[[380, 284], [905, 311], [1240, 272], [640, 366], [1120, 426], [700, 565]].map(([x, y], i) => (
        <circle key={i} cx={x} cy={y} r={i % 2 ? 3 : 4} fill="var(--accent-bright)" fillOpacity={.5 - i * .05} />
      ))}
    </svg>
  );
}

/* ----------------------------------------------------------------- продукты */
function Products() {
  return (
    <section className="lp-section lp-section--bg" id="compute">
      <div className="lp-wrap">
        <div className="lp-section__head">
          <div><span className="lp-section__kicker">Продукты</span><h2>Разворачивайте что хотите и когда хотите</h2></div>
        </div>
        <div className="lp-products">
          <Card panel className="lp-product">
            <div className="lp-product__tag">looma-compute<small>аренда GPU</small></div>
            <h3>Ray-кластер под вашу задачу</h3>
            <p>Выбираете карты и часы — кластер поднимается на домашних машинах сети. Если свободных узлов не хватает, платформа подвинет свои модели и вернёт их, когда аренда закончится.</p>
            <div className="lp-product__cta">
              <LinkButton kind="ink" href={`${CONSOLE}/compute/clusters`}>Арендовать кластер</LinkButton>
              <a className="lu-btn lu-btn--ghost" href="#fit" style={{ color: "var(--accent)" }}>Что здесь поедет →</a>
            </div>
          </Card>
          <Card panel className="lp-product" id="inference">
            <div className="lp-product__tag">looma-intelligence<small>инференс · модели · обучение</small></div>
            <h3>Готовые и свои модели по API</h3>
            <p>Чат и OpenAI-совместимый эндпоинт с ценой за токен. Загрузите модель с HuggingFace, дообучите LoRA на своих данных и разверните адаптер — без согласований и заявок.</p>
            <div className="lp-product__cta">
              <LinkButton kind="ink" href={`${CONSOLE}/intelligence/chat`}>Открыть чат</LinkButton>
              <a className="lu-btn lu-btn--ghost" href="#models" style={{ color: "var(--accent)" }}>Каталог моделей →</a>
            </div>
          </Card>
        </div>
      </div>
    </section>
  );
}

/* --------------------------------------------------------------- как устроено */
function How() {
  return (
    <section className="lp-section" id="how">
      <div className="lp-wrap">
        <div className="lp-section__head">
          <div><span className="lp-section__kicker">Как устроено</span><h2>Три факта вместо обещаний</h2><p>Сеть собрана из домашних машин. Вот что из этого следует — и что мы сделали, чтобы это работало.</p></div>
        </div>
        <div className="lp-facts">
          <Card className="lp-fact"><span className="lp-fact__icon"><Radio size={18} /></span><h3>Одно исходящее соединение</h3><p>У машины-поставщика нет и не будет входящих портов: это домашний компьютер за роутером, который никто не настраивает. Узел сам открывает канал наружу, и всё идёт обратно по нему же — команды, активации модели, порт до кластера.</p></Card>
          <Card className="lp-fact"><span className="lp-fact__icon"><Shuffle size={18} /></span><h3>Платформа — первый клиент своей сети</h3><p>Пока прямого арендатора нет, карты занимает инференс. Приходит клиент за кластером — модели уступают ему узлы и возвращаются, когда аренда кончилась.</p></Card>
          <Card className="lp-fact"><span className="lp-fact__icon"><Lock size={18} /></span><h3>Чужой код в песочнице</h3><p>Задача идёт под отдельным пользователем, в своём каталоге, с ограничениями по памяти и процессам. Владелец машины сдаёт мощность, а не доступ к себе.</p></Card>
        </div>
        <Card className="lp-diagram" id="fit">
          <Diagram />
        </Card>
      </div>
    </section>
  );
}

function Diagram() {
  const nodes = [80, 240, 400, 560, 720];
  return (
    <svg viewBox="0 0 800 200" role="img" aria-label="Пять узлов, от каждого одна нить к оркестратору; нити продолжаются полотном">
      <text x="400" y="22" textAnchor="middle" fontSize="12" fill="var(--text-3)" fontFamily="var(--font-mono)">оркестратор · loomafloat.ru:9000</text>
      <rect x="330" y="32" width="140" height="28" rx="6" fill="var(--accent-soft)" stroke="var(--accent)" />
      <text x="400" y="51" textAnchor="middle" fontSize="12" fill="var(--accent)" fontWeight="600" fontFamily="var(--font-sans)">одна точка входа</text>
      {nodes.map((x, i) => (
        <g key={x}>
          <path d={`M${x} 160 C ${x} 110, 400 110, 400 60`} stroke="var(--accent)" strokeOpacity=".55" strokeWidth="1.5" fill="none" />
          <rect x={x - 46} y="160" width="92" height="28" rx="6" fill="var(--surface)" stroke="var(--border-2)" />
          <text x={x} y="178" textAnchor="middle" fontSize="11" fill="var(--text-2)" fontFamily="var(--font-mono)">узел {i + 1} · NAT</text>
          <circle cx={x} cy="160" r="3" fill="var(--accent)" />
        </g>
      ))}
      <text x="400" y="118" textAnchor="middle" fontSize="11" fill="var(--text-3)" fontFamily="var(--font-sans)">← команды · активации слоёв · порт кластера →</text>
    </svg>
  );
}

/* ------------------------------------------------------------------- цены */
function Prices({ p, rivals, range }: { p: ReturnType<typeof usePublicPricing>["data"] | null; rivals: Record<string, string>; range: [number, number] | null }) {
  const [tab, setTab] = useState<"gpu" | "models" | "training">("gpu");
  const classes = (p?.gpu_classes ?? []).filter((c) => c.rate_kopecks > 0);
  const models = p?.models ?? [];
  const rivalNames = Object.values(rivals).join(" и ");
  return (
    <section className="lp-section lp-section--bg" id="prices">
      <div className="lp-wrap">
        <div className="lp-section__head">
          <div>
            <span className="lp-section__kicker">Цены</span>
            <h2>{range ? `В ${fmtTimes(range[0])}–${fmtTimes(range[1])} раза дешевле ${rivalNames}.` : "Одна ставка за GPU-час, цена за токен для инференса."}<br />Без минимума и договора.</h2>
            <p>Одна ставка за GPU-час для кластера и обучения, цена за токен для инференса. Считаем по факту: кластер — пока держит ресурс, модель — за выданные токены.</p>
          </div>
          <div className="lp-section__aside">
            <Segmented value={tab} onChange={setTab} options={[{ value: "gpu", label: "Кластер" }, { value: "models", label: "Инференс" }, { value: "training", label: "Обучение" }]} />
            {p?.as_of && <span className="lu-muted" style={{ fontSize: 12 }}>Цены актуальны на {new Date(p.as_of).toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" })} · конкуренты — публичные прайсы</span>}
          </div>
        </div>

        {tab === "gpu" && (classes.length ? (
          <div className="lp-gpu">
            {classes.map((c) => <GpuCard key={c.id} c={c} rivals={rivals} />)}
          </div>
        ) : <Card dashed className="lu-card--pad-lg lu-muted">Прайс по классам карт появится, как только его опубликует администратор.</Card>)}

        {tab === "models" && (
          <div className="lp-inference" id="models">
            <Card>
              <div className="lu-card__head"><div><b style={{ font: "600 18px var(--font-display)" }}>Инференс — ₽ за 1M токенов</b><div className="lu-muted" style={{ fontSize: 13 }}>платформенные модели; свои — по ставке кластера за GPU-час</div></div><a href={`${CONSOLE}/intelligence/models`} style={{ fontSize: 13, fontWeight: 500 }}>все модели →</a></div>
              <div className="lp-models__head"><span>Модель</span><span>Контекст</span><span>Вход</span><span>Выход</span><span /></div>
              {models.length === 0 && <div className="lu-empty__text" style={{ padding: 20 }}>Каталог появится после публикации прайса.</div>}
              {models.map((m) => (
                <div key={m.id} className="lp-models__row">
                  <span className="lp-models__name"><Logo src={m.logo_url} name={m.id} /><span style={{ minWidth: 0 }}><b>{m.id}</b><small className="lu-hide-desktop">{m.context ? `${Math.round(m.context / 1024)}K контекст` : ""}</small></span></span>
                  <span className="lp-models__price lu-hide-mobile">{m.context ? `${Math.round(m.context / 1024)}K` : "—"}</span>
                  <span className="lp-models__price lu-hide-mobile">{rubles(kop(m.price_in))}</span>
                  <span className="lp-models__price lu-hide-mobile">{rubles(kop(m.price_out))}</span>
                  <span className="lp-models__price lu-hide-desktop">{kop(m.price_in)} / {kop(m.price_out)}</span>
                  <a className="lu-hide-mobile" href={`${CONSOLE}/intelligence/chat?model=${encodeURIComponent(m.id)}`} style={{ fontSize: 13, fontWeight: 500, textAlign: "right" }}>в чат →</a>
                </div>
              ))}
            </Card>
            <HowWeCount />
          </div>
        )}

        {tab === "training" && (
          <div className="lp-inference" id="training">
            <Card pad="lg">
              <b style={{ font: "600 18px var(--font-display)" }}>Обучение LoRA</b>
              <p className="lu-dim" style={{ margin: "8px 0 12px", fontSize: 14 }}>
                {p?.training_rate_kopecks ? <>Считается по отдельной ставке <b>{rubles(kop(p.training_rate_kopecks))} за GPU-час</b>.</> : "Считается по той же ставке за GPU-час, что и кластер."} Датасет, база и точность — ваши; оценка стоимости и времени показывается в форме до запуска, адаптер после обучения разворачивается в один клик.
              </p>
              <KeyValue rows={[{ k: "Методы", v: "SFT сейчас · DPO скоро" }, { k: "Точность базы", v: "bf16 · nf4 (QLoRA)" }, { k: "Результат", v: "adapter_config.json + adapter_model.safetensors" }]} />
            </Card>
            <HowWeCount />
          </div>
        )}
      </div>
    </section>
  );
}

function GpuCard({ c, rivals }: { c: GpuClass; rivals: Record<string, string> }) {
  const save = savePercent(c);
  const best = cheapestRival(c);
  return (
    <Card className={`lp-gpu__card${c.featured ? " lp-gpu__card--featured" : ""}`}>
      {c.featured && <span className="lp-gpu__flag">Чаще берут</span>}
      <CardHead style={c.featured ? { paddingTop: 28 } : undefined}>
        <div className="lu-row"><Logo src={c.logo_url} name={c.vendor} /><b style={{ fontSize: 16 }}>{c.name}</b></div>
        {c.vram_gb > 0 && <Chip>{c.vram_gb} GB</Chip>}
      </CardHead>
      <CardBody>
        <div className="lu-muted" style={{ fontSize: 12 }}>₽ за GPU-час</div>
        <div className="lp-gpu__price"><b>{kop(c.rate_kopecks)}</b>{best && <s className="lu-strike">{kop(best.kopecks)}</s>}{save !== null && save > 0 && <Badge tone="ok" dot={false}>−{save}%</Badge>}</div>
        <div style={{ marginTop: 10 }}>
          <KeyValue rows={Object.entries(rivals).map(([key, label]) => ({ k: label, v: c.competitors?.[key] ? rubles(kop(c.competitors[key])) : <span className="lu-muted">нет такой карты</span> }))} />
        </div>
      </CardBody>
      <CardFoot><a href={`${CONSOLE}/compute/clusters`} style={{ fontSize: 13, fontWeight: 500 }}>Арендовать →</a></CardFoot>
    </Card>
  );
}

function HowWeCount() {
  return (
    <div className="lp-dark">
      <h3>Как считаем</h3>
      <p>Кредиты 1 : 1 к рублю. Кластер списывается по минутам, пока держит ресурс; токены — по факту выдачи. Ставка фиксируется в момент аренды — повышение цены завтра не перепишет вчерашний счёт.</p>
      <a href={`${DOCS}/billing`}>подробнее о биллинге →</a>
    </div>
  );
}

/* ---------------------------------------------------------------- владельцы */
function Owners() {
  return (
    <section className="lp-section lp-section--bg" id="owners">
      <div className="lp-wrap lp-owners">
        <div className="lp-section__head" style={{ marginBottom: 0, flexDirection: "column", alignItems: "flex-start" }}>
          <span className="lp-section__kicker">Для владельцев машин</span>
          <h2>Подключить машину — одна команда</h2>
          <p>Ключ несёт внутри адрес оркестратора: ничего не вводить, ничего не пробрасывать. Узел сам звонит наружу, чужой код идёт в песочнице под отдельным пользователем, а вы решаете, когда отдать карту.</p>
          <LinkButton kind="ink" href={`${DOCS}/nodes`}>Как подключить <ArrowRight size={16} /></LinkButton>
        </div>
        <div className="lu-stack">
          <Card className="lp-fact"><span className="lp-fact__icon"><Terminal size={18} /></span><h3>Без входящих портов</h3><p>Роутер не трогаем. Узел открывает одно исходящее соединение и держит его.</p></Card>
          <Card className="lp-fact"><span className="lp-fact__icon"><Layers size={18} /></span><h3>Код в песочнице</h3><p>Отдельный пользователь, свой каталог, лимиты по памяти и процессам.</p></Card>
          <Card className="lp-fact"><span className="lp-fact__icon"><Cpu size={18} /></span><h3>Карта возвращается</h3><p>Снять машину из сети можно в любой момент — задачи переедут на другие узлы.</p></Card>
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------- футер */
function Footer() {
  const year = new Date().getFullYear();
  return (
    <footer className="lp-foot">
      <div className="lp-wrap">
        <div className="lp-foot__grid">
          <div className="lp-foot__col">
            <span className="lp-head__brand"><Mark size={22} />Looma Float</span>
            <span>Распределённая сеть GPU: инференс, обучение и кластеры на домашних машинах.</span>
          </div>
          <div className="lp-foot__col"><b>Продукты</b><a href="#compute">Ray-кластер</a><a href="#inference">Инференс</a><a href="#models">Модели</a><a href="#training">Обучение</a></div>
          <div className="lp-foot__col"><b>Сеть</b><a href="#how">Как устроено</a><a href="#owners">Подключить машину</a><a href="#prices">Цены</a></div>
          <div className="lp-foot__col"><b>Ресурсы</b><a href={DOCS}>Документация</a><a href={CONSOLE}>Консоль</a><a href="mailto:support@loomafloat.ru">support@loomafloat.ru</a></div>
        </div>
        <div className="lp-foot__bottom"><span>© {year} Looma Float</span><span>Цены и условия — в консоли; оферта — по запросу.</span></div>
      </div>
    </footer>
  );
}
