"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type components } from "@/lib/api/client";
import { formatDateTime, problemMessage } from "@/lib/format";
import { HANDOFF_REASON, MODE, OBJECTION } from "@/lib/labels";
import { useLoad } from "@/lib/use-load";

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
  // Each filter keeps its own list, as the ads page does with its tabs.
  const [lists, setLists] = useState<Partial<Record<Filter, Conversation[]>>>({});
  const items = lists[filter] ?? null;
  const [state, setState] = useState<"ready" | "not_connected" | "error">("ready");
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    const query = filter === "attention" ? { needs_attention: true } : filter === "agent" ? { mode: "agent" as const } : {};
    const { data, error, response } = await api.GET("/conversations", { params: { query: { ...query, limit: 50 } } });
    if (data) {
      setLists((current) => ({ ...current, [filter]: data.items ?? [] }));
      setState("ready");
    } else if (response.status === 404) {
      setState("not_connected"); // the conversation engine has not published its routes yet
    } else {
      setState("error");
      setMessage(problemMessage(error, "No pudimos cargar las conversaciones."));
    }
  }, [filter]);

  useLoad(load);

  if (state === "not_connected") {
    return (
      <p className="empty">
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
            className="chip"
          >
            {label}
          </button>
        ))}
      </div>
      {state === "error" && (
        <p role="alert" className="notice notice--danger">
          {message}
        </p>
      )}
      {items === null ? (
        state === "ready" && <p className="text-sm text-fg-subtle">Cargando…</p>
      ) : items.length === 0 ? (
        <p className="text-sm text-fg-subtle">
          {filter === "attention" ? "Nadie te está esperando. El agente tiene todo bajo control." : "Aún no hay conversaciones."}
        </p>
      ) : (
        <ul className="card divide-y divide-line">
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
        <span className={`badge ${human ? "badge--warning" : ""}`}>
          {MODE[conversation.mode]}
        </span>
        {conversation.attributed_ad_code && (
          <span className="badge badge--code">#{conversation.attributed_ad_code}</span>
        )}
        {conversation.attribution_status === "unknown_code" && (
          <span className="text-xs text-fg-subtle">mencionó un código que no existe</span>
        )}
        {conversation.is_replay && <span className="badge badge--info">Demo</span>}
        {conversation.last_message_at && (
          <span className="ml-auto text-xs text-fg-subtle">
            {formatDateTime(conversation.last_message_at)}
          </span>
        )}
      </div>
      {human && conversation.handoff?.reason && (
        <p className="text-warning-fg">{HANDOFF_REASON[conversation.handoff.reason]}</p>
      )}
      {(conversation.handoff?.summary ?? conversation.summary) && (
        <p className="text-fg-muted">{conversation.handoff?.summary ?? conversation.summary}</p>
      )}
      {conversation.objection_tags && conversation.objection_tags.length > 0 && (
        <p className="flex flex-wrap gap-1">
          {conversation.objection_tags.map((tag) => (
            <span key={tag} className="badge">
              Objeción: {OBJECTION[tag]}
            </span>
          ))}
        </p>
      )}
    </li>
  );
}
