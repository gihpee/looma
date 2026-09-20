/** Витрина @looma/ui — только в dev-сборке. Здесь проверяются обе темы и
 *  все состояния компонентов до того, как их разнесут по экранам. */
import { useState } from "react";
import { Cpu, MessageSquare, Plus, Server } from "lucide-react";
import {
  Badge, Bar, Button, Card, CardBody, CardFoot, CardHead, Chip, CodeBlock, Confirm, Displacement, Empty, Field,
  FilePick, IconButton, Input, InputAffix, KeyValue, Logo, Modal, Notice, NumberStepper, Page, PageHead,
  ProgressPhase, RadioCard, SearchBox, Segmented, Select, Stat, StateBadge, Steps, Tabs, Textarea, ThemeToggle,
  Toggle, WizardFooter, useToast, NetworkFabric, rubles, money, plural, duration,
} from "@looma/ui";

const NODES = [
  { id: "nv3-8b66ed", state: "free" as const, gpus: 2 }, { id: "nv3-758b97", state: "free" as const, gpus: 1 },
  { id: "GTX", state: "inference" as const, gpus: 2 }, { id: "nv2", state: "inference" as const, gpus: 4 },
  { id: "nv3-f5c93f", state: "busy" as const, gpus: 1 }, { id: "work-MS-7C60", state: "busy" as const, gpus: 2 },
];

