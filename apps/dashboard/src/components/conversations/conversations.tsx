"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type components } from "@/lib/api/client";
import { problemMessage } from "@/lib/format";
import { HANDOFF_REASON, MODE, OBJECTION } from "@/lib/labels";

type Conversation = components["schemas"]["Conversation"];
type Filter = "attention" | "agent" | "all";

const FILTERS: { value: Filter; label: string }[] = [
  { value: "attention", label: "Esperan tu respuesta" },
  { value: "agent", label: "Atiende el agente" },
  { value: "all", label: "Todas" },
];

/** Chats the agent handles and the ones handed to the seller, with the ad that brought them (US-3, US-4). */
export function Conversations() {
  const [filter, setFilter] = useState<Filter>("attention");
  const [items, setItems] = useState<Conversation[] | null>(null);
  const [state, setState] = useState<"ready" | "not_connected" | "error">("ready");
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    const query = filter === "attention" ? { needs_attention: true } : filter === "agent" ? { mode: "agent" as const } : {};
    const { data, error, response } = await api.GET("/conversations", { params: { query: { ...query, limit: 50 } } });
    if (data) {
      setItems(data.items ?? []);
      setState("ready");
    } else if (response.status === 404) {
      setState("not_connected"); // the conversation engine (MX2) has not published its routes yet
    } else {
      setState("error");
      setMessage(problemMessage(error, "No pudimos cargar las conversaciones."));
    }
  }, [filter]);

  useEffect(() => {
    setItems(null);
    void load();
  }, [load]);

  if (state === "not_connected") {
    return (
      <p className="rounded-lg border border-dashed border-zinc-300 bg-white px-4 py-6 text-sm text-zinc-600">
        Las conversaciones aparecerán aquí cuando el agente de WhatsApp esté conectado.
      </p>
    );
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
      {state === "error" && (
        <p role="alert" className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-800">
          {message}
        </p>
      )}
      {items === null ? (
        state === "ready" && <p className="text-sm text-zinc-500">Cargando…</p>
      ) : items.length === 0 ? (
        <p className="text-sm text-zinc-500">
          {filter === "attention" ? "Nadie te está esperando. El agente tiene todo bajo control." : "Aún no hay conversaciones."}
        </p>
      ) : (
        <ul className="divide-y divide-zinc-200 rounded-lg border border-zinc-200 bg-white">
          {items.map((conversation) => (
            <ConversationRow key={conversation.id} conversation={conversation} />
          ))}
        </ul>
      )}
    </section>
  );
}

function ConversationRow({ conversation }: { conversation: Conversation }) {
  const [who, setWho] = useState<string | null>(null);

  useEffect(() => {
    void api
      .GET("/contacts/{contact_id}", { params: { path: { contact_id: conversation.contact_id } } })
      .then(({ data }) => {
        if (data) setWho(data.display_name ?? data.phone_masked ?? null);
      });
  }, [conversation.contact_id]);

  const human = conversation.mode === "human";
  return (
    <li className="space-y-2 px-4 py-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{who ?? "Cliente"}</span>
        <span className={`rounded px-2 py-0.5 text-xs ${human ? "bg-amber-100 text-amber-900" : "bg-zinc-100 text-zinc-700"}`}>
          {MODE[conversation.mode]}
        </span>
        {conversation.attributed_ad_code && (
          <span className="rounded bg-zinc-900 px-2 py-0.5 font-mono text-xs text-white">#{conversation.attributed_ad_code}</span>
        )}
        {conversation.attribution_status === "unknown_code" && (
          <span className="text-xs text-zinc-500">mencionó un código que no existe</span>
        )}
        {conversation.is_replay && <span className="rounded bg-sky-100 px-2 py-0.5 text-xs text-sky-900">Demo</span>}
        {conversation.last_message_at && (
          <span className="ml-auto text-xs text-zinc-500">
            {new Date(conversation.last_message_at).toLocaleString("es-CO")}
          </span>
        )}
      </div>
      {human && conversation.handoff?.reason && (
        <p className="text-amber-900">{HANDOFF_REASON[conversation.handoff.reason]}</p>
      )}
      {(conversation.handoff?.summary ?? conversation.summary) && (
        <p className="text-zinc-600">{conversation.handoff?.summary ?? conversation.summary}</p>
      )}
      {conversation.objection_tags && conversation.objection_tags.length > 0 && (
        <p className="flex flex-wrap gap-1">
          {conversation.objection_tags.map((tag) => (
            <span key={tag} className="rounded bg-zinc-100 px-2 py-0.5 text-xs text-zinc-700">
              Objeción: {OBJECTION[tag]}
            </span>
          ))}
        </p>
      )}
    </li>
  );
}
