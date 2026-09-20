/** Модели: платформенные (по цене за токен) и свои (на выделенных узлах по
 *  ставке за GPU-час). Визард деплоя: источник → движок и точность (пресеты
 *  с честным «вместят N узлов») → узлы → проверка. Узлы по именам клиент не
 *  выбирает — только сколько стадий. */
import { useEffect, useMemo, useState } from "react";
import { Link, Route, Routes, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowLeft, Plus } from "lucide-react";
import { describeModel, useCapacity, useDeploy, useDeployments, useModels, useRates, useTrainList, useUndeploy, type Deployment, type ModelDescription, type StageHealth } from "@looma/api";
import {
  Button, Card, CardBody, CardFoot, CardHead, Chip, Confirm, Empty, Field, Input, KeyValue, Logo, Notice, NumberStepper, Page, PageHead,
  ProgressPhase, RadioCard, SearchBox, Segmented, Select, StateBadge, Steps, WizardFooter, ago, bytes, money, rubles, useToast,
} from "@looma/ui";
import { ErrorLine } from "../lib";

export function Models() {
  return (
    <Routes>
      <Route index element={<ModelList />} />
      <Route path="deploy" element={<DeployWizard />} />
    </Routes>
  );
}

/** «running» — про процесс, а не про готовность отвечать. */
export function phase(s: StageHealth): [string, number] {
  if (s.state === "pending") return ["в очереди", 6];
  if (s.state === "provisioning") return ["окружение и веса", 30];
  if (s.state === "failed") return ["упала", 100];
  if (s.state === "cancelled") return ["снята", 100];
  if (s.state !== "running") return [s.state, 0];
  if (!s.stage) return ["стартует", 60];
  return s.ready ? ["готова", 100] : ["грузит веса", 82];
}
export function deploymentState(d: Deployment): { value: string; label: string } {
  if (!d.alive || d.stages.length === 0) return { value: "gone", label: "снята" };
  if (d.stages.some((s) => s.state === "failed")) return { value: "failed", label: "упала" };
  if (d.stages.every((s) => s.state === "cancelled")) return { value: "cancelled", label: "снята" };
  if (d.ready) return { value: "running", label: "отвечает" };
  return { value: "loading", label: "поднимается" };
}

