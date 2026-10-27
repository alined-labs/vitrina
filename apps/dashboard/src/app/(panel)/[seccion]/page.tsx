import { notFound } from "next/navigation";
import { sections } from "@/lib/nav";

// Placeholder for every section until its real page lands: a folder such as
// `(panel)/catalogo/` takes precedence over this dynamic segment.
export const dynamicParams = false;

export function generateStaticParams() {
  return sections.map((section) => ({ seccion: section.slug }));
}

export default async function SectionPage({ params }: { params: Promise<{ seccion: string }> }) {
  const { seccion } = await params;
  const section = sections.find((item) => item.slug === seccion);
  if (!section) notFound();

  return (
    <div className="mx-auto max-w-4xl space-y-2">
      <h1 className="text-2xl font-semibold tracking-tight">{section.label}</h1>
      <p className="text-zinc-600">{section.summary}</p>
      <p className="pt-4 text-sm text-zinc-500">En construcción · {section.story}</p>
    </div>
  );
}
