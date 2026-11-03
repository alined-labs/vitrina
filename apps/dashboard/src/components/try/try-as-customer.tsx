"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { oncePerAction } from "@/lib/api/action-key";
import { api, type components } from "@/lib/api/client";
import { formatCop, formatLatency, problemMessage } from "@/lib/format";
import { HANDOFF_REASON, OBJECTION, percent } from "@/lib/labels";
import { useLoad } from "@/lib/use-load";

type Turn = components["schemas"]["AgentTurnResult"];
type Fixture = { fixture_id: string; name: string; description?: string; message_count: number };

/** Questions a visitor can send with one click: each shows a different rule at work. */
const SUGGESTIONS: readonly { text: string; en: string }[] = [
  { text: "¿Cuánto vale la mochila azul?", en: "A price the catalog has: the reply cites it" },
  { text: "¿Me deja la mochila azul en 60 mil?", en: "A haggle: no fact gives that price, so the chat goes to the owner" },
  { text: "¿Cuánto cuesta el envío a Cali?", en: "Shipping outside Medellín: answered from the shop's facts" },
  { text: "¿Me ayudas con mi tarea de matemáticas?", en: "Off topic: the agent only talks about this shop" },
];

/** A visitor plays the customer: one message to the agent, with every check it passed or failed. */
export function TryAsCustomer() {
  const [names, setNames] = useState<Map<string, string>>(new Map());
  const loadNames = useCallback(async () => {
    const [facts, products] = await Promise.all([
      api.GET("/facts", { params: { query: { limit: 100 } } }),
      api.GET("/products", { params: { query: { limit: 100 } } }),
    ]);
    const found = new Map<string, string>();
    for (const fact of facts.data?.items ?? []) found.set(fact.id, fact.text);
    for (const product of products.data?.items ?? []) {
      found.set(product.id, `${product.name} · ${formatCop(product.price_cop)}`);
    }
    setNames(found);
  }, []);
  useLoad(loadNames);

  return (
    <div className="grid gap-8 lg:grid-cols-[1fr_20rem]">
      <AskTheAgent names={names} />
      <RecordedChats />
    </div>
  );
}

function AskTheAgent({ names }: { names: Map<string, string> }) {
  const [text, setText] = useState("");
  const [asked, setAsked] = useState<string | null>(null);
  const [turn, setTurn] = useState<Turn | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const send = async (question: string) => {
    const clean = question.trim();
    if (!clean || busy) return;
    setBusy(true);
    setMessage(null);
    setAsked(clean);
    setTurn(null);
    const { data, error } = await api.POST("/agent/turns", {
      body: { message: { kind: "text", text: clean }, dry_run: true, replay_model_outputs: false },
    });
    setBusy(false);
    if (data) setTurn(data);
    else setMessage(problemMessage(error, "El agente no pudo responder. Intenta de nuevo."));
  };

  return (
    <section className="space-y-4" aria-labelledby="ask-title">
      <div className="space-y-1">
        <h2 id="ask-title" className="text-lg font-semibold">
          Escríbele al agente como si fueras un cliente
        </h2>
        <p lang="en" className="text-sm text-fg-subtle">
          Write to the agent as a customer would. Nothing is sent on WhatsApp. Each question is answered on its own,
          with no memory of the previous one.
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        {SUGGESTIONS.map((item) => (
          <button
            key={item.text}
            type="button"
            className="chip"
            title={item.en}
            disabled={busy}
            onClick={() => {
              setText(item.text);
              void send(item.text);
            }}
          >
            {item.text}
          </button>
        ))}
      </div>

      <form
        className="space-y-2"
        onSubmit={(event) => {
          event.preventDefault();
          void send(text);
        }}
      >
        <label htmlFor="customer-message" className="block text-sm font-semibold">
          Mensaje del cliente <span lang="en" className="font-normal text-fg-subtle">· Customer message</span>
        </label>
        <textarea
          id="customer-message"
          className="input min-h-20 w-full"
          maxLength={1000}
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="Hola, ¿tienen mochilas rojas?"
        />
        <button type="submit" className="btn btn--primary" disabled={busy || !text.trim()}>
          {busy ? "El agente está respondiendo…" : "Enviar al agente"}
        </button>
      </form>

      {message && (
        <p role="alert" className="notice notice--danger">
          {message}
        </p>
      )}
      {asked && turn && <TurnReport asked={asked} turn={turn} names={names} />}
    </section>
  );
}

function Check({ passed, label, en, children }: { passed: boolean; label: string; en: string; children?: React.ReactNode }) {
  return (
    <li className="space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <span className={passed ? "badge badge--success" : "badge badge--danger"}>{passed ? "Pasó" : "Falló"}</span>
        <span className="font-semibold">{label}</span>
        <span lang="en" className="text-sm text-fg-subtle">
          {en} · {passed ? "passed" : "failed"}
        </span>
      </div>
      {children && <div className="pl-1 text-sm text-fg-muted">{children}</div>}
    </li>
  );
}

function amounts(values: number[] | undefined): string {
  return values && values.length ? values.map(formatCop).join(", ") : "ninguno";
}

