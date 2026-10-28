"use client";

import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";
import { api, type components } from "@/lib/api/client";
import { formatCop, problemMessage } from "@/lib/format";
import { useLoad } from "@/lib/use-load";

type Product = components["schemas"]["Product"];

const STATUS = { active: "Activo", draft: "Borrador", archived: "Archivado" } as const;

/** Products with their photos, and the button that turns them into ads (US-1, US-2). */
export function Catalog() {
  const router = useRouter();
  const [products, setProducts] = useState<Product[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    const { data, error } = await api.GET("/products", { params: { query: { limit: 100 } } });
    if (error) setMessage(problemMessage(error, "No pudimos cargar tus productos."));
    else setProducts(data.items ?? []);
  }, []);

  useLoad(load);

  async function upload(product: Product, file: File) {
    setBusy(product.id);
    setMessage(null);
    const { error } = await api.POST("/assets", {
      body: { product_id: product.id, file: file as unknown as string },
      bodySerializer(body) {
        const form = new FormData();
        form.append("product_id", body.product_id);
        form.append("file", body.file as unknown as Blob);
        return form;
      },
    });
    setBusy(null);
    if (error) setMessage(problemMessage(error, "No pudimos subir la foto."));
    else await load();
  }

  async function createAds(product: Product) {
    setBusy(product.id);
    setMessage(null);
    // The contract's defaults, spelled out: two formats, three scenes each, ten copy variants.
    const { data, error } = await api.POST("/creative/jobs", {
      body: {
        product_id: product.id,
        formats: ["story_9x16", "catalog_1x1"],
        scenes_per_format: 3,
        copy_variants: 10,
        overlay_text: false,
      },
    });
    setBusy(null);
    if (error) setMessage(problemMessage(error, "No pudimos empezar los anuncios."));
    else router.push(`/creativos?trabajo=${data.id}`);
  }

  return (
    <section className="space-y-4">
      <div className="flex items-baseline justify-between">
        <h2 className="text-lg font-semibold">Productos</h2>
        {products && <span className="text-sm text-zinc-500">{products.length} en el catálogo</span>}
      </div>
      <NewProduct onCreated={load} />
      {message && (
        <p role="alert" className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-800">
          {message}
        </p>
      )}
      {products === null ? (
        <p className="text-sm text-zinc-500">Cargando productos…</p>
      ) : products.length === 0 ? (
        <p className="text-sm text-zinc-500">Aún no hay productos. Crea el primero arriba.</p>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2">
          {products.map((product) => {
            const photos = product.asset_ids?.length ?? 0;
            return (
              <li key={product.id} className="space-y-3 rounded-lg border border-zinc-200 bg-white p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="font-medium">{product.name}</p>
                    <p className="text-sm text-zinc-600">
                      {formatCop(product.price_cop)}
                      {product.stock != null && ` · ${product.stock} en inventario`}
                    </p>
                  </div>
                  <span className="rounded bg-zinc-100 px-2 py-0.5 text-xs text-zinc-700">
                    {STATUS[product.status]}
                  </span>
                </div>
                {product.description && <p className="text-sm text-zinc-600">{product.description}</p>}
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <label className="cursor-pointer rounded border border-zinc-300 px-3 py-1.5 hover:border-zinc-500">
                    Subir foto
                    <input
                      type="file"
                      accept="image/jpeg,image/png,image/webp,video/mp4"
                      className="sr-only"
                      disabled={busy === product.id}
                      onChange={(event) => {
                        const file = event.target.files?.[0];
                        event.target.value = "";
                        if (file) void upload(product, file);
                      }}
                    />
                  </label>
                  <button
                    type="button"
                    disabled={photos === 0 || busy === product.id}
                    onClick={() => void createAds(product)}
                    className="rounded bg-zinc-900 px-3 py-1.5 text-white disabled:cursor-not-allowed disabled:bg-zinc-300"
                  >
                    Crear anuncios
                  </button>
                  <span className="text-zinc-500">
                    {photos === 0 ? "Sube una foto para crear anuncios" : `${photos} ${photos === 1 ? "foto" : "fotos"}`}
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function NewProduct({ onCreated }: { onCreated: () => Promise<void> }) {
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function submit(form: HTMLFormElement) {
    const fields = new FormData(form);
    const stock = String(fields.get("stock") ?? "").trim();
    const description = String(fields.get("description") ?? "").trim();
    setSaving(true);
    setError(null);
    const { error: problem } = await api.POST("/products", {
      body: {
        name: String(fields.get("name") ?? "").trim(),
        price_cop: Number(fields.get("price_cop")),
        ...(stock ? { stock: Number(stock) } : {}),
        ...(description ? { description } : {}),
      },
    });
    setSaving(false);
    if (problem) {
      setError(problemMessage(problem, "No pudimos guardar el producto."));
      return;
    }
    form.reset();
    await onCreated();
  }

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void submit(event.currentTarget);
      }}
      className="grid gap-3 rounded-lg border border-dashed border-zinc-300 bg-white p-4 sm:grid-cols-[minmax(0,2fr)_minmax(9rem,1fr)_minmax(7rem,1fr)_auto]"
    >
      <input name="name" required maxLength={120} placeholder="Nombre del producto" className="min-w-0 rounded border border-zinc-300 px-3 py-2" />
      <input name="price_cop" required type="number" min={1} step={1} placeholder="Precio (COP)" className="min-w-0 rounded border border-zinc-300 px-3 py-2" />
      <input name="stock" type="number" min={0} step={1} placeholder="Inventario" className="min-w-0 rounded border border-zinc-300 px-3 py-2" />
      <button type="submit" disabled={saving} className="rounded bg-zinc-900 px-4 py-2 text-white disabled:bg-zinc-300">
        Agregar
      </button>
      <textarea name="description" maxLength={2000} placeholder="Descripción (materiales, tallas, colores)" className="min-w-0 rounded border border-zinc-300 px-3 py-2 sm:col-span-4" rows={2} />
      {error && (
        <p role="alert" className="text-sm text-red-700 sm:col-span-4">
          {error}
        </p>
      )}
    </form>
  );
}
