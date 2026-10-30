"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type components } from "@/lib/api/client";
import { formatDateTime, problemMessage } from "@/lib/format";
import { CONSENT, CONSENT_CHANNEL, CONSENT_PURPOSE, OBJECTION, RECORDED_BY } from "@/lib/labels";
import { useLoad } from "@/lib/use-load";

type Contact = components["schemas"]["Contact"];
type ConsentRecord = components["schemas"]["ConsentRecord"];
type Filter = Contact["marketing_consent"] | "all";

const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "Todos" },
  { value: "granted", label: "Con autorización" },
  { value: "none", label: "Sin respuesta" },
  { value: "revoked", label: "Se dieron de baja" },
];

const CONSENT_STYLE: Record<Contact["marketing_consent"], string> = {
  granted: "bg-success-bg text-success-fg",
  none: "bg-sunken text-fg",
  revoked: "bg-danger-bg text-danger-fg",
};

/** Customers and their marketing consent, with the append-only ledger behind it (US-6, FR-060..065). */
export function Contacts() {
  const [filter, setFilter] = useState<Filter>("all");
  // Each filter keeps its own list and next page, as the ads page does with its tabs.
  const [lists, setLists] = useState<Partial<Record<Filter, { items: Contact[]; cursor: string | null }>>>({});
  const items = lists[filter]?.items ?? null;
  const cursor = lists[filter]?.cursor ?? null;
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(
    async (after?: string) => {
      const query = { limit: 50, ...(filter === "all" ? {} : { marketing_consent: filter }), ...(after ? { cursor: after } : {}) };
      const { data, error } = await api.GET("/contacts", { params: { query } });
      if (!data) {
        setMessage(problemMessage(error, "No pudimos cargar tus contactos."));
        return;
      }
      setLists((current) => ({
        ...current,
        [filter]: {
          items: after ? [...(current[filter]?.items ?? []), ...(data.items ?? [])] : (data.items ?? []),
          cursor: data.next_cursor ?? null,
        },
      }));
    },
    [filter],
  );

  useLoad(load);

  function replace(contact: Contact) {
    setLists((current) => {
      const list = current[filter];
      if (!list) return current;
      return { ...current, [filter]: { ...list, items: list.items.map((item) => (item.id === contact.id ? contact : item)) } };
    });
  }

  return (
    <section className="space-y-4">
      <div role="tablist" className="flex flex-wrap gap-2">
        {FILTERS.map(({ value, label }) => (
          <button
            key={value}
            role="tab"
            type="button"
            aria-selected={value === filter}
            onClick={() => {
              setFilter(value);
              setMessage(null);
            }}
            className="chip"
          >
            {label}
          </button>
        ))}
      </div>
      {message && (
        <p role="alert" className="notice notice--danger">
          {message}
        </p>
      )}
      {items === null ? (
        !message && <p className="text-sm text-fg-subtle">Cargando…</p>
      ) : items.length === 0 ? (
        <p className="text-sm text-fg-subtle">
          {filter === "all" ? "Aún no tienes contactos. Llegan cuando alguien te escribe por WhatsApp." : "Nadie en este grupo."}
        </p>
      ) : (
        <ul className="card divide-y divide-line">
          {items.map((contact) => (
            <ContactRow key={contact.id} contact={contact} onChange={replace} onError={setMessage} />
          ))}
        </ul>
      )}
      {cursor && (
        <button type="button" onClick={() => void load(cursor)} className="text-sm text-fg-muted underline">
          Ver más contactos
        </button>
      )}
    </section>
  );
}

