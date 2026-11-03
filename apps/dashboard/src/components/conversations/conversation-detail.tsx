"use client";

import Link from "next/link";
import { useId, useRef, useState } from "react";
import { Loader } from "@/components/brand/logo";
import { api } from "@/lib/api/client";
import { formatDateTime, problemMessage } from "@/lib/format";
import { ConversationBadges, ConversationNotes, useContactName } from "./conversations";
import { Transcript } from "./transcript";
import { FAILURES_BEFORE_NOTICE, useConversation, type Conversation } from "./use-conversation";
import { lastCustomerMessageAt, outsideWindow, WINDOW_CLOSED_WARNING } from "./whatsapp-window";

type Chat = ReturnType<typeof useConversation>;

const MAX_TEXT = 4096; // HumanMessageCreate.text

function newKey(): string {
  // randomUUID exists only on https and localhost; a phone on the local network has neither.
  return crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** The transcript, the reply box and takeover and release, refreshed while the page is open (US-3). */
export function ConversationDetail({ id }: { id: string }) {
  const chat = useConversation(id);
  const { conversation } = chat;

  if (chat.gone) {
    return (
      <section className="space-y-4">
        <BackLink />
        <p className="empty">No encontramos esta conversación; puede que ya no exista.</p>
      </section>
    );
  }

  return (
    <section className="space-y-4">
      <BackLink />
      {chat.failures >= FAILURES_BEFORE_NOTICE && <ConnectionNotice chat={chat} />}
      {conversation ? (
        <Loaded conversation={conversation} chat={chat} />
      ) : (
        <Loader size={32} label="Cargando la conversación" />
      )}
    </section>
  );
}

function BackLink() {
  return (
    <Link href="/conversaciones" className="inline-block text-sm text-primary underline">
      ← Conversaciones
    </Link>
  );
}

function ConnectionNotice({ chat }: { chat: Chat }) {
  const code = (chat.problem as { code?: string } | null)?.code;
  // A session or a permission will not come back by waiting; say that instead.
  const text =
    code === "UNAUTHORIZED" || code === "FORBIDDEN"
      ? problemMessage(chat.problem)
      : "No hemos podido actualizar esta conversación. Seguimos intentando.";
  return (
    <div role="alert" className="notice notice--warning flex flex-wrap items-center gap-x-3 gap-y-2">
      <p className="min-w-0 flex-1 basis-56">
        {text}
        {chat.syncedAt && ` Última actualización: ${formatDateTime(new Date(chat.syncedAt).toISOString())}`}
      </p>
      <button type="button" className="btn btn--secondary btn--sm" onClick={() => void chat.refresh()}>
        Reintentar ahora
      </button>
    </div>
  );
}

function Loaded({ conversation, chat }: { conversation: Conversation; chat: Chat }) {
  const who = useContactName(conversation.contact_id) ?? "Cliente";
  const closed = outsideWindow(lastCustomerMessageAt(chat.messages), chat.now);

  return (
    <>
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight wrap-anywhere">{who}</h1>
        <div className="flex flex-wrap items-center gap-2">
          <ConversationBadges conversation={conversation} />
        </div>
        <div className="space-y-2 text-sm">
          <ConversationNotes conversation={conversation} />
        </div>
      </header>
      <Transcript messages={chat.messages} customer={who} revision={chat.syncedAt} />
      {chat.syncedAt && chat.failures < FAILURES_BEFORE_NOTICE && (
        <p className="text-xs text-fg-subtle">
          Se actualiza sola · última vez: {formatDateTime(new Date(chat.syncedAt).toISOString())}
        </p>
      )}
      <ReplyPanel conversation={conversation} chat={chat} windowClosed={closed} />
    </>
  );
}

/**
 * What the seller can do. The draft lives here, outside what polling replaces, and survives a
 * refresh, a takeover, a release and a failed send; only a send that went out clears it.
 */
function ReplyPanel({
  conversation,
  chat,
  windowClosed,
}: {
  conversation: Conversation;
  chat: Chat;
  windowClosed: boolean;
}) {
  const fieldId = useId();
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState<"send" | "mode" | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  // Kept only while a send's outcome is unknown, so that trying again cannot send it twice.
  const pendingKey = useRef<{ text: string; key: string } | null>(null);
  const id = conversation.id;
  const human = conversation.mode === "human";

  if (conversation.mode === "closed") {
    return <p className="notice">Esta conversación está cerrada. Si el cliente vuelve a escribir, se abrirá una nueva.</p>;
  }

  async function changeMode(path: "takeover" | "release") {
    setBusy("mode");
    setMessage(null);
    chat.beginWrite();
    const params = { params: { path: { conversation_id: id } } };
    const failure =
      path === "takeover" ? "No pudimos tomar la conversación. Intenta de nuevo." : "No pudimos devolver la conversación. Intenta de nuevo.";
    try {
      const { data, error } =
        path === "takeover"
          ? await api.POST("/conversations/{conversation_id}/takeover", params)
          : await api.POST("/conversations/{conversation_id}/release", params);
      if (data) chat.showConversation(data);
      else setMessage(problemMessage(error, failure));
    } catch {
      setMessage(failure);
    }
    setBusy(null);
    await chat.refresh();
  }

  async function send() {
    const text = draft.trim();
    if (!text) return;
    setBusy("send");
    setMessage(null);
    chat.beginWrite();
    if (pendingKey.current?.text !== text) pendingKey.current = { text, key: newKey() };
    try {
      const { data, error } = await api.POST("/conversations/{conversation_id}/messages", {
        params: { path: { conversation_id: id }, header: { "Idempotency-Key": pendingKey.current.key } },
        body: { text },
      });
      // The API keeps its answer under the key, refusals too: a new attempt needs a new key.
      pendingKey.current = null;
      if (data) chat.showMessage(data);
      if (data?.delivery_status === "failed") {
        setMessage("Este mensaje no se entregó. Tu texto sigue en el cuadro por si quieres intentarlo de nuevo.");
      } else if (data) {
        setDraft((current) => (current.trim() === text ? "" : current)); // anything typed since stays
      } else if ((error as { code?: string } | undefined)?.code === "CONFLICT") {
        setMessage("No se pudo enviar: el número de este cliente ya no está guardado, o un envío anterior sigue en curso. Revisa los mensajes antes de intentar de nuevo.");
      } else {
        setMessage(problemMessage(error, "No pudimos enviar el mensaje. Intenta de nuevo."));
      }
    } catch {
      setMessage("No pudimos confirmar el envío por un problema de conexión. Intenta de nuevo: no se enviará dos veces.");
    }
    setBusy(null);
    await chat.refresh();
  }

  return (
    <form
      className="card space-y-3 p-4"
      onSubmit={(event) => {
        event.preventDefault();
        void send();
      }}
    >
      {!human && (
        <div className="notice notice--info flex flex-wrap items-center gap-x-3 gap-y-2">
          <p className="min-w-0 flex-1 basis-56">
            El agente está atendiendo este chat. Tómalo para escribirle tú; el agente dejará de responder hasta que lo devuelvas.
          </p>
          <button type="button" className="btn btn--primary btn--sm" disabled={busy !== null} onClick={() => void changeMode("takeover")}>
            {busy === "mode" ? "Tomando…" : "Tomar conversación"}
          </button>
        </div>
      )}
      {windowClosed && <p className="notice notice--warning">{WINDOW_CLOSED_WARNING}</p>}
      {message && (
        <p role="alert" className="notice notice--danger">
          {message}
        </p>
      )}
      <div className="space-y-1">
        <label htmlFor={fieldId} className="text-sm font-semibold">
          Tu respuesta
        </label>
        <textarea
          id={fieldId}
          className="input block w-full"
          rows={3}
          maxLength={MAX_TEXT}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
      </div>
      <div className="flex flex-wrap items-center justify-end gap-2">
        {human && (
          <button type="button" className="btn btn--secondary" disabled={busy !== null} onClick={() => void changeMode("release")}>
            {busy === "mode" ? "Devolviendo…" : "Devolver al agente"}
          </button>
        )}
        <button
          type="submit"
          className={`btn ${human ? "btn--primary" : "btn--secondary"}`}
          disabled={!human || busy !== null || draft.trim() === ""}
        >
          {busy === "send" ? "Enviando…" : windowClosed ? "Enviar de todas formas" : "Enviar"}
        </button>
      </div>
    </form>
  );
}
