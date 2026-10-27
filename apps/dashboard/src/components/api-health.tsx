"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api/client";

type State = { kind: "checking" } | { kind: "up"; version: string } | { kind: "down" };

/** Calls GET /health (no auth, contract operation getHealth) through the generated client. */
export function ApiHealth() {
  const [state, setState] = useState<State>({ kind: "checking" });

  useEffect(() => {
    let alive = true;
    api
      .GET("/health")
      .then(({ data }) => {
        if (alive) setState(data ? { kind: "up", version: data.version } : { kind: "down" });
      })
      .catch(() => {
        if (alive) setState({ kind: "down" });
      });
    return () => {
      alive = false;
    };
  }, []);

  const styles = {
    checking: "bg-zinc-100 text-zinc-700",
    up: "bg-emerald-50 text-emerald-800",
    down: "bg-red-50 text-red-800",
  } as const;
  const text = {
    checking: "Comprobando la conexión con la API…",
    up: state.kind === "up" ? `API conectada · versión ${state.version}` : "",
    down: "API sin conexión. Arráncala con `make api` en la raíz del repositorio.",
  } as const;

  return (
    <p role="status" className={`rounded-md px-4 py-3 text-sm ${styles[state.kind]}`}>
      {text[state.kind]}
    </p>
  );
}
