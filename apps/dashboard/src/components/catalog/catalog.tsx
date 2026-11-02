"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useRef, useState } from "react";
import { api } from "@/lib/api/client";
import {
  allProducts,
  catalogProblem,
  checkProduct,
  NO_PRODUCT,
  OFFLINE,
  type Product,
  type ProductErrors,
  type ProductStatus,
} from "@/lib/catalog";
import { problemMessage } from "@/lib/format";
import { useLoad } from "@/lib/use-load";
import { ProductCard } from "./product-card";
import { focusFirstError, ProductFields } from "./product-form";

type Filter = ProductStatus | "all";

const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "Todos" },
  { value: "draft", label: "Borradores" },
  { value: "active", label: "Activos" },
  { value: "archived", label: "Archivados" },
];
// What the seller still has to decide comes first, what left the catalog last.
const ORDER: Record<ProductStatus, number> = { draft: 0, active: 1, archived: 2 };

/** Products with their photos, inline editing, and the button that turns them into ads (US-1, US-2). */
export function Catalog() {
  const router = useRouter();
  const [products, setProducts] = useState<Product[] | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [message, setMessage] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const latest = useRef(0);

  const load = useCallback(async () => {
    const request = ++latest.current;
    const { items, error } = await allProducts();
    if (request !== latest.current) return; // a newer load is on its way
    if (items) setProducts(items);
    else setMessage(problemMessage(error, "No pudimos cargar tus productos."));
  }, []);

  useLoad(load);

  function saved(product: Product, text?: string) {
    latest.current++; // a load already in flight may not know about this change
    setProducts((current) => current?.map((item) => (item.id === product.id ? product : item)) ?? null);
    setMessage(null);
    setNote(text ?? null);
  }

  async function archived(product: Product) {
    setMessage(null);
    setNote(`«${product.name}» salió del catálogo: el agente ya no lo cita. Lo encuentras en Archivados.`);
    await load();
  }

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

  const count = (status: ProductStatus) => products?.filter((product) => product.status === status).length ?? 0;
  const drafts = count("draft");
  const shown = (products ?? [])
    .filter((product) => filter === "all" || product.status === filter)
    .toSorted((a, b) => ORDER[a.status] - ORDER[b.status]);

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-baseline gap-3">
          <h2 className="text-lg font-semibold">Productos</h2>
          {products && <span className="text-sm text-fg-subtle">{products.length} en total</span>}
        </div>
        <Link href="/catalogo/importar" className="btn btn--secondary btn--sm">
          Importar desde una foto
        </Link>
      </div>
      <NewProduct onCreated={load} />
      {drafts > 0 && filter !== "draft" && (
        <div className="notice notice--warning flex flex-wrap items-center justify-between gap-2">
          <p>
            {drafts === 1
              ? "Tienes 1 producto en borrador. El agente no lo cita hasta que lo actives."
              : `Tienes ${drafts} productos en borrador. El agente no los cita hasta que los actives.`}
          </p>
          <button type="button" className="btn btn--secondary btn--sm" onClick={() => setFilter("draft")}>
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
      <div role="group" aria-label="Filtrar productos por estado" className="flex flex-wrap gap-2">
        {FILTERS.map(({ value, label }) => (
          <button key={value} type="button" aria-pressed={filter === value} onClick={() => setFilter(value)} className="chip">
            {label}
            {value !== "all" && products ? ` · ${count(value)}` : ""}
          </button>
        ))}
      </div>
      {products === null ? (
        !message && <p className="text-sm text-fg-subtle">Cargando productos…</p>
      ) : products.length === 0 ? (
        <p className="text-sm text-fg-subtle">Aún no hay productos. Crea el primero arriba.</p>
      ) : shown.length === 0 ? (
        <p className="empty">No hay productos en esta vista.</p>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2">
          {shown.map((product) => (
            <ProductCard key={product.id} product={product} onSaved={saved} onArchived={archived}>
              {product.status !== "archived" && (
                <AdActions
                  product={product}
                  busy={busy === product.id}
                  onUpload={(file) => void upload(product, file)}
                  onCreateAds={() => void createAds(product)}
                />
              )}
            </ProductCard>
          ))}
        </ul>
      )}
    </section>
  );
}

function AdActions({
  product,
  busy,
  onUpload,
  onCreateAds,
}: {
  product: Product;
  busy: boolean;
  onUpload: (file: File) => void;
  onCreateAds: () => void;
}) {
  const photos = product.asset_ids?.length ?? 0;
  const active = product.status === "active";
  return (
    <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3 text-sm">
      <label className="btn btn--secondary btn--sm">
        Subir foto
        <input
          type="file"
          accept="image/jpeg,image/png,image/webp,video/mp4"
          className="sr-only"
          disabled={busy}
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (file) onUpload(file);
          }}
        />
      </label>
      {active && (
        <button type="button" disabled={photos === 0 || busy} onClick={onCreateAds} className="btn btn--primary btn--sm">
          Crear anuncios
        </button>
      )}
      <span className="text-fg-subtle">
        {!active
          ? "Activa el producto para crear anuncios"
          : photos === 0
            ? "Sube una foto para crear anuncios"
            : `${photos} ${photos === 1 ? "foto" : "fotos"}`}
      </span>
    </div>
  );
}

function NewProduct({ onCreated }: { onCreated: () => Promise<void> }) {
  const [values, setValues] = useState(NO_PRODUCT);
  const [errors, setErrors] = useState<ProductErrors>({});
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function submit(form: HTMLFormElement) {
    const { errors: found, data } = checkProduct(values);
    setErrors(found);
    setMessage(null);
    if (!data) {
      focusFirstError(form, found);
      return;
    }
    setSaving(true);
    try {
      const { error } = await api.POST("/products", {
        body: {
          name: data.name,
          price_cop: data.price_cop,
          ...(data.stock !== null ? { stock: data.stock } : {}),
          ...(data.description ? { description: data.description } : {}),
        },
      });
      if (error) setMessage(catalogProblem(error, "No pudimos guardar el producto."));
      else {
        setValues(NO_PRODUCT);
        await onCreated();
      }
    } catch {
      setMessage(OFFLINE);
    }
    setSaving(false);
  }

  return (
    <form
      noValidate
      aria-label="Agregar un producto"
      onSubmit={(event) => {
        event.preventDefault();
        void submit(event.currentTarget);
      }}
      className="space-y-3 card border-dashed border-control p-4"
    >
      <ProductFields
        values={values}
        errors={errors}
        onChange={(field, value) => {
          setValues((current) => ({ ...current, [field]: value }));
          setErrors((current) => ({ ...current, [field]: undefined }));
        }}
      />
      {message && (
        <p role="alert" className="notice notice--danger">
          {message}
        </p>
      )}
      <button type="submit" disabled={saving} className="btn btn--primary">
        Agregar
      </button>
    </form>
  );
}
