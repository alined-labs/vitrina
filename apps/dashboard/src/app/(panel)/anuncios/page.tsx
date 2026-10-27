import { Ads } from "@/components/ads/ads";

export default function AdsPage() {
  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <section className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Anuncios</h1>
        <p className="text-zinc-600">
          Cada anuncio aprobado tiene un código, como #A7, y un enlace que abre WhatsApp con «Hola, vi el anuncio #A7».
          Así sabes qué anuncio trajo cada conversación.
        </p>
      </section>
      <Ads />
    </div>
  );
}
