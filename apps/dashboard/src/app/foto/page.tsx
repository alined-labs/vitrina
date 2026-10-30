import type { Metadata } from "next";
import { Logo } from "@/components/brand/logo";
import { ShowcaseUpload } from "@/components/showcase/showcase-upload";

export const metadata: Metadata = {
  title: "Foto → Vitrina",
  description: "Sube la foto de un producto y recibe escenas de anuncio y textos listos para WhatsApp.",
  robots: { index: false },
};

export default function FotoPage() {
  return (
    <main className="mx-auto max-w-xl space-y-8 px-6 py-10">
      <header className="space-y-4">
        <Logo size={32} />
        <h1 className="text-3xl font-semibold tracking-tight">Tu foto, convertida en anuncios</h1>
        <p className="text-fg-muted">
          Sube una foto de tu producto. En un par de minutos tienes tres escenas, una para cada formato, y diez textos
          para WhatsApp. Cada escena se compara con tu foto: si cambia tu producto, no se publica.
        </p>
      </header>
      <ShowcaseUpload />
      <p className="text-xs text-fg-subtle">
        No guardamos tu foto: se borra al terminar. Las escenas llevan la etiqueta «Imagen generada con IA».
      </p>
    </main>
  );
}
