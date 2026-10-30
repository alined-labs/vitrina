"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Loader, Logo, LogoMark } from "@/components/brand/logo";
import { api, type components } from "@/lib/api/client";
import { FORMAT } from "@/lib/labels";

type Showcase = components["schemas"]["PublicShowcase"];

const POLL_MS = 2500;

/** The shareable page of Foto → Vitrina (AS-8.2): verified scenes with the AI label, and copy. */
export function ShowcasePage({ slug }: { slug: string }) {
  const [showcase, setShowcase] = useState<Showcase | null>(null);
  const [missing, setMissing] = useState(false);
  const [copied, setCopied] = useState<number | null>(null);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let alive = true;
    const tick = async () => {
      const { data, response } = await api.GET("/public/showcases/{slug}", { params: { path: { slug } } });
      if (!alive) return;
      if (data) {
        setShowcase(data);
        if (data.status === "processing") timer = setTimeout(() => void tick(), POLL_MS);
      } else if (response.status === 404 || response.status === 400) setMissing(true);
      else timer = setTimeout(() => void tick(), POLL_MS * 2);
    };
    void tick();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [slug]);

  async function copy(text: string, index: number) {
    await navigator.clipboard.writeText(text);
    setCopied(index);
    setTimeout(() => setCopied(null), 2000);
  }

  if (missing) {
    return (
      <Frame>
        <h1 className="text-2xl font-semibold">Esta vitrina no existe</h1>
        <Link href="/foto" className="underline">
          Crea la tuya con una foto
        </Link>
      </Frame>
    );
  }
  if (!showcase || showcase.status === "processing") {
    return (
      <Frame>
        <h1 className="text-2xl font-semibold">{showcase?.product_name ?? "Tu vitrina"}</h1>
        <Loader size={48} />
        <p role="status" className="text-fg-muted">
          Estamos creando tus escenas y verificando que muestren tu producto tal como es. Tarda un par de minutos.
        </p>
      </Frame>
    );
  }
  if (showcase.status === "failed") {
    return (
      <Frame>
        <h1 className="text-2xl font-semibold">{showcase.product_name}</h1>
        <p role="alert" className="text-danger-fg">
          No pudimos crear esta vitrina. Prueba con otra foto en la que el producto se vea completo.
        </p>
        <Link href="/foto" className="underline">
          Subir otra foto
        </Link>
      </Frame>
    );
  }

  const blocked = showcase.scenes.filter((scene) => scene.verification_status === "blocked").length;
  return (
    <Frame>
      <header className="space-y-2">
        <h1 className="text-3xl font-semibold tracking-tight">{showcase.product_name}</h1>
        <p className="text-sm text-fg-muted">{showcase.ai_label}. Cada escena se comparó con la foto original.</p>
      </header>
      <ul className="grid gap-4 sm:grid-cols-3">
        {showcase.scenes.map((scene, index) => (
          <li key={index} className="card overflow-hidden">
            {scene.image_url ? (
              // Signed links from the blob store; next/image would need every storage host configured.
              // eslint-disable-next-line @next/next/no-img-element
              <img src={scene.image_url} alt={`${showcase.product_name}, ${scene.format ? FORMAT[scene.format] : "escena"}`} className="w-full" />
            ) : (
              <div className="flex aspect-square items-center justify-center bg-sunken p-4 text-center text-sm text-fg-muted">
                Escena bloqueada: cambiaba el producto, por eso no se publica.
              </div>
            )}
            <p className="px-3 py-2 text-xs text-fg-subtle">{scene.format ? FORMAT[scene.format] : ""}</p>
          </li>
        ))}
      </ul>
      {blocked > 0 && (
        <p className="text-sm text-fg-muted">
          El verificador bloqueó {blocked === 1 ? "una escena" : `${blocked} escenas`} que alteraba{blocked === 1 ? "" : "n"} tu
          producto. Así nadie recibe algo distinto a lo que vio.
        </p>
      )}
      {showcase.copies.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-lg font-semibold">Textos para WhatsApp</h2>
          <ul className="space-y-2">
            {showcase.copies.map((text, index) => (
              <li key={index} className="flex items-start gap-3 card p-3 text-sm">
                <p className="min-w-0 flex-1">{text}</p>
                <button type="button" onClick={() => void copy(text, index)} className="btn btn--secondary btn--sm">
                  {copied === index ? "Copiado" : "Copiar"}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      <section className="band-night space-y-3 p-6 sm:p-8">
        <LogoMark size={40} ground="night" />
        <p className="font-display text-xl font-bold">¿Quieres que también responda tus chats?</p>
        <p className="text-sm text-fg-inverse-muted">
          Vitrina atiende tu WhatsApp con los precios de tu catálogo y te avisa cuando un cliente te necesita.
        </p>
        {showcase.cta_url && (
          <a href={showcase.cta_url} className="btn btn--inverse">
            Crear mi cuenta
          </a>
        )}
      </section>
      <p className="flex items-center justify-center gap-2 text-xs text-fg-subtle">
        <LogoMark size={16} />
        Hecho con Vitrina
      </p>
    </Frame>
  );
}

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <main className="mx-auto max-w-4xl space-y-8 px-6 py-10">
      <Link href="/foto" className="inline-block rounded-md">
        <Logo size={28} />
      </Link>
      {children}
    </main>
  );
}
