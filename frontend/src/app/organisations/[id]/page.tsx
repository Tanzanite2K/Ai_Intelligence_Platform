import type { Metadata } from "next";
import { Suspense } from "react";
import { OrganisationProfile } from "@/components/entities/org-profile";

export const metadata: Metadata = { title: "Organisation" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <Suspense>
      <OrganisationProfile id={decodeURIComponent(id)} />
    </Suspense>
  );
}
