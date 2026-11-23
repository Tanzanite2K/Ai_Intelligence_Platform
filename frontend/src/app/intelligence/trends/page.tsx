import type { Metadata } from "next";
import { Suspense } from "react";
import { TrendRadar } from "@/components/intelligence/trends";

export const metadata: Metadata = { title: "Trend radar" };

export default function Page() {
  return <Suspense><TrendRadar /></Suspense>;
}
