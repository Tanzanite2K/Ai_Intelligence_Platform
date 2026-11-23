"use client";

import Link from "next/link";
import { ShieldAlert } from "lucide-react";
import { METHOD_INFO, entityHref } from "@/lib/entities";
import { Badge } from "@/components/ui/badge";
import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export function MethodBadge({ method, count }: { method: string; count?: number }) {
  const m = METHOD_INFO[method];
  return (
    <Tooltip content={m?.help ?? method}>
      <Badge tone={method === "REVIEW" ? "warning" : method === "NEW" ? "neutral" : "accent"} tabIndex={0}>
        {m?.label ?? method}{count !== undefined && <span className="tabular opacity-80">{count}</span>}
      </Badge>
    </Tooltip>
  );
}

export function ReviewBadge() {
  return (
    <Tooltip content="At least one mapping for this name is pending human review; review-mapped records are kept apart from verified links.">
      <Badge tone="warning" tabIndex={0}><ShieldAlert className="size-3" /> review</Badge>
    </Tooltip>
  );
}

export function OriginBadge({ origin }: { origin: "SEED" | "DISCOVERED" }) {
  return (
    <Tooltip content={origin === "SEED" ? "Curated organisation from the seed list (aliases and domain maintained by hand)" : "Organisation discovered from source records by entity resolution"}>
      <Badge tone={origin === "SEED" ? "info" : "neutral"} tabIndex={0}>{origin === "SEED" ? "Curated" : "Discovered"}</Badge>
    </Tooltip>
  );
}

/** Link to an organisation profile. Sits above stretched row links (relative z-10). */
export function OrgLink({ name, id, className }: { name: string; id?: string; className?: string }) {
  return (
    <Link href={id ? entityHref(id, true) : entityHref(name)} className={cn("relative z-10 hover:text-accent hover:underline", className)}>
      {name}
    </Link>
  );
}
