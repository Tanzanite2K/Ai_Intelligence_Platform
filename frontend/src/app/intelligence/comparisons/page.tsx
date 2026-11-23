import type { Metadata } from "next";
import { IntelChanges } from "@/components/history/intel-changes";

export const metadata: Metadata = { title: "Historical Changes" };

export default function Page() {
  return <IntelChanges />;
}
