import type { Metadata } from "next";
import { Suspense } from "react";
import { DeadLetters } from "@/components/operations/dead-letters";

export const metadata: Metadata = { title: "Dead letter queue" };

export default function Page() {
  return (
    <Suspense>
      <DeadLetters />
    </Suspense>
  );
}
