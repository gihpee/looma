/** Сеть: ключи подключения (адрес оркестратора внутри ключа) и релизы
 *  агента с выкаткой по процентам. */
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { send, useConnect, useJoinKeys, useNodes, useVersions, type JoinKey } from "@looma/api";
import { Badge, Bar, Button, Card, CardHead, CodeBlock, Confirm, Empty, Field, FilePick, Input, Modal, Notice, NumberStepper, Page, PageHead, StateBadge, bytes, dateTime, useAction, useToast } from "@looma/ui";
import { ErrorLine, b64 } from "../lib";

export function JoinKeys() {
  const qc = useQueryClient();
  const keys = useJoinKeys();
  const connect = useConnect();
  const action = useAction(() => qc.invalidateQueries({ queryKey: ["/admin/keys"] }));
  const [issuing, setIssuing] = useState(false); const [label, setLabel] = useState(""); const [maxNodes, setMaxNodes] = useState(0);
  const [command, setCommand] = useState(""); const [revoking, setRevoking] = useState<JoinKey | null>(null);
  const list = keys.data?.keys ?? [];
  const issue = () => action.run(async () => {
    const key = await send<JoinKey>("/admin/keys", "POST", { label, max_nodes: maxNodes });
    setCommand(`docker run -d --gpus all --restart unless-stopped --network host \\\n  -v looma-data:/var/lib/looma ${key.agent_image} --key ${key.key}`);
    setLabel(""); setIssuing(false);
  }, "ключ выдан");
  return (
    <Page>
      <PageHead title="Ключи подключения" text="Адрес оркестратора внутри ключа — владелец машины больше ничего не вводит." actions={<Button kind="primary" icon={<Plus size={16} />} onClick={() => setIssuing(true)}>Выдать ключ</Button>} />
      <ErrorLine error={keys.error} />
      {connect.data?.warning && <Notice tone={connect.data.severity === "warn" ? "warn" : "info"}>{connect.data.warning}</Notice>}
      {command && (
        <Card pad style={{ borderColor: "var(--accent)" }}>
          <b>Команда для владельца машины</b>
          <div style={{ margin: "10px 0" }}><CodeBlock code={command} /></div>
          <div className="lu-muted" style={{ fontSize: 12 }}><code>--network host</code> — иначе прямой канал между узлами невозможен. <code>-v looma-data</code> — иначе кэш и обновления не переживут перезапуск.</div>
          <div style={{ marginTop: 8 }}><Button size="sm" kind="ghost" onClick={() => setCommand("")}>скрыть</Button></div>
        </Card>
      )}
      <Card>
        {list.length === 0 ? <Empty title="Ключей нет">Каждый ключ — приглашение для одной или нескольких машин.</Empty> : (
          <table className="lu-table lu-table--cards"><thead><tr><th>ID</th><th>Метка</th><th>Узлы</th><th>Лимит</th><th /></tr></thead>
            <tbody>{list.map((k) => <tr key={k.key_id} style={{ opacity: k.revoked ? .5 : 1 }}>
              <td data-label="ID" className="lu-mono">{k.key_id}</td><td data-label="Метка">{k.label || <span className="lu-muted">—</span>}</td>
              <td data-label="Узлы">{k.nodes.length === 0 ? <span className="lu-muted">ни одного</span> : <details><summary style={{ cursor: "pointer" }}>{k.nodes.length} узл.</summary><div className="lu-mono" style={{ fontSize: 12, marginTop: 4 }}>{k.nodes.slice(0, 40).map((n) => <div key={n}>{n}</div>)}{k.nodes.length > 40 && <div className="lu-muted">… и ещё {k.nodes.length - 40}</div>}</div></details>}</td>
              <td data-label="Лимит" className="lu-mono">{k.max_nodes || "∞"}</td>
              <td>{k.revoked ? <Badge>отозван</Badge> : <Button size="sm" kind="danger" onClick={() => setRevoking(k)}>отозвать</Button>}</td>
            </tr>)}</tbody></table>
        )}
      </Card>
      {issuing && (
        <Modal title="Выдать ключ" size="sm" onClose={() => setIssuing(false)} footer={<><span className="lu-spacer" /><Button kind="ghost" onClick={() => setIssuing(false)}>Отмена</Button><Button kind="primary" onClick={issue} disabled={action.busy}>Выдать</Button></>}>
          <div className="lu-stack">
            <Field label="Метка" hint="кому выдан: человек, площадка" htmlFor="jk-l"><Input id="jk-l" value={label} onChange={(e) => setLabel(e.target.value)} autoFocus /></Field>
            <Field label="Лимит узлов" hint="0 — без ограничения" htmlFor="jk-m"><NumberStepper id="jk-m" value={maxNodes} onChange={setMaxNodes} min={0} max={1000} /></Field>
          </div>
        </Modal>
      )}
      {revoking && <Confirm title={`Отозвать ключ ${revoking.key_id}?`} action="Отозвать" body="Новые узлы с этим ключом не подключатся. Уже подключённые продолжат работать." onClose={() => setRevoking(null)} onConfirm={() => action.run(() => send(`/admin/keys/${revoking.key_id}`, "DELETE"), "ключ отозван")} />}
    </Page>
  );
}

