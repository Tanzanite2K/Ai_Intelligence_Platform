import type { Metadata } from "next";
import { DataQuality } from "@/components/entities/review-queue";

export const metadata: Metadata = { title: "Data quality" };

export default function Page() {
  return <DataQuality />;
}
