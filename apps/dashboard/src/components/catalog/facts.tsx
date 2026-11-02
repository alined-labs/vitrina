"use client";

import { useCallback, useRef, useState } from "react";
import { api, type components } from "@/lib/api/client";
import { allFacts, catalogProblem, OFFLINE, type Fact } from "@/lib/catalog";
import { problemMessage } from "@/lib/format";
import { useLoad } from "@/lib/use-load";
import { Field } from "./product-form";

type FactKind = components["schemas"]["FactKind"];

const KINDS: Record<FactKind, string> = {
  faq: "Pregunta frecuente",
  shipping: "Envíos",
  payment: "Pagos",
  policy: "Políticas",
  hours: "Horarios",
  other: "Otro",
};
const TEXT_MAX = 1000;

function KindOptions() {
  return Object.entries(KINDS).map(([value, label]) => (
    <option key={value} value={value}>
      {label}
    </option>
  ));
}

/** What the agent may cite besides products: shipping, payment, hours (FR-002, FR-004). */
export function Facts() {
  const [facts, setFacts] = useState<Fact[] | null>(null);
  const [onlyDrafts, setOnlyDrafts] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [textError, setTextError] = useState<string | null>(null);
  const latest = useRef(0);

  const load = useCallback(async () => {
    const request = ++latest.current;
    const { items, error } = await allFacts();
    if (request !== latest.current) return; // a newer load is on its way
    if (items) setFacts(items);
    else setMessage(problemMessage(error, "No pudimos cargar las respuestas."));
  }, []);

  useLoad(load);

  async function submit(form: HTMLFormElement) {
    const fields = new FormData(form);
    const text = String(fields.get("text") ?? "").trim();
    setMessage(null);
    setNote(null);
    setTextError(text ? null : "Escribe la respuesta.");
    if (!text) {
      form.querySelector("textarea")?.focus();
      return;
    }
    setSaving(true);
    try {
      const { error } = await api.POST("/facts", { body: { kind: fields.get("kind") as FactKind, text } });
      if (error) setMessage(catalogProblem(error, "No pudimos guardar la respuesta."));
      else {
        form.reset();
        await load();
      }
    } catch {
      setMessage(OFFLINE);
    }
    setSaving(false);
  }

  function saved(fact: Fact, text: string) {
    latest.current++; // a load already in flight may not know about this change
    setFacts((current) => current?.map((item) => (item.id === fact.id ? fact : item)) ?? null);
    setNote(text);
  }

  async function deleted() {
    setNote("Eliminamos la respuesta. El agente ya no la cita.");
    await load();
  }

  const drafts = facts?.filter((fact) => fact.status === "draft").length ?? 0;
  const shown = (facts ?? [])
    .filter((fact) => !onlyDrafts || fact.status === "draft")
    .toSorted((a, b) => Number(b.status === "draft") - Number(a.status === "draft"));

  return (
    <section className="space-y-4">
      <h2 className="text-lg font-semibold">Respuestas del negocio</h2>
      <form
        noValidate
        aria-label="Agregar una respuesta"
        onSubmit={(event) => {
          event.preventDefault();
          void submit(event.currentTarget);
        }}
        className="grid items-start gap-3 card border-dashed border-control p-4 sm:grid-cols-[12rem_1fr]"
      >
        <Field label="Tipo">
          {({ id }) => (
            <select id={id} name="kind" defaultValue="shipping" className="input w-full min-w-0">
              <KindOptions />
            </select>
          )}
        </Field>
        <Field label="Respuesta" error={textError ?? undefined}>
          {({ id, describedBy, invalid }) => (
            <textarea
              id={id}
              name="text"
              rows={2}
              maxLength={TEXT_MAX}
              placeholder="Ej.: Envío a Medellín $12.000, llega en 2 a 3 días"
              aria-invalid={invalid}
              aria-describedby={describedBy}
              onChange={() => setTextError(null)}
              className="input w-full min-w-0 aria-invalid:border-danger"
            />
          )}
        </Field>
        <button type="submit" disabled={saving} className="btn btn--primary justify-self-start sm:col-start-2">
          Agregar
        </button>
      </form>
      {drafts > 0 && !onlyDrafts && (
        <div className="notice notice--warning flex flex-wrap items-center justify-between gap-2">
          <p>
            {drafts === 1
              ? "Tienes 1 respuesta en borrador. El agente no la cita hasta que la actives."
              : `Tienes ${drafts} respuestas en borrador. El agente no las cita hasta que las actives.`}
          </p>
          <button type="button" className="btn btn--secondary btn--sm" onClick={() => setOnlyDrafts(true)}>
            Ver borradores
          </button>
        </div>
      )}
      {message && (
        <p role="alert" className="notice notice--danger">
          {message}
        </p>
      )}
      {note && (
        <p role="status" className="notice notice--success">
          {note}
        </p>
      )}
      {facts && facts.length > 0 && (
        <div role="group" aria-label="Filtrar respuestas por estado" className="flex flex-wrap gap-2">
          <button type="button" aria-pressed={!onlyDrafts} onClick={() => setOnlyDrafts(false)} className="chip">
            Todas
          </button>
          <button type="button" aria-pressed={onlyDrafts} onClick={() => setOnlyDrafts(true)} className="chip">
            Borradores · {drafts}
          </button>
        </div>
      )}
      {facts === null ? (
        !message && <p className="text-sm text-fg-subtle">Cargando…</p>
      ) : facts.length === 0 ? (
        <p className="text-sm text-fg-subtle">
          Aún no hay respuestas. Agrega envíos, pagos y horarios para que el agente no tenga que inventarlos.
        </p>
      ) : shown.length === 0 ? (
        <p className="empty">No tienes respuestas en borrador.</p>
      ) : (
        <ul className="card divide-y divide-line">
          {shown.map((fact) => (
            <FactRow key={fact.id} fact={fact} onSaved={saved} onDeleted={deleted} />
          ))}
        </ul>
      )}
    </section>
  );
}

