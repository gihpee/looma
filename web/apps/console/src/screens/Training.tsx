/** Обучение LoRA: список заданий, страница задания с ходом и артефактами,
 *  визард Simple/Advanced. Advanced — только наши поля: точность базы, ранг и
 *  альфа адаптера, LR, эпохи, длина, батч. Не больше. */
import { useMemo, useState } from "react";
import { Link, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Download, Plus } from "lucide-react";
import { grab, useCapacity, useRates, useTrain, useTrainJob, useTrainList, useTrainStop, type TrainJob } from "@looma/api";
import {
  Bar, Button, Card, CardBody, CardHead, Chip, Confirm, Empty, Field, FilePick, Input, KeyValue, Notice, NumberStepper, Page, PageHead,
  RadioCard, Segmented, Select, StateBadge, Steps, WizardFooter, ago, duration, money, num, rubles, useToast,
} from "@looma/ui";
import { ErrorLine } from "../lib";

export function Training() {
  return (
    <Routes>
      <Route index element={<JobList />} />
      <Route path="new" element={<TrainWizard />} />
      <Route path=":jobId" element={<JobView />} />
    </Routes>
  );
}

const LABELS: Record<string, string> = { running: "обучается", pending: "в очереди", done: "готово", failed: "упало", stopped: "остановлено" };
const jobLabel = (j: TrainJob) => LABELS[j.state] ?? j.state;
const jobPercent = (j: TrainJob) => j.progress?.total_steps ? Math.min(100, Math.round(((j.progress.step ?? 0) / j.progress.total_steps) * 100)) : j.state === "done" ? 100 : 0;

function JobCard({ j }: { j: TrainJob }) {
  const r = j.request;
  return (
    <Card style={{ display: "flex", flexDirection: "column" }}>
      <CardHead>
        <div className="lu-row" style={{ minWidth: 0, flexWrap: "wrap" }}><b>{j.label}</b><StateBadge value={j.state} label={jobLabel(j)} /><span className="lu-muted lu-mono" style={{ fontSize: 12 }}>{r.repo} · {r.precision} · LoRA r={r.lora?.r ?? 16}</span></div>
        <span className="lu-muted" style={{ fontSize: 12, whiteSpace: "nowrap" }}>{ago(j.created_at * 1000)}</span>
      </CardHead>
      <CardBody className="lu-stack" style={{ gap: 8 }}>
        {j.state === "running" && (
          <div className="lu-stack" style={{ gap: 6 }}>
            <div className="lu-row lu-row--between" style={{ fontSize: 12, color: "var(--text-2)" }}><span>шаг {j.progress.step ?? 0}{j.progress.total_steps ? ` из ${j.progress.total_steps}` : ""}{j.progress.loss !== undefined ? ` · loss ${j.progress.loss.toFixed(4)}` : ""}</span><span>{j.progress.eta_s ? `~${duration(j.progress.eta_s)}` : ""}</span></div>
            <Bar percent={jobPercent(j)} />
          </div>
        )}
        {j.state === "done" && j.result && <div className="lu-row lu-row--wrap"><StateBadge value="ready" label="адаптер готов" /><span className="lu-muted" style={{ fontSize: 13 }}>{j.result.steps} шагов{j.result.final_loss !== undefined ? `, loss ${j.result.final_loss.toFixed(4)}` : ""}</span>{Object.keys(j.files ?? {}).map((f) => <Chip key={f}>{f}</Chip>)}</div>}
        {(j.state === "failed" || j.state === "stopped") && j.error && <div style={{ fontSize: 13, color: j.state === "failed" ? "var(--bad-text)" : "var(--text-3)" }}>{j.error}</div>}
        <div className="lu-row lu-row--between"><span className="lu-muted" style={{ fontSize: 12 }}>стадий: {j.request.stages ?? j.group?.ranks.length ?? 1}</span><Link to={j.group_id} style={{ fontSize: 13, fontWeight: 500 }}>открыть →</Link></div>
      </CardBody>
    </Card>
  );
}

function JobList() {
  const nav = useNavigate();
  const jobs = useTrainList();
  const rows = jobs.data?.jobs ?? [];
  const live = rows.filter((j) => j.state === "running" || j.state === "pending").length;
  return (
    <Page>
      <PageHead title="Обучение" text={rows.length ? `${live} идёт, ${rows.length} всего` : "Дообучите модель LoRA на своих данных — адаптер разворачивается в один клик."}
                actions={<Button kind="primary" icon={<Plus size={16} />} onClick={() => nav("new")}>Дообучить модель</Button>} />
      <ErrorLine error={jobs.error} />
      {jobs.isLoading ? <div className="lu-loading">…</div> : rows.length === 0 ? (
        <Card><Empty title="Заданий пока нет" action={<Button kind="primary" size="sm" onClick={() => nav("new")}>Дообучить модель</Button>}>Нужны база с HuggingFace и датасет JSONL с диалогами. Стоимость и время оцениваются до запуска.</Empty></Card>
      ) : <div className="lu-stack" style={{ gap: 12 }}>{rows.map((j) => <JobCard key={j.group_id} j={j} />)}</div>}
    </Page>
  );
}