function TurnReport({ asked, turn, names }: { asked: string; turn: Turn; names: Map<string, string> }) {
  const output = turn.output;
  const price = turn.validation.price_validator;
  const grounded = turn.validation.groundedness;
  const scope = turn.validation.scope;
  const draftDiffers = output && output.reply.trim() !== turn.final_reply.trim();
  const cited = output?.cited_fact_ids ?? [];
  return (
    <div className="card space-y-5 p-5">
      <div className="space-y-3">
        <p className="text-sm text-fg-subtle">Cliente · Customer</p>
        <p className="rounded-md bg-sunken p-3">{asked}</p>
        <p className="text-sm text-fg-subtle">
          Lo que recibiría el cliente <span lang="en">· What the customer would receive</span>
        </p>
        <p className="rounded-md bg-primary-soft p-3 text-primary-fg">{turn.final_reply}</p>
        {draftDiffers && (
          <details className="text-sm">
            <summary className="cursor-pointer text-fg-muted">
              El borrador del modelo no pasó las reglas y se reemplazó{" "}
              <span lang="en" className="text-fg-subtle">· The model&apos;s draft failed a rule and was replaced</span>
            </summary>
            <p className="mt-2 rounded-md bg-sunken p-3">{output?.reply}</p>
          </details>
        )}
      </div>

      {output?.handoff_reason && (
        <p className="notice notice--info">
          Pasa a quien atiende el negocio: {HANDOFF_REASON[output.handoff_reason]}.{" "}
          <span lang="en">Handed to the owner ({output.handoff_reason.replaceAll("_", " ")}).</span>
        </p>
      )}

      <div className="space-y-2">
        <h3 className="font-semibold">
          Reglas fuera del modelo <span lang="en" className="font-normal text-fg-subtle">· Rules checked outside the model</span>
        </h3>
        <ul className="space-y-3">
          <Check passed={price.passed} label="Precios" en="Price check">
            Montos en la respuesta: {amounts(price.amounts_in_reply)} · en los datos citados: {amounts(price.amounts_in_citations)}
            {price.violations && price.violations.length > 0 && <> · sin respaldo: {amounts(price.violations)}</>}
            {price.retried && <> · se reintentó una vez</>}
          </Check>
          <Check passed={grounded.passed} label="Respaldo en el catálogo" en="Groundedness">
            {grounded.uncited_claims && grounded.uncited_claims.length > 0
              ? `Sin cita: ${grounded.uncited_claims.join(" · ")}`
              : "Cada dato de la respuesta viene de un dato citado."}
          </Check>
          <Check passed={scope.passed} label="Tema del negocio" en="Scope">
            {scope.reason}
          </Check>
        </ul>
      </div>

      <div className="space-y-2">
        <h3 className="font-semibold">
          Datos citados <span lang="en" className="font-normal text-fg-subtle">· Facts the reply cites</span>
        </h3>
        {cited.length ? (
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {cited.map((id) => (
              <li key={id}>{names.get(id) ?? id}</li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-fg-muted">Ninguno · none</p>
        )}
      </div>

      <dl className="grid gap-x-6 gap-y-1 text-sm text-fg-muted sm:grid-cols-2">
        {output && (
          <>
            <dt className="text-fg-subtle">Confianza · Confidence</dt>
            <dd>{percent(output.confidence)}</dd>
          </>
        )}
        {output?.objection_tag && (
          <>
            <dt className="text-fg-subtle">Objeción · Objection</dt>
            <dd>{OBJECTION[output.objection_tag]}</dd>
          </>
        )}
        {turn.model_calls.map((call, index) => (
          <div key={index} className="contents">
            <dt className="text-fg-subtle">Modelo · Model</dt>
            <dd>
              {call.model}
              {call.latency_ms !== undefined && <> · {formatLatency(call.latency_ms)}</>}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function RecordedChats() {
  const [fixtures, setFixtures] = useState<Fixture[] | null>(null);
  const [started, setStarted] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    const { data, error } = await api.GET("/replay/fixtures");
    if (data) setFixtures(data);
    else setMessage(problemMessage(error, "No pudimos cargar las conversaciones grabadas."));
  }, []);
  useLoad(load);

  const play = async (fixture: Fixture) => {
    setBusy(fixture.fixture_id);
    setMessage(null);
    const { data, error } = await oncePerAction(`replay:${fixture.fixture_id}`, (headers) =>
      api.POST("/replay/sessions", {
        params: { header: headers },
        body: { fixture_id: fixture.fixture_id, use_recorded_model_outputs: false },
      }),
    );
    setBusy(null);
    if (data) setStarted((current) => ({ ...current, [fixture.fixture_id]: data.conversation_id }));
    else setMessage(problemMessage(error, "No pudimos iniciar la conversación."));
  };

  return (
    <aside className="space-y-4" aria-labelledby="recorded-title">
      <div className="space-y-1">
        <h2 id="recorded-title" className="text-lg font-semibold">
          Conversaciones de prueba
        </h2>
        <p lang="en" className="text-sm text-fg-subtle">
          Play a whole customer chat through the same pipeline WhatsApp uses. The customer&apos;s messages are recorded;
          the agent answers live. Nothing is sent. The chat appears in Conversaciones with a «Demo» badge.
        </p>
      </div>
      {message && (
        <p role="alert" className="notice notice--danger">
          {message}
        </p>
      )}
      {fixtures === null && !message && <p className="text-sm text-fg-subtle">Cargando…</p>}
      <ul className="space-y-3">
        {fixtures?.map((fixture) => {
          const conversation = started[fixture.fixture_id];
          return (
            <li key={fixture.fixture_id} className="card space-y-2 p-4">
              <p className="font-semibold">{fixture.name}</p>
              {fixture.description && <p className="text-sm text-fg-muted">{fixture.description}</p>}
              <p className="text-xs text-fg-subtle">{fixture.message_count} mensajes del cliente · customer messages</p>
              {conversation ? (
                <Link className="btn btn--secondary btn--sm" href={`/conversaciones/${conversation}`}>
                  Ver la conversación · Open the chat
                </Link>
              ) : (
                <button
                  type="button"
                  className="btn btn--secondary btn--sm"
                  disabled={busy !== null}
                  onClick={() => void play(fixture)}
                >
                  {busy === fixture.fixture_id ? "Iniciando…" : "Reproducir · Play"}
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </aside>
  );
}