export function UiShowcase() {
  const toast = useToast();
  const [seg, setSeg] = useState<"a" | "b" | "c">("a");
  const [tab, setTab] = useState<"x" | "y">("x");
  const [n, setN] = useState(6);
  const [on, setOn] = useState(true);
  const [radio, setRadio] = useState("displace");
  const [modal, setModal] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [q, setQ] = useState("");
  const [want, setWant] = useState(3);

  return (
    <Page>
      <PageHead title="Витрина компонентов" text="Светлая и тёмная тема, все состояния." actions={<ThemeToggle />} />

      <section className="lu-stack">
        <div className="lu-label">Кнопки</div>
        <div className="lu-row lu-row--wrap">
          <Button kind="primary" icon={<Plus size={16} />}>Арендовать кластер</Button>
          <Button kind="ink">Открыть чат</Button>
          <Button>Открыть в чате</Button>
          <Button kind="ghost">Отмена</Button>
          <Button kind="danger">Снять</Button>
          <Button disabled>Запустить</Button>
          <Button kind="primary" size="sm">Создать ключ</Button>
          <Button size="sm">Детали</Button>
          <Button kind="primary" size="lg" glow>Начать</Button>
          <IconButton label="Меню"><Cpu size={17} /></IconButton>
          <Button kind="secondary" onClick={() => toast("ok", "кластер поднимается")}>тост ok</Button>
          <Button kind="secondary" onClick={() => toast("bad", "стадия 1 не ответила на train_collect за 600 с")}>тост bad</Button>
        </div>
      </section>

      <section className="lu-stack">
        <div className="lu-label">Состояния</div>
        <div className="lu-row lu-row--wrap">
          <StateBadge value="running" label="работает" /><StateBadge value="loading" label="грузит веса" />
          <StateBadge value="failed" label="упала" /><StateBadge value="cancelled" label="снята" />
          <Badge tone="info" dot={false}>Рекомендуем</Badge><Badge tone="ink">Чаще берут</Badge>
          <Chip>6 часов</Chip><Chip>Ray 2.58.0</Chip>
          <Logo name="Qwen3-32B" /><Logo name="NVIDIA" size="lg" />
        </div>
        <div style={{ maxWidth: 420 }}><ProgressPhase phase="грузит веса" percent={82} /></div>
        <div style={{ maxWidth: 420 }}><Bar percent={40} tone="ok" /></div>
        <div className="lu-row lu-row--wrap">
          <Notice tone="ok">Баланс 48 200 ₽ покрывает. Ставка фиксируется сейчас.</Notice>
          <Notice tone="warn">Не хватает 1 узла — платформа подвинет свою модель.</Notice>
          <Notice tone="bad">Стадия 1 не ответила за 600 с.</Notice>
        </div>
      </section>

      <section className="lu-stack">
        <div className="lu-label">Stat · сетки</div>
        <div className="lu-grid lu-grid--stats">
          <Stat label="Баланс" value={money(4_820_000).replace(" ₽", "")} unit="₽" sub="хватит на ~3 суток" />
          <Stat label="Расход за месяц" value="12 640" unit="₽" sub="↓ 18% к прошлому" subTone="ok" />
          <Stat label="Сейчас работает" value="2" unit="кластера" sub="4 GPU · ~310 ₽/час" />
          <Stat label="Токенов" value="1,8" unit="M" sub="Qwen3-32B · 2 ключа" />
        </div>
      </section>

      <section className="lu-stack">
        <div className="lu-label">Поля</div>
        <div className="lu-grid lu-grid--3">
          <Field label="Базовая модель" hint="bf16-чекпоинт: квантованные не подходят" htmlFor="f1" required>
            <Input id="f1" placeholder="Qwen/Qwen3-8B" mono />
          </Field>
          <Field label="Часов" hint="не больше 24 — потолок на одну аренду" htmlFor="f2">
            <NumberStepper id="f2" value={n} onChange={setN} min={1} max={24} />
          </Field>
          <Field label="Точность" htmlFor="f3">
            <Select id="f3"><option>bf16 (полная)</option><option>nf4 (QLoRA)</option></Select>
          </Field>
          <Field label="Ставка" htmlFor="f4"><InputAffix id="f4" defaultValue="120" suffix="₽/ч" mono /></Field>
          <Field label="Датасет" hint="JSONL: в каждой строке {messages: [...]}" htmlFor="f5"><FilePick id="f5" label="выбрать .jsonl" accept=".jsonl" /></Field>
          <Field label="Ошибка" error="почта или пароль не подошли" htmlFor="f6"><Input id="f6" aria-invalid="true" defaultValue="a@b" /></Field>
          <Field label="Системный промпт" htmlFor="f7"><Textarea id="f7" defaultValue="Отвечай кратко, по-русски." /></Field>
        </div>
        <div className="lu-row lu-row--wrap">
          <Toggle checked={on} onChange={setOn} label="Продлевать автоматически" />
          <SearchBox value={q} onChange={setQ} placeholder="Поиск по кластерам, моделям, ключам" kbd="⌘K" />
          <Segmented value={seg} onChange={setSeg} options={[{ value: "a", label: "Кластер" }, { value: "b", label: "Инференс" }, { value: "c", label: "Обучение" }]} />
          <Segmented soft value={seg} onChange={setSeg} options={[{ value: "a", label: "Активные" }, { value: "b", label: "Все" }]} />
        </div>
        <Tabs value={tab} onChange={setTab} options={[{ value: "x", label: "Классы GPU · 5" }, { value: "y", label: "Модели · 6" }]} />
      </section>

      <section className="lu-stack">
        <div className="lu-label">Карточки</div>
        <div className="lu-grid lu-grid--3">
          <Card>
            <CardHead>
              <div className="lu-row"><Logo name="NV" /><b>RTX 4090</b><Chip>×2</Chip></div>
              <span className="lu-radiocard__dot" />
            </CardHead>
            <CardBody>
              <KeyValue rows={[
                { k: "Цена", v: <><s className="lu-strike">310 ₽</s> <b>120 ₽/час</b></> },
                { k: "VRAM на карту", v: "24 GB" }, { k: "RTT до соседа", v: "43 мс" },
                { k: "Экономия", v: <span style={{ color: "var(--ok-text)", fontWeight: 500 }}>−61% к Selectel</span> },
              ]} />
            </CardBody>
            <CardFoot><a href="#">Арендовать →</a></CardFoot>
          </Card>
          <div className="lu-stack">
            <RadioCard name="p" value="displace" checked={radio === "displace"} onChange={setRadio} recommended
                       title="Подвинуть модели платформы" text="Недостающие узлы забираем из-под инференса; старт сразу." />
            <RadioCard name="p" value="wait" checked={radio === "wait"} onChange={setRadio}
                       title="Ждать свободные" text="Счёт не идёт, пока ждём." />
            <RadioCard name="p" value="partial" checked={radio === "partial"} onChange={setRadio} icon={<Server size={16} />}
                       title="Взять сколько есть" text="Стартуем на свободных; за недостающие не платите."
                       meta={<><span className="lu-mono">≈ 20 GB</span><span style={{ color: "var(--ok-text)" }}>вместят 4 узла из 6</span></>} />
          </div>
          <Card dashed>
            <Empty title="Кластеров пока нет" action={<Button kind="primary" size="sm">Арендовать</Button>}>
              Аренда считается по GPU-часам. Первый кластер поднимается за пару минут.
            </Empty>
          </Card>
        </div>
      </section>

      <section className="lu-stack">
        <div className="lu-label">Сеть · вытеснение</div>
        <div className="lu-grid lu-grid--2">
          <Card pad><div className="lu-row lu-row--between" style={{ marginBottom: 10 }}><b>Сеть сейчас</b><span className="lu-mono lu-muted" style={{ fontSize: 12 }}>{plural(NODES.length, ["узел", "узла", "узлов"])} · 2 свободно</span></div><NetworkFabric nodes={NODES} /></Card>
          <Card pad>
            <div className="lu-row lu-row--between" style={{ marginBottom: 10 }}><b>Что произойдёт с сетью</b><div style={{ width: 140 }}><NumberStepper value={want} onChange={setWant} min={1} max={6} /></div></div>
            <Displacement nodes={NODES} want={want}>Счёт идёт всё время, пока кластер держит ресурс.</Displacement>
          </Card>
        </div>
      </section>

      <section className="lu-stack">
        <div className="lu-label">Визард · таблица · код</div>
        <Card panel>
          <div style={{ padding: "20px 24px 0" }}>
            <Steps current={2} steps={[{ label: "Узлы", summary: "2 × A30, 1 × RTX 4090" }, { label: "Параметры", summary: "6 часов" }, { label: "Проверка" }]} />
          </div>
          <div style={{ padding: 24 }} className="lu-muted">… содержимое шага …</div>
          <WizardFooter costLabel={`Итого за ${duration(6 * 3600)} · 3 узла`} cost={rubles(1860)} costNote="баланс покрывает" back={() => {}} next={() => setModal(true)} nextLabel="Запустить кластер" sticky={false} />
        </Card>
        <Card>
          <div className="lu-table--wrap">
            <table className="lu-table lu-table--cards">
              <thead><tr><th>Кластер</th><th>Узлов</th><th>Осталось</th><th>Состояние</th><th /></tr></thead>
              <tbody>
                <tr><td data-label="Кластер" className="lu-mono">group-e3f8869a3c</td><td data-label="Узлов" className="lu-num">2</td><td data-label="Осталось">4 ч 12 мин</td><td data-label="Состояние"><StateBadge value="running" label="работает" /></td><td><a href="#">открыть</a></td></tr>
                <tr><td data-label="Кластер" className="lu-mono">group-6c2628879d</td><td data-label="Узлов" className="lu-num">1</td><td data-label="Осталось">—</td><td data-label="Состояние"><StateBadge value="pending" label="поднимается" /></td><td><a href="#">открыть</a></td></tr>
              </tbody>
            </table>
          </div>
        </Card>
        <CodeBlock code={`curl https://api.loomafloat.ru/v1/chat/completions \\\n  -H "Authorization: Bearer $LOOMA_KEY" \\\n  -d '{"model":"Qwen3-32B","messages":[{"role":"user","content":"Привет"}]}'`} />
        <div className="lu-row"><Button onClick={() => setModal(true)} icon={<MessageSquare size={16} />}>Модалка</Button><Button kind="danger" onClick={() => setConfirm(true)}>Подтверждение</Button></div>
      </section>

      {modal && (
        <Modal title="Развернуть модель" subtitle="Шаг 2 из 4 · Движок и точность" onClose={() => setModal(false)}
               footer={<><div className="lu-wizfoot__cost"><small>Оценка · 1 узел A30</small><b>95 ₽/час</b></div><Button kind="primary" onClick={() => setModal(false)}>Далее: узлы</Button></>}>
          <div className="lu-stack">
            <RadioCard name="m" value="a" checked onChange={() => {}} recommended title="vLLM · bf16" text="Батчинг и prefix-cache, полная точность." />
            <RadioCard name="m" value="b" checked={false} onChange={() => {}} title="vLLM · fp8" text="Вдвое меньше памяти." />
            <Field label="Имя для API" htmlFor="m1" hint="так модель будет называться в запросе"><Input id="m1" mono defaultValue="qwen3-8b" /></Field>
          </div>
        </Modal>
      )}
      {confirm && <Confirm title="Снять кластер?" body="Счёт остановится, модели платформы вернутся на узлы." action="Снять" onClose={() => setConfirm(false)} onConfirm={() => toast("ok", "кластер снят")} />}
    </Page>
  );
}
