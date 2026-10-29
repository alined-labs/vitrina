import Link from "next/link";

// In Spanish, for an unknown address or a notFound() call; Next's default page is in English.
export default function NotFound() {
  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="max-w-sm space-y-3 text-center">
        <h1 className="text-xl font-semibold tracking-tight">No encontramos esta página</h1>
        <p className="text-sm text-zinc-600">Puede que el enlace esté mal escrito o que la página ya no exista.</p>
        <Link href="/" className="inline-block text-sm underline">
          Ir al inicio
        </Link>
      </div>
    </main>
  );
}