function ContactRow({
  contact,
  onChange,
  onError,
}: {
  contact: Contact;
  onChange: (contact: Contact) => void;
  onError: (message: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [ledger, setLedger] = useState<ConsentRecord[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [erasing, setErasing] = useState(false);

  const loadLedger = useCallback(async () => {
    const { data } = await api.GET("/contacts/{contact_id}/consents", { params: { path: { contact_id: contact.id } } });
    setLedger(data ?? []);
  }, [contact.id]);


  async function refresh() {
    const { data } = await api.GET("/contacts/{contact_id}", { params: { path: { contact_id: contact.id } } });
    if (data) onChange(data);
    await loadLedger();
  }

  async function optOut() {
    if (!window.confirm("Esta persona dejará de recibir promociones. ¿Continuar?")) return;
    setBusy(true);
    const { data, error } = await api.POST("/contacts/{contact_id}/opt-out", { params: { path: { contact_id: contact.id } } });
    setBusy(false);
    if (data) {
      onChange(data);
      await loadLedger();
    } else onError(problemMessage(error, "No pudimos darlo de baja."));
  }

  async function erase() {
    const question = "Se borrarán su nombre, su número y sus chats en las próximas 24 horas. No se puede deshacer. ¿Continuar?";
    if (!window.confirm(question)) return;
    setBusy(true);
    const { error } = await api.DELETE("/contacts/{contact_id}", { params: { path: { contact_id: contact.id } } });
    setBusy(false);
    if (error) onError(problemMessage(error, "No pudimos pedir el borrado."));
    else setErasing(true);
  }

  const name = contact.erased_at ? "Datos borrados" : (contact.display_name ?? contact.phone_masked ?? "Cliente");
  const active = !contact.erased_at && !erasing;
  return (
    <li className="space-y-3 px-4 py-3 text-sm">
      <button
        type="button"
        onClick={() => {
          setOpen(!open);
          if (!open) void loadLedger();
        }}
        aria-expanded={open}
        className="flex w-full flex-wrap items-center gap-2 text-left"
      >
        <span className="font-medium">{name}</span>
        {contact.display_name && contact.phone_masked && <span className="text-fg-subtle">{contact.phone_masked}</span>}
        <span className={`rounded px-2 py-0.5 text-xs ${CONSENT_STYLE[contact.marketing_consent]}`}>{CONSENT[contact.marketing_consent]}</span>
        {contact.last_objection_tag && (
          <span className="badge">Objeción: {OBJECTION[contact.last_objection_tag]}</span>
        )}
        {erasing && <span className="badge badge--warning">Borrado en curso</span>}
        <span className="ml-auto text-xs text-fg-subtle">
          {contact.conversation_count ?? 0} {contact.conversation_count === 1 ? "conversación" : "conversaciones"}
        </span>
      </button>
      {open && (
        <div className="space-y-3 border-l-2 border-line pl-3">
          {contact.last_marketing_sent_at && (
            <p className="text-fg-muted">Última promoción: {formatDateTime(contact.last_marketing_sent_at)}</p>
          )}
          <Ledger records={ledger} />
          {active && (
            <div className="flex flex-wrap gap-2">
              {contact.marketing_consent !== "granted" && <ManualConsent contactId={contact.id} onRecorded={refresh} onError={onError} />}
              {contact.marketing_consent !== "revoked" && (
                <button type="button" disabled={busy} onClick={() => void optOut()} className="btn btn--secondary btn--sm">
                  Dar de baja
                </button>
              )}
              <button type="button" disabled={busy} onClick={() => void erase()} className="rounded-md border border-danger px-3 py-1.5 text-danger-fg hover:bg-danger-bg disabled:opacity-50">
                Borrar sus datos
              </button>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

/** The consent ledger, newest first: what was asked, what the customer answered, and where. */
function Ledger({ records }: { records: ConsentRecord[] | null }) {
  if (records === null) return <p className="text-fg-subtle">Cargando historial…</p>;
  if (records.length === 0) return <p className="text-fg-subtle">Nunca se le pidió autorización.</p>;
  return (
    <ol className="space-y-2">
      {records.map((record) => (
        <li key={record.id} className="rounded-md bg-sunken px-3 py-2">
          <p className="flex flex-wrap gap-x-2 text-xs text-fg-subtle">
            <span className={record.status === "granted" ? "text-success-fg" : "text-danger-fg"}>
              {record.status === "granted" ? "Autorizó" : "Retiró la autorización"}
            </span>
            <span>· {CONSENT_PURPOSE[record.purpose]}</span>
            <span>· {CONSENT_CHANNEL[record.channel]}</span>
            {record.recorded_by && <span>· {RECORDED_BY[record.recorded_by]}</span>}
            <span className="ml-auto">{formatDateTime(record.recorded_at)}</span>
          </p>
          <p className="text-fg">«{record.text_shown}»</p>
          <p className="text-fg">Respuesta: «{record.response_text}»</p>
        </li>
      ))}
    </ol>
  );
}

/** A consent given outside WhatsApp (a paper form at the shop), attested by the seller. */
function ManualConsent({ contactId, onRecorded, onError }: { contactId: string; onRecorded: () => Promise<void>; onError: (message: string) => void }) {
  const [open, setOpen] = useState(false);
  const [shown, setShown] = useState("");
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open || shown) return;
    void api.GET("/sellers/me").then(({ data }) => {
      if (data?.consent_prompt_text) setShown(data.consent_prompt_text);
    });
  }, [open, shown]);

  async function submit(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    const { error } = await api.POST("/contacts/{contact_id}/consents", {
      params: { path: { contact_id: contactId } },
      body: { purpose: "marketing", status: "granted", text_shown: shown.trim(), response_text: answer.trim(), channel: "in_person" },
    });
    setBusy(false);
    if (error) {
      onError(problemMessage(error, "No pudimos guardar la autorización."));
      return;
    }
    setOpen(false);
    setAnswer("");
    await onRecorded();
  }

  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} className="btn btn--secondary btn--sm">
        Registrar autorización en persona
      </button>
    );
  }
  return (
    <form onSubmit={(event) => void submit(event)} className="w-full space-y-2 rounded-md border border-line p-3">
      <label className="block space-y-1">
        <span className="text-xs text-fg-muted">Lo que le preguntaste</span>
        <textarea required maxLength={500} rows={2} value={shown} onChange={(e) => setShown(e.target.value)} className="input input--sm w-full" />
      </label>
      <label className="block space-y-1">
        <span className="text-xs text-fg-muted">Lo que respondió o firmó</span>
        <input required maxLength={200} value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder="Firmó el formulario: «Sí, acepto»" className="input input--sm w-full" />
      </label>
      <p className="text-xs text-fg-subtle">Queda guardado a tu nombre, con fecha y hora. Solo regístralo si el cliente dijo que sí.</p>
      <div className="flex gap-2">
        <button type="submit" disabled={busy} className="btn btn--primary btn--sm">
          Guardar
        </button>
        <button type="button" onClick={() => setOpen(false)} className="rounded-md px-3 py-1.5 text-fg-muted">
          Cancelar
        </button>
      </div>
    </form>
  );
}
