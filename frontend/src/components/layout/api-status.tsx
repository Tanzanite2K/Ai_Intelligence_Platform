"use client";

import { useHealth } from "@/lib/api/hooks";
import { API_BASE_URL } from "@/lib/api/client";
import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/** Live backend status from GET /health (polled every 30 s). */
export function ApiStatus() {
  const { data, isError, isPending } = useHealth();
  const ok = !isError && data?.status === "ok";
  const label = isPending ? "Connecting" : ok ? "API online" : "API offline";
  return (
    <Tooltip content={`${label} — ${API_BASE_URL}`}>
      <span className="hidden items-center gap-1.5 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground sm:inline-flex" role="status">
        <span className={cn("size-1.5 rounded-full", isPending ? "bg-muted-foreground" : ok ? "bg-success" : "bg-danger")} />
        {label}
      </span>
    </Tooltip>
  );
}
