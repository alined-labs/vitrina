"use client";

import { useId } from "react";
import { parseWhole, type ProductErrors, type ProductField, type ProductValues } from "@/lib/catalog";
import { formatCop } from "@/lib/format";

/** A visible label, the field, and its error as a sentence (DESIGN.md, before shipping a page). */
export function Field({
  label,
  error,
  hint,
  className,
  children,
}: {
  label: string;
  error?: string | undefined;
  hint?: string;
  className?: string;
  children: (ids: { id: string; describedBy: string | undefined; invalid: boolean }) => React.ReactNode;
}) {
  const id = useId();
  const note = error ? `${id}-error` : hint ? `${id}-hint` : undefined;
  return (
    <div className={`min-w-0 space-y-1 ${className ?? ""}`}>
      <label htmlFor={id} className="block text-sm font-semibold">
        {label}
      </label>
      {children({ id, describedBy: note, invalid: Boolean(error) })}
      {error ? (
        <p id={`${id}-error`} role="alert" className="text-sm text-danger-fg">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="text-sm text-fg-subtle">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

const INPUT = "input w-full min-w-0 aria-invalid:border-danger";

/** The fields of a product, for creating one and for editing one in place. */
export function ProductFields({
  values,
  errors,
  onChange,
}: {
  values: ProductValues;
  errors: ProductErrors;
  onChange: (field: ProductField, value: string) => void;
}) {
  const price = parseWhole(values.price);
  return (
    <div className="grid gap-3 sm:grid-cols-[minmax(0,2fr)_minmax(9rem,1fr)_minmax(7rem,1fr)]">
      <Field label="Nombre" error={errors.name}>
        {({ id, describedBy, invalid }) => (
          <input
            id={id}
            name="name"
            value={values.name}
            maxLength={120}
            aria-invalid={invalid}
            aria-describedby={describedBy}
            onChange={(event) => onChange("name", event.target.value)}
            className={INPUT}
          />
        )}
      </Field>
      {/* Text, not type="number": some browsers read "85.000" there as 85 pesos. */}
      <Field
        label="Precio en pesos"
        error={errors.price}
        hint={price !== null && price > 0 ? `Se guarda como ${formatCop(price)}` : "Ej.: 85.000"}
      >
        {({ id, describedBy, invalid }) => (
          <input
            id={id}
            name="price"
            value={values.price}
            inputMode="numeric"
            autoComplete="off"
            aria-invalid={invalid}
            aria-describedby={describedBy}
            onChange={(event) => onChange("price", event.target.value)}
            className={`${INPUT} tabular`}
          />
        )}
      </Field>
      <Field label="Inventario" error={errors.stock} hint="Vacío si no lo controlas">
        {({ id, describedBy, invalid }) => (
          <input
            id={id}
            name="stock"
            value={values.stock}
            inputMode="numeric"
            autoComplete="off"
            aria-invalid={invalid}
            aria-describedby={describedBy}
            onChange={(event) => onChange("stock", event.target.value)}
            className={`${INPUT} tabular`}
          />
        )}
      </Field>
      <Field label="Descripción" error={errors.description} className="sm:col-span-3">
        {({ id, describedBy, invalid }) => (
          <textarea
            id={id}
            name="description"
            value={values.description}
            maxLength={2000}
            rows={2}
            placeholder="Materiales, tallas, colores"
            aria-invalid={invalid}
            aria-describedby={describedBy}
            onChange={(event) => onChange("description", event.target.value)}
            className={INPUT}
          />
        )}
      </Field>
    </div>
  );
}

/** Puts the cursor on the first field that needs fixing. */
export function focusFirstError(form: HTMLFormElement, errors: Record<string, string | undefined>) {
  const first = Object.keys(errors).find((name) => errors[name]);
  const element = first ? form.elements.namedItem(first) : null;
  if (element instanceof HTMLElement) element.focus();
}