function JobView() {
  const { jobId = "" } = useParams();
  const nav = useNavigate();
  const toast = useToast();
  const job = useTrainJob(jobId);
  const stop = useTrainStop();
  const [confirm, setConfirm] = useState(false);
  const j = job.data;
  if (job.isLoading) return <div className="lu-loading">…</div>;
  if (!j) return <Page><Empty title="Такого обучения за вами не числится"><Link to="/intelligence/training">К списку</Link></Empty></Page>;
  const history = j.progress.history ?? [];
  const maxLoss = Math.max(...history.map((h) => h.loss), 0.0001);
  return (
    <Page>
      <div><Link to="/intelligence/training" className="lu-row" style={{ fontSize: 13, color: "var(--text-3)", width: "fit-content" }}><ArrowLeft size={14} />Обучение</Link></div>
      <PageHead title={j.label} text={<span className="lu-mono">{j.group_id}</span>}
                actions={<>{j.state === "done" && <Button kind="primary" onClick={() => nav(`/intelligence/models/deploy?adapter=${j.group_id}&repo=${encodeURIComponent(j.request.repo ?? "")}`)}>Развернуть адаптер</Button>}{(j.state === "running" || j.state === "pending") && <Button kind="danger" onClick={() => setConfirm(true)}>Остановить</Button>}</>} />
      <div className="lu-grid lu-grid--side">
        <div className="lu-stack" style={{ gap: 16 }}>
          <Card pad>
            <div className="lu-row lu-row--between"><b>Ход обучения</b><StateBadge value={j.state} label={jobLabel(j)} /></div>
            {j.state === "running" && <div style={{ marginTop: 10 }}><Bar percent={jobPercent(j)} /></div>}
            <div style={{ marginTop: 12 }}>
              <KeyValue rows={[
                { k: "Шаг", v: `${j.progress.step ?? 0}${j.progress.total_steps ? ` из ${j.progress.total_steps}` : ""}` },
                { k: "Loss", v: j.progress.loss !== undefined ? j.progress.loss.toFixed(4) : "—" },
                { k: "LR", v: j.progress.lr !== undefined ? j.progress.lr.toExponential(1) : "—" },
                { k: "Скорость", v: j.progress.tokens_per_s ? `${num(j.progress.tokens_per_s)} ток/с` : "—" },
                { k: "Прошло", v: j.progress.elapsed_s ? duration(j.progress.elapsed_s) : "—" },
                { k: "Осталось", v: j.progress.eta_s ? `~${duration(j.progress.eta_s)}` : "—" },
              ]} />
            </div>
            {history.length > 1 && (
              <svg viewBox="0 0 400 100" style={{ width: "100%", height: 100, marginTop: 12 }} role="img" aria-label="loss по шагам">
                <polyline fill="none" stroke="var(--accent)" strokeWidth="1.5" points={history.map((h, i) => `${(i / (history.length - 1)) * 400},${100 - (h.loss / maxLoss) * 92 - 4}`).join(" ")} />
              </svg>
            )}
            {j.error && <div style={{ marginTop: 10 }}><Notice tone={j.state === "failed" ? "bad" : "warn"}>{j.error}</Notice></div>}
          </Card>
          {j.group && (
            <Card>
              <CardHead><b>Стадии</b></CardHead>
              <CardBody><KeyValue rows={j.group.ranks.map((r) => ({ k: `rank ${r.rank}`, v: <span className="lu-mono">{r.node_id}</span> }))} /></CardBody>
            </Card>
          )}
        </div>
        <div className="lu-stack" style={{ gap: 16 }}>
          <Card pad>
            <b>Параметры</b>
            <div style={{ marginTop: 8 }}><KeyValue rows={[
              { k: "База", v: <span className="lu-mono">{j.request.repo}</span> }, { k: "Точность", v: j.request.precision ?? "bf16" },
              { k: "LoRA", v: `r=${j.request.lora?.r ?? 16} · α=${j.request.lora?.alpha ?? 32}` }, { k: "Эпох", v: String(j.request.schedule?.epochs ?? 1) },
            ]} /></div>
          </Card>
          {j.state === "done" && (
            <Card pad>
              <b>Артефакты</b>
              <div className="lu-stack" style={{ marginTop: 8, gap: 6 }}>
                {["adapter_config.json", "adapter_model.safetensors"].map((f) => <Button key={f} size="sm" icon={<Download size={14} />} onClick={() => grab(`/api/train/${j.group_id}/adapter/${f}`, f).catch((e) => toast("bad", e.message))}>{f}</Button>)}
              </div>
            </Card>
          )}
        </div>
      </div>
      {confirm && <Confirm title="Остановить обучение?" action="Остановить" body="Прогресс после последнего чекпоинта будет потерян; счёт остановится." onClose={() => setConfirm(false)} onConfirm={() => stop.mutate(j.group_id, { onSuccess: () => toast("ok", "остановлено"), onError: (e) => toast("bad", e.message) })} />}
    </Page>
  );
}

