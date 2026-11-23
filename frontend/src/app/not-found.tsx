import Link from "next/link";
import { Card } from "@/components/ui/card";

export default function NotFound() {
  return (
    <Card className="mx-auto mt-10 max-w-lg p-8 text-center">
      <h1 className="text-base font-semibold">Page not found</h1>
      <p className="mt-1 text-sm text-muted-foreground">This page does not exist, or it belongs to a later build phase.</p>
      <Link href="/" className="mt-4 inline-block text-sm text-accent hover:underline">Back to overview</Link>
    </Card>
  );
}
