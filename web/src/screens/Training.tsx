import { useMemo, useState } from "react";
import { grab, send } from "../lib/api";
import { ago, duration, gb } from "../lib/format";
import type { Node } from "../lib/types";
import {
  Badge, Button, Confirm, Empty, ErrorLine, Field, FilePick, Modal, StateBadge,
  useAction, usePoll,
} from "../components";
import { Deploy } from "./Models";

/** Дообучение LoRA по конвейеру: форма, список, ход и результат.
 *
 *  Поля — наши, а не чьи-то ещё: модель, датасет, точность базы, размер
 *  адаптера, расписание. Что из этого становится конфигом стадии — дело
 *  оркестратора (`/admin/train`), и форма про это не знает. */

interface Step { step: number; epoch: number; loss: number; lr: number; tokens_per_s: number }
interface Progress {
  state?: string; error?: string; step?: number; total_steps?: number;
  loss?: number; lr?: number; tokens_per_s?: number; elapsed_s?: number; eta_s?: number;
  history?: Step[];
}
interface Job {
  group_id: string; label: string; state: string; error: string;
  created_at: number; finished_at: number | null;
  request: { repo?: string; precision?: string; stages?: number; node_ids?: string[];
             lora?: { r?: number }; schedule?: { epochs?: number } };
  result: { adapter?: string; steps?: number; final_loss?: number } | null;
  progress: Progress; files?: Record<string, string>; adapter_kept?: boolean;
  group: { ranks: { rank: number; task_id: string; node_id: string }[] } | null;
}

const PRESETS = {
  simple: { r: "16", alpha: "32", dropout: "0.05", lr: "0.0002", epochs: "2",
            batch: "8", micro: "2", maxLen: "2048", warmup: "20" },
};

