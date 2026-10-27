import type { Metadata } from "next";
import { ShowcaseUpload } from "@/components/showcase/showcase-upload";

export const metadata: Metadata = {
  title: "Foto → Vitrina",
  description: "Sube la foto de un producto y recibe escenas de anuncio y textos listos para WhatsApp.",
  robots: { index: false },
};

export default function FotoPage() {
  return (
    <main className="mx-auto max-w-xl space-y-8 px-6 py-12">
      <header className="space-y-3">
        <p className="text-sm font-medium uppercase tracking-wide text-zinc-500">Vitrina</p>
        <h1 className="text-3xl font-semibold tracking-tight">Tu foto, convertida en anuncios</h1>
        <p className="text-zinc-600">
          Sube una foto de tu producto. En un par de minutos tienes tres escenas, una para cada formato, y diez textos
          para WhatsApp. Cada escena se compara con tu foto: si cambia tu producto, no se publica.
        </p>
      </header>
      <ShowcaseUpload />
      <p className="text-xs text-zinc-500">
        No guardamos tu foto: se borra al terminar. Las escenas llevan la etiqueta «Imagen generada con IA».
      </p>
    </main>
  );
}