export function Release() {
  const qc = useQueryClient();
  const toast = useToast();
  const map = useVersions();
  const nodes = useNodes(4000);
  const action = useAction(() => qc.invalidateQueries({ queryKey: ["/admin/release"] }));
  const [publishing, setPublishing] = useState(false); const [version, setVersion] = useState(""); const [signature, setSignature] = useState(""); const [archive, setArchive] = useState<File | null>(null);
  const [stopping, setStopping] = useState(false);
  const current = map.data?.release; const list = nodes.data?.nodes ?? [];
  const onTarget = current ? list.filter((n) => n.agent_version === current.version).length : 0;
  const readManifest = async (f: File | null) => { if (!f) return; try { const p = JSON.parse(await f.text()); setVersion(p.version ?? ""); setSignature(p.signature ?? ""); toast("ok", `манифест ${p.version}`); } catch (e) { toast("bad", `манифест не читается: ${e}`); } };
  const publish = () => action.run(async () => { if (!archive) throw new Error("нужен архив .tar.gz"); await send("/admin/release", "POST", { version, signature, archive: await b64(archive) }); setPublishing(false); }, "опубликовано, выкатка на 0%");
  const wave = (percent: number) => action.run(async () => { const a = await send<{ wave_percent: number; nodes_told: number }>("/admin/release/wave", "POST", { percent }); toast("ok", `выкатка ${a.wave_percent}% · уведомлено узлов: ${a.nodes_told}`); });
  return (
    <Page>
      <PageHead title="Релизы агента" text={current ? `${current.version} · выкатка ${current.wave_percent}%` : "релиз не опубликован"} actions={<Button kind="primary" onClick={() => setPublishing(true)}>Опубликовать</Button>} />
      <ErrorLine error={map.error} />
      {current ? (
        <Card pad>
          <div className="lu-row lu-row--wrap"><b style={{ fontSize: 16 }} className="lu-mono">{current.version}</b><span className="lu-muted">{bytes(current.size_bytes)} · {dateTime(current.published_at * 1000)}</span><Badge tone={current.wave_percent === 0 ? "dim" : current.wave_percent === 100 ? "info" : "warn"}>выкатка {current.wave_percent}%</Badge><span className="lu-spacer" /><span>{onTarget} из {list.length} узлов обновились</span></div>
          <div style={{ margin: "12px 0" }}><Bar percent={list.length ? (onTarget / list.length) * 100 : 0} tone={onTarget === list.length && list.length > 0 ? "ok" : undefined} /></div>
          <div className="lu-row lu-row--wrap">{[10, 25, 50, 100].map((p) => <Button key={p} onClick={() => wave(p)} disabled={action.busy} kind={p === 100 ? "primary" : "secondary"}>{p === 100 ? "весь парк" : `${p}%`}</Button>)}<span className="lu-spacer" /><Button kind="danger" onClick={() => setStopping(true)}>остановить выкатку</Button></div>
          <div className="lu-muted" style={{ fontSize: 12, marginTop: 10 }}>Процент — доля узлов, которым сказано обновиться; узел обновляется, когда закончит текущие задачи.</div>
        </Card>
      ) : (
        <Card><Empty title="Релиз не опубликован">Архив и манифест делает <code>scripts/sign_release.py sign</code>. Подпись ставится ключом, которого у оркестратора нет.</Empty></Card>
      )}
      <Card>
        <CardHead><b>Узлы</b></CardHead>
        {list.length === 0 ? <Empty title="Узлов нет" /> : (
          <table className="lu-table lu-table--cards"><thead><tr><th>Узел</th><th>Версия</th><th>Обновление</th><th>Что мешает</th></tr></thead>
            <tbody>{list.map((n) => <tr key={n.node_id}><td data-label="Узел" className="lu-mono">{n.node_id}</td><td data-label="Версия" className="lu-mono">{n.agent_version}{current && n.agent_version === current.version && <Badge tone="ok" size="sm" className="lu-ml">на цели</Badge>}</td><td data-label="Обновление"><StateBadge value={n.update_state || "idle"} /></td><td data-label="Что мешает" style={{ color: n.update_error ? "var(--bad-text)" : undefined }}>{n.update_error || "—"}</td></tr>)}</tbody></table>
        )}
      </Card>
      {publishing && (
        <Modal title="Опубликовать релиз" size="sm" onClose={() => setPublishing(false)} footer={<><span className="lu-spacer" /><Button kind="ghost" onClick={() => setPublishing(false)}>Отмена</Button><Button kind="primary" onClick={publish} disabled={action.busy || !version || !signature || !archive}>Опубликовать</Button></>}>
          <div className="lu-stack">
            <Field label="Манифест" hint="release.json из sign_release.py — заполнит версию и подпись" htmlFor="rl-m"><FilePick id="rl-m" label="выбрать release.json" accept=".json" onPick={readManifest} /></Field>
            <Field label="Версия" htmlFor="rl-v"><Input id="rl-v" mono value={version} onChange={(e) => setVersion(e.target.value)} /></Field>
            <Field label="Подпись" htmlFor="rl-s"><Input id="rl-s" mono value={signature} onChange={(e) => setSignature(e.target.value)} /></Field>
            <Field label="Архив" htmlFor="rl-a"><FilePick id="rl-a" label="выбрать .tar.gz" accept=".gz,.tgz" onPick={setArchive} /></Field>
          </div>
        </Modal>
      )}
      {stopping && <Confirm title="Остановить выкатку?" action="Остановить" body="Узлы, которые ещё не обновились, останутся на своей версии." onClose={() => setStopping(false)} onConfirm={() => action.run(() => send("/admin/release/withdraw", "POST"), "выкатка остановлена")} />}
    </Page>
  );
}
