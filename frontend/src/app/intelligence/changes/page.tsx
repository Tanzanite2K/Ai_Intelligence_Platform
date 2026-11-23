import type { Metadata } from "next";
import { Suspense } from "react";
import { ChangeFeed } from "@/components/history/change-feed";

export const metadata: Metadata = { title: "Change Feed" };

export default function Page() {
  return <Suspense><ChangeFeed /></Suspense>;
}
