"use client";

import { useState } from "react";
import { api } from "@/lib/api/client";
import {
  catalogProblem,
  checkProduct,
  OFFLINE,
  productValues,
  type Product,
  type ProductErrors,
  type ProductField,
  type ProductStatus,
} from "@/lib/catalog";
import { formatCop } from "@/lib/format";
import { focusFirstError, ProductFields } from "./product-form";

const STATUS: Record<ProductStatus, { label: string; badge: string }> = {
  active: { label: "Activo", badge: "badge badge--success" },
  draft: { label: "Borrador", badge: "badge badge--warning" },
  archived: { label: "Archivado", badge: "badge" },
};

/**
 * One product: what it says, with its price editable in place, and the actions its status allows.
 * The API archives a product and never deletes it: a past chat or ad may still point to it.
 */
export function ProductCard({
  product,
  onSaved,
  onArchived,
  children,
}: {
  product: Product;
  onSaved: (product: Product, note?: string) => void;
  onArchived: (product: Product) => Promise<void>;
  children?: React.ReactNode;
}) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function act(run: () => Promise<{ error?: unknown }>, failure: string, then: () => void | Promise<void>) {
    setBusy(true);
    setMessage(null);
    try {
      const { error } = await run();
      if (error) setMessage(catalogProblem(error, failure));
      else await then();
    } catch {
      setMessage(OFFLINE);
    }
    setBusy(false);
  }

  function setStatus(status: "active", note: string, failure: string) {
    let updated: Product | undefined;
    return act(
      async () => {
        const { data, error } = await api.PATCH("/products/{product_id}", {
          params: { path: { product_id: product.id } },
          body: { status },
        });
        updated = data;
        return { error };
      },
      failure,
      () => {
        if (updated) onSaved(updated, note);
      },
    );
  }

  if (editing) {
    return (
      <li className="card p-4 sm:col-span-2">
        <ProductEditor
          product={product}
          onCancel={() => setEditing(false)}
          onSaved={(updated) => {
            setEditing(false);
            onSaved(updated, `Guardamos los cambios de «${updated.name}».`);
          }}
        />
      </li>
    );
  }

  const status = STATUS[product.status];
  return (
    <li className="min-w-0 space-y-3 card p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-medium [overflow-wrap:anywhere]">{product.name}</p>
          <p className="text-sm text-fg-muted tabular">
            {formatCop(product.price_cop)}
            {product.stock != null && ` · ${product.stock} en inventario`}
            {(product.reserved ?? 0) > 0 && ` · ${product.reserved} apartados`}
          </p>
        </div>
        <div className="flex flex-wrap gap-1">
          {product.source === "image_import" && <span className="badge badge--info">Leído de una foto</span>}
          <span className={status.badge}>{status.label}</span>
        </div>
      </div>
      {product.description && <p className="text-sm text-fg-muted [overflow-wrap:anywhere]">{product.description}</p>}
      {product.status === "draft" && (
        <p className="text-sm text-fg-subtle">El agente no lo cita hasta que lo actives. Revisa el nombre y el precio.</p>
      )}
      {message && (
        <p role="alert" className="notice notice--danger">
          {message}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {product.status === "draft" && (
          <button
            type="button"
            disabled={busy}
            className="btn btn--primary btn--sm"
            onClick={() =>
              void setStatus("active", `«${product.name}» ya está activo: el agente puede citarlo.`, "No pudimos activar el producto.")
            }
          >
            Activar
          </button>
        )}
        {product.status === "archived" ? (
          <button
            type="button"
            disabled={busy}
            className="btn btn--secondary btn--sm"
            onClick={() =>
              void setStatus("active", `«${product.name}» volvió al catálogo.`, "No pudimos reactivar el producto.")
            }
          >
            Reactivar
          </button>
        ) : (
          <>
            <button type="button" disabled={busy} className="btn btn--secondary btn--sm" onClick={() => setEditing(true)}>
              Editar
            </button>
            <button
              type="button"
              disabled={busy}
              className="btn btn--ghost btn--sm"
              onClick={() =>
                void act(
                  () => api.DELETE("/products/{product_id}", { params: { path: { product_id: product.id } } }),
                  "No pudimos archivar el producto.",
                  () => onArchived(product),
                )
              }
            >
              Archivar
            </button>
          </>
        )}
      </div>
      {children}
    </li>
  );
}

function ProductEditor({
  product,
  onCancel,
  onSaved,
}: {
  product: Product;
  onCancel: () => void;
  onSaved: (product: Product) => void;
}) {
  const [values, setValues] = useState(() => productValues(product));
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
    // Only what changed, so an edit never overwrites a field someone else just saved.
    const body = {
      ...(data.name !== product.name ? { name: data.name } : {}),
      ...(data.price_cop !== product.price_cop ? { price_cop: data.price_cop } : {}),
      ...(data.stock !== (product.stock ?? null) ? { stock: data.stock } : {}),
      ...(data.description !== (product.description ?? "") ? { description: data.description } : {}),
    };
    if (Object.keys(body).length === 0) {
      onCancel();
      return;
    }
    setSaving(true);
    try {
      const { data: updated, error } = await api.PATCH("/products/{product_id}", {
        params: { path: { product_id: product.id } },
        body,
      });
      if (updated) onSaved(updated);
      else setMessage(catalogProblem(error, "No pudimos guardar los cambios."));
    } catch {
      setMessage(OFFLINE);
    }
    setSaving(false);
  }

  return (
    <form
      noValidate
      aria-label={`Editar ${product.name}`}
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        void submit(event.currentTarget);
      }}
    >
      <ProductFields
        values={values}
        errors={errors}
        onChange={(field: ProductField, value) => {
          setValues((current) => ({ ...current, [field]: value }));
          setErrors((current) => ({ ...current, [field]: undefined }));
        }}
      />
      {message && (
        <p role="alert" className="notice notice--danger">
          {message}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <button type="submit" disabled={saving} className="btn btn--primary btn--sm">
          {saving ? "Guardando…" : "Guardar"}
        </button>
        <button type="button" disabled={saving} className="btn btn--ghost btn--sm" onClick={onCancel}>
          Cancelar
        </button>
      </div>
    </form>
  );
}
