import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Vitrina",
  description: "Tus fotos se vuelven anuncios, tus anuncios se vuelven ventas.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es-CO">
      <body className="bg-zinc-50 text-zinc-900 antialiased">{children}</body>
    </html>
  );
}
