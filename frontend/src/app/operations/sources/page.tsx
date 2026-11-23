import type { Metadata } from "next";
import { Suspense } from "react";
import { SourceHealthPage } from "@/components/operations/sources";

export const metadata: Metadata = { title: "Source health" };

export default function Page() {
  return (
    <Suspense>
      <SourceHealthPage />
    </Suspense>
  );
}
