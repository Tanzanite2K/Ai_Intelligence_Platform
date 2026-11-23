"use client";

import { AlertOctagon, Boxes, Briefcase, Building2, FileText, GitMerge, Newspaper, Rocket, UserCheck, type LucideIcon } from "lucide-react";
import { useOverview } from "@/lib/api/hooks";
import { fmtNumber, fmtRelative } from "@/lib/format";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip } from "@/components/ui/tooltip";
import { ErrorState } from "@/components/common/states";
import { cn } from "@/lib/utils";

interface Kpi {
  label: string;
  value: number | undefined;
  icon: LucideIcon;
  hint: string;
  tone?: "warning" | "danger";
}

function KpiCard({ k }: { k: Kpi }) {
  const Icon = k.icon;
  const alert = k.tone && (k.value ?? 0) > 0;
  return (
    <Tooltip content={k.hint}>
      <Card className="flex flex-col gap-2 p-3.5" tabIndex={0} aria-label={`${k.label}: ${fmtNumber(k.value)}`}>
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span className="truncate">{k.label}</span>
          <Icon className={cn("size-4", alert ? (k.tone === "danger" ? "text-danger" : "text-warning") : "text-muted-foreground/70")} />
        </div>
        <div className={cn("tabular text-2xl font-semibold tracking-tight", alert && (k.tone === "danger" ? "text-danger" : "text-warning"))}>
          {fmtNumber(k.value)}
        </div>
      </Card>
    </Tooltip>
  );
}

export function KpiGrid() {
  const { data, isPending, isError, error, refetch } = useOverview();
  if (isError) {
    return (
      <Card>
        <ErrorState error={error} onRetry={() => void refetch()} />
      </Card>
    );
  }
  const r = data?.records ?? {};
  const kpis: Kpi[] = [
    { label: "Startups", value: r.startups, icon: Rocket, hint: "Stored startup records (Y Combinator, Hugging Face organisations)" },
    { label: "Products", value: r.products, icon: Boxes, hint: "Stored product records (Hugging Face Hub models/spaces)" },
    { label: "Research papers", value: r.papers, icon: FileText, hint: "Stored papers with live GitHub stars where a repository exists" },
    { label: "Jobs", value: r.jobs, icon: Briefcase, hint: "Stored job postings (collected inside the 24 h freshness window)" },
    { label: "News", value: r.news, icon: Newspaper, hint: "Stored news articles (collected inside the 24 h freshness window)" },
    { label: "Organisations", value: data?.organisations, icon: Building2, hint: `Distinct canonical organisations from entity resolution (${fmtNumber(data?.seed_organisations)} curated seed entries)` },
    { label: "Entity mappings", value: data?.entity_mappings, icon: GitMerge, hint: "Raw name → canonical organisation decisions logged by entity resolution" },
    { label: "Pending reviews", value: data?.pending_reviews, icon: UserCheck, hint: "Ambiguous matches kept separate until a human decides", tone: "warning" },
    { label: "Failed ingestion", value: data ? data.failed_runs + data.dead_letters : undefined, icon: AlertOctagon,
      hint: data ? `${fmtNumber(data.failed_runs)} failed/access-denied crawl runs + ${fmtNumber(data.dead_letters)} dead-lettered records` : "Failed runs + dead letters", tone: "danger" },
  ];
  return (
    <section aria-label="Key metrics">
      {isPending ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5 2xl:grid-cols-9">
          {Array.from({ length: 9 }, (_, i) => (
            <Card key={i} className="flex flex-col gap-3 p-3.5">
              <Skeleton className="h-3 w-20" />
              <Skeleton className="h-7 w-16" />
            </Card>
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5 2xl:grid-cols-9">
          {kpis.map((k) => (
            <KpiCard key={k.label} k={k} />
          ))}
        </div>
      )}
      {data && (
        <p className="mt-2 text-[11px] text-muted-foreground">
          {fmtNumber(data.total_records)} records · {fmtNumber(data.ingestion_runs)} ingestion runs · last run {fmtRelative(data.last_run_at)} · live from the database
        </p>
      )}
    </section>
  );
}
