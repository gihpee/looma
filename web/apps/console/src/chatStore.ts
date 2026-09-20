/** История чата — в браузере. Абстракция одна, чтобы серверное хранение
 *  (`/api/chat/threads`, этап 8) подключилось заменой этого модуля, а не
 *  правкой экрана. localStorage может быть недоступен (приватный режим):
 *  тогда история живёт до перезагрузки. */

export interface ChatMessage {
  role: "user" | "assistant"; content: string; think?: string;
  at: number; tokens?: number; tps?: number; ttft?: number; cost?: number;
}
export interface ChatThread { id: string; title: string; model: string; at: number; messages: ChatMessage[] }

const KEY = "looma_chat_threads";
let memory: ChatThread[] | null = null;

function load(): ChatThread[] {
  if (memory) return memory;
  try { memory = JSON.parse(localStorage.getItem(KEY) || "[]"); } catch { memory = []; }
  return memory!;
}
function save(threads: ChatThread[]) {
  memory = threads;
  try { localStorage.setItem(KEY, JSON.stringify(threads.slice(0, 200))); } catch { /* приватный режим */ }
}

export const chatStore = {
  list: (): ChatThread[] => [...load()].sort((a, b) => b.at - a.at),
  get: (id: string) => load().find((t) => t.id === id) ?? null,
  create(model: string): ChatThread {
    const t: ChatThread = { id: Math.random().toString(36).slice(2, 10), title: "Новый чат", model, at: Date.now(), messages: [] };
    save([t, ...load()]);
    return t;
  },
  update(t: ChatThread) {
    const rest = load().filter((x) => x.id !== t.id);
    save([{ ...t, at: Date.now() }, ...rest]);
  },
  remove(id: string) { save(load().filter((t) => t.id !== id)); },
  clear() { save([]); },
};
