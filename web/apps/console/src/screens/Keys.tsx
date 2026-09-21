/** Ключи API. Ключ показывается один раз: в базе только хэш. Рядом —
 *  base_url и примеры, потому что ключ без адреса бесполезен. */
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { KeyRound, Plus } from "lucide-react";
import { send, useApiKeys, useModels, type ApiKey } from "@looma/api";
import { Button, Card, CardBody, CardHead, CodeBlock, Confirm, Empty, Field, Input, Modal, Page, PageHead, Segmented, dateOnly, useAction, useToast } from "@looma/ui";
import { ErrorLine, curlSnippet, pythonSnippet } from "../lib";

export function Keys() {
  const qc = useQueryClient();
  const toast = useToast();
  const keys = useApiKeys();
  const models = useModels();
  const action = useAction(() => qc.invalidateQueries({ queryKey: ["/api/keys"] }));
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [fresh, setFresh] = useState<string>("");
  const [revoking, setRevoking] = useState<ApiKey | null>(null);
  const [lang, setLang] = useState<"curl" | "python">("curl");
  const rows = (keys.data?.keys ?? []).filter((k) => !k.revoked_at);
  const model = models.data?.data?.[0]?.id;

  const create = () => action.run(async () => {
    const made = await send<{ key: string }>("/api/keys", "POST", { name: name.trim() });
    setFresh(made.key); setCreating(false); setName("");
  });

  return (
    <Page>
      <PageHead title="Ключи API" text="Ключ подписывает запросы к инференсу и кластерам. Не показывайте его в браузере и клиентском коде."
                actions={<Button kind="primary" icon={<Plus size={16} />} onClick={() => setCreating(true)}>Создать ключ</Button>} />
      <ErrorLine error={keys.error} />
      {fresh && (
        <Card pad style={{ borderColor: "var(--accent)" }}>
          <b>Сохраните ключ сейчас — он больше не будет показан.</b>
          <div className="lu-weft" style={{ margin: "10px 0" }}><CodeBlock code={fresh} /></div>
          <div className="lu-row">
            <Button size="sm" onClick={() => { navigator.clipboard?.writeText(fresh); toast("ok", "скопирован"); }}>Скопировать</Button>
            <Button size="sm" kind="ghost" onClick={() => setFresh("")}>Убрать</Button>
          </div>
        </Card>
      )}
      <div className="lu-grid lu-grid--main">
        <Card>
          {keys.isLoading ? <div className="lu-loading">…</div> : rows.length === 0 ? (
            <Empty title="Ключей нет" icon={<KeyRound size={40} />} action={<Button kind="primary" size="sm" onClick={() => setCreating(true)}>Создать ключ</Button>}>
              Ключ подписывает запросы к инференсу; base_url — справа.
            </Empty>
          ) : (
            <table className="lu-table lu-table--cards">
              <thead><tr><th>Ключ</th><th>Имя</th><th>Создан</th><th>Использован</th><th /></tr></thead>
              <tbody>
                {rows.map((k) => (
                  <tr key={k.id}>
                    <td data-label="Ключ" className="lu-mono">{k.hint}…</td>
                    <td data-label="Имя">{k.name || <span className="lu-muted">без имени</span>}</td>
                    <td data-label="Создан">{dateOnly(k.created_at)}</td>
                    <td data-label="Использован">{k.last_used_at ? dateOnly(k.last_used_at) : <span className="lu-muted">ни разу</span>}</td>
                    <td><Button size="sm" kind="ghost" onClick={() => setRevoking(k)}>отозвать</Button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
        <div className="lu-stack" style={{ gap: 16 }}>
          <Card>
            <CardHead><b>Как использовать</b><Segmented soft value={lang} onChange={setLang} options={[{ value: "curl", label: "curl" }, { value: "python", label: "python" }]} /></CardHead>
            <CardBody className="lu-stack">
              <CodeBlock code={lang === "curl" ? curlSnippet(model) : pythonSnippet(model)} />
            </CardBody>
          </Card>
        </div>
      </div>

      {creating && (
        <Modal title="Новый ключ" size="sm" onClose={() => setCreating(false)} footer={<><span className="lu-spacer" /><Button kind="ghost" onClick={() => setCreating(false)}>Отмена</Button><Button kind="primary" onClick={create} disabled={action.busy}>Создать</Button></>}>
          <Field label="Имя" hint="необязательно: для какого проекта или сервиса" htmlFor="kname"><Input id="kname" value={name} onChange={(e) => setName(e.target.value)} placeholder="прод-бот" autoFocus /></Field>
        </Modal>
      )}
      {revoking && (
        <Confirm title="Отозвать ключ?" action="Отозвать" body={<>Запросы с ключом <code>{revoking.hint}…</code> перестанут проходить сразу.</>}
                 onClose={() => setRevoking(null)} onConfirm={() => action.run(() => send(`/api/keys/${revoking.id}`, "DELETE"), "ключ отозван")} />
      )}
    </Page>
  );
}
