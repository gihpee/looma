/** Биллинг: баланс кредитов (начисляет админ), расход по продуктам за период,
 *  журнал начислений, выгрузка CSV, ставки. */
import { useState } from "react";
import { Download } from "lucide-react";
import { useBalance, useRates, useUsage } from "@looma/api";
import { Card, CardBody, CardHead, Empty, Input, KeyValue, Notice, Page, PageHead, Segmented, Stat, StateBadge, dateTime, money, num, tokensK } from "@looma/ui";
import { API_BASE, ErrorLine, running } from "../lib";

type Product = "" | "looma-compute" | "looma-inference" | "looma-training";

export function Billing() {
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [product, setProduct] = useState<Product>("");
  const query = new URLSearchParams();
  if (since) query.set("since", since);
  if (until) query.set("until", until);
  if (product) query.set("resource", product);
  const qs = query.toString();
  const usage = useUsage(qs ? `?${qs}` : "");
  const balance = useBalance();
  const rates = useRates();
  const leases = usage.data?.leases ?? [];
  const tokens = usage.data?.tokens ?? [];
  const total = usage.data?.total ?? leases.reduce((s, l) => s + l.cost, 0) + tokens.reduce((s, t) => s + (t.cost ?? 0), 0);
  const compute = rates.data?.rates.find((r) => r.resource === "looma-compute");
  const inference = rates.data?.rates.find((r) => r.resource === "looma-inference");
  const csv = `${API_BASE}/api/usage/export.csv${qs ? `?${qs}` : ""}`;

  return (
    <Page>
      <PageHead title="Биллинг" text="Кредиты 1 : 1 к рублю. Кластер списывается по минутам, токены — по факту ответа; ставка и цена фиксируются в момент действия."
                actions={<a className="lu-btn lu-btn--secondary" href={csv} download><Download size={15} />CSV</a>} />
      <ErrorLine error={usage.error} />
      <div className="lu-grid lu-grid--stats">
        <Stat label="Баланс" value={balance.data ? money(balance.data.kopecks, balance.data.currency, 0).replace(/ ₽$/, "") : "—"} unit={balance.data ? "₽" : undefined}
              sub={balance.data ? `начислено ${money(balance.data.credited, balance.data.currency, 0)}` : "начисляет администратор"} subTone={balance.data && balance.data.kopecks < 0 ? "bad" : undefined} />
        <Stat label="Расход за период" value={money(total, "RUB", 0).replace(/ ₽$/, "")} unit="₽" sub={product || since || until ? "по фильтру" : "за всё время"} />
        <Stat label="Идёт сейчас" value={running(usage.data)} unit="аренд" sub="считается по «сейчас»" />
        <Stat label="Токенов" value={tokensK(tokens.reduce((s, t) => s + t.prompt + t.completion, 0))} sub="вход + выход" />
      </div>
      <div className="lu-row lu-row--wrap">
        <Segmented soft value={product} onChange={setProduct} options={[{ value: "", label: "все продукты" }, { value: "looma-compute", label: "кластер" }, { value: "looma-inference", label: "инференс" }, { value: "looma-training", label: "обучение" }]} />
        <label className="lu-row lu-muted" htmlFor="since" style={{ fontSize: 13 }}>с <Input id="since" type="date" value={since} onChange={(e) => setSince(e.target.value)} style={{ minHeight: 34, width: 150 }} /></label>
        <label className="lu-row lu-muted" htmlFor="until" style={{ fontSize: 13 }}>по <Input id="until" type="date" value={until} onChange={(e) => setUntil(e.target.value)} style={{ minHeight: 34, width: 150 }} /></label>
      </div>
      <div className="lu-grid lu-grid--main">
        <div className="lu-stack" style={{ gap: 16 }}>
          <Card>
            <CardHead><b>Потребление</b></CardHead>
            {leases.length === 0 && tokens.length === 0 ? <Empty title="За период ничего не израсходовано">Аренда кластера считается по GPU-часам, инференс — по токенам.</Empty> : (
              <table className="lu-table lu-table--cards">
                <thead><tr><th>Ресурс</th><th className="lu-num">GPU-часов</th><th className="lu-num">Аренд</th><th className="lu-num">Стоимость</th></tr></thead>
                <tbody>
                  {leases.map((l) => (
                    <tr key={l.resource}>
                      <td data-label="Ресурс"><span className="lu-row"><span className="lu-mono">{l.resource}</span>{l.running > 0 && <StateBadge value="running" label="идёт" />}</span></td>
                      <td data-label="GPU-часов" className="lu-num">{num(l.gpu_hours, 2)}</td>
                      <td data-label="Аренд" className="lu-num">{l.leases}</td>
                      <td data-label="Стоимость" className="lu-num">{money(l.cost, l.currency)}</td>
                    </tr>
                  ))}
                  {tokens.length > 0 && <tr><td data-label="Ресурс"><span className="lu-mono">токены</span></td><td /><td data-label="Моделей" className="lu-num">{tokens.length}</td><td data-label="Стоимость" className="lu-num">{money(tokens.reduce((s, t) => s + (t.cost ?? 0), 0), "RUB")}</td></tr>}
                </tbody>
              </table>
            )}
          </Card>
          {tokens.length > 0 && (
            <Card>
              <CardHead><b>Инференс по моделям</b></CardHead>
              <table className="lu-table lu-table--cards">
                <thead><tr><th>Модель</th><th className="lu-num">Промпт</th><th className="lu-num">Ответ</th><th className="lu-num">Стоимость</th></tr></thead>
                <tbody>{tokens.map((t) => <tr key={t.model}><td data-label="Модель" className="lu-mono">{t.model}</td><td data-label="Промпт" className="lu-num">{num(t.prompt)}</td><td data-label="Ответ" className="lu-num">{num(t.completion)}</td><td data-label="Стоимость" className="lu-num">{money(t.cost ?? 0, "RUB")}</td></tr>)}</tbody>
              </table>
            </Card>
          )}
          <Card>
            <CardHead><b>Начисления</b>{balance.data && <span className="lu-muted" style={{ fontSize: 12 }}>израсходовано всего {money(balance.data.spent, balance.data.currency, 0)}</span>}</CardHead>
            {!balance.data || balance.data.grants.length === 0 ? <Empty title="Начислений пока не было" /> : (
              <table className="lu-table lu-table--cards">
                <thead><tr><th>Когда</th><th>За что</th><th className="lu-num">Сумма</th></tr></thead>
                <tbody>{balance.data.grants.map((g) => <tr key={g.id}><td data-label="Когда">{g.at ? dateTime(g.at) : "—"}</td><td data-label="За что">{g.note || <span className="lu-muted">без комментария</span>}</td><td data-label="Сумма" className="lu-num" style={{ color: g.kopecks < 0 ? "var(--bad-text)" : "var(--ok-text)" }}>{g.kopecks > 0 ? "+" : ""}{money(g.kopecks, "RUB")}</td></tr>)}</tbody>
              </table>
            )}
          </Card>
        </div>
        <div className="lu-stack" style={{ gap: 16 }}>
          <Card>
            <CardHead><b>Текущие ставки</b></CardHead>
            <CardBody>
              <KeyValue rows={[
                { k: "Кластер", v: compute ? `${money(compute.per_hour, compute.currency, 0)} за GPU-час` : "—" },
                { k: "Инференс (свои модели)", v: inference ? `${money(inference.per_hour, inference.currency, 0)} за GPU-час` : "—" },
                { k: "Обучение", v: rates.data?.training_rate_kopecks ? `${money(rates.data.training_rate_kopecks, "RUB", 0)} за GPU-час` : "по ставке кластера" },
              ]} />
            </CardBody>
          </Card>
          <Card pad>
            <b>Пополнить</b>
            <Notice tone="info">Платёжный модуль пока не подключён: кредиты начисляет администратор. Напишите на support@loomafloat.ru — пополним по счёту.</Notice>
          </Card>
        </div>
      </div>
    </Page>
  );
}
