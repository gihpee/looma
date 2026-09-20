/** Настройки: профиль и тема. Смена пароля — через администратора, пока у
 *  клиента нет своей ручки (этап 8). */
import type { Who } from "@looma/api";
import { Card, CardBody, CardHead, KeyValue, Notice, Page, PageHead, ThemeToggle, useTheme } from "@looma/ui";

export function Settings({ who }: { who: Who }) {
  const [theme] = useTheme();
  return (
    <Page>
      <PageHead title="Настройки" />
      <div className="lu-grid lu-grid--2">
        <Card>
          <CardHead><b>Профиль</b></CardHead>
          <CardBody>
            <KeyValue rows={[{ k: "Почта", v: who.email }, { k: "Имя", v: who.display_name || <span className="lu-muted">не задано</span> }, { k: "Роль", v: who.role === "admin" ? "администратор" : "клиент" }]} />
            <div style={{ marginTop: 12 }}><Notice tone="info">Сменить пароль или имя пока может только администратор — напишите на support@loomafloat.ru.</Notice></div>
          </CardBody>
        </Card>
        <Card>
          <CardHead><b>Тема</b><ThemeToggle /></CardHead>
          <CardBody className="lu-dim" style={{ fontSize: 13 }}>Сейчас — {theme === "dark" ? "тёмная" : "светлая"}. Выбор хранится в этом браузере.</CardBody>
        </Card>
      </div>
    </Page>
  );
}
