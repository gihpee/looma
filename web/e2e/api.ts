/** Подменный API для e2e: ровно те ответы, которых ждут экраны, и ничего
 *  сверх. Состояние — в замыкании, чтобы «войти», «создать ключ» и
 *  «арендовать» меняли то, что видно потом. */
import type { Page, Route } from "@playwright/test";

export const CLIENT = { id: 7, email: "client@looma.ru", role: "client", display_name: "Иван", how: "session" };

export type World = ReturnType<typeof world>;

export function world() {
  return {
    signedIn: false,
    keys: [] as { id: number; hint: string; name: string; created_at: string; last_used_at: null; revoked_at: null }[],
    clusters: [] as Record<string, unknown>[],
    rented: [] as Record<string, unknown>[],
    nodes: [
      { state: "free", gpus: 1, gpu_class: "4090", gpu_name: "RTX 4090", vram_gb: 24, rtt_ms: 12 },
      { state: "free", gpus: 1, gpu_class: "4090", gpu_name: "RTX 4090", vram_gb: 24, rtt_ms: 15 },
      { state: "inference", gpus: 1, gpu_class: "a100", gpu_name: "A100", vram_gb: 80, rtt_ms: 9 },
    ],
  };
}

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
const refuse = (route: Route, status: number, message: string) => json(route, { error: { message, type: "invalid_request_error" } }, status);

/** Повесить подмену на страницу. Всё, что не описано, — 404 с понятным текстом:
 *  молчаливый проход к настоящему серверу превратил бы тест в лотерею. */
export async function mockApi(page: Page, w: World) {
  // Только сами вызовы API: модули vite тоже лежат в каталоге `packages/api`,
  // и регулярка по подстроке перехватила бы их вместе с запросами.
  await page.route((u) => u.pathname.startsWith("/api/") || u.pathname.startsWith("/v1/"), async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const method = req.method();
    const body = () => (req.postDataJSON?.() ?? {}) as Record<string, unknown>;

    if (path === "/api/session" && method === "POST") {
      const { email, password } = body();
      if (email === CLIENT.email && password === "верный-пароль") { w.signedIn = true; return json(route, CLIENT); }
      return refuse(route, 401, "почта или пароль не подходят");
    }
    if (path === "/api/session" && method === "DELETE") { w.signedIn = false; return json(route, { ok: true }); }
    if (!w.signedIn) return refuse(route, 401, "представьтесь");

    if (path === "/api/me") return json(route, CLIENT);
    if (path === "/api/balance") return json(route, { kopecks: 1_250_000, credited: 1_500_000, spent: 250_000, currency: "RUB", grants: [] });
    if (path === "/api/usage") return json(route, { leases: [], tokens: [], total: 0, currency: "RUB" });
    if (path === "/api/capacity") return json(route, { nodes: w.nodes });
    if (path === "/api/rates") return json(route, { rates: [{ resource: "looma-compute", per_hour: 12000, currency: "RUB" }, { resource: "looma-inference", per_hour: 12000, currency: "RUB" }], gpu_classes: [], training_rate_kopecks: null, currency: "RUB" });
    if (path === "/v1/models") return json(route, { object: "list", data: [] });
    if (path === "/api/deployments") return json(route, { deployments: [] });
    if (path === "/api/train") return json(route, { jobs: [] });

    if (path === "/api/keys" && method === "GET") return json(route, { keys: w.keys });
    if (path === "/api/keys" && method === "POST") {
      const id = w.keys.length + 1;
      const key = `lk_${"a".repeat(8)}${id}_${"f".repeat(24)}`;
      w.keys.push({ id, hint: key.slice(0, 8), name: String(body().name ?? ""), created_at: new Date().toISOString(), last_used_at: null, revoked_at: null });
      return json(route, { key, id });
    }

    if (path === "/api/compute" && method === "GET") return json(route, { clusters: w.clusters, pending: [] });
    if (path === "/api/compute" && method === "POST") {
      const b = body();
      w.rented.push(b);
      const group_id = "g-e2e-1";
      w.clusters.push({ id: 1, group_id, label: b.label || "ray", nodes: b.size, gpus: b.size, per_hour: 12000, currency: "RUB", opened_at: new Date().toISOString(), alive: true });
      return json(route, { group_id, label: b.label || "ray", nodes: ["n1", "n2"], requested: b.size, granted: b.size, path: "direct", relayed_pairs: 0, warning: "" });
    }
    if (path.startsWith("/api/compute/")) return json(route, { group_id: path.split("/").pop(), label: "ray", alive: true, nodes: 2 });

    return refuse(route, 404, `e2e: маршрут ${method} ${path} не подменён`);
  });
}

export async function signIn(page: Page) {
  await page.goto("/");
  await page.getByLabel("Почта").fill(CLIENT.email);
  await page.getByLabel("Пароль").fill("верный-пароль");
  await page.getByRole("button", { name: "Войти" }).click();
}
