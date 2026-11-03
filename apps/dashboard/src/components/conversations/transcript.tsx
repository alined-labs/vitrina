"use client";

import { useEffect, useRef, useState } from "react";
import { api, type components } from "@/lib/api/client";
import { formatCop, formatDateTime } from "@/lib/format";
import { DELIVERY, FACT_KIND, MESSAGE_TYPE, SENDER } from "@/lib/labels";
import type { Message } from "./use-conversation";
import { failureReason, outsideWindow } from "./whatsapp-window";

type Delivery = NonNullable<Message["delivery_status"]>;
type Fact = components["schemas"]["Fact"];

const DELIVERY_BADGE: Record<Delivery, string> = {
  queued: "",
  sent: "",
  delivered: "badge--success",
  read: "badge--success",
  failed: "badge--danger",
  mocked: "badge--info",
};

/** What the agent's reply rests on, once the customer really received that reply. */
function citedIds(message: Message): string[] {
  const output = message.agent_output;
  return output && answersWith(message) ? output.cited_fact_ids : [];
}

// The validators can replace the model's reply; its citations then support words nobody read.
// The sent text may also be the reply with a consent question taken out, so it may be shorter.
function answersWith(message: Message): boolean {
  const reply = message.agent_output?.reply;
  return Boolean(reply && message.text && reply.includes(message.text.trim()));
}

async function readFacts(): Promise<Fact[] | null> {
  const facts: Fact[] = [];
  let cursor: string | undefined;
  do {
    const { data } = await api.GET("/facts", { params: { query: cursor ? { limit: 100, cursor } : { limit: 100 } } });
    if (!data) return null;
    facts.push(...(data.items ?? []));
    cursor = data.next_cursor ?? undefined;
  } while (cursor);
  return facts;
}

/** Each id as a sentence, or null when it is known to be gone. A failed lookup stays unknown. */
async function readCitations(ids: string[]): Promise<Record<string, string | null>> {
  const facts = await readFacts();
  if (!facts) return {};
  const found: Record<string, string | null> = {};
  await Promise.all(
    ids.map(async (id) => {
      const fact = facts.find((item) => item.id === id);
      if (fact) {
        found[id] = `${FACT_KIND[fact.kind]}: ${fact.text}`;
        return;
      }
      const { data, response } = await api.GET("/products/{product_id}", {
        params: { path: { product_id: id } },
      });
      if (data) found[id] = `${data.name} · ${formatCop(data.price_cop)}`;
      else if (response.status === 404) found[id] = null;
    }),
  );
  return found;
}

/** Names for the cited ids. What could not be read is tried again when `revision` changes. */
function useCitations(ids: string[], revision: number | null) {
  const [found, setFound] = useState<Record<string, string | null>>({});
  const running = useRef(false);
  const wanted = ids.join(",");

  useEffect(() => {
    const missing = wanted ? wanted.split(",").filter((id) => !(id in found)) : [];
    if (missing.length === 0 || running.current) return;
    running.current = true;
    void readCitations(missing)
      .then((more) => setFound((current) => ({ ...current, ...more })))
      .catch(() => undefined)
      .finally(() => {
        running.current = false;
      });
  }, [wanted, found, revision]);

  return found;
}

function Citations({ ids, found }: { ids: string[]; found: Record<string, string | null> }) {
  return (
    <div className="mt-2 border-t border-line pt-2 text-xs text-fg-muted">
      <p className="font-semibold">Datos en los que se basó</p>
      <ul className="mt-1 space-y-1">
        {ids.map((id) => (
          <li key={id}>
            {id in found ? (found[id] ?? "Un dato que ya no está en tu catálogo") : "Buscando el dato…"}
          </li>
        ))}
      </ul>
    </div>
  );
}

function body(message: Message): string | null {
  return message.text ?? message.transcript ?? message.normalized?.text ?? null;
}

function MessageItem({
  message,
  customer,
  found,
  afterWindow,
}: {
  message: Message;
  customer: string;
  found: Record<string, string | null>;
  afterWindow: boolean;
}) {
  const sender = message.sender ?? (message.direction === "inbound" ? "customer" : "system");
  const text = body(message);
  const kind = MESSAGE_TYPE[message.type];
  const cited = citedIds(message);
  const replaced = sender === "agent" && Boolean(message.agent_output) && !answersWith(message);
  const delivery = message.delivery_status;
  const fromCustomer = message.direction === "inbound";

  return (
    <li className={`flex flex-col gap-1 ${fromCustomer ? "items-start" : "items-end"}`}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-fg-subtle">
        <span className="font-semibold text-fg-muted">{sender === "customer" ? customer : SENDER[sender]}</span>
        <span>{formatDateTime(message.created_at)}</span>
        {delivery && !fromCustomer && <span className={`badge ${DELIVERY_BADGE[delivery]}`}>{DELIVERY[delivery]}</span>}
      </div>
      <div
        className={`max-w-[90%] rounded-lg px-3 py-2 text-sm wrap-anywhere ${
          fromCustomer ? "bg-sunken" : sender === "seller" ? "border border-control bg-surface" : "bg-primary-soft"
        }`}
      >
        {kind && <p className="text-xs font-semibold text-fg-muted">{kind}</p>}
        {text ? (
          <p className="whitespace-pre-wrap">{text}</p>
        ) : (
          !kind && <p className="text-fg-subtle">Sin contenido: puede que se haya borrado.</p>
        )}
        {cited.length > 0 && <Citations ids={cited} found={found} />}
        {replaced && (
          <p className="mt-2 border-t border-line pt-2 text-xs text-fg-muted">
            Este mensaje reemplazó la respuesta del agente, que no pasó las comprobaciones.
          </p>
        )}
      </div>
      {delivery === "failed" && (
        <p className="notice notice--danger max-w-[90%]">{failureReason(afterWindow)}</p>
      )}
    </li>
  );
}

/** The chat, oldest first, kept at its end unless the seller scrolled up to read. */
export function Transcript({
  messages,
  customer,
  revision,
}: {
  messages: Message[];
  customer: string;
  revision: number | null;
}) {
  const box = useRef<HTMLDivElement>(null);
  const atEnd = useRef(true);
  const found = useCitations([...new Set(messages.flatMap(citedIds))], revision);

  useEffect(() => {
    if (atEnd.current && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [messages.length]);

  if (messages.length === 0) return <p className="empty">Aún no hay mensajes en esta conversación.</p>;

  const items = [];
  let lastCustomerAt: string | null = null;
  for (const message of messages) {
    const afterWindow = outsideWindow(lastCustomerAt, new Date(message.created_at).getTime());
    if (message.direction === "inbound") lastCustomerAt = message.created_at;
    items.push(
      <MessageItem key={message.id} message={message} customer={customer} found={found} afterWindow={afterWindow} />,
    );
  }

  return (
    <div
      ref={box}
      role="log"
      aria-label="Mensajes de la conversación"
      tabIndex={0}
      onScroll={({ currentTarget: el }) => {
        atEnd.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
      }}
      className="card max-h-[60vh] overflow-y-auto p-4"
    >
      <ol className="space-y-4">{items}</ol>
    </div>
  );
}
