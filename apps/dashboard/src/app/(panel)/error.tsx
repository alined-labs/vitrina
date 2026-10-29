"use client";

import { useEffect } from "react";

// A panel section that fails while rendering shows this inside the panel, in Spanish, instead of
// Next's default error page in English.
export default function PanelError({ error, retry }: { error: unknown; retry: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="mx-auto max-w-4xl space-y-3">
      <h1 className="text-2xl font-semibold tracking-tight">Algo salió mal</h1>
      <p className="text-zinc-600">No pudimos mostrar esta sección. Intenta de nuevo; si sigue fallando, recarga la página.</p>
      <button type="button" onClick={() => retry()} className="rounded-md bg-zinc-900 px-4 py-2 text-sm text-white">
        Intentar de nuevo
      </button>
    </div>
  );
}
