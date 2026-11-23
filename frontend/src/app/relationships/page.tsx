import type { Metadata } from "next";
import { RelationshipOverview } from "@/components/entities/relationships";

export const metadata: Metadata = { title: "Entity relationships" };

export default function Page() {
  return <RelationshipOverview />;
}
