import type { Metadata } from "next";
import { Suspense } from "react";
import { MarketSignals } from "@/components/intelligence/signals-map";

export const metadata: Metadata = { title: "Market signals" };

export default function Page() {
  return <Suspense><MarketSignals /></Suspense>;
}
