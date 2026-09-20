/** Занятость сети полосами-нитями. Не украшение: отсюда видно, хватит ли
 *  свободных узлов или придётся подвинуть модели платформы. Один цвет — одно
 *  состояние, три тона одного акцента. */
import { type ReactNode } from "react";

export type NodeState = "free" | "inference" | "rented" | "mine" | "busy" | "displaced";
export interface FabricNode { id?: string; state: NodeState; gpus?: number }

const LEGEND: { state: NodeState; label: string }[] = [
  { state: "mine", label: "ваш" },
  { state: "displaced", label: "подвинем модель" },
  { state: "inference", label: "под инференсом" },
  { state: "free", label: "свободен" },
  { state: "busy", label: "занят другим" },
];

export function NetworkFabric({ nodes, names = false, legend = true, only }: {
  nodes: FabricNode[]; names?: boolean; legend?: boolean; only?: NodeState[];
}) {
  const present = new Set(nodes.map((n) => n.state));
  const items = LEGEND.filter((l) => (only ? only.includes(l.state) : present.has(l.state)));
  const count = (s: NodeState) => nodes.filter((n) => n.state === s).length;
  return (
    <div className="lu-stack" style={{ gap: 10 }}>
      <div className="lu-fabric" role="img" aria-label={`${nodes.length} узлов: ${items.map((l) => `${l.label} ${count(l.state)}`).join(", ")}`}>
        {nodes.map((n, i) => (
          <div key={n.id ?? i} className="lu-fabric__row">
            <div className="lu-fabric__bar" data-state={n.state} title={n.gpus ? `${n.gpus} GPU` : undefined} />
            {names && n.id && <span className="lu-fabric__name">{n.id}{n.state === "displaced" ? " ← подвинем" : ""}</span>}
          </div>
        ))}
      </div>
      {legend && (
        <div className="lu-fabric__legend">
          {items.map((l) => <span key={l.state}><i className="lu-fabric__bar" data-state={l.state} style={{ flexGrow: 0 }} />{l.label} ({count(l.state)})</span>)}
        </div>
      )}
    </div>
  );
}

/** Что произойдёт с сетью при этой аренде. Числа настоящие: сколько свободно
 *  сейчас и скольких придётся подвинуть. Сначала берутся свободные, потом —
 *  столько узлов из-под инференса, скольких не хватило. */
export function displacement(nodes: FabricNode[], want: number): { preview: FabricNode[]; free: number; short: number } {
  const free = nodes.filter((n) => n.state === "free").length;
  const short = Math.max(0, want - free);
  let takeFree = Math.min(want, free);
  let takeBusy = short;
  const preview = nodes.map((n) => {
    if (n.state === "free" && takeFree > 0) { takeFree--; return { ...n, state: "mine" as NodeState }; }
    if (n.state === "inference" && takeBusy > 0) { takeBusy--; return { ...n, state: "displaced" as NodeState }; }
    return n;
  });
  return { preview, free, short };
}

export function Displacement({ nodes, want, names = true, children }: { nodes: FabricNode[]; want: number; names?: boolean; children?: ReactNode }) {
  const { preview, free, short } = displacement(nodes, want);
  const text = nodes.length === 0
    ? "Пока нет ни одного подключённого узла."
    : short === 0
      ? `Свободных узлов хватает: ${free} из ${nodes.length}.`
      : `Свободно ${free}, не хватает ${short}. Платформа снимет столько же своих моделей и вернёт их, когда аренда закончится.`;
  return (
    <div className="lu-stack" style={{ gap: 12 }}>
      <NetworkFabric nodes={preview} names={names} only={["mine", "displaced", "inference", "busy"]} />
      <div className="lu-dim" style={{ fontSize: 13 }}>{text} {children}</div>
    </div>
  );
}
