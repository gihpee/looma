/** Клиенты: учётные записи, аренды всех клиентов (с закрытием зависших),
 *  ставки и цены (черновик → «опубликовать» одним документом). */
import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { get, send, useAccountCredits, useAccounts, useAdminPricing, useAdminRates, useLeases, useNodes, type Account, type AdminPricing, type GpuClass, type ModelPrice } from "@looma/api";
import { Badge, Button, Card, CardBody, CardHead, Chip, Confirm, Empty, Field, Input, InputAffix, KeyValue, Logo, Modal, Notice, Page, PageHead, Segmented, Select, Stat, StateBadge, Toggle, dateTime, money, rubles, useAction, useToast } from "@looma/ui";
import { ErrorLine } from "../lib";

/* ---------------------------------------------------------------- клиенты */
export function Accounts() {
  const qc = useQueryClient();
  const list = useAccounts();
  const action = useAction(() => qc.invalidateQueries({ queryKey: ["/admin/accounts"] }));
  const [adding, setAdding] = useState(false);
  const [email, setEmail] = useState(""); const [password, setPassword] = useState(""); const [role, setRole] = useState("client"); const [name, setName] = useState("");
  const [pwFor, setPwFor] = useState<Account | null>(null); const [pw, setPw] = useState("");
  const [creditsFor, setCreditsFor] = useState<Account | null>(null);
  const rows = list.data?.accounts ?? [];
  return (
    <Page>
      <PageHead title="Учётные записи" text="Регистрация по приглашению: клиента заводит администратор." actions={<Button kind="primary" icon={<Plus size={16} />} onClick={() => setAdding(true)}>Завести запись</Button>} />
      <ErrorLine error={list.error} />
      <Card>
        {rows.length === 0 ? <Empty title="Записей нет" /> : (
          <table className="lu-table lu-table--cards"><thead><tr><th>Почта</th><th>Имя</th><th>Роль</th><th>Состояние</th><th /></tr></thead>
            <tbody>{rows.map((a) => <tr key={a.id}>
              <td data-label="Почта">{a.email}</td><td data-label="Имя">{a.display_name || <span className="lu-muted">—</span>}</td><td data-label="Роль" className="lu-mono">{a.role}</td>
              <td data-label="Состояние">{a.disabled ? <Badge tone="bad">отключена</Badge> : <Badge tone="ok">работает</Badge>}</td>
              <td><div className="lu-row"><Button size="sm" onClick={() => setCreditsFor(a)}>кредиты</Button><Button size="sm" kind="ghost" onClick={() => { setPwFor(a); setPw(""); }}>пароль</Button><Button size="sm" kind="ghost" onClick={() => action.run(() => send(`/admin/accounts/${a.id}/disabled`, "POST", { disabled: !a.disabled }), a.disabled ? "включена" : "отключена")}>{a.disabled ? "включить" : "отключить"}</Button></div></td>
            </tr>)}</tbody></table>
        )}
      </Card>
      <Notice tone="info">Платёжный модуль не подключён: кредиты начисляются здесь, по кнопке «кредиты». 1 кредит = 1 ₽; расход считается из журнала аренд и токенов.</Notice>
      {adding && (
        <Modal title="Новая учётная запись" size="sm" onClose={() => setAdding(false)} footer={<><span className="lu-spacer" /><Button kind="ghost" onClick={() => setAdding(false)}>Отмена</Button><Button kind="primary" disabled={action.busy || !email || !password} onClick={() => action.run(async () => { await send("/admin/accounts", "POST", { email, password, role, display_name: name }); setAdding(false); setEmail(""); setPassword(""); setName(""); }, "запись создана")}>Создать</Button></>}>
          <div className="lu-stack">
            <Field label="Почта" htmlFor="a-e" required><Input id="a-e" type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
            <Field label="Имя" htmlFor="a-n"><Input id="a-n" value={name} onChange={(e) => setName(e.target.value)} /></Field>
            <Field label="Пароль" hint="передайте клиенту; сменить сможете здесь же" htmlFor="a-p" required><Input id="a-p" type="text" mono value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
            <Field label="Роль" htmlFor="a-r"><Select id="a-r" value={role} onChange={(e) => setRole(e.target.value)}><option value="client">client</option><option value="admin">admin</option></Select></Field>
          </div>
        </Modal>
      )}
      {creditsFor && <CreditsModal account={creditsFor} onClose={() => setCreditsFor(null)} />}
      {pwFor && (
        <Modal title={`Новый пароль · ${pwFor.email}`} size="sm" onClose={() => setPwFor(null)} footer={<><span className="lu-spacer" /><Button kind="primary" disabled={action.busy || pw.length < 6} onClick={() => action.run(async () => { await send(`/admin/accounts/${pwFor.id}/password`, "POST", { password: pw }); setPwFor(null); }, "пароль сменён")}>Сменить</Button></>}>
          <Field label="Пароль" hint="не короче 6 символов; все сессии клиента закроются" htmlFor="pw"><Input id="pw" type="text" mono value={pw} onChange={(e) => setPw(e.target.value)} /></Field>
        </Modal>
      )}
    </Page>
  );
}

