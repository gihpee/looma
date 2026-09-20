/** Вход. Единственная страница, доступная без представления. Одна и та же
 *  для консоли и админки: cookie ставится на общий домен, роль решает, куда
 *  пускать. */
import { useState } from "react";
import { useSignIn, message } from "@looma/api";
import { Button, Card, Field, Input, Mark } from "@looma/ui";

export function SignIn({ title = "Вход в консоль", note }: { title?: string; note?: string }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const signIn = useSignIn();
  const error = signIn.error ? message(signIn.error) : "";

  return (
    <main style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 16 }}>
      <Card panel raised style={{ width: "min(420px, 100%)", padding: 28 }}>
        <form className="lu-stack lu-stack--lg" onSubmit={(e) => { e.preventDefault(); signIn.mutate({ email, password }); }}>
          <div className="lu-row" style={{ color: "var(--accent)" }}>
            <Mark size={26} /><b style={{ color: "var(--text)", font: "600 16px var(--font-display)" }}>Looma Float</b>
          </div>
          <div>
            <h1 className="lu-title" style={{ margin: 0 }}>{title}</h1>
            {note && <p className="lu-muted" style={{ margin: "4px 0 0", fontSize: 13 }}>{note}</p>}
          </div>
          <Field label="Почта" htmlFor="email">
            <Input id="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </Field>
          <Field label="Пароль" htmlFor="password" error={error}>
            <Input id="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
          <Button kind="primary" size="lg" block type="submit" disabled={signIn.isPending}>{signIn.isPending ? "…" : "Войти"}</Button>
          <p className="lu-muted" style={{ margin: 0, fontSize: 12 }}>Регистрация по приглашению: учётные записи заводит администратор.</p>
        </form>
      </Card>
    </main>
  );
}
