import Link from "next/link";
import { ApiHealth } from "@/components/api-health";
import { sections } from "@/lib/nav";

export default function Home() {
  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <section className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Inicio</h1>
        <p className="text-zinc-600">
          Tus fotos se vuelven anuncios, tus anuncios se vuelven ventas, y cada conversación mejora el próximo anuncio.
        </p>
      </section>
      <ApiHealth />
      <ul className="grid gap-3 sm:grid-cols-2">
        {sections.map((section) => (
          <li key={section.slug}>
            <Link
              href={`/${section.slug}`}
              className="block h-full rounded-lg border border-zinc-200 bg-white p-4 hover:border-zinc-400"
            >
              <span className="font-medium">{section.label}</span>
              <span className="mt-1 block text-sm text-zinc-600">{section.summary}</span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
