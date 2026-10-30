import Link from "next/link";
import { LogoMark } from "@/components/brand/logo";

// In Spanish, for an unknown address or a notFound() call; Next's default page is in English.
export default function NotFound() {
  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="flex max-w-sm flex-col items-center space-y-3 text-center">
        <LogoMark size={48} />
        <h1 className="text-xl font-semibold tracking-tight">No encontramos esta página</h1>
        <p className="text-sm text-fg-muted">Puede que el enlace esté mal escrito o que la página ya no exista.</p>
        <Link href="/" className="inline-block text-sm underline">
          Ir al inicio
        </Link>
      </div>
    </main>
  );
}