function CreditsModal({ account, onClose }: { account: Account; onClose: () => void }) {
  const qc = useQueryClient();
  const credits = useAccountCredits(account.id);
  const action = useAction(() => qc.invalidateQueries({ queryKey: [`/admin/accounts/${account.id}/credits`] }));
  const [amount, setAmount] = useState(""); const [note, setNote] = useState("");
  const kopecks = Math.round((Number(String(amount).replace(",", ".")) || 0) * 100);
  const grant = () => action.run(async () => { await send(`/admin/accounts/${account.id}/credits`, "POST", { kopecks, note }); setAmount(""); setNote(""); }, kopecks > 0 ? "начислено" : "скорректировано");
  const b = credits.data;
  return (
    <Modal title={`Кредиты · ${account.email}`} onClose={onClose} footer={<><span className="lu-spacer" /><Button kind="ghost" onClick={onClose}>Закрыть</Button></>}>
      <div className="lu-stack lu-stack--lg">
        <ErrorLine error={credits.error} />
        <div className="lu-grid lu-grid--3">
          <Stat label="Баланс" value={b ? money(b.kopecks, b.currency, 0).replace(/ ₽$/, "") : "—"} unit="₽" subTone={b && b.kopecks < 0 ? "bad" : undefined} sub={b && b.kopecks < 0 ? "в минусе" : undefined} />
          <Stat label="Начислено" value={b ? money(b.credited, b.currency, 0).replace(/ ₽$/, "") : "—"} unit="₽" />
          <Stat label="Израсходовано" value={b ? money(b.spent, b.currency, 0).replace(/ ₽$/, "") : "—"} unit="₽" sub="аренды + токены" />
        </div>
        <Card pad>
          <b>Начислить</b>
          <div className="lu-grid lu-grid--2" style={{ marginTop: 8 }}>
            <Field label="Сумма, ₽" hint="со знаком минус — корректировка" htmlFor="cr-a"><InputAffix id="cr-a" mono value={amount} onChange={(e) => setAmount(e.target.value)} suffix="₽" placeholder="5000" /></Field>
            <Field label="За что" hint="по счёту №, промо, возврат" htmlFor="cr-n"><Input id="cr-n" value={note} onChange={(e) => setNote(e.target.value)} /></Field>
          </div>
          <div className="lu-row" style={{ marginTop: 8 }}><Button kind="primary" size="sm" disabled={!kopecks || action.busy} onClick={grant}>{kopecks < 0 ? "Списать" : "Начислить"}{kopecks ? ` ${money(Math.abs(kopecks), "RUB", 0)}` : ""}</Button></div>
        </Card>
        <Card>
          <CardHead><b>Журнал начислений</b></CardHead>
          {!b || b.grants.length === 0 ? <Empty title="Начислений не было" /> : (
            <table className="lu-table lu-table--cards"><thead><tr><th>Когда</th><th>За что</th><th>Кем</th><th className="lu-num">Сумма</th></tr></thead>
              <tbody>{b.grants.map((g) => <tr key={g.id}><td data-label="Когда">{g.at ? dateTime(g.at) : "—"}</td><td data-label="За что">{g.note || <span className="lu-muted">—</span>}</td><td data-label="Кем" className="lu-mono">{g.granted_by != null ? `#${g.granted_by}` : "токен"}</td><td data-label="Сумма" className="lu-num" style={{ color: g.kopecks < 0 ? "var(--bad-text)" : "var(--ok-text)" }}>{g.kopecks > 0 ? "+" : ""}{money(g.kopecks, "RUB")}</td></tr>)}</tbody></table>
          )}
        </Card>
      </div>
    </Modal>
  );
}

