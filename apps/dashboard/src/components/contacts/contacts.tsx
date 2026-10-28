"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type components } from "@/lib/api/client";
import { problemMessage } from "@/lib/format";
import { CONSENT, CONSENT_CHANNEL, CONSENT_PURPOSE, OBJECTION, RECORDED_BY } from "@/lib/labels";

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
  granted: "bg-emerald-100 text-emerald-900",
  none: "bg-zinc-100 text-zinc-700",
  revoked: "bg-red-100 text-red-900",
};

/** Customers and their marketing consent, with the append-only ledger behind it (US-6, FR-060..065). */
export function Contacts() {
  const [filter, setFilter] = useState<Filter>("all");
  const [items, setItems] = useState<Contact[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(
    async (after?: string) => {
      const query = { limit: 50, ...(filter === "all" ? {} : { marketing_consent: filter }), ...(after ? { cursor: after } : {}) };
      const { data, error } = await api.GET("/contacts", { params: { query } });
      if (!data) {
        setMessage(problemMessage(error, "No pudimos cargar tus contactos."));
        return;
      }
      setItems((current) => (after ? [...(current ?? []), ...(data.items ?? [])] : (data.items ?? [])));
      setCursor(data.next_cursor);
    },
    [filter],
  );

  useEffect(() => {
    setItems(null);
    setMessage(null);
    void load();
  }, [load]);

  function replace(contact: Contact) {
    setItems((current) => current?.map((item) => (item.id === contact.id ? contact : item)) ?? null);
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
            onClick={() => setFilter(value)}
            className={`rounded-full px-4 py-1.5 text-sm ${value === filter ? "bg-zinc-900 text-white" : "bg-zinc-100 hover:bg-zinc-200"}`}
          >
            {label}
          </button>
        ))}
      </div>
      {message && (
        <p role="alert" className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-800">
          {message}
        </p>
      )}
      {items === null ? (
        !message && <p className="text-sm text-zinc-500">Cargando…</p>
      ) : items.length === 0 ? (
        <p className="text-sm text-zinc-500">
          {filter === "all" ? "Aún no tienes contactos. Llegan cuando alguien te escribe por WhatsApp." : "Nadie en este grupo."}
        </p>
      ) : (
        <ul className="divide-y divide-zinc-200 rounded-lg border border-zinc-200 bg-white">
          {items.map((contact) => (
            <ContactRow key={contact.id} contact={contact} onChange={replace} onError={setMessage} />
          ))}
        </ul>
      )}
      {cursor && (
        <button type="button" onClick={() => void load(cursor)} className="text-sm text-zinc-600 underline">
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

  useEffect(() => {
    if (open) void loadLedger();
  }, [open, loadLedger]);

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
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="flex w-full flex-wrap items-center gap-2 text-left">
        <span className="font-medium">{name}</span>
        {contact.display_name && contact.phone_masked && <span className="text-zinc-500">{contact.phone_masked}</span>}
        <span className={`rounded px-2 py-0.5 text-xs ${CONSENT_STYLE[contact.marketing_consent]}`}>{CONSENT[contact.marketing_consent]}</span>
        {contact.last_objection_tag && (
          <span className="rounded bg-zinc-100 px-2 py-0.5 text-xs text-zinc-700">Objeción: {OBJECTION[contact.last_objection_tag]}</span>
        )}
        {erasing && <span className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-900">Borrado en curso</span>}
        <span className="ml-auto text-xs text-zinc-500">
          {contact.conversation_count ?? 0} {contact.conversation_count === 1 ? "conversación" : "conversaciones"}
        </span>
      </button>
      {open && (
        <div className="space-y-3 border-l-2 border-zinc-200 pl-3">
          {contact.last_marketing_sent_at && (
            <p className="text-zinc-600">Última promoción: {new Date(contact.last_marketing_sent_at).toLocaleString("es-CO")}</p>
          )}
          <Ledger records={ledger} />
          {active && (
            <div className="flex flex-wrap gap-2">
              {contact.marketing_consent !== "granted" && <ManualConsent contactId={contact.id} onRecorded={refresh} onError={onError} />}
              {contact.marketing_consent !== "revoked" && (
                <button type="button" disabled={busy} onClick={() => void optOut()} className="rounded-md border border-zinc-300 px-3 py-1.5 hover:bg-zinc-50 disabled:opacity-50">
                  Dar de baja
                </button>
              )}
              <button type="button" disabled={busy} onClick={() => void erase()} className="rounded-md border border-red-300 px-3 py-1.5 text-red-800 hover:bg-red-50 disabled:opacity-50">
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
  if (records === null) return <p className="text-zinc-500">Cargando historial…</p>;
  if (records.length === 0) return <p className="text-zinc-500">Nunca se le pidió autorización.</p>;
  return (
    <ol className="space-y-2">
      {records.map((record) => (
        <li key={record.id} className="rounded-md bg-zinc-50 px-3 py-2">
          <p className="flex flex-wrap gap-x-2 text-xs text-zinc-500">
            <span className={record.status === "granted" ? "text-emerald-800" : "text-red-800"}>
              {record.status === "granted" ? "Autorizó" : "Retiró la autorización"}
            </span>
            <span>· {CONSENT_PURPOSE[record.purpose]}</span>
            <span>· {CONSENT_CHANNEL[record.channel]}</span>
            {record.recorded_by && <span>· {RECORDED_BY[record.recorded_by]}</span>}
            <span className="ml-auto">{new Date(record.recorded_at).toLocaleString("es-CO")}</span>
          </p>
          <p className="text-zinc-700">«{record.text_shown}»</p>
          <p className="text-zinc-900">Respuesta: «{record.response_text}»</p>
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

  async function submit(event: React.FormEvent) {
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
      <button type="button" onClick={() => setOpen(true)} className="rounded-md border border-zinc-300 px-3 py-1.5 hover:bg-zinc-50">
        Registrar autorización en persona
      </button>
    );
  }
  return (
    <form onSubmit={(event) => void submit(event)} className="w-full space-y-2 rounded-md border border-zinc-200 p-3">
      <label className="block space-y-1">
        <span className="text-xs text-zinc-600">Lo que le preguntaste</span>
        <textarea required maxLength={500} rows={2} value={shown} onChange={(e) => setShown(e.target.value)} className="w-full rounded-md border border-zinc-300 px-2 py-1" />
      </label>
      <label className="block space-y-1">
        <span className="text-xs text-zinc-600">Lo que respondió o firmó</span>
        <input required maxLength={200} value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder="Firmó el formulario: «Sí, acepto»" className="w-full rounded-md border border-zinc-300 px-2 py-1" />
      </label>
      <p className="text-xs text-zinc-500">Queda guardado a tu nombre, con fecha y hora. Solo regístralo si el cliente dijo que sí.</p>
      <div className="flex gap-2">
        <button type="submit" disabled={busy} className="rounded-md bg-zinc-900 px-3 py-1.5 text-white disabled:opacity-50">
          Guardar
        </button>
        <button type="button" onClick={() => setOpen(false)} className="rounded-md px-3 py-1.5 text-zinc-600">
          Cancelar
        </button>
      </div>
    </form>
  );
}