function ModelList() {
  const nav = useNavigate();
  const toast = useToast();
  const models = useModels();
  const deps = useDeployments();
  const rates = useRates();
  const undeploy = useUndeploy();
  const [tab, setTab] = useState<"platform" | "mine">("platform");
  const [q, setQ] = useState("");
  const [removing, setRemoving] = useState<Deployment | null>(null);
  const platform = (models.data?.data ?? []).filter((m) => !m.mine && m.id.toLowerCase().includes(q.toLowerCase()));
  const mine = (deps.data?.deployments ?? []).filter((d) => d.label.toLowerCase().includes(q.toLowerCase()));
  const inferenceRate = rates.data?.rates.find((r) => r.resource === "looma-inference")?.per_hour ?? 0;
  useEffect(() => { if (mine.length && !platform.length && tab === "platform" && !models.isLoading) setTab("mine"); }, [mine.length, platform.length, models.isLoading, tab]);

  return (
    <Page>
      <PageHead title="Модели" text="Платформенные отвечают по цене за токен; свои — на выделенных узлах по ставке за GPU-час."
                actions={<Button kind="primary" icon={<Plus size={16} />} onClick={() => nav("deploy")}>Развернуть модель</Button>} />
      <ErrorLine error={models.error ?? deps.error} />
      <div className="lu-row lu-row--wrap">
        <Segmented value={tab} onChange={setTab} options={[{ value: "platform", label: `Платформенные · ${platform.length}` }, { value: "mine", label: `Мои · ${mine.length}` }]} />
        <SearchBox value={q} onChange={setQ} placeholder="Поиск по имени" />
      </div>

      {tab === "platform" && (platform.length === 0 ? (
        <Card><Empty title="Сейчас ни одна платформенная модель не отвечает">Каталог заполняется, когда в сети поднимаются модели платформы. Свою можно развернуть прямо сейчас.</Empty></Card>
      ) : (
        <div className="lu-grid lu-grid--cards">
          {platform.map((m) => (
            <Card key={m.id} style={{ display: "flex", flexDirection: "column" }}>
              <CardHead>
                <div className="lu-row"><Logo src={m.logo_url} name={m.id} size="lg" /><div><b>{m.id}</b>{m.context ? <div className="lu-muted" style={{ fontSize: 12 }}>{Math.round(m.context / 1024)}K контекст</div> : null}</div></div>
                <StateBadge value="running" label="отвечает" />
              </CardHead>
              <CardBody>
                <KeyValue rows={[
                  { k: "Вход", v: m.price_in != null ? `${rubles(Math.round(m.price_in / 100))} за 1M` : "—" },
                  { k: "Выход", v: m.price_out != null ? `${rubles(Math.round((m.price_out ?? 0) / 100))} за 1M` : "—" },
                ]} />
              </CardBody>
              <CardFoot>
                <Link className="lu-btn lu-btn--secondary lu-btn--sm" to={`/intelligence/chat?model=${encodeURIComponent(m.id)}`}>В чат</Link>
                <Link className="lu-btn lu-btn--secondary lu-btn--sm" to="/intelligence/keys">Код</Link>
              </CardFoot>
            </Card>
          ))}
        </div>
      ))}

      {tab === "mine" && (
        <div className="lu-grid lu-grid--cards">
          {mine.map((d) => {
            const st = deploymentState(d);
            return (
              <Card key={d.group_id} style={{ display: "flex", flexDirection: "column" }}>
                <CardHead>
                  <div className="lu-row" style={{ minWidth: 0 }}><Logo name={d.label} size="lg" /><div style={{ minWidth: 0 }}><b>{d.label}</b><div className="lu-muted lu-mono" style={{ fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.adapter ? `адаптер на ${d.repo}` : d.repo}</div></div></div>
                  <StateBadge value={st.value} label={st.label} />
                </CardHead>
                <CardBody className="lu-stack" style={{ gap: 8 }}>
                  {d.stages.map((s) => { const [ph, pct] = phase(s); return <ProgressPhase key={s.rank} phase={<span className="lu-mono">rank {s.rank}{s.stage?.layers ? ` · слои ${s.stage.layers[0]}–${s.stage.layers[1]}` : ""} · {ph}</span>} percent={pct} tone={s.state === "failed" ? "bad" : s.ready ? "ok" : undefined} right={s.error ? <span style={{ color: "var(--bad-text)" }}>{s.error}</span> : undefined} />; })}
                  <KeyValue rows={[
                    { k: "Движок", v: `${d.engine === "vllm" ? "vLLM" : "transformers"} · ${d.precision}` },
                    { k: "Стоимость", v: inferenceRate ? `${money(inferenceRate * Math.max(1, d.stages.length), "RUB", 0)}/час` : "по ставке инференса" },
                    { k: "Поднята", v: d.submitted_at ? ago(d.submitted_at * 1000) : "—" },
                  ]} />
                </CardBody>
                <CardFoot>
                  {d.ready && <Link className="lu-btn lu-btn--secondary lu-btn--sm" to={`/intelligence/chat?model=${encodeURIComponent(d.label)}`}>В чат</Link>}
                  <span className="lu-spacer" />
                  <Button kind="ghost" size="sm" style={{ color: "var(--bad-text)" }} onClick={() => setRemoving(d)}>Снять</Button>
                </CardFoot>
              </Card>
            );
          })}
          <AdapterOffer />
          {mine.length === 0 && (
            <Card dashed><Empty title="Своих моделей пока нет" action={<Button kind="primary" size="sm" onClick={() => nav("deploy")}>Развернуть</Button>}>Любой репозиторий с HuggingFace или адаптер после обучения — без заявок.</Empty></Card>
          )}
        </div>
      )}

      {removing && (
        <Confirm title={`Снять ${removing.label}?`} action="Снять" body="Модель перестанет отвечать, счёт за узлы остановится. Веса останутся в кэше узлов — повторный запуск быстрее."
                 onClose={() => setRemoving(null)} onConfirm={() => undeploy.mutate(removing.group_id, { onSuccess: () => toast("ok", "модель снята"), onError: (e) => toast("bad", e.message) })} />
      )}
    </Page>
  );
}

/** Готовый адаптер, который ещё не развёрнут, — мост из Обучения. */
function AdapterOffer() {
  const jobs = useTrainList();
  const deps = useDeployments();
  const nav = useNavigate();
  const deployed = new Set((deps.data?.deployments ?? []).map((d) => d.adapter).filter(Boolean));
  const ready = (jobs.data?.jobs ?? []).filter((j) => j.state === "done" && !deployed.has(j.group_id));
  if (ready.length === 0) return null;
  const j = ready[0];
  return (
    <Card dashed>
      <Empty title={`Адаптер ${j.label} готов`} action={<Button kind="primary" size="sm" onClick={() => nav(`deploy?adapter=${j.group_id}&repo=${encodeURIComponent(j.request.repo ?? "")}`)}>Развернуть адаптер</Button>}>
        Обучение закончилось {j.finished_at ? ago(j.finished_at * 1000) : "недавно"}. Разверните его поверх {j.request.repo}.
      </Empty>
    </Card>
  );
}

/* ------------------------------------------------------------------ визард */
const STEPS = [{ label: "Источник" }, { label: "Движок и точность" }, { label: "Узлы" }, { label: "Проверка" }];
type Preset = "vllm-bf16" | "vllm-fp16" | "torch-bf16" | "custom";
const GB = 1024 ** 3;

function DeployWizard() {
  const nav = useNavigate();
  const toast = useToast();
  const [search] = useSearchParams();
  const capacity = useCapacity();
  const rates = useRates();
  const deploy = useDeploy();
  const [step, setStep] = useState(0);
  const [repo, setRepo] = useState(search.get("repo") ?? "");
  const adapter = search.get("adapter") ?? "";
  const [desc, setDesc] = useState<ModelDescription | null>(null);
  const [descError, setDescError] = useState("");
  const [describing, setDescribing] = useState(false);
  const [preset, setPreset] = useState<Preset>("vllm-bf16");
  const [engine, setEngine] = useState<"vllm" | "torch">("vllm");
  const [dtype, setDtype] = useState<"bfloat16" | "float16" | "float32">("bfloat16");
  const [label, setLabel] = useState("");
  const [stages, setStages] = useState(1);

  const nodes = capacity.data?.nodes ?? [];
  const usable = nodes.filter((n) => n.state === "free" || n.state === "inference");
  const inferenceRate = rates.data?.rates.find((r) => r.resource === "looma-inference")?.per_hour ?? 0;
  const perHour = inferenceRate * stages;

  const describe = async () => {
    if (!repo.trim()) return;
    setDescribing(true); setDescError("");
    try { setDesc(await describeModel(repo.trim())); } catch (e) { setDesc(null); setDescError(e instanceof Error ? e.message : String(e)); }
    finally { setDescribing(false); }
  };
  useEffect(() => { if (search.get("repo")) void describe(); /* eslint-disable-next-line */ }, []);

  // Размер весов — из параметров: bf16/fp16 — 2 байта на параметр, fp32 — 4.
  const bytesPerParam = dtype === "float32" ? 4 : 2;
  const weightsGb = desc?.size_bytes ? desc.size_bytes / GB : desc?.params ? (Number(desc.params) * bytesPerParam) / GB : null;
  const needGb = (mult: number) => (weightsGb ? weightsGb * mult * 1.15 : null);   // + KV-кэш и служебное
  const fits = (gb: number | null, perNode: number) => gb === null ? null : usable.filter((n) => (n.vram_gb ?? 0) * 1 >= gb / perNode).length;
  const presets: { id: Preset; title: string; text: string; need: number | null; engine: "vllm" | "torch"; dtype: "bfloat16" | "float16"; note?: string }[] = [
    { id: "vllm-bf16", title: "vLLM · bf16", text: "Батчинг и prefix-cache, полная точность. Нужна карта NVIDIA с CUDA 12.6+.", need: needGb(1), engine: "vllm", dtype: "bfloat16" },
    { id: "vllm-fp16", title: "vLLM · fp16", text: "То же, половинная точность — старые карты без bf16.", need: needGb(1), engine: "vllm", dtype: "float16" },
    { id: "torch-bf16", title: `transformers · bf16, по слоям на ${Math.max(2, stages)} узла`, text: "Переносимый: работает на любой карте и на CPU. Медленнее vLLM в 3–5 раз, зато режется между узлами.", need: needGb(1), engine: "torch", dtype: "bfloat16", note: "по слоям" },
  ];
  const choose = (p: Preset) => {
    setPreset(p);
    const found = presets.find((x) => x.id === p);
    if (found) { setEngine(found.engine); setDtype(found.dtype); if (found.id === "torch-bf16" && stages < 2) setStages(2); if (found.engine === "vllm") setStages(1); }
  };
  const perNodeGb = useMemo(() => { const n = needGb(1); return n === null ? null : n / Math.max(1, stages); }, [desc, stages]);   // eslint-disable-line
  const fitCount = perNodeGb === null ? null : usable.filter((n) => (n.vram_gb ?? 0) >= perNodeGb).length;

  const submit = () => deploy.mutate(
    { repo: repo.trim(), label: label.trim() || undefined, engine, dtype, stages, adapter: adapter || undefined, device: engine === "vllm" ? "cuda" : "auto" },
    { onSuccess: () => { toast("ok", `${label || repo.split("/").pop()} поднимается`); nav("/intelligence/models"); }, onError: (e) => toast("bad", e.message) },
  );
  const summaries = [desc ? `${desc.repo.split("/").pop()}` : "", preset === "custom" ? `${engine} · ${dtype}` : presets.find((p) => p.id === preset)?.title ?? "", `${stages}`, ""];

  return (
    <Page>
      <div><Link to="/intelligence/models" className="lu-row" style={{ fontSize: 13, color: "var(--text-3)", width: "fit-content" }}><ArrowLeft size={14} />Модели</Link></div>
      <PageHead title={adapter ? "Развернуть адаптер" : "Развернуть модель"} text={`Шаг ${step + 1} из 4 · ${STEPS[step].label}`} />
      <ErrorLine error={capacity.error} />
      <Card panel style={{ overflow: "hidden" }}>
        <div style={{ padding: "20px 24px 0" }}><Steps steps={STEPS.map((s, i) => ({ ...s, summary: summaries[i] }))} current={step} /></div>
        <div style={{ padding: 24 }} className="lu-stack lu-stack--lg">
          {step === 0 && (
            <div className="lu-grid lu-grid--2">
              <div className="lu-stack lu-stack--lg">
                <Field label="Модель на HuggingFace" hint="владелец/название; bf16-чекпоинт" htmlFor="repo" required>
                  <div className="lu-row"><Input id="repo" mono value={repo} onChange={(e) => { setRepo(e.target.value); setDesc(null); }} placeholder="Qwen/Qwen3-8B" onBlur={describe} /><Button onClick={describe} disabled={describing || !repo.trim()}>{describing ? "…" : "Проверить"}</Button></div>
                </Field>
                {adapter && <Notice tone="info">Адаптер <code>{adapter}</code> ляжет поверх этой базы: та же модель, на которой он обучен.</Notice>}
                {descError && <Notice tone="bad">{descError}</Notice>}
                <Field label="Имя для API" hint="так модель будет называться в model: запроса; пусто — хвост репозитория" htmlFor="label"><Input id="label" mono value={label} onChange={(e) => setLabel(e.target.value)} placeholder={repo.split("/").pop() || "qwen3-8b"} /></Field>
              </div>
              <Card pad style={{ background: "var(--bg)" }}>
                {desc ? (
                  <KeyValue rows={[{ k: "Репозиторий", v: <span className="lu-mono">{desc.repo}</span> }, { k: "Слоёв", v: String(desc.num_layers) }, { k: "Веса (bf16)", v: desc.params ? bytes(Number(desc.params) * 2) : "—" }, { k: "Параметров", v: desc.params ? `${(Number(desc.params) / 1e9).toFixed(1)}B` : "—" }]} />
                ) : <div className="lu-muted" style={{ fontSize: 13 }}>Укажите репозиторий — покажем слои и размер, а на следующем шаге — на сколько узлов её резать.</div>}
              </Card>
            </div>
          )}
          {step === 1 && (
            <div className="lu-stack lu-stack--lg">
              <div className="lu-row lu-row--between"><b>Пресет</b><span className="lu-muted" style={{ fontSize: 12 }}>требование VRAM — по размеру весов; ниже — сколько узлов сети её вместят</span></div>
              <div className="lu-grid lu-grid--2">
                {presets.map((p) => {
                  const fit = fits(p.need, p.id === "torch-bf16" ? Math.max(2, stages) : 1);
                  return <RadioCard key={p.id} name="preset" value={p.id} checked={preset === p.id} onChange={(v) => choose(v as Preset)} recommended={p.id === "vllm-bf16"} title={p.title} text={p.text}
                                    meta={<>{p.need !== null && <span className="lu-mono">≈ {p.need.toFixed(0)} GB{p.id === "torch-bf16" ? " всего" : ""}</span>}{fit !== null && <span style={{ color: fit > 0 ? "var(--ok-text)" : "var(--bad-text)" }}>{fit > 0 ? `вместят ${fit} из ${usable.length} узлов` : "ни один узел не вместит"}</span>}</>} />;
                })}
                <RadioCard name="preset" value="custom" checked={preset === "custom"} onChange={(v) => choose(v as Preset)} title="Свои параметры" text="Движок, точность и число стадий — вручную." />
              </div>
              {preset === "custom" && (
                <div className="lu-grid lu-grid--3">
                  <Field label="Движок" htmlFor="eng"><Select id="eng" value={engine} onChange={(e) => setEngine(e.target.value as "vllm" | "torch")}><option value="vllm">vLLM — батчинг, CUDA 12.6+</option><option value="torch">transformers — переносимый</option></Select></Field>
                  <Field label="Точность" htmlFor="dt"><Select id="dt" value={dtype} onChange={(e) => setDtype(e.target.value as typeof dtype)}><option value="bfloat16">bfloat16</option><option value="float16">float16</option><option value="float32">float32</option></Select></Field>
                </div>
              )}
            </div>
          )}
          {step === 2 && (
            <div className="lu-grid lu-grid--2">
              <div className="lu-stack lu-stack--lg">
                <Field label="Стадий (узлов)" hint={engine === "vllm" ? "vLLM держит модель на одной карте — стадий одна" : "модель режется по слоям пропорционально свободной VRAM узлов"} htmlFor="stages">
                  <NumberStepper id="stages" value={stages} onChange={setStages} min={1} max={Math.max(1, Math.min(4, usable.length))} disabled={engine === "vllm"} />
                </Field>
                <Notice tone={fitCount === 0 ? "bad" : "info"}>{perNodeGb !== null ? `На узел нужно ≈ ${perNodeGb.toFixed(0)} GB VRAM — подходят ${fitCount} из ${usable.length} узлов.` : "Размер модели неизвестен — платформа выберет самые свободные узлы."} Какие именно машины — решает платформа: сначала свободные, потом узлы из-под своих моделей.</Notice>
              </div>
              <Card pad style={{ background: "var(--bg)" }}>
                <div className="lu-label" style={{ marginBottom: 8 }}>Карты в сети сейчас</div>
                {usable.length === 0 && <div className="lu-muted" style={{ fontSize: 13 }}>Нет узлов, которые берут работу.</div>}
                {Object.entries(usable.reduce<Record<string, { n: number; free: number; vram: number }>>((acc, n) => { const k = n.gpu_name || "прочее"; acc[k] = acc[k] ?? { n: 0, free: 0, vram: n.vram_gb ?? 0 }; acc[k].n++; if (n.state === "free") acc[k].free++; return acc; }, {})).map(([name, r]) => (
                  <div key={name} className="lu-row lu-row--between" style={{ fontSize: 13, padding: "6px 0", borderBottom: "1px solid var(--border)" }}><span className="lu-row"><b>{name}</b>{r.vram > 0 && <Chip>{r.vram} GB</Chip>}</span><span className="lu-muted">{r.free} свободно из {r.n}</span></div>
                ))}
              </Card>
            </div>
          )}
          {step === 3 && (
            <div className="lu-grid lu-grid--2">
              <div className="lu-stack">
                <b>Состав</b>
                <KeyValue rows={[
                  { k: "Модель", v: <span className="lu-mono">{repo}</span> }, ...(adapter ? [{ k: "Адаптер", v: <span className="lu-mono">{adapter}</span> }] : []),
                  { k: "Имя для API", v: <span className="lu-mono">{label || repo.split("/").pop()}</span> },
                  { k: "Движок", v: `${engine === "vllm" ? "vLLM" : "transformers"} · ${dtype}` }, { k: "Стадий", v: String(stages) },
                ]} />
              </div>
              <Card pad>
                <b>Стоимость</b>
                <div style={{ marginTop: 8 }}><KeyValue rows={[{ k: `${stages} × ${money(inferenceRate, "RUB", 0)}/GPU-час`, v: inferenceRate ? `${money(perHour, "RUB", 0)}/час` : "ставка не задана" }, { k: "В сутки", v: inferenceRate ? money(perHour * 24, "RUB", 0) : "—" }]} /></div>
                <div style={{ marginTop: 10 }}><Notice tone="info">Веса скачиваются на узел один раз и остаются в его кэше — повторный запуск на том же узле стартует быстрее. Модель работает, пока вы её не снимете.</Notice></div>
              </Card>
            </div>
          )}
        </div>
        <WizardFooter costLabel={`Оценка · ${stages} ${stages === 1 ? "узел" : "узла"} · ${engine === "vllm" ? "vLLM" : "transformers"} ${dtype}`}
                      cost={inferenceRate ? `${rubles(Math.round(perHour / 100))}/час` : "ставка не задана"} costNote={inferenceRate ? `~${money(perHour * 24, "RUB", 0)} в сутки` : undefined}
                      back={step > 0 ? () => setStep(step - 1) : undefined}
                      next={step < 3 ? () => setStep(step + 1) : submit}
                      nextLabel={step < 3 ? ["Далее: движок", "Далее: узлы", "Далее: проверка"][step] : "Развернуть"}
                      nextDisabled={(step === 0 && !repo.trim()) || (step === 2 && usable.length === 0)} busy={deploy.isPending} />
      </Card>
    </Page>
  );
}
