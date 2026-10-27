"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api/client";
import { problemMessage } from "@/lib/format";

/** The public upload form of Foto → Vitrina (FR-072): no account, five tries an hour. */
export function ShowcaseUpload() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  async function submit(form: HTMLFormElement) {
    const fields = new FormData(form);
    const image = fields.get("image");
    if (!(image instanceof File) || image.size === 0) {
      setError("Elige una foto de tu producto.");
      return;
    }
    const price = String(fields.get("price_cop") ?? "").trim();
    const city = String(fields.get("city") ?? "").trim();
    setSending(true);
    setError(null);
    const { data, error: problem } = await api.POST("/public/showcases", {
      body: {
        image: image as unknown as string,
        product_name: String(fields.get("product_name") ?? "").trim(),
        ...(price ? { price_cop: Number(price) } : {}),
        ...(city ? { city } : {}),
      },
      bodySerializer(body) {
        const payload = new FormData();
        for (const [name, value] of Object.entries(body)) {
          const item: unknown = value; // the photo is a File although the generated type says string
          if (item !== undefined) payload.append(name, item instanceof Blob ? item : String(item));
        }
        return payload;
      },
    });
    setSending(false);
    if (data) router.push(`/v/${data.slug}`);
    else setError(problemMessage(problem, "No pudimos recibir tu foto. Intenta de nuevo."));
  }

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void submit(event.currentTarget);
      }}
      className="space-y-4 rounded-lg border border-zinc-200 bg-white p-6"
    >
      <label className="block space-y-1 text-sm">
        <span className="font-medium">Foto del producto</span>
        <input name="image" type="file" required accept="image/jpeg,image/png,image/webp" className="block w-full text-sm" />
      </label>
      <label className="block space-y-1 text-sm">
        <span className="font-medium">¿Qué vendes?</span>
        <input name="product_name" required maxLength={80} placeholder="Bolso tejido a mano" className="w-full rounded border border-zinc-300 px-3 py-2" />
      </label>
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block space-y-1 text-sm">
          <span className="font-medium">Precio en pesos (opcional)</span>
          <input name="price_cop" type="number" min={0} step={1} placeholder="85000" className="w-full min-w-0 rounded border border-zinc-300 px-3 py-2" />
        </label>
        <label className="block space-y-1 text-sm">
          <span className="font-medium">Ciudad (opcional)</span>
          <input name="city" maxLength={60} placeholder="Medellín" className="w-full min-w-0 rounded border border-zinc-300 px-3 py-2" />
        </label>
      </div>
      {error && (
        <p role="alert" className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-800">
          {error}
        </p>
      )}
      <button type="submit" disabled={sending} className="w-full rounded bg-zinc-900 px-4 py-2.5 text-white disabled:bg-zinc-300">
        {sending ? "Enviando…" : "Crear mi vitrina"}
      </button>
    </form>
  );
}
