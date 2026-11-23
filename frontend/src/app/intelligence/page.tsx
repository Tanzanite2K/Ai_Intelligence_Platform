import type { Metadata } from "next";
import { IntelligenceBrief } from "@/components/intelligence/brief";

export const metadata: Metadata = { title: "Intelligence brief" };

export default function Page() {
  return <IntelligenceBrief />;
}
