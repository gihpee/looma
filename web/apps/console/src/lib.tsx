/** Общее для экранов консоли: расчёты из ответов API, мелкие блоки. */
import type { Cluster, LeaseLine, Usage } from "@looma/api";
import { Notice } from "@looma/ui";
import { AlertTriangle } from "lucide-react";
import { type ReactNode } from "react";

export const CONSOLE_HOST = location.hostname;
export const API_BASE = `https://api.${CONSOLE_HOST.replace(/^console\./, "")}`;
export const DOCS = "https://loomafloat.ru/docs";

/** Сколько потрачено — сумма по ресурсам в копейках. */
export const spent = (u?: Usage | null) => (u?.leases ?? []).reduce((s, l) => s + (l.cost ?? 0), 0);
export const running = (u?: Usage | null) => (u?.leases ?? []).reduce((s, l) => s + (l.running ?? 0), 0);
export const tokensOut = (u?: Usage | null) => (u?.tokens ?? []).reduce((s, t) => s + (t.completion ?? 0), 0);
export const lineFor = (u: Usage | null | undefined, resource: string): LeaseLine | undefined => u?.leases?.find((l) => l.resource === resource);

/** Сколько кластер стоит в час: ставка за GPU-час × карты. */
export const clusterPerHour = (c: Cluster) => (c.per_hour ?? 0) * (c.gpus || c.nodes || 1);
/** Сколько натикало с открытия аренды. */
export const clusterSpent = (c: Cluster) => Math.round(clusterPerHour(c) * (Date.now() - new Date(c.opened_at).getTime()) / 3_600_000);

/** Ошибка запроса одной строкой над экраном — не вместо экрана. */
export function ErrorLine({ error }: { error?: Error | null }) {
  if (!error) return null;
  return <Notice tone="bad" icon={<AlertTriangle size={14} />}>{error.message}</Notice>;
}

export function Section({ title, aside, children }: { title: ReactNode; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="lu-stack">
      <div className="lu-row lu-row--between"><h2 style={{ margin: 0, fontSize: 15, fontWeight: 600 }}>{title}</h2>{aside}</div>
      {children}
    </section>
  );
}

/** Сниппет для ключа: base_url на api.<домен>. */
export const curlSnippet = (model = "Qwen3-32B") =>
  `curl ${API_BASE}/v1/chat/completions \\\n  -H "Authorization: Bearer $LOOMA_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model":"${model}","messages":[{"role":"user","content":"Привет"}]}'`;
export const pythonSnippet = (model = "Qwen3-32B") =>
  `from openai import OpenAI\n\nclient = OpenAI(base_url="${API_BASE}/v1", api_key="LOOMA_KEY")\nanswer = client.chat.completions.create(\n    model="${model}",\n    messages=[{"role": "user", "content": "Привет"}],\n)\nprint(answer.choices[0].message.content)`;
