import type { Metadata } from "next";
import { Suspense } from "react";
import { SourceReliability } from "@/components/history/reliability";

export const metadata: Metadata = { title: "Source Reliability" };

export default function Page() {
  return <Suspense><SourceReliability /></Suspense>;
}
