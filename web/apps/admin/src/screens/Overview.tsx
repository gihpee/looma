/** Обзор: всё ли в порядке — одним взглядом. Плитки, «требует внимания» без
 *  дублей, сеть полосами, живые группы. */
import { Link } from "react-router-dom";
import { useGroupsHealth, useNodes, useTasks, useVersions, useAdminTrainJobs, useLeases } from "@looma/api";
import { Badge, Card, CardBody, CardHead, Empty, NetworkFabric, Page, PageHead, Stat, StateBadge, bytes, plural } from "@looma/ui";
import { ErrorLine, phase } from "../lib";

export function Overview() {
  const nodes = useNodes();
  const tasks = useTasks();
  const health = useGroupsHealth();
  const versions = useVersions();
  const jobs = useAdminTrainJobs();
  const leases = useLeases();
  const list = nodes.data?.nodes ?? [];
  const groups = health.data?.groups ?? [];
  const gpusFree = list.reduce((n, x) => n + x.gpus_free, 0);
  const gpusAll = list.reduce((n, x) => n + x.gpus_total, 0);
  const vram = list.reduce((n, x) => n + x.vram_free_bytes, 0);
  const active = (tasks.data?.tasks ?? []).filter((t) => !["done", "failed", "cancelled", "gone"].includes(t.state));
  const failed = (tasks.data?.tasks ?? []).filter((t) => t.state === "failed").length;
  const ready = groups.filter((g) => g.ready).length;
  const training = (jobs.data?.jobs ?? []).filter((j) => j.state === "running").length;

  // «Требует внимания» — по одному пункту на причину, без дублей.
  const attention: { key: string; to: string; tone: "warn" | "bad"; text: string }[] = [];
  for (const n of list) {
    if (!n.accepts_tasks && n.refusal) attention.push({ key: `n-${n.node_id}`, to: "/nodes", tone: "warn", text: `${n.node_id}: не берёт задачи — ${n.refusal}` });
    else if (!n.in_network && n.peer_id) attention.push({ key: `p-${n.node_id}`, to: "/nodes", tone: "bad", text: `${n.node_id}: вне p2p-сети` });
    if (n.update_error) attention.push({ key: `u-${n.node_id}`, to: "/release", tone: "warn", text: `${n.node_id}: обновление — ${n.update_error}` });
  }
  for (const g of groups) {
    const bad = g.stages.find((s) => s.state === "failed");
    if (bad) attention.push({ key: `g-${g.group_id}`, to: "/models", tone: "bad", text: `${g.label}: стадия ${bad.rank} упала${bad.error ? ` — ${bad.error}` : ""}` });
  }
  for (const l of leases.data?.leases ?? []) if (!l.alive) attention.push({ key: `l-${l.group_id}`, to: "/leases", tone: "warn", text: `аренда ${l.label || l.group_id}: группа кончилась, счёт открыт` });
  const nodeStates = list.map((n) => ({ id: n.node_id, state: n.tasks_running ? ("inference" as const) : ("free" as const), gpus: n.gpus_total }));

  return (
    <Page>
      <PageHead title="Обзор" text={list.length ? `${plural(list.length, ["узел", "узла", "узлов"])} на связи` : "узлов на связи нет"} />
      <ErrorLine error={nodes.error} />
      <div className="lu-grid lu-grid--stats">
        <Stat label="Узлы" value={list.length} sub={list.every((n) => n.accepts_tasks) ? "все принимают" : `${list.filter((n) => !n.accepts_tasks).length} не берут задачи`} subTone={list.every((n) => n.accepts_tasks) ? "ok" : "bad"} />
        <Stat label="GPU свободно" value={<>{gpusFree} <small>из {gpusAll}</small></>} sub={`${bytes(vram)} VRAM`} />
        <Stat label="Модели" value={ready} sub={`${groups.length - ready} поднимается · ${training} обучается`} />
        <Stat label="Задачи" value={active.length} sub={failed ? `${failed} упало` : "падений нет"} subTone={failed ? "bad" : undefined} />
      </div>
      <div className="lu-grid lu-grid--main">
        <div className="lu-stack" style={{ gap: 16 }}>
          <Card>
            <CardHead><b>Требует внимания</b>{attention.length > 0 && <Badge tone="warn">{attention.length}</Badge>}</CardHead>
            {attention.length === 0 ? <Empty title="Всё в порядке">Узлы на связи, стадии не падают, зависших аренд нет.</Empty> : (
              <CardBody className="lu-stack" style={{ gap: 6 }}>
                {attention.slice(0, 12).map((a) => <Link key={a.key} to={a.to} className="lu-row" style={{ fontSize: 13, color: "var(--text)", padding: "6px 0", borderBottom: "1px solid var(--border)" }}><Badge tone={a.tone} size="sm" dot>{a.to.slice(1)}</Badge><span>{a.text}</span></Link>)}
              </CardBody>
            )}
          </Card>
          <Card>
            <CardHead><b>Живые группы</b><Link to="/models" style={{ fontSize: 13, fontWeight: 500 }}>все →</Link></CardHead>
            {groups.length === 0 ? <Empty title="Ничего не запущено" /> : (
              <table className="lu-table lu-table--cards">
                <thead><tr><th>Группа</th><th>Стадий</th><th>Состояние</th></tr></thead>
                <tbody>{groups.map((g) => { const worst = g.stages.find((s) => s.state === "failed") ?? g.stages.find((s) => !s.ready) ?? g.stages[0]; const [ph] = worst ? phase(worst) : ["—", 0];
                  return <tr key={g.group_id}><td data-label="Группа"><b>{g.label}</b> <span className="lu-mono lu-muted" style={{ fontSize: 11 }}>{g.group_id}</span></td><td data-label="Стадий" className="lu-num">{g.stages.length}</td><td data-label="Состояние"><StateBadge value={g.ready ? "ready" : worst?.state ?? "pending"} label={g.ready ? "отвечает" : ph} /></td></tr>; })}</tbody>
              </table>
            )}
          </Card>
        </div>
        <div className="lu-stack" style={{ gap: 16 }}>
          <Card pad>
            <div className="lu-row lu-row--between" style={{ marginBottom: 10 }}><b>Сеть</b><span className="lu-mono lu-muted" style={{ fontSize: 12 }}>{list.length} узлов · {list.filter((n) => !n.tasks_running).length} свободно</span></div>
            {list.length ? <NetworkFabric nodes={nodeStates} names only={["inference", "free"]} /> : <div className="lu-muted" style={{ fontSize: 13 }}>Нет узлов.</div>}
          </Card>
          <Card pad>
            <b>Агент</b>
            <div className="lu-dim" style={{ fontSize: 13, marginTop: 6 }}>
              {versions.data?.release ? <>релиз <span className="lu-mono">{versions.data.release.version}</span> · выкатка {versions.data.release.wave_percent}% · {versions.data.nodes_on_target} из {versions.data.nodes_total} на цели</> : "релиз не опубликован"}
            </div>
            <Link to="/release" style={{ fontSize: 13, fontWeight: 500 }}>к релизам →</Link>
          </Card>
        </div>
      </div>
    </Page>
  );
}
