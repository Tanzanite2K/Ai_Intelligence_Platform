import type { Metadata } from "next";
import { Suspense } from "react";
import { IngestionMonitor } from "@/components/operations/ingestion";

export const metadata: Metadata = { title: "Ingestion monitor" };

export default function Page() {
  return (
    <Suspense>
      <IngestionMonitor />
    </Suspense>
  );
}
