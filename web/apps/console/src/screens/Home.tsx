/** Главная: что запущено и что можно запустить — на одном экране. Деньги,
 *  активность, объём; чеклист первых шагов; сеть; быстрый старт по API. */
import { Link, useNavigate } from "react-router-dom";
import { ArrowRight, Check, Plus } from "lucide-react";
import { useApiKeys, useBalance, useCapacity, useClusters, useModels, useUsage, type Who } from "@looma/api";
import { Button, Card, CardHead, CodeBlock, Empty, Logo, NetworkFabric, Page, PageHead, Stat, StateBadge, ago, money, plural, tokensK, duration, rubles } from "@looma/ui";
import { ErrorLine, Section, clusterPerHour, curlSnippet, running, spent, tokensOut } from "../lib";

export function Home({ who }: { who: Who }) {
  const nav = useNavigate();
  const usage = useUsage();
  const balance = useBalance();
  const clusters = useClusters();
  const capacity = useCapacity();
  const keys = useApiKeys();
  const models = useModels();

  const live = (clusters.data?.clusters ?? []).filter((c) => c.alive);
  const perHour = live.reduce((s, c) => s + clusterPerHour(c), 0);
  const gpus = live.reduce((s, c) => s + (c.gpus || 0), 0);
  const nodes = capacity.data?.nodes ?? [];
  const free = nodes.filter((n) => n.state === "free").length;
  const hasKey = (keys.data?.keys ?? []).some((k) => !k.revoked_at);
  const hasTokens = tokensOut(usage.data) > 0;
  const hadCluster = (usage.data?.leases ?? []).some((l) => l.resource === "looma-compute" && l.leases > 0);
  const steps = [
    { done: hasKey, label: "Создать ключ API", to: "/intelligence/keys" },
    { done: hasTokens, label: "Отправить первый запрос в чате", to: "/intelligence/chat" },
    { done: hadCluster, label: "Арендовать кластер на час", to: "/compute/clusters/new" },
    { done: false, label: "Дообучить модель на своих данных", to: "/intelligence/training" },
  ];
  const doneCount = steps.filter((s) => s.done).length;
  const firstTodo = steps.find((s) => !s.done);
  const name = who.display_name || who.email.split("@")[0] || (who.role === "admin" ? "администратор" : "");

  return (
    <Page>
      <PageHead title={name ? `Здравствуйте, ${name}` : "Здравствуйте"} text="Всё, что у вас запущено, и что можно запустить — на одном экране."
                actions={<><Button onClick={() => nav("/intelligence/chat")}>Открыть чат</Button><Button kind="primary" icon={<Plus size={16} />} onClick={() => nav("/compute/clusters/new")}>Арендовать кластер</Button></>} />
      <ErrorLine error={usage.error ?? clusters.error} />

      <div className="lu-grid lu-grid--stats">
        <Stat label="Баланс" value={balance.data ? money(balance.data.kopecks, balance.data.currency, 0).replace(/ ₽$/, "") : "—"} unit={balance.data ? "₽" : undefined}
              sub={balance.data && perHour > 0 ? `хватит на ~${duration(balance.data.kopecks / perHour * 3600)}` : "кредиты начисляет администратор"} />
        <Stat label="Расход" value={money(spent(usage.data), "RUB", 0).replace(/ ₽$/, "")} unit="₽"
              sub={usage.data ? (usage.data.leases.map((l) => `${l.resource.replace("looma-", "")} ${money(l.cost, l.currency, 0)}`).join(" · ") || "пока ничего") : "…"} />
        <Stat label="Сейчас работает" value={running(usage.data)} unit={plural(running(usage.data), ["аренда", "аренды", "аренд"]).replace(/^\S+ /, "")}
              sub={gpus ? `${gpus} GPU · ~${rubles(Math.round(perHour / 100))}/час` : "кластеров нет"} />
        <Stat label="Токенов выдано" value={tokensK(tokensOut(usage.data))} sub={usage.data?.tokens?.length ? usage.data.tokens.map((t) => t.model).slice(0, 2).join(" · ") : "инференс ещё не использовался"} />
      </div>

      <div className="lu-grid lu-grid--main">
        <div className="lu-stack" style={{ gap: 16, minWidth: 0 }}>
          <Card>
            <CardHead><b>Ваши ресурсы</b><Link to="/compute/clusters" style={{ fontSize: 13, fontWeight: 500 }}>все →</Link></CardHead>
            {live.length === 0 ? (
              <Empty title="Кластеров пока нет" action={<Button kind="primary" size="sm" onClick={() => nav("/compute/clusters/new")}>Арендовать</Button>}>
                Аренда считается по GPU-часам. Первый кластер поднимается за пару минут.
              </Empty>
            ) : (
              <table className="lu-table lu-table--cards">
                <thead><tr><th>Ресурс</th><th>Продукт</th><th>С</th><th>Состояние</th><th /></tr></thead>
                <tbody>
                  {live.map((c) => (
                    <tr key={c.group_id}>
                      <td data-label="Ресурс"><div style={{ display: "flex", flexDirection: "column" }}><span style={{ fontWeight: 500 }}>{c.label || c.group_id}</span><span className="lu-mono lu-muted" style={{ fontSize: 11 }}>{c.group_id} · {c.nodes} × {c.gpus ? `${c.gpus / Math.max(1, c.nodes)} GPU` : "узел"}</span></div></td>
                      <td data-label="Продукт" className="lu-mono" style={{ fontSize: 12 }}>compute</td>
                      <td data-label="С">{ago(c.opened_at)}</td>
                      <td data-label="Состояние"><StateBadge value={c.alive ? "running" : "pending"} label={c.alive ? "работает" : "поднимается"} /></td>
                      <td><Link to={`/compute/clusters/${c.group_id}`} style={{ fontSize: 13, fontWeight: 500 }}>открыть</Link></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>

          <Card pad>
            <div className="lu-row lu-row--between" style={{ marginBottom: 10 }}>
              <b>Сеть сейчас</b>
              <span className="lu-mono lu-muted" style={{ fontSize: 12 }}>{nodes.length ? `${plural(nodes.length, ["узел", "узла", "узлов"])} · ${free} свободно` : "нет узлов на связи"}</span>
            </div>
            {nodes.length ? <NetworkFabric nodes={nodes} only={["mine", "inference", "free", "busy"]} /> : <div className="lu-muted" style={{ fontSize: 13 }}>Ни одного подключённого узла.</div>}
            {nodes.length > 0 && free === 0 && <div className="lu-muted" style={{ fontSize: 12, marginTop: 10 }}>Свободных нет — при аренде платформа подвинет свои модели.</div>}
          </Card>
        </div>

        <div className="lu-stack" style={{ gap: 16 }}>
          <Card pad>
            <div className="lu-row lu-row--between" style={{ marginBottom: 10 }}><b>Первые шаги</b><span className="lu-mono lu-muted" style={{ fontSize: 12 }}>{doneCount} из {steps.length}</span></div>
            <div className="lu-stack" style={{ gap: 6 }}>
              {steps.map((s) => (
                <Link key={s.label} to={s.to} className="lu-row" style={{ padding: "8px 10px", borderRadius: 8, color: s.done ? "var(--text-3)" : "var(--text)", textDecoration: s.done ? "line-through" : "none", background: s === firstTodo ? "var(--accent-soft)" : undefined, fontSize: 13, fontWeight: s === firstTodo ? 500 : 400 }}>
                  <span style={{ display: "inline-flex", width: 20, height: 20, borderRadius: "50%", alignItems: "center", justifyContent: "center", flexShrink: 0, background: s.done ? "var(--accent)" : undefined, color: "#fff", border: s.done ? 0 : `1.5px solid ${s === firstTodo ? "var(--accent)" : "var(--border-2)"}` }}>{s.done && <Check size={12} strokeWidth={3} />}</span>
                  {s.label}{s === firstTodo && <ArrowRight size={14} style={{ marginLeft: "auto", color: "var(--accent)" }} />}
                </Link>
              ))}
            </div>
          </Card>

          <Card pad>
            <b>Быстрый старт по API</b>
            <div style={{ marginTop: 10 }}><CodeBlock code={curlSnippet(models.data?.data?.[0]?.id)} /></div>
            <div className="lu-row" style={{ marginTop: 10 }}><Link to="/intelligence/keys" style={{ fontSize: 13, fontWeight: 500 }}>ключи и python-пример →</Link></div>
          </Card>

          <Card pad>
            <Section title="Модели, которые отвечают">
              {(models.data?.data ?? []).length === 0 ? <div className="lu-muted" style={{ fontSize: 13 }}>Сейчас ни одна модель не отвечает.</div> :
                models.data!.data.slice(0, 4).map((m) => (
                  <div key={m.id} className="lu-row lu-row--between" style={{ fontSize: 13 }}>
                    <span className="lu-row"><Logo src={m.logo_url} name={m.id} />{m.id}</span>
                    {m.price_in !== undefined && <span className="lu-mono lu-muted" style={{ fontSize: 12 }}>{Math.round(m.price_in / 100)} / {Math.round((m.price_out ?? 0) / 100)} ₽</span>}
                  </div>
                ))}
              <Link to="/intelligence/models" style={{ fontSize: 13, fontWeight: 500 }}>весь каталог →</Link>
            </Section>
          </Card>
        </div>
      </div>
    </Page>
  );
}
