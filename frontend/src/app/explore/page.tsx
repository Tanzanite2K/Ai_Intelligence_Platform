import type { Metadata } from "next";
import { Suspense } from "react";
import { GlobalSearch } from "@/components/explore/global-search";

export const metadata: Metadata = { title: "Explore intelligence" };

export default function Page() {
  return (
    <Suspense>
      <GlobalSearch />
    </Suspense>
  );
}