/* ------------------------------------------------------------------ визард */
const STEPS = [{ label: "Метод и база" }, { label: "Датасет" }, { label: "Параметры" }, { label: "Проверка" }];
type Mode = "simple" | "advanced";

function TrainWizard() {
  const nav = useNavigate();
  const toast = useToast();
  const capacity = useCapacity();
  const rates = useRates();
  const train = useTrain();
  const [step, setStep] = useState(0);
  const [method, setMethod] = useState("sft");
  const [repo, setRepo] = useState("");
  const [label, setLabel] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<{ lines: number; sample: string; error: string } | null>(null);
  const [mode, setMode] = useState<Mode>("simple");
  const [precision, setPrecision] = useState<"bf16" | "nf4">("bf16");
  const [r, setR] = useState(16); const [alpha, setAlpha] = useState(32); const [dropout, setDropout] = useState("0.05");
  const [lr, setLr] = useState("0.0002"); const [epochs, setEpochs] = useState(2); const [maxLen, setMaxLen] = useState(2048);
  const [batch, setBatch] = useState(8); const [micro, setMicro] = useState(2); const [stages, setStages] = useState(1);
  const [force, setForce] = useState(false);
  const [refusal, setRefusal] = useState("");

  const usable = (capacity.data?.nodes ?? []).filter((n) => n.state === "free" || n.state === "inference");
  const rate = rates.data?.training_rate_kopecks ?? rates.data?.rates.find((x) => x.resource === "looma-compute")?.per_hour ?? 0;
  const estHours = useMemo(() => {
    // Грубая оценка: примеры × эпохи × длина / (скорость на стадию). Числа — ориентир до запуска, сервер считает по факту.
    const lines = preview?.lines ?? 0;
    const tokens = lines * epochs * Math.min(maxLen, 512);
    const tps = precision === "nf4" ? 900 : 1500;
    return lines ? Math.max(0.1, tokens / tps / 3600) : 0;
  }, [preview, epochs, maxLen, precision]);
  const estCost = rate * stages * estHours;

  const pick = async (f: File | null) => {
    setFile(f); setPreview(null);
    if (!f) return;
    const text = await f.text();
    const lines = text.split("\n").filter((l) => l.trim());
    let error = "";
    try {
      const first = JSON.parse(lines[0] ?? "{}");
      if (!Array.isArray(first.messages) || first.messages[first.messages.length - 1]?.role !== "assistant") error = "в строке должен быть messages: [...], последнее сообщение — от assistant";
    } catch { error = "первая строка — не JSON"; }
    setPreview({ lines: lines.length, sample: lines.slice(0, 2).join("\n"), error });
  };
  const toB64 = (f: File) => new Promise<string>((res, rej) => { const rd = new FileReader(); rd.onload = () => res(String(rd.result).split(",")[1] ?? ""); rd.onerror = rej; rd.readAsDataURL(f); });

  const submit = async () => {
    if (!file) return;
    setRefusal("");
    const body = {
      repo: repo.trim(), label: label.trim() || undefined, precision, dataset: await toB64(file), stages, max_len: maxLen, force,
      lora: { r, alpha, dropout: Number(dropout) || 0 },
      schedule: { epochs, batch_size: batch, micro_size: micro, lr: Number(lr) || 2e-4, warmup_steps: 20, save_every: 50 },
    };
    train.mutate(body, {
      onSuccess: (made) => { toast("ok", `обучение ${label || made.group_id} запущено`); nav(`/intelligence/training/${made.group_id}`); },
      onError: (e) => { if (e.message.includes("не влезает")) setRefusal(e.message); else toast("bad", e.message); },
    });
  };
  const summaries = [repo ? `SFT · ${repo.split("/").pop()}` : "", preview ? `${preview.lines} примеров` : "", mode === "simple" ? "Simple" : `r=${r} · ${precision}`, ""];

  return (
    <Page>
      <div><Link to="/intelligence/training" className="lu-row" style={{ fontSize: 13, color: "var(--text-3)", width: "fit-content" }}><ArrowLeft size={14} />Обучение</Link></div>
      <PageHead title="Дообучить модель" text={`Шаг ${step + 1} из 4 · ${STEPS[step].label}`} />
      <ErrorLine error={capacity.error} />
      <Card panel style={{ overflow: "hidden" }}>
        <div style={{ padding: "20px 24px 0" }}><Steps steps={STEPS.map((s, i) => ({ ...s, summary: summaries[i] }))} current={step} /></div>
        <div style={{ padding: 24 }} className="lu-stack lu-stack--lg">
          {step === 0 && (
            <div className="lu-grid lu-grid--2">
              <div className="lu-stack">
                <b>Метод</b>
                <RadioCard name="m" value="sft" checked={method === "sft"} onChange={setMethod} recommended title="Supervised fine-tuning (SFT)" text="Учится на примерах диалогов: вопрос → правильный ответ. Нужен для стиля, формата, предметной области." />
                <RadioCard name="m" value="dpo" checked={method === "dpo"} onChange={setMethod} disabled title="Direct preference optimization (DPO)" text="Учится на парах «лучше / хуже». Следующий шаг после SFT." badge={<Chip>скоро</Chip>} />
              </div>
              <div className="lu-stack lu-stack--lg">
                <Field label="Базовая модель (HuggingFace)" hint="bf16-чекпоинт: квантованные (MXFP4, GPTQ, AWQ) не подходят" htmlFor="repo" required><Input id="repo" mono value={repo} onChange={(e) => setRepo(e.target.value)} placeholder="Qwen/Qwen3-8B" /></Field>
                <Field label="Имя результата" hint="так адаптер будет называться в списке и при деплое" htmlFor="lbl"><Input id="lbl" mono value={label} onChange={(e) => setLabel(e.target.value)} placeholder="support-lora-v1" /></Field>
              </div>
            </div>
          )}
          {step === 1 && (
            <div className="lu-grid lu-grid--2">
              <div className="lu-stack lu-stack--lg">
                <Field label="Датасет" hint='JSONL: в каждой строке {"messages": [{role, content}, …]}, последнее — assistant' htmlFor="ds" required><FilePick id="ds" label="выбрать .jsonl" accept=".jsonl,.json" onPick={pick} /></Field>
                {preview && (preview.error ? <Notice tone="bad">{preview.error}</Notice> : <Notice tone="ok">{num(preview.lines)} примеров · {file ? `${(file.size / 1024 / 1024).toFixed(1)} MB` : ""} · до 64 MB</Notice>)}
              </div>
              <Card pad style={{ background: "var(--bg)" }}>
                <div className="lu-label" style={{ marginBottom: 8 }}>Первые строки</div>
                <pre className="lu-code" style={{ maxHeight: 220, overflow: "auto", fontSize: 11 }}>{preview?.sample || '{"messages": [{"role": "user", "content": "…"}, {"role": "assistant", "content": "…"}]}'}</pre>
              </Card>
            </div>
          )}
          {step === 2 && (
            <div className="lu-stack lu-stack--lg">
              <Segmented value={mode} onChange={setMode} options={[{ value: "simple", label: "Simple" }, { value: "advanced", label: "Advanced" }]} />
              {mode === "simple" ? (
                <div className="lu-grid lu-grid--2">
                  <RadioCard name="p" value="bf16" checked={precision === "bf16"} onChange={(v) => setPrecision(v as "bf16")} recommended title="bf16 — полная база" text="Веса среза в памяти как есть. Больше VRAM, точнее." />
                  <RadioCard name="p" value="nf4" checked={precision === "nf4"} onChange={(v) => setPrecision(v as "nf4")} title="nf4 — QLoRA" text="База квантована в 4 бита, адаптер учится поверх. В 3–4 раза меньше памяти." />
                  <Field label="Эпох" hint="сколько раз пройти датасет; 1–3 обычно достаточно" htmlFor="ep"><NumberStepper id="ep" value={epochs} onChange={setEpochs} min={1} max={10} /></Field>
                  <Field label="Стадий (узлов)" hint="модель режется по слоям между узлами; не влезает в один — берите больше" htmlFor="st"><NumberStepper id="st" value={stages} onChange={setStages} min={1} max={Math.max(1, Math.min(4, usable.length))} /></Field>
                </div>
              ) : (
                <div className="lu-grid lu-grid--3">
                  <Field label="Точность базы" htmlFor="prec"><Select id="prec" value={precision} onChange={(e) => setPrecision(e.target.value as "bf16" | "nf4")}><option value="bf16">bf16 (полная)</option><option value="nf4">nf4 (QLoRA)</option></Select></Field>
                  <Field label="LoRA rank" hint="размер адаптера: 8–64" htmlFor="r"><NumberStepper id="r" value={r} onChange={setR} min={4} max={256} step={4} /></Field>
                  <Field label="LoRA alpha" hint="обычно 2 × rank" htmlFor="a"><NumberStepper id="a" value={alpha} onChange={setAlpha} min={4} max={512} step={4} /></Field>
                  <Field label="Dropout" htmlFor="do"><Input id="do" mono value={dropout} onChange={(e) => setDropout(e.target.value)} /></Field>
                  <Field label="Learning rate" htmlFor="lr"><Input id="lr" mono value={lr} onChange={(e) => setLr(e.target.value)} /></Field>
                  <Field label="Эпох" htmlFor="ep2"><NumberStepper id="ep2" value={epochs} onChange={setEpochs} min={1} max={20} /></Field>
                  <Field label="Длина последовательности" hint="токенов на пример" htmlFor="ml"><NumberStepper id="ml" value={maxLen} onChange={setMaxLen} min={256} max={8192} step={256} /></Field>
                  <Field label="Batch" htmlFor="b"><NumberStepper id="b" value={batch} onChange={setBatch} min={1} max={128} /></Field>
                  <Field label="Micro-batch" hint="сколько примеров за проход на карте" htmlFor="mb"><NumberStepper id="mb" value={micro} onChange={setMicro} min={1} max={32} /></Field>
                  <Field label="Стадий (узлов)" htmlFor="st2"><NumberStepper id="st2" value={stages} onChange={setStages} min={1} max={Math.max(1, Math.min(4, usable.length))} /></Field>
                </div>
              )}
            </div>
          )}
          {step === 3 && (
            <div className="lu-grid lu-grid--2">
              <div className="lu-stack">
                <b>Состав</b>
                <KeyValue rows={[
                  { k: "Метод", v: "SFT" }, { k: "База", v: <span className="lu-mono">{repo}</span> }, { k: "Датасет", v: preview ? `${num(preview.lines)} примеров` : "—" },
                  { k: "Точность", v: precision }, { k: "LoRA", v: `r=${r} · α=${alpha} · dropout ${dropout}` }, { k: "Расписание", v: `${epochs} эп. · lr ${lr} · batch ${batch}/${micro} · ${maxLen} ток.` },
                  { k: "Стадий", v: String(stages) }, { k: "Имя", v: <span className="lu-mono">{label || `train-${repo.split("/").pop()}`}</span> },
                ]} />
              </div>
              <Card pad>
                <b>Оценка</b>
                <div style={{ marginTop: 8 }}><KeyValue rows={[
                  { k: "Время", v: estHours ? `~${duration(estHours * 3600)}` : "—" },
                  { k: `${stages} × ${money(rate, "RUB", 0)}/GPU-час`, v: estCost ? `≈ ${money(Math.round(estCost), "RUB", 0)}` : "—" },
                ]} /></div>
                <div style={{ marginTop: 10 }}><Notice tone="info">Оценка ориентировочная; списание — по факту, пока идёт обучение. Адаптер сохраняется на оркестраторе и разворачивается в один клик.</Notice></div>
                {refusal && <div style={{ marginTop: 10 }} className="lu-stack"><Notice tone="warn">{refusal}</Notice><label className="lu-toggle"><input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} /><span className="lu-toggle__track" /><span>Всё равно запустить</span></label></div>}
              </Card>
            </div>
          )}
        </div>
        <WizardFooter costLabel={estHours ? `Оценка · ~${duration(estHours * 3600)} · ${stages} ${stages === 1 ? "узел" : "узла"}` : "Оценка появится после датасета"} cost={estCost ? `≈ ${rubles(Math.round(estCost / 100))}` : undefined}
                      back={step > 0 ? () => setStep(step - 1) : undefined} next={step < 3 ? () => setStep(step + 1) : submit}
                      nextLabel={step < 3 ? "Далее" : "Запустить обучение"}
                      nextDisabled={(step === 0 && !repo.trim()) || (step === 1 && (!file || !!preview?.error)) || (step === 3 && !!refusal && !force)} busy={train.isPending} />
      </Card>
    </Page>
  );
}
