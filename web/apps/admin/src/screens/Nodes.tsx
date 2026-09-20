/** Узлы: таблица с фильтром и дровер с железом, кэшами, p2p-каналом и логом. */
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { get, send, useConnect, useNodes, type Node } from "@looma/api";
import { Badge, Bar, Button, Card, Drawer, Empty, KeyValue, Page, PageHead, SearchBox, Segmented, Stat, bytes, useAction } from "@looma/ui";
import { ErrorLine, linkBadge } from "../lib";

type Filter = "all" | "idle" | "trouble";

export function Nodes() {
  const nodes = useNodes();
  const connect = useConnect();
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [open, setOpen] = useState<string | null>(null);
  const all = nodes.data?.nodes ?? [];
  const rows = all.filter((n) => {
    const s = q.toLowerCase();
    if (s && !n.node_id.toLowerCase().includes(s) && !n.gpu_name.toLowerCase().includes(s)) return false;
    if (filter === "idle") return n.tasks_running === 0;
    if (filter === "trouble") return !n.accepts_tasks || !n.in_network || !!n.update_error || n.seconds_since_seen > 20;
    return true;
  });
  const current = all.find((n) => n.node_id === open) ?? null;

  return (
    <Page>
      <PageHead title="Узлы" text={connect.data?.dial_address ? <>звонят на <span className="lu-mono">{connect.data.dial_address}</span></> : undefined} />
      <ErrorLine error={nodes.error} />
      <div className="lu-row lu-row--wrap">
        <SearchBox value={q} onChange={setQ} placeholder="поиск по имени или карте" />
        <Segmented soft value={filter} onChange={setFilter} options={[{ value: "all", label: "все" }, { value: "idle", label: "простаивают" }, { value: "trouble", label: "с проблемой" }]} />
        <span className="lu-muted" style={{ fontSize: 13, marginLeft: "auto" }}>{rows.length} из {all.length}</span>
      </div>
      <Card>
        {rows.length === 0 ? <Empty title="Узлов нет">Подключите машину ключом из раздела «Ключи подключения».</Empty> : (
          <div className="lu-table--wrap">
            <table className="lu-table lu-table--cards">
              <thead><tr><th>Узел</th><th>Железо</th><th>GPU</th><th>VRAM</th><th>Состояние</th><th>Снаружи</th><th>Агент</th><th /></tr></thead>
              <tbody>
                {rows.map((n) => { const lb = linkBadge(n); return (
                  <tr key={n.node_id}>
                    <td data-label="Узел"><div style={{ display: "flex", flexDirection: "column" }}><b className="lu-mono" style={{ fontSize: 13 }}>{n.node_id}</b><span className="lu-muted" style={{ fontSize: 11 }}>{n.seconds_since_seen.toFixed(1)}s назад{n.region && n.region !== "default" ? ` · ${n.region}` : ""}</span></div></td>
                    <td data-label="Железо"><div style={{ display: "flex", flexDirection: "column" }}><span>{n.gpu_name || n.device}</span><span className="lu-muted" style={{ fontSize: 11 }}>{n.cuda_version ? `CUDA ${n.cuda_version}` : n.device}</span></div></td>
                    <td data-label="GPU"><div style={{ minWidth: 70 }}><span className="lu-mono">{n.gpus_free}/{n.gpus_total}</span><Bar percent={n.gpus_total ? ((n.gpus_total - n.gpus_free) / n.gpus_total) * 100 : 0} /></div></td>
                    <td data-label="VRAM" className="lu-num">{bytes(n.vram_free_bytes)}</td>
                    <td data-label="Состояние">{n.accepts_tasks ? <Badge tone={n.tasks_running ? "ok" : "dim"} pulse={n.tasks_running > 0}>{n.tasks_running} задач</Badge> : <Badge tone="bad">{n.refusal || "не берёт"}</Badge>}</td>
                    <td data-label="Снаружи"><Badge tone={lb.tone}>{lb.label}</Badge></td>
                    <td data-label="Агент" className="lu-mono" style={{ fontSize: 12 }}>{n.agent_version}{n.update_state && n.update_state !== "idle" ? <Badge tone="warn" size="sm" dot={false}>{n.update_state}</Badge> : null}</td>
                    <td><Button size="sm" kind="ghost" onClick={() => setOpen(n.node_id)}>детали</Button></td>
                  </tr>); })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {current && <NodeDrawer node={current} onClose={() => setOpen(null)} />}
    </Page>
  );
}

function NodeDrawer({ node: n, onClose }: { node: Node; onClose: () => void }) {
  const qc = useQueryClient();
  const action = useAction(() => qc.invalidateQueries({ queryKey: ["/admin/agents"] }));
  const [log, setLog] = useState<string | null>(null);
  const lb = linkBadge(n);
  const showLog = () => action.run(async () => { const r = await get<{ text: string }>(`/admin/agents/${encodeURIComponent(n.node_id)}/logs`); setLog(r.text); });
  return (
    <Drawer title={<span className="lu-mono">{n.node_id}</span>} onClose={onClose}>
      <div className="lu-stack lu-stack--lg">
        <div className="lu-grid lu-grid--2">
          <Stat label="GPU" value={<>{n.gpus_free} <small>из {n.gpus_total}</small></>} sub={`${bytes(n.vram_free_bytes)} свободно`} />
          <Stat label="Диск" value={bytes(n.disk_total_bytes - n.disk_free_bytes).replace(" GB", "")} unit={`из ${bytes(n.disk_total_bytes)}`} sub="том с кэшами и задачами" />
          <Stat label="Кэш окружений" value={bytes(n.env_cache_bytes)} sub={n.environment_kinds.join(", ")} />
          <Stat label="Кэш моделей" value={bytes(n.model_cache_bytes)} sub="веса, скачанные один раз на узел" />
        </div>
        <section className="lu-stack">
          <div className="lu-row lu-row--between"><b>Железо</b><Button size="sm" kind="ghost" disabled={action.busy} onClick={() => action.run(() => send(`/admin/agents/${encodeURIComponent(n.node_id)}/rescan`, "POST"), "перечитано")}>перечитать</Button></div>
          <Card pad><KeyValue rows={[{ k: "карта", v: n.gpu_name || "—" }, { k: "устройство", v: n.device }, { k: "CUDA драйвера", v: n.cuda_version || "—" }, { k: "RAM хоста", v: `${n.host_ram_gb} GB` }, { k: "регион", v: n.region }]} /></Card>
        </section>
        <section className="lu-stack">
          <div className="lu-row lu-row--between"><b>Канал к соседям</b><Badge tone={lb.tone}>{lb.label}</Badge></div>
          <Card pad><KeyValue rows={[
            { k: "peer id", v: <span className="lu-mono">{n.peer_id ? `${n.peer_id.slice(0, 20)}…` : "—"}</span> },
            { k: "в сети узлов", v: n.in_network ? "да — видит точку встречи" : "нет" },
            { k: "прямых / через оркестратор", v: `${n.direct} / ${n.relayed}` },
            { k: "RTT до соседа", v: n.link_rtt_ms ? `${n.link_rtt_ms.toFixed(1)} ms` : "—" },
            ...(n.relay_rtt_ms ? [{ k: "RTT до оркестратора", v: `${n.relay_rtt_ms.toFixed(1)} ms` }] : []),
          ]} />
          {n.visible_addrs.length > 0 && <details style={{ marginTop: 8, fontSize: 12 }}><summary className="lu-muted">адреса ({n.visible_addrs.length})</summary><div className="lu-mono" style={{ wordBreak: "break-all", marginTop: 6 }}>{n.visible_addrs.map((a) => <div key={a}>{a}</div>)}</div></details>}
          </Card>
        </section>
        <section className="lu-stack">
          <div className="lu-row lu-row--between"><b>Агент</b><Button size="sm" kind="ghost" disabled={action.busy} onClick={() => action.run(() => send(`/admin/agents/${encodeURIComponent(n.node_id)}/restart`, "POST"), "перезапуск запрошен")}>перезапустить</Button></div>
          <Card pad><KeyValue rows={[{ k: "версия", v: <span className="lu-mono">{n.agent_version}</span> }, { k: "обновление", v: n.update_state || "idle" }, ...(n.update_error ? [{ k: "ошибка", v: <span style={{ color: "var(--bad-text)" }}>{n.update_error}</span> }] : [])]} /></Card>
        </section>
        <section className="lu-stack">
          <div className="lu-row lu-row--between"><b>Лог агента</b><Button size="sm" kind="ghost" onClick={showLog} disabled={action.busy}>{log === null ? "показать" : "обновить"}</Button></div>
          {log !== null && <pre className="lu-code" style={{ maxHeight: 320, overflow: "auto", fontSize: 11 }}>{log || "пусто"}</pre>}
        </section>
      </div>
    </Drawer>
  );
}
