import type { Metadata } from "next";
import { Suspense } from "react";
import { RecordExplorer } from "@/components/explore/explorer";
import { CONFIGS } from "@/components/explore/configs";

export const metadata: Metadata = { title: "Jobs" };

export default function Page() {
  return (
    <Suspense>
      <RecordExplorer cfg={CONFIGS.jobs} />
    </Suspense>
  );
}
