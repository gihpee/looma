/** Каркас приложения: сайдбар на десктопе, верхняя панель + нижний tab-bar на
 *  телефоне. Навигация — данные (группы и пункты), а не разметка: консоль и
 *  админка отличаются только списком. Ссылки — <NavLink> роутера, чтобы
 *  aria-current ставился по адресу, а не по догадке. */
import { useState, type ReactNode } from "react";
import { NavLink, Link } from "react-router-dom";
import { Menu, MoreHorizontal } from "lucide-react";
import { Mark } from "./mark";
import { IconButton, cx } from "./primitives";
import { Drawer } from "./layers";

export interface NavItem { to: string; label: string; icon: ReactNode; count?: number; end?: boolean }
export interface NavGroup { label?: string; items: NavItem[] }

export function Nav({ groups, onNavigate }: { groups: NavGroup[]; onNavigate?: () => void }) {
  return (
    <nav className="lu-nav">
      {groups.map((g, i) => (
        <div key={i} className="lu-nav">
          {g.label && <div className="lu-nav__group">{g.label}</div>}
          {g.items.map((it) => (
            <NavLink key={it.to} to={it.to} end={it.end} onClick={onNavigate}>
              {it.icon}{it.label}
              {it.count !== undefined && it.count > 0 && <span className="lu-nav__count">{it.count}</span>}
            </NavLink>
          ))}
        </div>
      ))}
    </nav>
  );
}

export function Brand({ name = "Looma Float", to = "/", tag }: { name?: string; to?: string; tag?: ReactNode }) {
  return (
    <Link to={to} className="lu-brand">
      <Mark size={24} className="lu-brand__mark" />
      <b>{name}</b>
      {tag}
    </Link>
  );
}

/** Каркас. `sidebarFoot` — низ сайдбара (профиль, чеклист); `tabs` — до пяти
 *  пунктов для нижней панели телефона (последний обычно «Ещё» = открыть
 *  полное меню). */
export function AppShell({ brand, groups, sidebarFoot, tabs, topbar, title, subtitle, children, fab }: {
  brand?: ReactNode; groups: NavGroup[]; sidebarFoot?: ReactNode; tabs?: NavItem[];
  topbar?: ReactNode; title?: ReactNode; subtitle?: ReactNode; children: ReactNode; fab?: ReactNode;
}) {
  const [menu, setMenu] = useState(false);
  const mobileTabs = (tabs ?? groups.flatMap((g) => g.items)).slice(0, 4);
  return (
    <div className="lu-shell">
      <aside className="lu-sidebar">
        {brand ?? <Brand />}
        <Nav groups={groups} />
        {sidebarFoot && <div className="lu-sidebar__foot">{sidebarFoot}</div>}
      </aside>
      <div className="lu-main">
        <header className="lu-topbar">
          <IconButton label="Меню" kind="ghost" className="lu-topbar__burger" onClick={() => setMenu(true)}><Menu size={22} /></IconButton>
          {title && <div className="lu-topbar__title"><b>{title}</b>{subtitle && <small>{subtitle}</small>}</div>}
          {topbar}
        </header>
        {children}
        {fab && <div className="lu-fab">{fab}</div>}
      </div>
      <nav className="lu-tabbar" aria-label="Разделы">
        {mobileTabs.map((it) => <NavLink key={it.to} to={it.to} end={it.end}>{it.icon}{it.label}</NavLink>)}
        <a href="#menu" onClick={(e) => { e.preventDefault(); setMenu(true); }}><MoreHorizontal size={22} />Ещё</a>
      </nav>
      {menu && (
        <Drawer title="Меню" side="left" onClose={() => setMenu(false)} footer={sidebarFoot}>
          <Nav groups={groups} onNavigate={() => setMenu(false)} />
        </Drawer>
      )}
    </div>
  );
}

/** Шапка страницы: заголовок, подзаголовок, действия справа. */
export function PageHead({ title, text, actions }: { title: ReactNode; text?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="lu-page__head">
      <div><h1>{title}</h1>{text && <p>{text}</p>}</div>
      {actions && <div className="lu-page__actions">{actions}</div>}
    </div>
  );
}
export const Page = ({ children, className }: { children: ReactNode; className?: string }) =>
  <div className={cx("lu-page", className)}>{children}</div>;
