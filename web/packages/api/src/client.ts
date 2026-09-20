/** Единственный способ ходить в API: кто ты, разбор ошибки, отмена.
 *
 *  Представиться можно двумя способами. Сессия ездит в cookie и ставится
 *  входом по паролю — так ходит человек, и на console./admin. cookie одна
 *  (Domain=.<домен>). Админ-токен остаётся запасным входом на случай, когда
 *  база недоступна или пароль потерян.
 *
 *  Пути относительные: браузер ходит на свой origin, а nginx проксирует ровно
 *  те префиксы, которые этому хосту положены (web/nginx). */

const TOKEN = "looma_token";

export const adminToken = {
  get: (): string => { try { return localStorage.getItem(TOKEN) ?? ""; } catch { return ""; } },
  set: (v: string) => { try { localStorage.setItem(TOKEN, v); } catch { /* приватный режим */ } },
};

function headers(json = true): Record<string, string> {
  const h: Record<string, string> = {};
  if (json) h["Content-Type"] = "application/json";
  const t = adminToken.get();
  if (t) h["X-Looma-Admin-Token"] = t;
  return h;
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly code?: string) { super(message); }
}

async function boom(r: Response): Promise<never> {
  let detail = `HTTP ${r.status}`; let code: string | undefined;
  try {
    const body = await r.json();
    detail = body?.error?.message ?? body?.detail ?? detail;
    code = body?.error?.code;
  } catch { /* не JSON */ }
  throw new ApiError(detail, r.status, code);
}

export async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const r = await fetch(path, { headers: headers(false), signal, credentials: "same-origin" });
  if (!r.ok) await boom(r);
  return r.json();
}

export async function send<T>(path: string, method: "POST" | "PUT" | "PATCH" | "DELETE", body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method, headers: headers(), credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) await boom(r);
  return r.json();
}

/** Файл в multipart: датасет для обучения, архив релиза, логотип. */
export async function upload<T>(path: string, form: FormData): Promise<T> {
  const r = await fetch(path, { method: "POST", headers: headers(false), credentials: "same-origin", body: form });
  if (!r.ok) await boom(r);
  return r.json();
}

/** Файл отдаётся байтами, а токен едет в заголовке — ссылкой это не сделать. */
export async function grab(path: string, filename: string): Promise<void> {
  const r = await fetch(path, { headers: headers(false), credentials: "same-origin" });
  if (!r.ok) await boom(r);
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

/** Поток строк SSE/NDJSON: чат и демо. Отдаёт куски по мере прихода;
 *  прерывается через signal. */
export async function* stream(path: string, body: unknown, signal?: AbortSignal): AsyncGenerator<string> {
  const r = await fetch(path, {
    method: "POST", headers: headers(), credentials: "same-origin", body: JSON.stringify(body), signal,
  });
  if (!r.ok || !r.body) await boom(r);
  const reader = r.body!.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const lines = buf.split("\n");
    buf = lines.pop() ?? "";
    for (const line of lines) if (line.trim()) yield line;
  }
  if (buf.trim()) yield buf;
}

export const message = (e: unknown) =>
  e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e);
