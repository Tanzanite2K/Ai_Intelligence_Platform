"use client";

import { AlertTriangle, Inbox, RefreshCw, WifiOff } from "lucide-react";
import type { ReactNode } from "react";
import { ApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export function ErrorState({ error, onRetry, className }: { error: unknown; onRetry?: () => void; className?: string }) {
  const network = error instanceof ApiError && error.kind === "network";
  const limited = error instanceof ApiError && error.kind === "rate_limited";
  const Icon = network ? WifiOff : AlertTriangle;
  const title = network ? "API unavailable" : limited ? "Too many requests" : "Could not load data";
  const msg = error instanceof Error ? error.message : "Unknown error";
  return (
    <div role="alert" className={cn("flex flex-col items-center justify-center gap-2 px-4 py-8 text-center", className)}>
      <Icon className="size-5 text-danger" />
      <div className="text-sm font-medium">{title}</div>
      <p className="max-w-sm text-xs text-muted-foreground">{msg}</p>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry} className="mt-1">
          <RefreshCw /> Retry
        </Button>
      )}
    </div>
  );
}

export function EmptyState({ title, children, className }: { title: string; children?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-1.5 px-4 py-8 text-center", className)}>
      <Inbox className="size-5 text-muted-foreground" />
      <div className="text-sm font-medium">{title}</div>
      {children && <div className="max-w-sm text-xs text-muted-foreground">{children}</div>}
    </div>
  );
}

export function LimitationNote({ children }: { children: ReactNode }) {
  return (
    <div className="flex gap-2 rounded-md border border-warning/30 bg-warning/8 px-3 py-2 text-xs text-muted-foreground">
      <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warning" />
      <div>{children}</div>
    </div>
  );
}

export function ListSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="flex flex-col gap-3" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex flex-col gap-1.5">
          <Skeleton className="h-3.5 w-3/4" />
          <Skeleton className="h-3 w-1/2" />
        </div>
      ))}
    </div>
  );
}

const BARS = [45, 70, 35, 85, 55, 65, 30];

export function ChartSkeleton({ height = 220 }: { height?: number }) {
  return (
    <div aria-busy="true" aria-label="Loading chart" style={{ height }} className="flex items-end gap-2">
      {BARS.map((h, i) => (
        <div key={i} className="flex-1" style={{ height: `${h}%` }}>
          <Skeleton className="h-full w-full" />
        </div>
      ))}
    </div>
  );
}
