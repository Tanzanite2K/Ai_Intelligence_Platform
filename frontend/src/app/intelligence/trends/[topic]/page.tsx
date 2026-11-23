import type { Metadata } from "next";
import { Suspense } from "react";
import { TopicDetail } from "@/components/intelligence/trends";

export const metadata: Metadata = { title: "Topic" };

export default async function Page({ params }: { params: Promise<{ topic: string }> }) {
  const { topic } = await params;
  return <Suspense><TopicDetail topicId={decodeURIComponent(topic)} /></Suspense>;
}
