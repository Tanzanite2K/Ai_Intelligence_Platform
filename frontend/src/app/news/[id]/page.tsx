import type { Metadata } from "next";
import { RecordDetailView } from "@/components/detail/record-detail";

export const metadata: Metadata = { title: "News" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <RecordDetailView slug="news" id={id} />;
}
