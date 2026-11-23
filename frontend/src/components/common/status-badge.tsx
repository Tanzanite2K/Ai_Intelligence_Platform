import type * as React from "react";
import { Badge, type BadgeProps } from "@/components/ui/badge";

const TONE: Record<string, BadgeProps["tone"]> = {
  OK: "success",
  PARTIAL: "warning",
  FAILED: "danger",
  INACCESSIBLE: "danger",
  SKIPPED: "neutral",
  VERIFIED: "success",
  UNVERIFIED: "warning",
  REJECTED: "danger",
};

const LABEL: Record<string, string> = { INACCESSIBLE: "Access denied" };

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={TONE[status] ?? "neutral"}>{LABEL[status] ?? status.charAt(0) + status.slice(1).toLowerCase()}</Badge>;
}

/** Operational category of a source (from /operations/source-health ``health``). */
export const HEALTH_LABEL: Record<string, string> = {
  HEALTHY: "Healthy",
  RATE_LIMITED: "Rate limited",
  CREDENTIAL_REQUIRED: "Credential required",
  DISABLED: "Disabled",
  PARTIAL: "Partial",
  FAILED: "Failed",
  SKIPPED: "Skipped",
};
const HEALTH_TONE: Record<string, BadgeProps["tone"]> = {
  HEALTHY: "success",
  RATE_LIMITED: "warning",
  CREDENTIAL_REQUIRED: "danger",
  DISABLED: "neutral",
  PARTIAL: "warning",
  FAILED: "danger",
  SKIPPED: "neutral",
};

export function HealthBadge({ health, ...rest }: { health: string } & React.HTMLAttributes<HTMLSpanElement>) {
  return <Badge tone={HEALTH_TONE[health] ?? "neutral"} data-health={health} {...rest}>{HEALTH_LABEL[health] ?? health}</Badge>;
}
