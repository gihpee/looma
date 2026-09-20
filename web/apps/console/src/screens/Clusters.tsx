/** looma-compute: аренда Ray-кластера. Список аренд, карточка кластера с
 *  инструкцией подключения и визард из трёх шагов. Стоимость считается до
 *  действия и показывается в футере на каждом шаге. */
import { useMemo, useState } from "react";
import { Link, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Plus } from "lucide-react";
import { useCapacity, useClusters, useDropPending, useRates, useRelease, useRent, type CapacityNode, type Cluster, type ShortfallPolicy } from "@looma/api";
import {
  Button, Card, CardBody, CardHead, Chip, CodeBlock, Confirm, Displacement, Empty, Field, Input, KeyValue, NetworkFabric,
  NumberStepper, Page, PageHead, RadioCard, Select, StateBadge, Steps, Textarea, WizardFooter, ago, dateTime, duration,
  money, plural, rubles, useToast,
} from "@looma/ui";
import { API_BASE, ErrorLine, clusterPerHour, clusterSpent } from "../lib";

export function Clusters() {
  return (
    <Routes>
      <Route index element={<ClusterList />} />
      <Route path="new" element={<RentWizard />} />
      <Route path=":groupId" element={<ClusterView />} />
    </Routes>
  );
}

/* ---------------------------------------------------------------- список */
function ClusterList() {
  const nav = useNavigate();
  const clusters = useClusters();
  const capacity = useCapacity();
  const drop = useDropPending();
  const rows = clusters.data?.clusters ?? [];
  const pending = clusters.data?.pending ?? [];
  const nodes = capacity.data?.nodes ?? [];
  return (
    <Page>
      <PageHead title="Кластеры" text="Ray-кластер под вашу задачу: платите за GPU-час, пока он держит ресурс."
                actions={<Button kind="primary" icon={<Plus size={16} />} onClick={() => nav("new")}>Арендовать кластер</Button>} />
      <ErrorLine error={clusters.error} />
      {pending.length > 0 && (
        <Card>
          <CardHead><b>Очередь ожидания</b><span className="lu-muted" style={{ fontSize: 12 }}>заявка живёт до 6 часов; счёт пойдёт со старта</span></CardHead>
          <table className="lu-table lu-table--cards">
            <thead><tr><th>Заявка</th><th>Узлов</th><th>Подана</th><th>Состояние</th><th /></tr></thead>
            <tbody>{pending.map((t) => (
              <tr key={t.id} style={{ opacity: t.state === "waiting" || t.state === "started" ? 1 : .6 }}>
                <td data-label="Заявка"><div style={{ display: "flex", flexDirection: "column" }}><span style={{ fontWeight: 500 }}>{t.label}</span><span className="lu-mono lu-muted" style={{ fontSize: 11 }}>{t.id}</span></div></td>
                <td data-label="Узлов" className="lu-num">{t.size}{t.granted != null && t.granted !== t.size ? ` → ${t.granted}` : ""}</td>
                <td data-label="Подана">{ago(t.created_at * 1000)}</td>
                <td data-label="Состояние"><StateBadge value={t.state === "waiting" ? "pending" : t.state === "started" ? "running" : t.state === "failed" ? "failed" : "cancelled"}
                  label={t.state === "waiting" ? `ждём (свободно было ${t.free_then})` : t.state === "started" ? "поднят" : t.state === "failed" ? `не вышло: ${t.error}` : t.state === "expired" ? "истекла" : "отменена"} /></td>
                <td>{t.state === "waiting" ? <Button size="sm" kind="ghost" disabled={drop.isPending} onClick={() => drop.mutate(t.id)}>отменить</Button>
                  : t.state === "started" && t.group_id ? <Link to={t.group_id} style={{ fontSize: 13, fontWeight: 500 }}>открыть</Link>
                  : <Button size="sm" kind="ghost" onClick={() => drop.mutate(t.id)}>скрыть</Button>}</td>
              </tr>))}</tbody>
          </table>
        </Card>
      )}
      <Card>
        {clusters.isLoading ? <div className="lu-loading">…</div> : rows.length === 0 ? (
          <Empty title="Кластеров пока нет" action={<Button kind="primary" size="sm" onClick={() => nav("new")}>Арендовать</Button>}>
            Выберите число узлов и часы — кластер поднимется на домашних машинах сети за пару минут.
          </Empty>
        ) : (
          <table className="lu-table lu-table--cards">
            <thead><tr><th>Кластер</th><th>Узлов × GPU</th><th>С</th><th>Натикало</th><th>Состояние</th><th /></tr></thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.group_id}>
                  <td data-label="Кластер"><div style={{ display: "flex", flexDirection: "column" }}><span style={{ fontWeight: 500 }}>{c.label || c.group_id}</span><span className="lu-mono lu-muted" style={{ fontSize: 11 }}>{c.group_id}</span></div></td>
                  <td data-label="Узлов × GPU" className="lu-num">{c.nodes} × {Math.max(1, Math.round((c.gpus || c.nodes) / Math.max(1, c.nodes)))}</td>
                  <td data-label="С">{dateTime(c.opened_at)}</td>
                  <td data-label="Натикало" className="lu-num">{money(clusterSpent(c), c.currency, 0)} <span className="lu-muted">· {rubles(Math.round(clusterPerHour(c) / 100))}/ч</span></td>
                  <td data-label="Состояние"><StateBadge value={c.alive ? "running" : "pending"} label={c.alive ? "работает" : "не отвечает"} /></td>
                  <td><Link to={c.group_id} style={{ fontSize: 13, fontWeight: 500 }}>открыть</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {rows.length > 0 && <p className="lu-muted" style={{ margin: 0, fontSize: 13 }}>Счёт идёт, пока кластер держит ресурс. Снятие возвращает платформе модели, которые были подвинуты ради этой аренды.</p>}
      {nodes.length > 0 && (
        <Card pad>
          <div className="lu-row lu-row--between" style={{ marginBottom: 10 }}><b>Сеть сейчас</b><span className="lu-mono lu-muted" style={{ fontSize: 12 }}>{plural(nodes.length, ["узел", "узла", "узлов"])} · {nodes.filter((n) => n.state === "free").length} свободно</span></div>
          <NetworkFabric nodes={nodes} only={["mine", "inference", "free", "busy"]} />
        </Card>
      )}
    </Page>
  );
}

/* --------------------------------------------------------------- карточка */
function ClusterView() {
  const { groupId = "" } = useParams();
  const nav = useNavigate();
  const toast = useToast();
  const clusters = useClusters();
  const release = useRelease();
  const [confirm, setConfirm] = useState(false);
  const c: Cluster | undefined = clusters.data?.clusters.find((x) => x.group_id === groupId);

  if (clusters.isLoading) return <div className="lu-loading">…</div>;
  if (!c) return <Page><Empty title="Такой аренды за вами не числится"><Link to="/compute/clusters">К списку кластеров</Link></Empty></Page>;

  const connect = `looma-connect --api ${API_BASE} --cluster ${c.group_id} --key $LOOMA_KEY\n# дальше в вашем коде: ray.init("ray://127.0.0.1:10001")`;
  return (
    <Page>
      <div><Link to="/compute/clusters" className="lu-row" style={{ fontSize: 13, color: "var(--text-3)", width: "fit-content" }}><ArrowLeft size={14} />Кластеры</Link></div>
      <PageHead title={c.label || c.group_id} text={<span className="lu-mono">{c.group_id}</span>}
                actions={<Button kind="danger" onClick={() => setConfirm(true)} disabled={release.isPending}>Снять кластер</Button>} />
      <div className="lu-grid lu-grid--side">
        <div className="lu-stack" style={{ gap: 16 }}>
          <Card pad>
            <KeyValue rows={[
              { k: "Состояние", v: <StateBadge value={c.alive ? "running" : "pending"} label={c.alive ? "работает" : "не отвечает"} /> },
              { k: "Узлов × GPU", v: `${c.nodes} × ${Math.max(1, Math.round((c.gpus || c.nodes) / Math.max(1, c.nodes)))}` },
              { k: "Открыт", v: `${dateTime(c.opened_at)} · ${ago(c.opened_at)}` },
              { k: "Ставка", v: `${money(c.per_hour, c.currency, 0)} за GPU-час · ${rubles(Math.round(clusterPerHour(c) / 100))}/час за кластер` },
              { k: "Натикало", v: <b>{money(clusterSpent(c), c.currency)}</b> },
            ]} />
          </Card>
          <Card>
            <CardHead><b>Подключиться</b><Chip>Ray Client</Chip></CardHead>
            <CardBody className="lu-stack">
              <p className="lu-dim" style={{ margin: 0, fontSize: 13 }}>Канал до кластера идёт через оркестратор: <code>looma-connect</code> открывает порт на вашем localhost, и Ray про NAT не узнаёт.</p>
              <CodeBlock code={connect} />
            </CardBody>
          </Card>
        </div>
        <Card pad>
          <b>Что дальше</b>
          <p className="lu-dim" style={{ fontSize: 13, margin: "8px 0 0" }}>Счёт идёт по минутам, пока кластер держит ресурс. Потолок одной аренды — 24 часа; снять можно в любой момент, модели платформы вернутся на узлы.</p>
        </Card>
      </div>
      {confirm && (
        <Confirm title="Снять кластер?" action="Снять" body={<>Счёт остановится. Задачи на кластере будут прерваны, а подвинутые модели платформы вернутся на узлы.</>}
                 onClose={() => setConfirm(false)}
                 onConfirm={() => release.mutate(c.group_id, { onSuccess: () => { toast("ok", "кластер снят"); nav("/compute/clusters"); }, onError: (e) => toast("bad", e.message) })} />
      )}
    </Page>
  );
}

/* ------------------------------------------------------------------ визард */
const STEPS = [{ label: "Узлы" }, { label: "Параметры" }, { label: "Проверка" }];
const MAX_HOURS = 24;

function byClass(nodes: CapacityNode[]) {
  const map = new Map<string, { name: string; vram: number; total: number; free: number; cls: string | null }>();
  for (const n of nodes) {
    const key = n.gpu_name || "прочее";
    const row = map.get(key) ?? { name: key, vram: n.vram_gb ?? 0, total: 0, free: 0, cls: n.gpu_class ?? null };
    row.total += 1; if (n.state === "free") row.free += 1;
    map.set(key, row);
  }
  return [...map.values()].sort((a, b) => b.free - a.free);
}

function RentWizard() {
  const nav = useNavigate();
  const toast = useToast();
  const capacity = useCapacity();
  const rates = useRates();
  const rent = useRent();
  const [step, setStep] = useState(0);
  const [size, setSize] = useState(2);
  const [hours, setHours] = useState(6);
  const [label, setLabel] = useState("");
  const [requirements, setRequirements] = useState("");
  const [rayVersion, setRayVersion] = useState("");
  const [policy, setPolicy] = useState<ShortfallPolicy>("displace");

  const nodes = capacity.data?.nodes ?? [];
  const free = nodes.filter((n) => n.state === "free").length;
  const classes = useMemo(() => byClass(nodes), [nodes]);
  const computeRate = rates.data?.rates.find((r) => r.resource === "looma-compute")?.per_hour ?? 0;
  const gpusPerNode = 1;
  const perHour = computeRate * size * gpusPerNode;
  const total = perHour * hours;
  const costLabel = `Итого за ${duration(hours * 3600)} · ${plural(size, ["узел", "узла", "узлов"])}`;
  const cost = computeRate ? rubles(Math.round(total / 100)) : "ставка не задана";
  const short = Math.max(0, size - free);

  const submit = () => rent.mutate(
    { size, hours, label: label.trim() || undefined, requirements: requirements.trim() || undefined, ray_version: rayVersion.trim() || undefined, policy },
    {
      onSuccess: (r) => {
        if (r.state === "waiting") { toast("ok", "заявка в очереди — кластер поднимется, когда освободятся узлы"); nav("/compute/clusters"); return; }
        if (r.granted != null && r.requested != null && r.granted < r.requested) toast("warn", `дали ${r.granted} из ${r.requested} узлов`);
        else toast("ok", `кластер ${r.group_id} поднимается`);
        nav(`/compute/clusters/${r.group_id}`);
      },
      onError: (e) => toast("bad", e.message),
    },
  );

  const summaries = [`${size} × ${gpusPerNode} GPU`, `${duration(hours * 3600)}${label ? ` · ${label}` : ""}`, ""];
  return (
    <Page>
      <div><Link to="/compute/clusters" className="lu-row" style={{ fontSize: 13, color: "var(--text-3)", width: "fit-content" }}><ArrowLeft size={14} />Кластеры</Link></div>
      <PageHead title="Арендовать Ray-кластер" text={<span>Шаг {step + 1} из 3 · {STEPS[step].label}. {step === 2 ? "Проверьте состав и стоимость — счёт пойдёт с момента, когда кластер поднимется." : "Стоимость считается сразу и видна внизу."}</span>} />
      <ErrorLine error={capacity.error ?? rates.error} />
      <Card panel style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <div style={{ padding: "20px 24px 0" }}><Steps steps={STEPS.map((s, i) => ({ ...s, summary: summaries[i] }))} current={step} /></div>
        <div style={{ padding: 24 }} className="lu-stack lu-stack--lg">
          {step === 0 && (
            <div className="lu-grid lu-grid--2">
              <div className="lu-stack lu-stack--lg">
                <Field label="Сколько узлов" hint="по одной карте с узла; какие именно машины — решает платформа, а не арендатор" htmlFor="size">
                  <NumberStepper id="size" value={size} onChange={setSize} min={1} max={Math.max(1, nodes.length)} />
                </Field>
                <div className="lu-stack" style={{ gap: 8 }}>
                  <div className="lu-label">Карты в сети сейчас</div>
                  {classes.length === 0 && <div className="lu-muted" style={{ fontSize: 13 }}>Нет узлов на связи.</div>}
                  {classes.map((c) => (
                    <div key={c.name} className="lu-row lu-row--between lu-card lu-card--pad" style={{ padding: "10px 14px" }}>
                      <span className="lu-row"><b>{c.name}</b>{c.vram > 0 && <Chip>{c.vram} GB</Chip>}</span>
                      <span className="lu-muted" style={{ fontSize: 13 }}>{c.free} свободно из {c.total}</span>
                    </div>
                  ))}
                </div>
              </div>
              <Card pad>
                <div className="lu-row lu-row--between" style={{ marginBottom: 10 }}><b>Что произойдёт с сетью</b><span className="lu-mono lu-muted" style={{ fontSize: 12 }}>{nodes.length} узлов · нужно {size}</span></div>
                <Displacement nodes={nodes} want={size} names={false}>Счёт идёт всё время, пока кластер держит ресурс.</Displacement>
              </Card>
            </div>
          )}
          {step === 1 && (
            <div className="lu-grid lu-grid--2">
              <Field label="Часов" hint={`не больше ${MAX_HOURS} — потолок на одну аренду`} htmlFor="hours"><NumberStepper id="hours" value={hours} onChange={setHours} min={1} max={MAX_HOURS} /></Field>
              <Field label="Метка" hint="чтобы найти кластер потом" htmlFor="label"><Input id="label" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="перебор-гиперпараметров" /></Field>
              <Field label="Библиотеки" hint="по строке на пакет, как в requirements.txt — ставятся на каждый узел" htmlFor="req"><Textarea id="req" mono value={requirements} onChange={(e) => setRequirements(e.target.value)} placeholder={"torch\nnumpy"} /></Field>
              <Field label="Версия Ray" hint="пусто — последняя" htmlFor="ray"><Select id="ray" value={rayVersion} onChange={(e) => setRayVersion(e.target.value)}><option value="">последняя</option><option value="2.58.0">2.58.0</option><option value="2.49.0">2.49.0</option></Select></Field>
            </div>
          )}
          {step === 2 && (
            <div className="lu-grid lu-grid--2">
              <div className="lu-stack lu-stack--lg">
                <div className="lu-stack">
                  <b>Состав</b>
                  <KeyValue rows={[
                    { k: "Узлов", v: `${size} × ${gpusPerNode} GPU` }, { k: "Длительность", v: duration(hours * 3600) },
                    { k: "Метка", v: label || <span className="lu-muted">без метки</span> }, { k: "Ray", v: rayVersion || "последняя" },
                    { k: "Библиотеки", v: requirements.trim() ? requirements.trim().split(/\n+/).join(", ") : <span className="lu-muted">нет</span> },
                  ]} />
                </div>
                <div className="lu-stack">
                  <b>Если узлов не хватит к старту</b>
                  <RadioCard name="policy" value="displace" checked={policy === "displace"} onChange={(v) => setPolicy(v as ShortfallPolicy)} recommended
                             title="Подвинуть модели платформы" text="Недостающие узлы забираем из-под инференса; платформа вернёт свои модели, когда аренда закончится. Старт сразу." />
                  <RadioCard name="policy" value="wait" checked={policy === "wait"} onChange={(v) => setPolicy(v as ShortfallPolicy)}
                             title="Ждать свободные" text="Заявка встаёт в очередь; кластер стартует, когда узлы освободятся (до 6 часов). Счёт не идёт, пока ждём." />
                  <RadioCard name="policy" value="partial" checked={policy === "partial"} onChange={(v) => setPolicy(v as ShortfallPolicy)}
                             title="Взять сколько есть" text="Стартуем сразу на свободных узлах; кластер выйдет меньше, платите только за него." badge={free > 0 && short > 0 ? <Chip>{free} из {size}</Chip> : undefined} />
                </div>
              </div>
              <div className="lu-stack lu-stack--lg">
                <Card pad style={{ background: "var(--bg)" }}>
                  <div className="lu-row lu-row--between" style={{ marginBottom: 10 }}><b>Что произойдёт с сетью</b><span className="lu-mono lu-muted" style={{ fontSize: 12 }}>{nodes.length} узлов · нужно {size}</span></div>
                  <Displacement nodes={nodes} want={size} names={false} />
                </Card>
                <Card pad>
                  <b>Стоимость</b>
                  <div style={{ marginTop: 8 }}>
                    <KeyValue rows={[
                      { k: `${size} × ${gpusPerNode} GPU · ${money(computeRate, "RUB", 0)}/ч · ${hours} ч`, v: computeRate ? money(total, "RUB", 0) : "—" },
                      { k: <b>{costLabel}</b>, v: <b style={{ font: "600 18px var(--font-display)" }}>{cost}</b> },
                    ]} />
                  </div>
                  <div style={{ marginTop: 10 }}>
                    {short === 0 ? <span className="lu-notice lu-notice--ok">Свободных узлов хватает. Ставка фиксируется сейчас; списание — по минутам.</span>
                      : policy === "displace" ? <span className="lu-notice lu-notice--warn">Свободно {free}, не хватает {short} — платформа подвинет свои модели.</span>
                      : policy === "wait" ? <span className="lu-notice lu-notice--info">Свободно {free}, не хватает {short} — заявка встанет в очередь, счёт пойдёт со старта.</span>
                      : <span className="lu-notice lu-notice--warn">Свободно {free} — кластер поднимется на {plural(free, ["узле", "узлах", "узлах"])}, итог ≈ {computeRate ? money(computeRate * free * gpusPerNode * hours, "RUB", 0) : "—"}.</span>}
                  </div>
                </Card>
              </div>
            </div>
          )}
        </div>
        <WizardFooter costLabel={costLabel} cost={cost} costNote={computeRate ? "ставка фиксируется при аренде" : undefined}
                      back={step > 0 ? () => setStep(step - 1) : undefined}
                      next={step < 2 ? () => setStep(step + 1) : submit}
                      nextLabel={step < 2 ? "Далее" : short > 0 && policy === "wait" ? "Встать в очередь" : "Запустить кластер"} nextDisabled={step === 0 && nodes.length === 0} busy={rent.isPending} />
      </Card>
    </Page>
  );
}
