import type { Metadata } from "next";
import { Suspense } from "react";
import { InnovationMap } from "@/components/intelligence/signals-map";

export const metadata: Metadata = { title: "Innovation map" };

export default function Page() {
  return <Suspense><InnovationMap /></Suspense>;
}