function FactRow({
  fact,
  onSaved,
  onDeleted,
}: {
  fact: Fact;
  onSaved: (fact: Fact, note: string) => void;
  onDeleted: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function setStatus(status: Fact["status"], note: string, failure: string) {
    setBusy(true);
    setMessage(null);
    try {
      const { data, error } = await api.PATCH("/facts/{fact_id}", {
        params: { path: { fact_id: fact.id } },
        body: { status },
      });
      if (data) onSaved(data, note);
      else setMessage(catalogProblem(error, failure));
    } catch {
      setMessage(OFFLINE);
    }
    setBusy(false);
  }

  async function remove() {
    setBusy(true);
    setMessage(null);
    try {
      const { error } = await api.DELETE("/facts/{fact_id}", { params: { path: { fact_id: fact.id } } });
      if (error) {
        setMessage(catalogProblem(error, "No pudimos eliminar la respuesta."));
        setBusy(false);
        return;
      }
      await onDeleted();
    } catch {
      setMessage(OFFLINE);
      setBusy(false);
    }
  }

  if (editing) {
    return (
      <li className="px-4 py-3">
        <FactEditor
          fact={fact}
          onCancel={() => setEditing(false)}
          onSaved={(updated) => {
            setEditing(false);
            onSaved(updated, "Guardamos los cambios de la respuesta.");
          }}
        />
      </li>
    );
  }

  return (
    <li className="space-y-2 px-4 py-3 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-xs uppercase tracking-wide text-fg-subtle">{KINDS[fact.kind]}</p>
          <p className="[overflow-wrap:anywhere]">{fact.text}</p>
        </div>
        {fact.status === "draft" ? (
          <span className="badge badge--warning">Borrador</span>
        ) : (
          <span className="badge badge--success">Activa</span>
        )}
      </div>
      {message && (
        <p role="alert" className="notice notice--danger">
          {message}
        </p>
      )}
      {confirming ? (
        <div className="flex flex-wrap items-center gap-2">
          <p className="font-semibold">¿Eliminar esta respuesta? No se puede deshacer.</p>
          <button type="button" disabled={busy} className="btn btn--secondary btn--sm" onClick={() => void remove()}>
            Sí, eliminar
          </button>
          <button type="button" disabled={busy} className="btn btn--ghost btn--sm" onClick={() => setConfirming(false)}>
            No
          </button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          {fact.status === "draft" ? (
            <button
              type="button"
              disabled={busy}
              className="btn btn--primary btn--sm"
              onClick={() =>
                void setStatus("active", "La respuesta ya está activa: el agente puede citarla.", "No pudimos activar la respuesta.")
              }
            >
              Activar
            </button>
          ) : (
            <button
              type="button"
              disabled={busy}
              className="btn btn--ghost btn--sm"
              onClick={() =>
                void setStatus("draft", "La respuesta pasó a borrador: el agente ya no la cita.", "No pudimos desactivar la respuesta.")
              }
            >
              Pasar a borrador
            </button>
          )}
          <button type="button" disabled={busy} className="btn btn--secondary btn--sm" onClick={() => setEditing(true)}>
            Editar
          </button>
          <button type="button" disabled={busy} className="btn btn--ghost btn--sm" onClick={() => setConfirming(true)}>
            Eliminar
          </button>
        </div>
      )}
    </li>
  );
}

function FactEditor({
  fact,
  onCancel,
  onSaved,
}: {
  fact: Fact;
  onCancel: () => void;
  onSaved: (fact: Fact) => void;
}) {
  const [kind, setKind] = useState<FactKind>(fact.kind);
  const [text, setText] = useState(fact.text);
  const [textError, setTextError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function submit(form: HTMLFormElement) {
    const clean = text.trim();
    setMessage(null);
    setTextError(clean ? null : "Escribe la respuesta.");
    if (!clean) {
      form.querySelector("textarea")?.focus();
      return;
    }
    const body = { ...(kind !== fact.kind ? { kind } : {}), ...(clean !== fact.text ? { text: clean } : {}) };
    if (Object.keys(body).length === 0) {
      onCancel();
      return;
    }
    setSaving(true);
    try {
      const { data, error } = await api.PATCH("/facts/{fact_id}", { params: { path: { fact_id: fact.id } }, body });
      if (data) onSaved(data);
      else setMessage(catalogProblem(error, "No pudimos guardar los cambios."));
    } catch {
      setMessage(OFFLINE);
    }
    setSaving(false);
  }

  return (
    <form
      noValidate
      aria-label="Editar respuesta"
      className="grid items-start gap-3 sm:grid-cols-[12rem_1fr]"
      onSubmit={(event) => {
        event.preventDefault();
        void submit(event.currentTarget);
      }}
    >
      <Field label="Tipo">
        {({ id }) => (
          <select
            id={id}
            value={kind}
            onChange={(event) => setKind(event.target.value as FactKind)}
            className="input w-full min-w-0"
          >
            <KindOptions />
          </select>
        )}
      </Field>
      <Field label="Respuesta" error={textError ?? undefined}>
        {({ id, describedBy, invalid }) => (
          <textarea
            id={id}
            value={text}
            rows={2}
            maxLength={TEXT_MAX}
            aria-invalid={invalid}
            aria-describedby={describedBy}
            onChange={(event) => {
              setText(event.target.value);
              setTextError(null);
            }}
            className="input w-full min-w-0 aria-invalid:border-danger"
          />
        )}
      </Field>
      {message && (
        <p role="alert" className="notice notice--danger sm:col-span-2">
          {message}
        </p>
      )}
      <div className="flex flex-wrap gap-2 sm:col-start-2">
        <button type="submit" disabled={saving} className="btn btn--primary btn--sm">
          {saving ? "Guardando…" : "Guardar"}
        </button>
        <button type="button" disabled={saving} className="btn btn--ghost btn--sm" onClick={onCancel}>
          Cancelar
        </button>
      </div>
    </form>
  );
}
