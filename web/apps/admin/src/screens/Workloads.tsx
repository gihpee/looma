/** Нагрузка: модели (статус выводится из стадий; снятые и упавшие — во
 *  вкладке «История»), обучение (все клиенты), задачи с группами. */
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { get, grab, send, useAdminDeployments, useAdminTrainJobs, useGroups, useGroupsHealth, useNodes, useTasks, type Group, type GroupHealth, type Task, type TrainJob } from "@looma/api";
import {
  Badge, Bar, Button, Card, CardBody, CardFoot, CardHead, Chip, Confirm, Drawer, Empty, Field, FilePick, Input, KeyValue, Modal, Notice, NumberStepper,
  Page, PageHead, ProgressPhase, SearchBox, Segmented, Select, StateBadge, Textarea, Toggle, ago, duration, num, useAction, useToast,
} from "@looma/ui";
import { ErrorLine, b64, phase } from "../lib";

/* ------------------------------------------------------------------ модели */
export function AdminModels() {
  const qc = useQueryClient();
  const health = useGroupsHealth();
  const groups = useGroups();
  const deps = useAdminDeployments();
  const jobs = useAdminTrainJobs();
  const nodes = useNodes();
  const action = useAction(() => qc.invalidateQueries({ predicate: (q) => String(q.queryKey[0]).startsWith("/admin") || String(q.queryKey[0]).startsWith("/v1") }));
  const [tab, setTab] = useState<"live" | "history">("live");
  const [deploying, setDeploying] = useState(false);
  const [stopping, setStopping] = useState<GroupHealth | Group | null>(null);
  const [ask, setAsk] = useState<string | null>(null);

  const training = new Set((jobs.data?.jobs ?? []).map((j) => j.group_id));
  const live = (health.data?.groups ?? []).filter((g) => !training.has(g.group_id) && !g.stages.every((s) => s.state === "cancelled" || s.state === "failed"));
  const history = (groups.data?.groups ?? []).filter((g) => !training.has(g.group_id) && (g.finished || !live.some((l) => l.group_id === g.group_id)));
  const owner = (gid: string) => deps.data?.deployments.find((d) => d.group_id === gid);
  const protectedIds = new Set((deps.data?.deployments ?? []).filter((d) => d.protected).map((d) => d.group_id));

  return (
    <Page>
      <PageHead title="Модели" text={`${live.filter((g) => g.ready).length} отвечает, ${live.filter((g) => !g.ready).length} поднимается`}
                actions={<Button kind="primary" icon={<Plus size={16} />} onClick={() => setDeploying(true)}>Развернуть</Button>} />
      <ErrorLine error={health.error ?? groups.error} />
      <Segmented value={tab} onChange={setTab} options={[{ value: "live", label: `Живые · ${live.length}` }, { value: "history", label: `История · ${history.length}` }]} />
      {tab === "live" && (live.length === 0 ? <Card><Empty title="Ничего не развёрнуто" action={<Button kind="primary" size="sm" onClick={() => setDeploying(true)}>Развернуть</Button>} /></Card> : (
        <div className="lu-grid lu-grid--cards">
          {live.map((g) => { const o = owner(g.group_id); return (
            <Card key={g.group_id} style={{ display: "flex", flexDirection: "column" }}>
              <CardHead>
                <div className="lu-row" style={{ minWidth: 0, flexWrap: "wrap" }}><b>{g.label}</b><StateBadge value={g.ready ? "ready" : "loading"} label={g.ready ? "отвечает" : "поднимается"} />{protectedIds.has(g.group_id) && <Badge tone="info" dot={false} size="sm">защищена</Badge>}{o?.account_id != null && <Chip>клиент #{o.account_id}</Chip>}</div>
                <span className="lu-mono lu-muted" style={{ fontSize: 11 }}>{g.group_id}</span>
              </CardHead>
              <CardBody className="lu-stack" style={{ gap: 8 }}>
                {g.stages.map((s) => { const [ph, pct] = phase(s); return <ProgressPhase key={s.rank} percent={pct} tone={s.state === "failed" ? "bad" : s.ready ? "ok" : undefined}
                  phase={<span className="lu-mono">rank {s.rank} · {s.node_id}{s.stage?.layers ? ` · слои ${s.stage.layers[0]}–${s.stage.layers[1]}` : ""} · {ph}</span>} right={s.error ? <span style={{ color: "var(--bad-text)" }}>{s.error}</span> : undefined} />; })}
              </CardBody>
              <CardFoot>
                {g.ready && <Button size="sm" onClick={() => setAsk(g.label)}>спросить</Button>}
                <Toggle checked={protectedIds.has(g.group_id)} onChange={(v) => action.run(() => send(`/admin/deployments/${g.group_id}/protected`, "POST", { protected: v }), v ? "защищена от вытеснения" : "защита снята")} label={<span style={{ fontSize: 12 }}>не вытеснять</span>} />
                <span className="lu-spacer" />
                <Button size="sm" kind="ghost" style={{ color: "var(--bad-text)" }} onClick={() => setStopping(g)}>снять</Button>
              </CardFoot>
            </Card>); })}
        </div>
      ))}
      {tab === "history" && (
        <Card>
          {history.length === 0 ? <Empty title="История пуста" /> : (
            <table className="lu-table lu-table--cards">
              <thead><tr><th>Группа</th><th>Стадий</th><th>Отправлена</th><th /></tr></thead>
              <tbody>{history.map((g) => <tr key={g.group_id}><td data-label="Группа"><b>{g.label}</b> <span className="lu-mono lu-muted" style={{ fontSize: 11 }}>{g.group_id}</span></td><td data-label="Стадий" className="lu-num">{g.ranks.length}</td><td data-label="Отправлена">{ago(g.submitted_at * 1000)}</td><td><Button size="sm" kind="ghost" onClick={() => action.run(() => send(`/admin/groups/${g.group_id}`, "DELETE"), "удалена")}>удалить запись</Button></td></tr>)}</tbody>
            </table>
          )}
        </Card>
      )}
      {deploying && <DeployModal nodes={nodes.data?.nodes ?? []} onClose={() => setDeploying(false)} onDone={() => { setDeploying(false); action.run(async () => {}); }} />}
      {stopping && <Confirm title={`Снять ${stopping.label}?`} action="Снять" body="Стадии остановятся, счёт клиента (если модель его) закроется." onClose={() => setStopping(null)}
                            onConfirm={() => action.run(() => send(`/admin/groups/${stopping.group_id}/stop`, "POST"), "снята")} />}
      {ask && <AskModal model={ask} onClose={() => setAsk(null)} />}
    </Page>
  );
}

function DeployModal({ nodes, onClose, onDone }: { nodes: { node_id: string; vram_free_bytes: number; gpus_total: number; cuda_version: string }[]; onClose: () => void; onDone: () => void }) {
  const action = useAction(onDone);
  const [repo, setRepo] = useState(""); const [label, setLabel] = useState("");
  const [engine, setEngine] = useState<"torch" | "vllm">("torch"); const [dtype, setDtype] = useState("bfloat16"); const [device, setDevice] = useState("auto");
  const [stages, setStages] = useState(1); const [picked, setPicked] = useState<string[]>([]); const [byVram, setByVram] = useState(true);
  const toggle = (id: string) => setPicked((p) => p.includes(id) ? p.filter((x) => x !== id) : [...p, id]);
  return (
    <Modal title="Развернуть модель" onClose={onClose} footer={<><span className="lu-spacer" /><Button kind="ghost" onClick={onClose}>Отмена</Button><Button kind="primary" disabled={action.busy || !repo.trim()} onClick={() => action.run(() => send("/admin/deploy", "POST", { repo: repo.trim(), label: label.trim() || undefined, engine, dtype, device, stages: picked.length ? undefined : stages, node_ids: picked.length ? picked : undefined, by_vram: byVram }), "поднимается")}>Развернуть</Button></>}>
      <div className="lu-stack lu-stack--lg">
        <div className="lu-grid lu-grid--2">
          <Field label="Модель на HuggingFace" hint="владелец/название" htmlFor="d-repo" required><Input id="d-repo" mono value={repo} onChange={(e) => setRepo(e.target.value)} placeholder="Qwen/Qwen3-8B" /></Field>
          <Field label="Имя для клиентов" hint="по умолчанию — хвост repo" htmlFor="d-label"><Input id="d-label" mono value={label} onChange={(e) => setLabel(e.target.value)} /></Field>
          <Field label="Движок" hint={engine === "vllm" ? "батчинг; только cuda 12.6+" : "переносимый, работает везде"} htmlFor="d-eng"><Select id="d-eng" value={engine} onChange={(e) => setEngine(e.target.value as "torch" | "vllm")}><option value="torch">transformers — переносимый</option><option value="vllm">vLLM — батчинг, CUDA 12.6+</option></Select></Field>
          <Field label="Точность" htmlFor="d-dt"><Select id="d-dt" value={dtype} onChange={(e) => setDtype(e.target.value)}><option>bfloat16</option><option>float16</option><option>float32</option></Select></Field>
          <Field label="Устройство" hint="каждая стадия берёт ускоритель своей машины" htmlFor="d-dev"><Select id="d-dev" value={device} onChange={(e) => setDevice(e.target.value)}><option value="auto">авто — по железу узла</option><option value="cuda">cuda</option><option value="mps">mps — Metal на Apple</option><option value="cpu">cpu</option></Select></Field>
          <Field label="Стадий" hint="если узлы не выбраны" htmlFor="d-st"><NumberStepper id="d-st" value={stages} onChange={setStages} min={1} max={Math.max(1, nodes.length)} disabled={picked.length > 0} /></Field>
        </div>
        <Field label="Узлы" hint="пусто — самые свободные по VRAM">
          <div className="lu-stack" style={{ gap: 4, maxHeight: 200, overflowY: "auto" }}>
            {nodes.map((n) => <label key={n.node_id} className="lu-row" style={{ padding: "6px 8px", borderRadius: 6, background: picked.includes(n.node_id) ? "var(--accent-soft)" : undefined, cursor: "pointer" }}><input type="checkbox" checked={picked.includes(n.node_id)} onChange={() => toggle(n.node_id)} style={{ accentColor: "var(--accent)" }} /><span className="lu-mono" style={{ fontSize: 13 }}>{n.node_id}</span><span className="lu-muted" style={{ marginLeft: "auto", fontSize: 12 }}>{(n.vram_free_bytes / 1024 ** 3).toFixed(1)} GB · {n.gpus_total} GPU{engine === "vllm" && parseFloat(n.cuda_version) < 12.6 ? " · нет vLLM" : ""}</span></label>)}
          </div>
        </Field>
        <Toggle checked={byVram} onChange={setByVram} label="резать слои пропорционально свободной VRAM" />
      </div>
    </Modal>
  );
}

function AskModal({ model, onClose }: { model: string; onClose: () => void }) {
  const [q, setQ] = useState("привет"); const [a, setA] = useState(""); const [busy, setBusy] = useState(false);
  const ask = async () => { setBusy(true); setA(""); try { const r = await send<{ choices: { message: { content: string } }[] }>("/v1/chat/completions", "POST", { model, messages: [{ role: "user", content: q }], max_tokens: 128 }); setA(r.choices?.[0]?.message?.content ?? ""); } catch (e) { setA(`ошибка: ${e instanceof Error ? e.message : e}`); } finally { setBusy(false); } };
  return (
    <Modal title={`Спросить ${model}`} size="sm" onClose={onClose} footer={<><span className="lu-spacer" /><Button kind="primary" onClick={ask} disabled={busy}>{busy ? "…" : "Спросить"}</Button></>}>
      <div className="lu-stack"><Input value={q} onChange={(e) => setQ(e.target.value)} />{a && <div className="lu-card lu-card--pad" style={{ whiteSpace: "pre-wrap", fontSize: 13 }}>{a}</div>}</div>
    </Modal>
  );
}

/* ---------------------------------------------------------------- обучение */
const JOB: Record<string, string> = { running: "обучается", pending: "в очереди", done: "готово", failed: "упало", stopped: "остановлено" };
export function AdminTraining() {
  const qc = useQueryClient();
  const toast = useToast();
  const jobs = useAdminTrainJobs();
  const nodes = useNodes();
  const action = useAction(() => qc.invalidateQueries({ queryKey: ["/admin/train"] }));
  const [creating, setCreating] = useState(false);
  const [stopping, setStopping] = useState<TrainJob | null>(null);
  const rows = jobs.data?.jobs ?? [];
  return (
    <Page>
      <PageHead title="Обучение" text={`${rows.filter((j) => j.state === "running").length} идёт, ${rows.length} всего`} actions={<Button kind="primary" icon={<Plus size={16} />} onClick={() => setCreating(true)}>Дообучить</Button>} />
      <ErrorLine error={jobs.error} />
      {rows.length === 0 ? <Card><Empty title="Заданий нет" /></Card> : (
        <div className="lu-stack" style={{ gap: 12 }}>
          {rows.map((j) => (
            <Card key={j.group_id}>
              <CardHead>
                <div className="lu-row" style={{ flexWrap: "wrap" }}><b>{j.label}</b><StateBadge value={j.state} label={JOB[j.state] ?? j.state} /><span className="lu-mono lu-muted" style={{ fontSize: 12 }}>{j.request.repo} · {j.request.precision} · r={j.request.lora?.r ?? 16}</span>{j.account_id != null && <Chip>клиент #{j.account_id}</Chip>}</div>
                <span className="lu-muted" style={{ fontSize: 12 }}>{ago(j.created_at * 1000)}</span>
              </CardHead>
              <CardBody className="lu-stack" style={{ gap: 8 }}>
                {j.group && <div className="lu-muted" style={{ fontSize: 12 }}>стадии: {j.group.ranks.map((r) => `${r.rank}→${r.node_id}`).join(", ")}</div>}
                {j.state === "running" && <><div className="lu-row lu-row--between" style={{ fontSize: 12 }}><span>шаг {j.progress.step ?? 0}{j.progress.total_steps ? ` из ${j.progress.total_steps}` : ""}{j.progress.loss !== undefined ? ` · loss ${j.progress.loss.toFixed(4)}` : ""}</span><span>{j.progress.eta_s ? `~${duration(j.progress.eta_s)}` : ""}</span></div><Bar percent={j.progress.total_steps ? ((j.progress.step ?? 0) / j.progress.total_steps) * 100 : 0} /></>}
                {j.state === "done" && j.result && <div className="lu-row lu-row--wrap"><StateBadge value="ready" label="адаптер готов" /><span className="lu-muted" style={{ fontSize: 13 }}>{j.result.steps} шагов, loss {j.result.final_loss?.toFixed(4)}</span>{["adapter_config.json", "adapter_model.safetensors"].map((f) => <Button key={f} size="sm" onClick={() => grab(`/admin/train/${j.group_id}/adapter/${f}`, f).catch((e) => toast("bad", e.message))}>{f}</Button>)}</div>}
                {j.error && <div style={{ fontSize: 13, color: j.state === "failed" ? "var(--bad-text)" : "var(--text-3)" }}>{j.error}</div>}
                {(j.state === "running" || j.state === "pending") && <div><Button size="sm" kind="ghost" style={{ color: "var(--bad-text)" }} onClick={() => setStopping(j)}>остановить</Button></div>}
              </CardBody>
            </Card>
          ))}
        </div>
      )}
      {creating && <TrainModal nodes={(nodes.data?.nodes ?? []).map((n) => n.node_id)} onClose={() => setCreating(false)} onDone={() => { setCreating(false); action.run(async () => {}); }} />}
      {stopping && <Confirm title={`Остановить ${stopping.label}?`} action="Остановить" body="Прогресс после последнего чекпоинта будет потерян." onClose={() => setStopping(null)} onConfirm={() => action.run(() => send(`/admin/train/${stopping.group_id}/stop`, "POST"), "остановлено")} />}
    </Page>
  );
}

function TrainModal({ nodes, onClose, onDone }: { nodes: string[]; onClose: () => void; onDone: () => void }) {
  const action = useAction(onDone);
  const [repo, setRepo] = useState(""); const [label, setLabel] = useState(""); const [file, setFile] = useState<File | null>(null);
  const [precision, setPrecision] = useState<"bf16" | "nf4">("bf16"); const [stages, setStages] = useState(1);
  const [r, setR] = useState(16); const [alpha, setAlpha] = useState(32); const [epochs, setEpochs] = useState(2); const [lr, setLr] = useState("0.0002"); const [maxLen, setMaxLen] = useState(2048); const [batch, setBatch] = useState(8); const [micro, setMicro] = useState(2);
  const [refusal, setRefusal] = useState(""); const [force, setForce] = useState(false);
  const start = () => action.run(async () => {
    if (!file) throw new Error("нужен датасет .jsonl");
    setRefusal("");
    try {
      await send("/admin/train", "POST", { repo: repo.trim(), label: label.trim() || undefined, precision, dataset: await b64(file), stages, max_len: maxLen, force,
        lora: { r, alpha, dropout: 0.05 }, schedule: { epochs, batch_size: batch, micro_size: micro, lr: Number(lr) || 2e-4, warmup_steps: 20, save_every: 50 } });
    } catch (e) { const t = e instanceof Error ? e.message : String(e); if (t.includes("не влезает")) { setRefusal(t); return; } throw e; }
  }, "обучение запущено");
  return (
    <Modal title="Дообучить модель" onClose={onClose} footer={<><span className="lu-spacer" /><Button kind="ghost" onClick={onClose}>Отмена</Button><Button kind="primary" disabled={action.busy || !repo.trim() || !file || (!!refusal && !force)} onClick={start}>Дообучить</Button></>}>
      <div className="lu-stack lu-stack--lg">
        <div className="lu-grid lu-grid--2">
          <Field label="Базовая модель" hint="bf16-чекпоинт: квантованные не подходят" htmlFor="t-repo" required><Input id="t-repo" mono value={repo} onChange={(e) => setRepo(e.target.value)} placeholder="Qwen/Qwen3-8B" /></Field>
          <Field label="Имя результата" htmlFor="t-label"><Input id="t-label" mono value={label} onChange={(e) => setLabel(e.target.value)} placeholder="my-lora" /></Field>
          <Field label="Датасет" hint='JSONL: {"messages": [...]}, последнее — assistant' htmlFor="t-ds" required><FilePick id="t-ds" label="выбрать .jsonl" accept=".jsonl,.json" onPick={setFile} /></Field>
          <Field label="Точность базы" htmlFor="t-p"><Select id="t-p" value={precision} onChange={(e) => setPrecision(e.target.value as "bf16" | "nf4")}><option value="bf16">bf16 (полная)</option><option value="nf4">nf4 (QLoRA)</option></Select></Field>
        </div>
        <div className="lu-grid lu-grid--3">
          <Field label="Стадий" hint={`доступно ${nodes.length}`} htmlFor="t-st"><NumberStepper id="t-st" value={stages} onChange={setStages} min={1} max={Math.max(1, nodes.length)} /></Field>
          <Field label="LoRA r" htmlFor="t-r"><NumberStepper id="t-r" value={r} onChange={setR} min={4} max={256} step={4} /></Field>
          <Field label="LoRA alpha" htmlFor="t-a"><NumberStepper id="t-a" value={alpha} onChange={setAlpha} min={4} max={512} step={4} /></Field>
          <Field label="Эпох" htmlFor="t-e"><NumberStepper id="t-e" value={epochs} onChange={setEpochs} min={1} max={20} /></Field>
          <Field label="LR" htmlFor="t-lr"><Input id="t-lr" mono value={lr} onChange={(e) => setLr(e.target.value)} /></Field>
          <Field label="Длина" htmlFor="t-ml"><NumberStepper id="t-ml" value={maxLen} onChange={setMaxLen} min={256} max={8192} step={256} /></Field>
          <Field label="Batch" htmlFor="t-b"><NumberStepper id="t-b" value={batch} onChange={setBatch} min={1} max={128} /></Field>
          <Field label="Micro" htmlFor="t-m"><NumberStepper id="t-m" value={micro} onChange={setMicro} min={1} max={32} /></Field>
        </div>
        {refusal && <div className="lu-stack"><Notice tone="warn">{refusal}</Notice><Toggle checked={force} onChange={setForce} label="всё равно запустить" /></div>}
      </div>
    </Modal>
  );
}

/* ------------------------------------------------------------------ задачи */
const DONE = ["done", "failed", "cancelled", "gone"];
export function Tasks() {
  const qc = useQueryClient();
  const toast = useToast();
  const tasks = useTasks();
  const groups = useGroups();
  const action = useAction(() => { qc.invalidateQueries({ queryKey: ["/admin/tasks"] }); qc.invalidateQueries({ queryKey: ["/admin/groups"] }); });
  const [q, setQ] = useState(""); const [filter, setFilter] = useState<"active" | "failed" | "all">("active");
  const [open, setOpen] = useState<Task | null>(null); const [log, setLog] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const rows = (tasks.data?.tasks ?? []).filter((t) => {
    const s = q.toLowerCase();
    if (s && !t.task_id.includes(s) && !t.node_id.toLowerCase().includes(s) && !t.command.join(" ").toLowerCase().includes(s)) return false;
    return filter === "all" ? true : filter === "failed" ? t.state === "failed" : !DONE.includes(t.state);
  });
  const current = open ? (tasks.data?.tasks ?? []).find((t) => t.task_id === open.task_id) ?? open : null;
  return (
    <Page>
      <PageHead title="Задачи" text={`${(tasks.data?.tasks ?? []).filter((t) => !DONE.includes(t.state)).length} выполняется`} actions={<Button kind="primary" icon={<Plus size={16} />} onClick={() => setRunning(true)}>Запустить</Button>} />
      <ErrorLine error={tasks.error} />
      <div className="lu-row lu-row--wrap"><SearchBox value={q} onChange={setQ} placeholder="поиск по id, узлу или команде" /><Segmented soft value={filter} onChange={setFilter} options={[{ value: "active", label: "активные" }, { value: "failed", label: "упавшие" }, { value: "all", label: "все" }]} /></div>
      <Card>
        {rows.length === 0 ? <Empty title="Задач нет" /> : (
          <div className="lu-table--wrap"><table className="lu-table lu-table--cards">
            <thead><tr><th>Задача</th><th>Команда</th><th>Состояние</th><th>Время</th><th>Результат</th><th /></tr></thead>
            <tbody>{rows.map((t) => <tr key={t.task_id}>
              <td data-label="Задача"><div style={{ display: "flex", flexDirection: "column" }}><span className="lu-mono" style={{ fontSize: 12 }}>{t.group_id ? `${t.group_id}-r${t.rank}` : t.task_id.slice(0, 12)}</span><span className="lu-muted" style={{ fontSize: 11 }}>{t.node_id}{t.group_id ? ` · rank ${t.rank}` : ""}</span></div></td>
              <td data-label="Команда" className="lu-mono" style={{ fontSize: 12, maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.command.join(" ") || (t.adopted ? "принята по докладу узла" : "—")}</td>
              <td data-label="Состояние"><StateBadge value={t.state} /></td>
              <td data-label="Время" className="lu-num">{duration(t.seconds)}</td>
              <td data-label="Результат">{t.results.length ? `${t.results.length} файл.` : t.error ? <span style={{ color: "var(--bad-text)", fontSize: 12 }}>{t.error.slice(0, 40)}</span> : "—"}</td>
              <td><Button size="sm" kind="ghost" onClick={() => { setOpen(t); setLog(null); }}>детали</Button></td>
            </tr>)}</tbody>
          </table></div>
        )}
      </Card>
      <Card>
        <CardHead><b>Группы</b></CardHead>
        {(groups.data?.groups ?? []).length === 0 ? <Empty title="Групп нет" /> : (
          <table className="lu-table lu-table--cards">
            <thead><tr><th>Группа</th><th>Ранги</th><th /></tr></thead>
            <tbody>{(groups.data?.groups ?? []).map((g) => <tr key={g.group_id}><td data-label="Группа"><b>{g.label}</b> <span className="lu-mono lu-muted" style={{ fontSize: 11 }}>{g.group_id}</span>{g.finished && <Badge size="sm" className="lu-ml">кончилась</Badge>}</td><td data-label="Ранги" className="lu-mono" style={{ fontSize: 12 }}>{g.ranks.map((r) => `${r.rank}: ${r.node_id}`).join("  ")}</td><td>{g.finished ? <Button size="sm" kind="ghost" onClick={() => action.run(() => send(`/admin/groups/${g.group_id}`, "DELETE"), "удалена")}>удалить</Button> : <Button size="sm" kind="ghost" style={{ color: "var(--bad-text)" }} onClick={() => action.run(() => send(`/admin/groups/${g.group_id}/stop`, "POST"), "снята")}>снять</Button>}</td></tr>)}</tbody>
          </table>
        )}
      </Card>
      {current && (
        <Drawer title={<span className="lu-mono">{current.task_id}</span>} onClose={() => setOpen(null)}>
          <div className="lu-stack lu-stack--lg">
            <KeyValue rows={[{ k: "узел", v: current.node_id }, { k: "состояние", v: <StateBadge value={current.state} /> }, { k: "время", v: duration(current.seconds) }, { k: "карты", v: current.devices.join(", ") || "—" }, { k: "код выхода", v: String(current.exit_code) }, { k: "группа", v: current.group_id ? `${current.group_id} · rank ${current.rank}` : "—" }]} />
            <pre className="lu-code" style={{ fontSize: 11 }}>{current.command.join(" ") || "—"}</pre>
            {current.error && <Notice tone="bad">{current.error}</Notice>}
            {current.results.length > 0 && <div className="lu-stack" style={{ gap: 4 }}><b>Результаты</b>{current.results.map((f) => <Button key={f.name} size="sm" onClick={() => grab(`/admin/tasks/${current.task_id}/results/${f.name}`, f.name).catch((e) => toast("bad", e.message))}>{f.name} · {num(f.size_bytes / 1024)} KB</Button>)}</div>}
            <div className="lu-row lu-row--wrap">
              <Button size="sm" onClick={() => action.run(async () => { const r = await get<{ text: string }>(`/admin/tasks/${current.task_id}/logs`); setLog(r.text); })}>лог</Button>
              {!DONE.includes(current.state) && <Button size="sm" kind="danger" onClick={() => action.run(() => send(`/admin/tasks/${current.task_id}/stop`, "POST"), "остановлена")}>остановить</Button>}
              {DONE.includes(current.state) && <Button size="sm" kind="ghost" onClick={() => action.run(() => send(`/admin/tasks/${current.task_id}`, "DELETE"), "удалена").then(() => setOpen(null))}>удалить</Button>}
            </div>
            {log !== null && <pre className="lu-code" style={{ maxHeight: 360, overflow: "auto", fontSize: 11 }}>{log || "пусто"}</pre>}
          </div>
        </Drawer>
      )}
      {running && <RunTaskModal onClose={() => setRunning(false)} onDone={() => { setRunning(false); action.run(async () => {}); }} />}
    </Page>
  );
}

function RunTaskModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const action = useAction(onDone);
  const [command, setCommand] = useState(""); const [req, setReq] = useState(""); const [gpus, setGpus] = useState(0); const [timeout, setTimeoutS] = useState(600); const [file, setFile] = useState<File | null>(null);
  const run = () => action.run(async () => {
    const deps = req.split(/[,\s]+/).filter(Boolean);
    const body: Record<string, unknown> = { command, timeout_s: timeout, resources: { gpus }, environment: deps.length ? { kind: "python", requirements: deps } : { kind: "none" } };
    if (file) body.inputs = { [file.name]: await b64(file) };
    await send("/admin/tasks", "POST", body);
  }, "задача отправлена");
  return (
    <Modal title="Запустить задачу" onClose={onClose} footer={<><span className="lu-spacer" /><Button kind="ghost" onClick={onClose}>Отмена</Button><Button kind="primary" onClick={run} disabled={action.busy || !command}>Запустить</Button></>}>
      <div className="lu-stack lu-stack--lg">
        <Field label="Команда" hint='кавычки понимаются: python -c "print(1)"' htmlFor="rt-cmd"><Input id="rt-cmd" mono autoFocus value={command} onChange={(e) => setCommand(e.target.value)} placeholder='python -c "print(1)"' /></Field>
        <div className="lu-grid lu-grid--3">
          <Field label="Зависимости" hint="pip, через запятую" htmlFor="rt-req"><Input id="rt-req" value={req} onChange={(e) => setReq(e.target.value)} placeholder="numpy, pillow" /></Field>
          <Field label="GPU" htmlFor="rt-g"><NumberStepper id="rt-g" value={gpus} onChange={setGpus} min={0} max={8} /></Field>
          <Field label="Таймаут, с" htmlFor="rt-t"><NumberStepper id="rt-t" value={timeout} onChange={setTimeoutS} min={60} max={86400} step={60} /></Field>
        </div>
        <Field label="Входной файл" hint="положится в каталог задачи" htmlFor="rt-f"><FilePick id="rt-f" label="выбрать файл" onPick={setFile} /></Field>
      </div>
    </Modal>
  );
}

/* --------------------------------------------------------------------- Ray */
const RAY_TEMPLATE = `import os
import ray

# ДО импорта ray. Плазма-сокет ложится внутрь временного каталога Ray, а путь
# unix-сокета не может быть длиннее 103 байт — каталог задачи в лимит не
# влезает. LOOMA_TASK_TMP агент даёт как раз для этого.
os.environ.setdefault("RAY_TMPDIR", os.environ["LOOMA_TASK_TMP"])

ray.init()          # подключится к кластеру, который уже поднял ранг 0

@ray.remote
def work(n):        # имя латиницей: Ray кодирует его в ASCII
    return n * n

answers = ray.get([work.remote(i) for i in range(100)])

# Результат — только то, что легло сюда. Всё остальное считается черновиком.
with open(os.path.join(os.environ["LOOMA_TASK_OUT"], "answer.txt"), "w") as f:
    f.write(str(sum(answers)))`;

export function Ray() {
  const qc = useQueryClient();
  const toast = useToast();
  const groups = useGroups();
  const tasks = useTasks();
  const nodes = useNodes();
  const action = useAction(() => { qc.invalidateQueries({ queryKey: ["/admin/groups"] }); qc.invalidateQueries({ queryKey: ["/admin/tasks"] }); });
  const [picked, setPicked] = useState<string[]>([]); const [size, setSize] = useState(1); const [gpus, setGpus] = useState(1);
  const [label, setLabel] = useState(""); const [reqs, setReqs] = useState(""); const [version, setVersion] = useState(""); const [script, setScript] = useState<File | null>(null);
  const [stopping, setStopping] = useState<Group | null>(null);
  const free = (nodes.data?.nodes ?? []).filter((n) => n.accepts_tasks);
  const isRay = (g: Group) => (tasks.data?.tasks ?? []).some((t) => t.group_id === g.group_id && t.command.join(" ").includes("looma_ray.server"));
  const clusters = (groups.data?.groups ?? []).filter((g) => isRay(g) && !g.finished);
  const launch = () => action.run(async () => {
    await send("/admin/ray", "POST", { node_ids: picked.length ? picked.flatMap((n) => Array(size).fill(n)) : undefined, size: picked.length ? undefined : size, resources: { gpus }, script: script ? await b64(script) : undefined, label: label || undefined, ray_version: version || undefined, requirements: reqs.trim() || undefined });
    setScript(null); setLabel("");
  }, script ? "кластер поднимается, скрипт запустится сам" : "кластер поднимается");
  return (
    <Page>
      <PageHead title="Ray" text="кластер под задачу: живёт, пока живёт задача, и умирает вместе с ней" />
      <ErrorLine error={groups.error ?? nodes.error} />
      <Card panel>
        <CardHead><b>Поднять кластер</b></CardHead>
        <CardBody className="lu-stack lu-stack--lg">
          <div className="lu-grid lu-grid--3">
            <Field label="Узлы" hint={!free.length ? "нет узлов, берущих работу" : picked.length ? `выбрано ${picked.length} из ${free.length}` : "ничего не выбрано — возьмёт тех, кто легче сходится с соседями"}>
              <div className="lu-stack" style={{ gap: 4, maxHeight: 180, overflowY: "auto" }}>{free.map((n) => <label key={n.node_id} className="lu-row" style={{ padding: "6px 8px", borderRadius: 6, cursor: "pointer", background: picked.includes(n.node_id) ? "var(--accent-soft)" : undefined }}><input type="checkbox" style={{ accentColor: "var(--accent)" }} checked={picked.includes(n.node_id)} onChange={() => setPicked((p) => p.includes(n.node_id) ? p.filter((x) => x !== n.node_id) : [...p, n.node_id])} /><span className="lu-mono" style={{ fontSize: 13 }}>{n.node_id}</span><span className="lu-muted" style={{ marginLeft: "auto", fontSize: 12 }}>{n.gpus_free}/{n.gpus_total} GPU</span></label>)}</div>
            </Field>
            <Field label={picked.length ? "рангов на узел" : "узлов"} hint={picked.length ? "узел, названный дважды, получит два ранга" : `доступно ${free.length}`} htmlFor="ray-size"><NumberStepper id="ray-size" value={size} onChange={setSize} min={1} max={Math.max(1, free.length * 4)} /></Field>
            <Field label="карт на ранг" hint="столько GPU получит каждый ранг" htmlFor="ray-g"><NumberStepper id="ray-g" value={gpus} onChange={setGpus} min={0} max={8} /></Field>
            <Field label="метка" hint="чтобы найти его потом" htmlFor="ray-l"><Input id="ray-l" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="перебор-гиперпараметров" /></Field>
            <Field label="библиотеки" hint="по строке на пакет — ставятся на каждый узел" htmlFor="ray-r"><Textarea id="ray-r" mono value={reqs} onChange={(e) => setReqs(e.target.value)} placeholder={"torch\nnumpy"} /></Field>
            <Field label="версия ray" hint="пусто — последняя" htmlFor="ray-v"><Input id="ray-v" mono value={version} onChange={(e) => setVersion(e.target.value)} placeholder="2.58.0" /></Field>
            <Field label="точка входа" hint="без неё кластер просто стоит и ждёт" htmlFor="ray-s"><FilePick id="ray-s" label="выбрать .py" accept=".py" onPick={setScript} /></Field>
          </div>
          <div className="lu-row lu-row--wrap"><Button kind="primary" onClick={launch} disabled={action.busy || !free.length}>Поднять кластер</Button><span className="lu-muted" style={{ fontSize: 12 }}>ранги находят друг друга через агента: порты соседей он держит у себя на локалхосте, и Ray про NAT не узнаёт</span></div>
        </CardBody>
      </Card>
      <Card>
        <CardHead><b>Работают</b></CardHead>
        {clusters.length === 0 ? <Empty title="Кластеров нет">Ray здесь — обычная задача: агент не отличает её от любой другой.</Empty> : (
          <table className="lu-table lu-table--cards"><thead><tr><th>Кластер</th><th>Ранги</th><th>С</th><th /></tr></thead>
            <tbody>{clusters.map((g) => <tr key={g.group_id}><td data-label="Кластер"><b>{g.label}</b> <span className="lu-mono lu-muted" style={{ fontSize: 11 }}>{g.group_id}</span></td><td data-label="Ранги" className="lu-mono" style={{ fontSize: 12 }}>{g.ranks.map((r) => `${r.rank}: ${r.node_id}`).join("  ")}</td><td data-label="С">{ago(g.submitted_at * 1000)}</td><td><Button size="sm" kind="ghost" style={{ color: "var(--bad-text)" }} onClick={() => setStopping(g)}>снять</Button></td></tr>)}</tbody></table>
        )}
      </Card>
      <Card>
        <CardHead><b>Как писать задачу</b><Button size="sm" onClick={() => { navigator.clipboard?.writeText(RAY_TEMPLATE); toast("ok", "шаблон скопирован"); }}>скопировать шаблон</Button></CardHead>
        <CardBody><pre className="lu-code" style={{ fontSize: 11 }}>{RAY_TEMPLATE}</pre></CardBody>
      </Card>
      <Card>
        <CardHead><b>Что здесь поедет, а что нет</b></CardHead>
        <table className="lu-table"><thead><tr><th>Форма задачи</th><th>Обмен между узлами</th><th>На этом железе</th></tr></thead>
          <tbody>
            <tr><td>независимые куски</td><td>ничего</td><td><Badge tone="ok">да</Badge></td></tr>
            <tr><td>конвейер по слоям</td><td>активации, ~8 КБ на токен</td><td><Badge tone="ok">да</Badge></td></tr>
            <tr><td>тензорный параллелизм</td><td>allreduce внутри каждого слоя</td><td><Badge tone="bad">нет</Badge></td></tr>
            <tr><td>обучение DDP / FSDP</td><td>градиенты каждый шаг = размер модели</td><td><Badge tone="bad">нет</Badge></td></tr>
          </tbody></table>
        <CardBody className="lu-dim" style={{ fontSize: 13 }}>Ray не объединяет VRAM: одна аллокация CUDA не может лежать на двух машинах. Он планировщик и транспорт — разрезать задачу должна стратегия, и две нижние на домашних каналах не работают. Обучение модели на 7B требует ~14 ГБ обмена на шаг: на канале 100 Мбит это двадцать минут за шаг. Ray запустит это и не предупредит.</CardBody>
      </Card>
      {stopping && <Confirm title={`Снять ${stopping.label}?`} action="Снять" body="Ранги остановятся; результат — только то, что уже легло в LOOMA_TASK_OUT." onClose={() => setStopping(null)} onConfirm={() => action.run(() => send(`/admin/groups/${stopping.group_id}/stop`, "POST"), "снят")} />}
    </Page>
  );
}
