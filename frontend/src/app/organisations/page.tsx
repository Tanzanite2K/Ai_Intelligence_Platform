import type { Metadata } from "next";
import { Suspense } from "react";
import { OrganisationExplorer } from "@/components/entities/org-explorer";

export const metadata: Metadata = { title: "Organisations" };

export default function Page() {
  return (
    <Suspense>
      <OrganisationExplorer />
    </Suspense>
  );
}