/* ----------------------------------------------------------------- аренды */
export function Leases() {
  const qc = useQueryClient();
  const leases = useLeases();
  const accounts = useAccounts();
  const action = useAction(() => qc.invalidateQueries({ queryKey: ["/admin/leases"] }));
  const [filter, setFilter] = useState<"all" | "stale">("all");
  const [closing, setClosing] = useState<string | null>(null);
  const emailOf = (id: number | null) => accounts.data?.accounts.find((a) => a.id === id)?.email ?? (id === null ? "аварийный вход" : `#${id}`);
  const rows = (leases.data?.leases ?? []).filter((l) => filter === "all" || !l.alive);
  const stale = (leases.data?.leases ?? []).filter((l) => !l.alive).length;
  return (
    <Page>
      <PageHead title="Аренды" text="Все идущие аренды по всем клиентам. Зависшая — группа кончилась, а счёт открыт: закройте руками, сверка закроет её сама в течение минуты." />
      <ErrorLine error={leases.error} />
      <div className="lu-row"><Segmented soft value={filter} onChange={setFilter} options={[{ value: "all", label: `все · ${leases.data?.leases.length ?? 0}` }, { value: "stale", label: `зависшие · ${stale}` }]} /></div>
      <Card>
        {rows.length === 0 ? <Empty title={filter === "stale" ? "Зависших аренд нет" : "Идущих аренд нет"} /> : (
          <table className="lu-table lu-table--cards"><thead><tr><th>Клиент</th><th>Ресурс</th><th>Группа</th><th>Узлов × GPU</th><th>Ставка</th><th>Открыта</th><th>Группа жива</th><th /></tr></thead>
            <tbody>{rows.map((l) => <tr key={l.id}>
              <td data-label="Клиент">{emailOf(l.account_id)}</td><td data-label="Ресурс" className="lu-mono" style={{ fontSize: 12 }}>{l.resource.replace("looma-", "")}</td>
              <td data-label="Группа"><b>{l.label}</b> <span className="lu-mono lu-muted" style={{ fontSize: 11 }}>{l.group_id}</span></td>
              <td data-label="Узлов × GPU" className="lu-num">{l.nodes} × {Math.max(1, Math.round(l.gpus / Math.max(1, l.nodes)))}</td>
              <td data-label="Ставка" className="lu-num">{money(l.per_hour, l.currency, 0)}/ч</td><td data-label="Открыта">{dateTime(l.opened_at)}</td>
              <td data-label="Группа жива"><StateBadge value={l.alive ? "running" : l.known ? "done" : "gone"} label={l.alive ? "да" : l.known ? "кончилась" : "нет записи"} /></td>
              <td>{!l.alive && <Button size="sm" kind="danger" onClick={() => setClosing(l.group_id)}>закрыть счёт</Button>}</td>
            </tr>)}</tbody></table>
        )}
      </Card>
      {closing && <Confirm title="Закрыть аренду?" action="Закрыть" body="Счёт остановится по текущему моменту. Сама группа не трогается." onClose={() => setClosing(null)} onConfirm={() => action.run(() => send(`/admin/leases/${closing}/close`, "POST"), "аренда закрыта")} />}
    </Page>
  );
}

/* ------------------------------------------------------------ ставки и цены */
const EMPTY: AdminPricing = { as_of: "", currency: "RUB", gpu_classes: [], models: [], training_rate_kopecks: null, competitors: { selectel: "Selectel", aws: "AWS" } };
const rub = (k: number | undefined | null) => k ? String(Math.round(k / 100)) : "";
const kop = (s: string) => Math.round((Number(String(s).replace(",", ".")) || 0) * 100);

