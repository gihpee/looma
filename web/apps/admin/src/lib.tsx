/** Общее для экранов админки. */
import { AlertTriangle } from "lucide-react";
import { Notice } from "@looma/ui";
import type { StageHealth } from "@looma/api";

export function ErrorLine({ error }: { error?: Error | null }) {
  if (!error) return null;
  return <Notice tone="bad" icon={<AlertTriangle size={14} />}>{error.message}</Notice>;
}
/** «running» — про процесс, а не про готовность отвечать. */
export function phase(s: StageHealth): [string, number] {
  if (s.state === "pending") return ["в очереди", 6];
  if (s.state === "provisioning") return ["окружение и веса", 30];
  if (s.state === "failed") return ["упала", 100];
  if (s.state === "cancelled") return ["снята", 100];
  if (s.state !== "running") return [s.state, 0];
  if (!s.stage) return ["стартует", 60];
  return s.ready ? ["готова", 100] : ["грузит веса", 82];
}
export const b64 = (f: File) => new Promise<string>((res, rej) => { const rd = new FileReader(); rd.onload = () => res(String(rd.result).split(",")[1] ?? ""); rd.onerror = rej; rd.readAsDataURL(f); });
export const linkBadge = (n: { peer_id: string; in_network: boolean; symmetric_nat: boolean; reachable: boolean }): { tone: "ok" | "warn" | "bad" | "dim"; label: string } =>
  !n.peer_id ? { tone: "dim", label: "нет p2p" } : !n.in_network ? { tone: "bad", label: "вне сети" }
  : n.symmetric_nat ? { tone: "warn", label: "symmetric NAT" } : n.reachable ? { tone: "ok", label: "принимает" } : { tone: "warn", label: "за NAT" };
