import type { Metadata } from "next";
import Link from "next/link";
import { Logo } from "@/components/brand/logo";

export const metadata: Metadata = {
  title: "Cómo funciona",
  description: "Qué hace Vitrina por una tienda que vende por WhatsApp y cómo probarla.",
};

const STEPS = [
  {
    title: "Tu catálogo",
    text: "Pegas tus precios y preguntas frecuentes y subes las fotos de cada producto. De ahí sale todo lo demás.",
  },
  {
    title: "Tus anuncios",
    text: "Tus fotos se vuelven escenas de anuncio. Cada escena se compara con la foto original: si cambia tu producto, se bloquea y no se publica. Tú eliges y apruebas cada anuncio.",
  },
  {
    title: "Tus chats",
    text: "El agente atiende tu WhatsApp con la información de tu catálogo. Cuando no puede responder, te pasa la conversación.",
  },
  {
    title: "Lo que aprendes",
    text: "Un informe reúne lo que frenó a tus clientes en cada anuncio y propone los próximos.",
  },
  {
    title: "Tus clientes de siempre",
    text: "Solo les vuelve a escribir a quienes autorizaron promociones, dentro del horario legal en Colombia.",
  },
];

export default function ComoFuncionaPage() {
  return (
    <main className="mx-auto max-w-xl space-y-8 px-6 py-10">
      <header className="space-y-4">
        <Link href="/foto" className="inline-block rounded-md">
          <Logo size={32} />
        </Link>
        <h1 className="text-3xl font-semibold tracking-tight">Cómo funciona Vitrina</h1>
        <p className="text-fg-muted">
          Tus fotos se vuelven anuncios, tus anuncios se vuelven ventas, y cada conversación mejora el próximo anuncio.
        </p>
      </header>
      <ol className="space-y-3">
        {STEPS.map((step, index) => (
          <li key={step.title} className="card p-4">
            <h2 className="text-lg font-semibold">
              <span className="text-fg-subtle tabular">{index + 1}.</span> {step.title}
            </h2>
            <p className="mt-1 text-sm text-fg-muted">{step.text}</p>
          </li>
        ))}
      </ol>
      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Cómo probarla</h2>
        <p className="text-sm text-fg-muted">
          Con una foto de tu producto ves las escenas y los textos en un par de minutos, sin cuenta. Para el panel completo,
          la tienda la crea quien opera Vitrina y la conecta a tu correo: por ahora no hay registro abierto.
        </p>
        <div className="flex flex-wrap gap-3">
          <Link href="/foto" className="btn btn--primary">
            Probar con una foto
          </Link>
          <Link href="/login" className="btn btn--secondary">
            Entrar al panel
          </Link>
        </div>
      </section>
    </main>
  );
}