function Train({ nodes, onClose, onDone }: {
  nodes: Node[]; onClose: () => void; onDone: () => void;
}) {
  const action = useAction(onDone);
  const [repo, setRepo] = useState("");
  const [label, setLabel] = useState("");
  const [precision, setPrecision] = useState<"bf16" | "nf4">("bf16");
  const [stages, setStages] = useState("1");
  const [picked, setPicked] = useState<string[]>([]);
  const [advanced, setAdvanced] = useState(false);
  const [form, setForm] = useState(PRESETS.simple);
  const [dataset, setDataset] = useState<{ name: string; b64: string; rows: number } | null>(null);
  const [force, setForce] = useState(false);
  const [refusal, setRefusal] = useState("");
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));

  const pick = (file: File) => {
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result ?? "");
      const rows = text.split("\n").filter((line) => line.trim()).length;
      // FileReader отдаёт текст; API нужен base64 — кодируем как байты UTF-8.
      const bytes = new TextEncoder().encode(text);
      let binary = "";
      bytes.forEach((b) => { binary += String.fromCharCode(b); });
      setDataset({ name: file.name, b64: btoa(binary), rows });
    };
    reader.readAsText(file);
  };

  const body = () => ({
    repo, label: label || undefined, precision, dataset: dataset?.b64,
    stages: picked.length ? undefined : Math.max(1, Number(stages) || 1),
    node_ids: picked.length ? picked : undefined,
    max_len: Number(form.maxLen) || 2048, force,
    lora: { r: Number(form.r) || 16, alpha: Number(form.alpha) || 32,
            dropout: Number(form.dropout) || 0 },
    schedule: { epochs: Number(form.epochs) || 1, batch_size: Number(form.batch) || 8,
                micro_size: Number(form.micro) || 2, lr: Number(form.lr) || 2e-4,
                warmup_steps: Number(form.warmup) || 0, save_every: 50 },
  });

  const start = () => action.run(async () => {
    setRefusal("");
    try {
      await send("/admin/train", "POST", body());
    } catch (e) {
      const text = String((e as Error).message ?? e);
      // «Не влезает» — не ошибка формы, а оценка: покажем и дадим настоять.
      if (text.includes("не влезает")) { setRefusal(text); return; }
      throw e;
    }
    onClose();
  }, "обучение запущено");

  const toggle = (id: string) =>
    setPicked((p) => p.includes(id) ? p.filter((x) => x !== id) : [...p, id]);

  return (
    <Modal title="Дообучить модель" onClose={onClose} footer={
      <div className="form-actions" style={{ borderTop: 0, paddingTop: 0 }}>
        <Button onClick={onClose}>отмена</Button>
        <Button kind="primary" onClick={start} disabled={action.busy || !repo || !dataset}>
          {action.busy ? "запускаю…" : "дообучить"}
        </Button>
      </div>
    }>
        <div style={{ display: "grid", gap: 12 }}>
          <Field label="Базовая модель (HuggingFace)" hint="bf16-чекпоинт: квантованные (MXFP4, GPTQ, AWQ) не подходят">
            <input value={repo} placeholder="Qwen/Qwen3-8B" onChange={(e) => setRepo(e.target.value)} />
          </Field>
          <Field label="Датасет" hint={'JSONL: в каждой строке {"messages": [{role, content}, …]}, последнее — assistant'}>
            <FilePick label="выбрать .jsonl" accept=".jsonl,.json,.txt" onPick={pick} />
            {dataset && <span className="hint">{dataset.name}: {dataset.rows} примеров</span>}
          </Field>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <Field label="Точность базы" hint={precision === "bf16"
              ? "полная; веса среза в памяти как есть" : "сжатая в 4 бита: вчетверо меньше памяти, качество почти то же; только CUDA"}>
              <select value={precision} onChange={(e) => setPrecision(e.target.value as "bf16" | "nf4")}>
                <option value="bf16">bf16 (полная)</option>
                <option value="nf4">nf4 (QLoRA)</option>
              </select>
            </Field>
            <Field label="Имя результата">
              <input value={label} placeholder="my-lora" onChange={(e) => setLabel(e.target.value)} />
            </Field>
          </div>

          <Field label={picked.length ? `Узлы: ${picked.length} выбрано` : "Сколько узлов"}
                 hint="модель режется по слоям между узлами; не влезает в один — берите больше">
            {!picked.length && (
              <input type="number" min={1} value={stages} onChange={(e) => setStages(e.target.value)} />
            )}
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
              {nodes.map((n) => (
                <button key={n.node_id} type="button"
                        className={`btn sm${picked.includes(n.node_id) ? " primary" : ""}`}
                        onClick={() => toggle(n.node_id)}>
                  {n.node_id} · {gb(n.vram_free_bytes)} ГБ
                </button>
              ))}
            </div>
          </Field>

          <Button kind="ghost" size="sm" onClick={() => setAdvanced((a) => !a)}>
            {advanced ? "скрыть подробности" : "подробно: адаптер и расписание"}
          </Button>
          {advanced && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10 }}>
              <Field label="LoRA r"><input type="number" value={form.r} onChange={set("r")} /></Field>
              <Field label="alpha"><input type="number" value={form.alpha} onChange={set("alpha")} /></Field>
              <Field label="dropout"><input type="number" step={0.01} value={form.dropout} onChange={set("dropout")} /></Field>
              <Field label="lr"><input type="number" step={0.00001} value={form.lr} onChange={set("lr")} /></Field>
              <Field label="эпох"><input type="number" value={form.epochs} onChange={set("epochs")} /></Field>
              <Field label="разогрев, шагов"><input type="number" value={form.warmup} onChange={set("warmup")} /></Field>
              <Field label="батч"><input type="number" value={form.batch} onChange={set("batch")} /></Field>
              <Field label="микробатч"><input type="number" value={form.micro} onChange={set("micro")} /></Field>
              <Field label="длина, токенов"><input type="number" value={form.maxLen} onChange={set("maxLen")} /></Field>
            </div>
          )}

          {refusal && (
            <div className="card" style={{ borderColor: "var(--warn-soft)" }}>
              <b>По оценке не влезает</b>
              <div className="sub" style={{ marginTop: 4 }}>{refusal}</div>
              <label style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8 }}>
                <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
                всё равно запустить — оценка грубая, стадия скажет точно
              </label>
            </div>
          )}
        </div>
    </Modal>
  );
}

/** Кривая loss: одна линия, без библиотек — тут важна форма, а не точность. */
function LossChart({ history }: { history: Step[] }) {
  const points = history.filter((h) => Number.isFinite(h.loss));
  if (points.length < 2) return null;
  const W = 560, H = 140, pad = 24;
  const xs = points.map((p) => p.step), ys = points.map((p) => p.loss);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const x = (v: number) => pad + ((v - minX) / Math.max(1, maxX - minX)) * (W - 2 * pad);
  const y = (v: number) => H - pad - ((v - minY) / Math.max(1e-9, maxY - minY)) * (H - 2 * pad);
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(p.step).toFixed(1)},${y(p.loss).toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", maxWidth: W, display: "block" }}>
      <line x1={pad} y1={H - pad} x2={W - pad} y2={H - pad} stroke="var(--line-soft)" />
      <line x1={pad} y1={pad} x2={pad} y2={H - pad} stroke="var(--line-soft)" />
      <path d={path} fill="none" stroke="var(--accent)" strokeWidth={1.6} />
      <text x={pad} y={pad - 8} fontSize={10} fill="var(--text-mute)">loss {maxY.toFixed(3)}</text>
      <text x={pad} y={H - 6} fontSize={10} fill="var(--text-mute)">{minY.toFixed(3)} · шаги {minX}–{maxX}</text>
    </svg>
  );
}

