/** Консоль клиента. Роутер: вход → каркас с экранами. URL на каждый экран —
 *  deep-link и «назад» работают, а закладку на «Кластеры» можно дать коллеге. */
import { lazy, Suspense } from "react";
import { Route, Routes, useLocation } from "react-router-dom";
import { Home as HomeIcon, Server, MessageSquare, Boxes, Sparkles, KeyRound, CreditCard, Settings as SettingsIcon, ExternalLink } from "lucide-react";
import { useWho, useSignOut, useBalance } from "@looma/api";
import { AppShell, Avatar, Button, NotFound, ThemeToggle, money, type NavGroup } from "@looma/ui";
import { SignIn } from "./screens/SignIn";
import { Home } from "./screens/Home";
import { Clusters } from "./screens/Clusters";
import { Keys } from "./screens/Keys";
import { Billing } from "./screens/Billing";
import { Settings } from "./screens/Settings";
import { Chat } from "./screens/Chat";
import { Models } from "./screens/Models";
import { Training } from "./screens/Training";

const UiShowcase = lazy(() => import("./screens/UiShowcase").then((m) => ({ default: m.UiShowcase })));

const GROUPS: NavGroup[] = [
  { items: [{ to: "/", label: "Главная", icon: <HomeIcon size={18} />, end: true }] },
  { label: "looma-compute", items: [{ to: "/compute/clusters", label: "Кластеры", icon: <Server size={18} /> }] },
  { label: "looma-intelligence", items: [
    { to: "/intelligence/chat", label: "Чат", icon: <MessageSquare size={18} /> },
    { to: "/intelligence/models", label: "Модели", icon: <Boxes size={18} /> },
    { to: "/intelligence/training", label: "Обучение", icon: <Sparkles size={18} /> },
    { to: "/intelligence/keys", label: "Ключи API", icon: <KeyRound size={18} /> },
  ] },
  { label: "аккаунт", items: [
    { to: "/billing", label: "Биллинг", icon: <CreditCard size={18} /> },
    { to: "/settings", label: "Настройки", icon: <SettingsIcon size={18} /> },
  ] },
];
const TABS = [GROUPS[0].items[0], GROUPS[1].items[0], GROUPS[2].items[0], GROUPS[2].items[1]];

const DOCS: Record<string, string> = {
  "/compute": "https://loomafloat.ru/docs/compute", "/intelligence/chat": "https://loomafloat.ru/docs/chat",
  "/intelligence/models": "https://loomafloat.ru/docs/models", "/intelligence/training": "https://loomafloat.ru/docs/training",
  "/intelligence/keys": "https://loomafloat.ru/docs/keys", "/billing": "https://loomafloat.ru/docs/billing",
};

export function App() {
  const who = useWho();
  const signOut = useSignOut();
  const balance = useBalance();
  const { pathname } = useLocation();

  // Витрина компонентов — без входа и без API, только в dev-сборке.
  if (import.meta.env.DEV && pathname === "/ui") {
    return <Suspense fallback={<div className="lu-loading">…</div>}><UiShowcase /></Suspense>;
  }
  // Пока не спросили — ничего: мигнуть страницей входа человеку, который уже
  // вошёл, хуже, чем задержаться на долю секунды.
  if (who.isLoading) return <div className="lu-loading">…</div>;
  if (!who.data) return <SignIn />;

  const docs = Object.entries(DOCS).find(([p]) => pathname.startsWith(p))?.[1] ?? "https://loomafloat.ru/docs";
  const foot = (
    <div className="lu-row" style={{ padding: "6px 8px" }}>
      <Avatar name={who.data.display_name || who.data.email} />
      <div style={{ minWidth: 0, display: "flex", flexDirection: "column" }}>
        <span style={{ fontSize: 13, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{who.data.display_name || "Клиент"}</span>
        <span className="lu-muted" style={{ fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{who.data.email}</span>
      </div>
      <Button kind="ghost" size="sm" style={{ marginLeft: "auto" }} onClick={() => signOut.mutate(undefined, { onSettled: () => { location.href = "/"; } })}>выйти</Button>
    </div>
  );
  const topbar = (
    <div className="lu-topbar__right">
      <a className="lu-btn lu-btn--secondary lu-btn--sm" href="/billing" style={{ gap: 6 }}><span className="lu-muted lu-hide-mobile">Баланс</span><b style={{ fontFamily: "var(--font-display)" }}>{balance.data ? money(balance.data.kopecks, balance.data.currency, 0) : "—"}</b></a>
      {who.data.role === "admin" && <a className="lu-btn lu-btn--secondary lu-btn--sm lu-hide-mobile" href={`https://admin.${location.hostname.replace(/^console\./, "")}`}>Админка</a>}
      <a className="lu-btn lu-btn--secondary lu-btn--sm lu-hide-mobile" href={docs} target="_blank" rel="noreferrer">Документация <ExternalLink size={13} /></a>
      <ThemeToggle />
    </div>
  );

  return (
    <AppShell groups={GROUPS} tabs={TABS} sidebarFoot={foot} topbar={topbar} title="Looma Float">
      <Suspense fallback={<div className="lu-loading">…</div>}>
        <Routes>
          <Route path="/" element={<Home who={who.data} />} />
          <Route path="/compute/clusters/*" element={<Clusters />} />
          <Route path="/intelligence/chat" element={<Chat />} />
          <Route path="/intelligence/models/*" element={<Models />} />
          <Route path="/intelligence/training/*" element={<Training />} />
          <Route path="/intelligence/keys" element={<Keys />} />
          <Route path="/billing" element={<Billing />} />
          <Route path="/settings" element={<Settings who={who.data} />} />
          <Route path="*" element={<NotFound home="/" />} />
        </Routes>
      </Suspense>
    </AppShell>
  );
}
