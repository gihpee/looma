/** Админка. Тот же каркас и вход, что у консоли; пускает только role=admin. */
import { Navigate, Route, Routes } from "react-router-dom";
import { Home, Server, KeyRound, Package, Boxes, Sparkles, ListTodo, Network, Users, Receipt, Tags } from "lucide-react";
import { useWho, useSignOut } from "@looma/api";
import { AppShell, Avatar, Badge, Brand, Button, ThemeToggle, type NavGroup } from "@looma/ui";
import { SignIn } from "./screens/SignIn";
import { Overview } from "./screens/Overview";
import { Nodes } from "./screens/Nodes";
import { AdminModels, AdminTraining, Ray, Tasks } from "./screens/Workloads";
import { Accounts, Leases, Pricing } from "./screens/Clients";
import { JoinKeys, Release } from "./screens/Network";

const GROUPS: NavGroup[] = [
  { items: [{ to: "/", label: "Обзор", icon: <Home size={17} />, end: true }] },
  { label: "сеть", items: [
    { to: "/nodes", label: "Узлы", icon: <Server size={17} /> },
    { to: "/keys", label: "Ключи подключения", icon: <KeyRound size={17} /> },
    { to: "/release", label: "Релизы агента", icon: <Package size={17} /> },
  ] },
  { label: "нагрузка", items: [
    { to: "/models", label: "Модели", icon: <Boxes size={17} /> },
    { to: "/training", label: "Обучение", icon: <Sparkles size={17} /> },
    { to: "/tasks", label: "Задачи", icon: <ListTodo size={17} /> },
    { to: "/ray", label: "Ray", icon: <Network size={17} /> },
  ] },
  { label: "клиенты", items: [
    { to: "/accounts", label: "Учётные записи", icon: <Users size={17} /> },
    { to: "/leases", label: "Аренды", icon: <Receipt size={17} /> },
    { to: "/pricing", label: "Ставки и цены", icon: <Tags size={17} /> },
  ] },
];
const TABS = [GROUPS[0].items[0], GROUPS[1].items[0], GROUPS[2].items[0], GROUPS[3].items[2]];

export function App() {
  const who = useWho();
  const signOut = useSignOut();
  if (who.isLoading) return <div className="lu-loading">…</div>;
  if (!who.data) return <SignIn title="Вход в панель" note="Только для администраторов." />;
  if (who.data.role !== "admin") {
    const console_ = `https://console.${location.hostname.replace(/^admin\./, "")}`;
    return (
      <main className="lu-page" style={{ paddingTop: 64, textAlign: "center", alignItems: "center" }}>
        <h1 className="lu-title">Панель — для администраторов</h1>
        <p className="lu-dim">Вы вошли как {who.data.email}. Ваши ресурсы — в консоли.</p>
        <a className="lu-btn lu-btn--primary" href={console_}>В консоль</a>
      </main>
    );
  }
  const foot = (
    <div className="lu-stack" style={{ gap: 8 }}>
      <Badge tone="ok" pulse>API отвечает</Badge>
      <div className="lu-row" style={{ padding: "4px 8px" }}>
        <Avatar name={who.data.email} />
        <span className="lu-muted" style={{ fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{who.data.email}</span>
        <Button kind="ghost" size="sm" style={{ marginLeft: "auto" }} onClick={() => signOut.mutate(undefined, { onSettled: () => { location.href = "/"; } })}>выйти</Button>
      </div>
    </div>
  );
  return (
    <AppShell groups={GROUPS} tabs={TABS} sidebarFoot={foot} title="Looma · админка"
              brand={<Brand name="Looma" tag={<span className="lu-badge lu-badge--ink lu-badge--sm" style={{ marginLeft: "auto" }}>admin</span>} />}
              topbar={<div className="lu-topbar__right"><a className="lu-btn lu-btn--secondary lu-btn--sm lu-hide-mobile" href={`https://console.${location.hostname.replace(/^admin\./, "")}`}>В консоль →</a><ThemeToggle /></div>}>
      <Routes>
        <Route path="/" element={<Overview />} />
        <Route path="/nodes" element={<Nodes />} />
        <Route path="/keys" element={<JoinKeys />} />
        <Route path="/release" element={<Release />} />
        <Route path="/models" element={<AdminModels />} />
        <Route path="/training" element={<AdminTraining />} />
        <Route path="/tasks" element={<Tasks />} />
        <Route path="/ray" element={<Ray />} />
        <Route path="/accounts" element={<Accounts />} />
        <Route path="/leases" element={<Leases />} />
        <Route path="/pricing" element={<Pricing />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}