function JobCard({ job, onStop, onDeploy }: {
  job: Job; onStop: (j: Job) => void; onDeploy: (j: Job) => void;
}) {
  const p = job.progress ?? {};
  const running = job.state === "running";
  const done = p.step ?? 0, total = p.total_steps ?? 0;
  const percent = total ? Math.round((done / total) * 100) : 0;
  return (
    <div className="card" style={{ display: "grid", gap: 10 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <b>{job.label}</b>
        <StateBadge value={job.state} pulse={running} />
        <span className="sub">{job.request.repo} · {job.request.precision} · LoRA r={job.request.lora?.r ?? 16}</span>
        <span className="sub" style={{ marginLeft: "auto" }}>{ago(job.created_at)}</span>
        {running && <Button size="sm" kind="danger" onClick={() => onStop(job)}>остановить</Button>}
      </div>
      {job.group && (
        <div className="sub">
          стадии: {job.group.ranks.map((r) => `${r.rank}→${r.node_id}`).join(", ")}
        </div>
      )}
      {(running || total > 0) && (
        <div style={{ display: "grid", gap: 6 }}>
          <div className="bar"><i style={{ width: `${percent}%` }} /></div>
          <div className="sub">
            шаг {done} из {total || "?"}
            {p.loss != null && ` · loss ${p.loss.toFixed(4)}`}
            {p.tokens_per_s ? ` · ${Math.round(p.tokens_per_s)} ток/с` : ""}
            {p.eta_s != null && running ? ` · осталось ~${duration(p.eta_s)}` : ""}
            {p.elapsed_s != null ? ` · прошло ${duration(p.elapsed_s)}` : ""}
          </div>
          {p.history && <LossChart history={p.history} />}
        </div>
      )}
      {job.state === "running" && !total && <div className="sub">стадии поднимаются: веса грузятся минутами</div>}
      {job.error && <div className="sub" style={{ color: "var(--bad)" }}>{job.error}</div>}
      {job.result && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <Badge tone="ok">адаптер готов</Badge>
          <span className="sub">{job.result.steps} шагов, loss {job.result.final_loss?.toFixed(4)}</span>
          {job.adapter_kept && (
            <Button size="sm" kind="primary" onClick={() => onDeploy(job)}>развернуть</Button>
          )}
          {job.files && Object.entries(job.files).map(([name, url]) => (
            // Не ссылкой: файл отдаётся по токену в заголовке (см. api.grab).
            <Button key={name} size="sm" onClick={() => void grab(url, name)}>{name}</Button>
          ))}
        </div>
      )}
    </div>
  );
}

export function Training() {
  const jobs = usePoll<{ jobs: Job[] }>("/admin/train", 5000);
  const nodes = usePoll<{ nodes: Node[] }>("/admin/agents", 10000);
  const [training, setTraining] = useState(false);
  const [stopping, setStopping] = useState<Job | null>(null);
  const [deploying, setDeploying] = useState<Job | null>(null);
  const action = useAction(jobs.refresh);

  const list = jobs.data?.jobs ?? [];
  const usable = useMemo(() => (nodes.data?.nodes ?? []).filter((n) => n.accepts_tasks), [nodes.data]);

  return (
    <div className="page">
      <header>
        <div>
          <h1>Обучение</h1>
          <p>{list.length
            ? `${list.filter((j) => j.state === "running").length} идёт, ${list.length} всего`
            : "LoRA-дообучение по конвейеру: модель режется по слоям между узлами, адаптер — результат"}</p>
        </div>
        <div className="actions">
          <Button kind="primary" onClick={() => setTraining(true)}>дообучить модель</Button>
        </div>
      </header>

      <ErrorLine error={jobs.error} />

      {!list.length && !jobs.loading ? (
        <div className="card">
          <Empty title="Обучений ещё не было">
            Загрузите JSONL с диалогами и выберите базовую модель.
          </Empty>
        </div>
      ) : (
        <div style={{ display: "grid", gap: 12 }}>
          {list.map((job) => (
            <JobCard key={job.group_id} job={job} onStop={setStopping} onDeploy={setDeploying} />
          ))}
        </div>
      )}
      {training && <Train nodes={usable} onClose={() => setTraining(false)} onDone={jobs.refresh} />}
      {deploying && (
        // Та же форма, что на «Моделях», с подставленным адаптером и его базой.
        <Deploy nodes={nodes.data?.nodes ?? []} onClose={() => setDeploying(null)}
                onDone={() => setDeploying(null)}
                preset={{ id: deploying.group_id, label: deploying.label,
                          repo: deploying.request.repo ?? "", precision: deploying.request.precision }} />
      )}
      {stopping && (
        <Confirm title="Остановить обучение?" action="остановить"
                 body={<>Стадии {stopping.label} будут сняты; последний чекпоинт останется в результатах головы.</>}
                 onClose={() => setStopping(null)}
                 onConfirm={() => { const j = stopping; setStopping(null);
                   void action.run(() => send(`/admin/train/${j.group_id}/stop`, "POST", {}), "остановлено"); }} />
      )}
    </div>
  );
}
