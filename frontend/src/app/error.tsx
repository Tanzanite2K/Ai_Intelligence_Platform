"use client";

import { ErrorState } from "@/components/common/states";
import { Card } from "@/components/ui/card";

export default function RouteError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <Card className="mx-auto mt-10 max-w-lg">
      <ErrorState error={error} onRetry={reset} />
    </Card>
  );
}
