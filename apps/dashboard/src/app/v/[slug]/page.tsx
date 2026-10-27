import type { Metadata } from "next";
import { ShowcasePage } from "@/components/showcase/showcase-page";

export const metadata: Metadata = {
  title: "Vitrina",
  robots: { index: false },
};

export default async function SharedShowcase({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return <ShowcasePage slug={slug} />;
}