export function Pricing() {
  const qc = useQueryClient();
  const server = useAdminPricing();
  const rates = useAdminRates();
  const nodes = useNodes();
  const [draft, setDraft] = useState<AdminPricing>(EMPTY);
  const [dirty, setDirty] = useState(false);
  const [tab, setTab] = useState<"gpu" | "models" | "training" | "rates">("gpu");
  const action = useAction(() => { qc.invalidateQueries({ queryKey: ["/admin/pricing"] }); qc.invalidateQueries({ queryKey: ["/api/public/pricing"] }); qc.invalidateQueries({ queryKey: ["/admin/rates"] }); });
  useEffect(() => { if (server.data && !dirty) setDraft({ ...EMPTY, ...server.data }); }, [server.data, dirty]);
  const edit = (next: AdminPricing) => { setDraft(next); setDirty(true); };
  const inNet = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const n of nodes.data?.nodes ?? []) for (const c of draft.gpu_classes) if ((c.match?.length ? c.match : [c.name]).some((m) => n.gpu_name.toLowerCase().includes(m.toLowerCase()))) counts[c.id] = (counts[c.id] ?? 0) + n.gpus_total;
    return counts;
  }, [nodes.data, draft.gpu_classes]);
  const rivals = Object.keys(draft.competitors);
  const setClass = (i: number, patch: Partial<GpuClass>) => edit({ ...draft, gpu_classes: draft.gpu_classes.map((c, k) => (k === i ? { ...c, ...patch } : c)) });
  const setModel = (i: number, patch: Partial<ModelPrice>) => edit({ ...draft, models: draft.models.map((m, k) => (k === i ? { ...m, ...patch } : m)) });
  const toast = useToast();
  const lookupLogo = async (i: number, repo: string) => {
    try {
      const r = await get<{ logo_url: string | null }>(`/admin/logos/lookup?repo=${encodeURIComponent(repo)}`);
      if (r.logo_url) { setModel(i, { logo_url: r.logo_url }); toast("ok", "логотип найден"); } else toast("bad", "на HuggingFace нет аватара у этого владельца — вставьте ссылку руками");
    } catch (e) { toast("bad", (e as Error).message); }
  };
  const cheapest = (c: GpuClass) => Object.values(c.competitors ?? {}).filter((v) => v > 0).sort((a, b) => a - b)[0];
  const publish = () => action.run(async () => { await send("/admin/pricing", "PUT", { ...draft, as_of: new Date().toISOString().slice(0, 10) }); setDirty(false); }, "прайс опубликован");
  const computeRate = rates.data?.rates.find((r) => r.resource === "looma-compute"); const inferenceRate = rates.data?.rates.find((r) => r.resource === "looma-inference"); const trainingRate = rates.data?.rates.find((r) => r.resource === "looma-training");

  return (
    <Page>
      <PageHead title="Ставки и цены" text="Прайс — то, что обещано снаружи (лендинг, карточки). Ставки — то, по чему считается счёт; фиксируются в момент аренды."
                actions={<>{dirty && <Badge tone="warn">не опубликовано</Badge>}<Button kind="ghost" disabled={!dirty} onClick={() => { setDirty(false); if (server.data) setDraft({ ...EMPTY, ...server.data }); }}>Отменить</Button><Button kind="primary" disabled={!dirty || action.busy} onClick={publish}>Опубликовать в прайс</Button></>} />
      <ErrorLine error={server.error} />
      <Segmented value={tab} onChange={setTab} options={[{ value: "gpu", label: `Классы GPU · ${draft.gpu_classes.length}` }, { value: "models", label: `Модели · ${draft.models.length}` }, { value: "training", label: "Обучение" }, { value: "rates", label: "Ставки биллинга" }]} />

      {tab === "gpu" && (
        <div className="lu-stack" style={{ gap: 16 }}>
          <Card>
            <div className="lu-table--wrap"><table className="lu-table lu-table--cards">
              <thead><tr><th>Лого</th><th>Класс</th><th>VRAM</th><th>Ставка ₽/ч</th>{rivals.map((r) => <th key={r}>{draft.competitors[r]} ₽/ч</th>)}<th>Ниже на</th><th>В сети</th><th /></tr></thead>
              <tbody>{draft.gpu_classes.map((c, i) => { const best = cheapest(c); const save = best && c.rate_kopecks ? Math.round((1 - c.rate_kopecks / best) * 100) : null; return (
                <tr key={i} style={{ background: c.featured ? "var(--accent-soft)" : undefined }}>
                  <td data-label="Лого"><LogoField value={c.logo_url ?? ""} name={c.vendor} onChange={(v) => setClass(i, { logo_url: v || null })} /></td>
                  <td data-label="Класс" style={{ minWidth: 220 }}><div className="lu-stack" style={{ gap: 4 }}><Input value={c.name} onChange={(e) => setClass(i, { name: e.target.value })} placeholder="RTX 4090" style={{ minHeight: 34 }} /><div className="lu-row" style={{ gap: 4 }}><Input value={c.vendor} onChange={(e) => setClass(i, { vendor: e.target.value })} placeholder="NVIDIA" style={{ minHeight: 30, fontSize: 12 }} /><Input value={(c.match ?? []).join(", ")} onChange={(e) => setClass(i, { match: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} placeholder="подстроки: 4090" style={{ minHeight: 30, fontSize: 12 }} /></div></div></td>
                  <td data-label="VRAM"><InputAffix value={String(c.vram_gb || "")} onChange={(e) => setClass(i, { vram_gb: Number(e.target.value) || 0 })} suffix="GB" mono style={{ width: 62, textAlign: "right" }} /></td>
                  <td data-label="Ставка"><InputAffix value={rub(c.rate_kopecks)} onChange={(e) => setClass(i, { rate_kopecks: kop(e.target.value) })} suffix="₽" mono style={{ width: 62, textAlign: "right" }} /></td>
                  {rivals.map((r) => <td key={r} data-label={draft.competitors[r]}><InputAffix value={rub(c.competitors?.[r])} onChange={(e) => setClass(i, { competitors: { ...c.competitors, [r]: kop(e.target.value) } })} placeholder="—" suffix="₽" mono style={{ width: 62, textAlign: "right" }} /></td>)}
                  <td data-label="Ниже на" style={{ color: "var(--ok-text)", fontWeight: 500 }}>{save !== null ? `−${save}%` : "—"}</td>
                  <td data-label="В сети" className="lu-mono">{inNet[c.id] ? `${inNet[c.id]} карт` : <span className="lu-muted">нет</span>}</td>
                  <td><div className="lu-row"><Toggle checked={!!c.featured} onChange={(v) => setClass(i, { featured: v })} label={<span style={{ fontSize: 11 }}>hero</span>} /><Button size="sm" kind="ghost" onClick={() => edit({ ...draft, gpu_classes: draft.gpu_classes.filter((_, k) => k !== i) })}><Trash2 size={13} /></Button></div></td>
                </tr>); })}</tbody>
            </table></div>
            <div className="lu-card__foot"><Button size="sm" icon={<Plus size={13} />} onClick={() => edit({ ...draft, gpu_classes: [...draft.gpu_classes, { id: `c${Date.now().toString(36)}`, name: "", vendor: "NVIDIA", vram_gb: 24, rate_kopecks: 0, competitors: {}, match: [] }] })}>Добавить класс</Button><span className="lu-muted" style={{ fontSize: 12 }}>Узлы с картой вне справочника класса не получают и в прайс не попадают.</span></div>
          </Card>
          <div className="lu-grid lu-grid--2" style={{ alignItems: "start" }}>
            <Preview c={draft.gpu_classes.find((x) => x.featured) ?? draft.gpu_classes[0]} rivals={draft.competitors} />
            <Card pad>
              <b>Конкуренты</b>
              <div className="lu-stack" style={{ marginTop: 8, gap: 6 }}>
                {rivals.map((r) => <div key={r} className="lu-row"><Chip>{r}</Chip><Input value={draft.competitors[r]} onChange={(e) => edit({ ...draft, competitors: { ...draft.competitors, [r]: e.target.value } })} style={{ minHeight: 32 }} /><Button size="sm" kind="ghost" onClick={() => { const c = { ...draft.competitors }; delete c[r]; edit({ ...draft, competitors: c }); }}><Trash2 size={13} /></Button></div>)}
                <Button size="sm" onClick={() => { const key = prompt("ключ конкурента латиницей, например yandex"); if (key) edit({ ...draft, competitors: { ...draft.competitors, [key]: key } }); }}>добавить конкурента</Button>
              </div>
            </Card>
          </div>
        </div>
      )}

      {tab === "models" && (
        <Card>
          <CardHead><b>Модели — ₽ за 1M токенов</b><span className="lu-muted" style={{ fontSize: 12 }}>имя должно совпадать с именем модели в /v1/models; логотип — url (с HuggingFace или свой)</span></CardHead>
          <div className="lu-table--wrap"><table className="lu-table lu-table--cards">
            <thead><tr><th>Лого</th><th>Модель · репозиторий</th><th>Контекст</th><th>Вход</th><th>Выход</th><th>В прайсе</th><th /></tr></thead>
            <tbody>{draft.models.map((m, i) => <tr key={i}>
              <td data-label="Лого"><LogoField value={m.logo_url ?? ""} name={m.id} onChange={(v) => setModel(i, { logo_url: v || null })} /></td>
              <td data-label="Модель" style={{ minWidth: 220 }}><div className="lu-stack" style={{ gap: 4 }}><Input mono value={m.id} onChange={(e) => setModel(i, { id: e.target.value })} placeholder="Qwen3-32B" style={{ minHeight: 34 }} /><div className="lu-row" style={{ gap: 4 }}><Input mono value={m.repo ?? ""} onChange={(e) => setModel(i, { repo: e.target.value })} placeholder="Qwen/Qwen3-32B (HF)" style={{ minHeight: 30, fontSize: 12 }} /><Button size="sm" kind="ghost" title="найти логотип на HuggingFace по владельцу репозитория" disabled={!(m.repo || m.id).includes("/")} onClick={() => lookupLogo(i, m.repo || m.id)}>лого</Button></div></div></td>
              <td data-label="Контекст"><InputAffix value={m.context ? String(Math.round(m.context / 1024)) : ""} onChange={(e) => setModel(i, { context: (Number(e.target.value) || 0) * 1024 })} suffix="K" mono style={{ width: 60, textAlign: "right" }} /></td>
              <td data-label="Вход"><InputAffix value={rub(m.price_in)} onChange={(e) => setModel(i, { price_in: kop(e.target.value) })} suffix="₽" mono style={{ width: 62, textAlign: "right" }} /></td>
              <td data-label="Выход"><InputAffix value={rub(m.price_out)} onChange={(e) => setModel(i, { price_out: kop(e.target.value) })} suffix="₽" mono style={{ width: 62, textAlign: "right" }} /></td>
              <td data-label="В прайсе"><Toggle checked={m.visible !== false} onChange={(v) => setModel(i, { visible: v })} label={<span style={{ fontSize: 12 }}>{m.visible !== false ? "да" : "скрыта"}</span>} /></td>
              <td><Button size="sm" kind="ghost" onClick={() => edit({ ...draft, models: draft.models.filter((_, k) => k !== i) })}><Trash2 size={13} /></Button></td>
            </tr>)}</tbody>
          </table></div>
          <div className="lu-card__foot"><Button size="sm" icon={<Plus size={13} />} onClick={() => edit({ ...draft, models: [...draft.models, { id: "", context: 32768, price_in: 0, price_out: 0, visible: true }] })}>Добавить модель</Button></div>
        </Card>
      )}

      {tab === "training" && (
        <Card pad style={{ maxWidth: 520 }}>
          <b>Обучение</b>
          <div style={{ marginTop: 10 }} className="lu-stack">
            <Toggle checked={draft.training_rate_kopecks == null} onChange={(v) => edit({ ...draft, training_rate_kopecks: v ? null : (computeRate?.per_hour ?? 0) })} label="По ставке кластера" />
            {draft.training_rate_kopecks != null && <Field label="Отдельная ставка за GPU-час (публичная)" htmlFor="tr"><InputAffix id="tr" value={rub(draft.training_rate_kopecks)} onChange={(e) => edit({ ...draft, training_rate_kopecks: kop(e.target.value) })} suffix="₽" mono /></Field>}
            <Notice tone="info">Это цена в прайсе. Ставка, по которой считается счёт за обучение, — во вкладке «Ставки биллинга» (looma-training; без неё — ставка кластера).</Notice>
          </div>
        </Card>
      )}

      {tab === "rates" && <BillingRates compute={computeRate?.per_hour ?? 0} inference={inferenceRate?.per_hour ?? 0} training={trainingRate?.per_hour ?? 0} onSaved={() => action.run(async () => {})} error={rates.error} />}
    </Page>
  );
}

function LogoField({ value, name, onChange }: { value: string; name: string; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className="lu-btn lu-btn--ghost lu-iconbtn" aria-label={value ? "Логотип — заменить" : "Логотип не задан — задать"} title="логотип" onClick={() => setOpen(true)} style={{ border: value ? "1px solid var(--border)" : "1px dashed var(--border-2)" }}><Logo src={value || null} name={name} size="lg" /></button>
      {open && (
        <Modal title="Логотип" size="sm" onClose={() => setOpen(false)} footer={<><span className="lu-spacer" /><Button kind="primary" onClick={() => setOpen(false)}>Готово</Button></>}>
          <Field label="URL картинки" hint="SVG или PNG; для моделей с HuggingFace можно вставить аватар организации; пусто — плашка с инициалами" htmlFor="logo-url"><Input id="logo-url" mono value={value} onChange={(e) => onChange(e.target.value)} placeholder="https://…/logo.svg" /></Field>
        </Modal>
      )}
    </>
  );
}

function Preview({ c, rivals }: { c?: GpuClass; rivals: Record<string, string> }) {
  if (!c) return null;
  const best = Object.entries(c.competitors ?? {}).filter(([, v]) => v > 0).sort((a, b) => a[1] - b[1])[0];
  const save = best && c.rate_kopecks ? Math.round((1 - c.rate_kopecks / best[1]) * 100) : null;
  return (
    <div className="lu-stack" style={{ gap: 8 }}>
      <div className="lu-row lu-row--between"><b>Что увидит клиент</b><span className="lu-muted" style={{ fontSize: 12 }}>{c.name || "класс"} · после публикации</span></div>
      <Card>
        <CardHead><div className="lu-row"><Logo src={c.logo_url} name={c.vendor} /><b>{c.name || "—"}</b></div>{c.vram_gb > 0 && <Chip>{c.vram_gb} GB</Chip>}</CardHead>
        <CardBody>
          <div className="lu-muted" style={{ fontSize: 12 }}>₽ за GPU-час</div>
          <div className="lu-row" style={{ alignItems: "baseline" }}><b style={{ font: "700 30px var(--font-display)" }}>{Math.round(c.rate_kopecks / 100)}</b>{best && <s className="lu-strike">{Math.round(best[1] / 100)}</s>}{save !== null && save > 0 && <Badge tone="ok" dot={false}>−{save}%</Badge>}</div>
          <div style={{ marginTop: 8 }}><KeyValue rows={Object.entries(rivals).map(([k, label]) => ({ k: label, v: c.competitors?.[k] ? rubles(Math.round(c.competitors[k] / 100)) : <span className="lu-muted">нет такой карты</span> }))} /></div>
        </CardBody>
      </Card>
      <div className="lu-muted" style={{ fontSize: 12 }}>Строка конкурента без цены скрывается. «Ниже на» считается от самого дешёвого.</div>
    </div>
  );
}

function BillingRates({ compute, inference, training, onSaved, error }: { compute: number; inference: number; training: number; onSaved: () => void; error?: Error | null }) {
  const action = useAction(onSaved);
  const [c, setC] = useState(rub(compute)); const [i, setI] = useState(rub(inference)); const [t, setT] = useState(rub(training));
  useEffect(() => { setC(rub(compute)); setI(rub(inference)); setT(rub(training)); }, [compute, inference, training]);
  const save = (resource: string, value: string) => action.run(() => send("/admin/rates", "POST", { resource, per_hour: kop(value), currency: "RUB" }), `ставка ${resource} сохранена`);
  return (
    <div className="lu-grid lu-grid--3">
      <ErrorLine error={error} />
      {[["looma-compute", "Кластер", c, setC], ["looma-inference", "Инференс (свои модели клиента)", i, setI], ["looma-training", "Обучение", t, setT]].map(([res, title, v, set]) => (
        <Card key={res as string} pad>
          <div className="lu-label">{res as string}</div>
          <b>{title as string}</b>
          <div className="lu-row" style={{ marginTop: 8 }}><InputAffix value={v as string} onChange={(e) => (set as (s: string) => void)(e.target.value)} suffix="₽ за GPU-час" mono /><Button size="sm" onClick={() => save(res as string, v as string)} disabled={action.busy}>ok</Button></div>
          <div className="lu-muted" style={{ fontSize: 12, marginTop: 6 }}>{res === "looma-training" ? "пусто или 0 — по ставке кластера" : "в копейках за GPU-час; фиксируется в аренде"}</div>
        </Card>
      ))}
    </div>
  );
}
