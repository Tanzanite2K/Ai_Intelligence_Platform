import type { Metadata } from "next";
import { Suspense } from "react";
import { HistoricalTrends } from "@/components/intelligence/history";

export const metadata: Metadata = { title: "Historical trends" };

export default function Page() {
  return <Suspense><HistoricalTrends /></Suspense>;
}
