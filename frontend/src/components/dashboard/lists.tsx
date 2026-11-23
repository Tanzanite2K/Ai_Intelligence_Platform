"use client";

import Link from "next/link";
import { ChevronRight, Star } from "lucide-react";
import { recordHref } from "@/lib/records";
import { useRecent, useSourceHealth } from "@/lib/api/hooks";
import type { RecordSummary, RunItem, SourceHealth } from "@/lib/api/schemas";
import { fmtDate, fmtNumber, fmtRelative } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tooltip } from "@/components/ui/tooltip";
import { EmptyState, ErrorState, ListSkeleton } from "@/components/common/states";
import { HealthBadge, StatusBadge } from "@/components/common/status-badge";

type Kind = "startups" | "papers" | "news" | "jobs";

const META: Record<Kind, { title: string; description: string; empty: string }> = {
  startups: { title: "Recently added startups", description: "Latest collected", empty: "No startups stored yet" },
  papers: { title: "Recently published research", description: "By publication date", empty: "No papers stored yet" },
  news: { title: "Latest news", description: "By publication date", empty: "No news stored yet" },
  jobs: { title: "Recent jobs", description: "By posting date", empty: "No jobs stored yet" },
};

function meta(kind: Kind, r: RecordSummary): React.ReactNode {
  switch (kind) {
    case "startups":
      return [r.location, r.source_name].filter(Boolean).join(" · ") || r.source_name;
    case "papers":
      return (
        <span className="inline-flex items-center gap-2">
          <span>{fmtDate(r.published_at)}</span>
          {r.github_stars !== null ? (
            <span className="inline-flex items-center gap-0.5"><Star className="size-3" /> {fmtNumber(r.github_stars)}</span>
          ) : (
            <span className="text-muted-foreground/70">no repo</span>
          )}
        </span>
      );
    case "news":
      return `${r.source_name} · ${fmtRelative(r.published_at)}`;
    case "jobs":
      return [r.organisation, r.location, fmtRelative(r.published_at)].filter(Boolean).join(" · ");
  }
}

function RecordRow({ kind, r }: { kind: Kind; r: RecordSummary }) {
  return (
    <li>
      <Link href={recordHref(r.type, r.id)} className="group flex items-start gap-2 rounded-md px-2 py-1.5 hover:bg-surface-2">
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span className="truncate text-sm">{r.name ?? "Untitled"}</span>
            {kind === "jobs" && r.is_remote && <Badge tone="info">Remote</Badge>}
            {kind === "news" && (r.organizations.length === 0 ? (
              <Badge>No organisation tags</Badge>
            ) : (
              <Badge tone="accent">{r.organizations.length} org{r.organizations.length === 1 ? "" : "s"}</Badge>
            ))}
          </span>
          <span className="block truncate text-xs text-muted-foreground">{meta(kind, r)}</span>
        </span>
        <ChevronRight className="mt-1 size-3.5 shrink-0 text-muted-foreground opacity-0 group-hover:opacity-100" aria-hidden />
      </Link>
    </li>
  );
}

export function RecentList({ kind }: { kind: Kind }) {
  const { data, isPending, isError, error, refetch } = useRecent(6);
  const m = META[kind];
  const rows = data?.[kind] ?? [];
  return (
    <Card className="flex flex-col">
      <CardHeader>
        <div>
          <CardTitle>{m.title}</CardTitle>
          <CardDescription>{m.description}</CardDescription>
        </div>
        <Link href={`/${kind}`} className="shrink-0 text-xs text-accent hover:underline">View all</Link>
      </CardHeader>
      <CardContent className="flex-1 px-2">
        {isError ? <ErrorState error={error} onRetry={() => void refetch()} /> : isPending ? <div className="px-2"><ListSkeleton rows={5} /></div> : rows.length === 0 ? (
          <EmptyState title={m.empty} />
        ) : (
          <ul className="flex flex-col">{rows.map((r) => <RecordRow key={r.id} kind={kind} r={r} />)}</ul>
        )}
      </CardContent>
    </Card>
  );
}

export function RunsTable({ runs }: { runs: RunItem[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-muted-foreground">
            <th scope="col" className="py-2 pr-3 font-medium">Source</th>
            <th scope="col" className="py-2 pr-3 font-medium">Status</th>
            <th scope="col" className="py-2 pr-3 text-right font-medium">Fetched</th>
            <th scope="col" className="py-2 pr-3 text-right font-medium">Stored</th>
            <th scope="col" className="py-2 pr-3 text-right font-medium">Dupes</th>
            <th scope="col" className="py-2 pr-3 text-right font-medium">Rejected</th>
            <th scope="col" className="py-2 pr-3 font-medium">Started</th>
            <th scope="col" className="py-2 font-medium">Duration</th>
          </tr>
        </thead>
        <tbody className="tabular">
          {runs.map((r) => (
            <tr key={r.id} className="border-b border-border/60 last:border-0">
              <td className="py-2 pr-3">
                <div className="font-medium">{r.source}</div>
                {r.error && (
                  <Tooltip content={r.error}>
                    <div className="max-w-[260px] truncate text-xs text-danger">{r.error}</div>
                  </Tooltip>
                )}
              </td>
              <td className="py-2 pr-3"><StatusBadge status={r.status} /></td>
              <td className="py-2 pr-3 text-right">{fmtNumber(r.fetched)}</td>
              <td className="py-2 pr-3 text-right">{fmtNumber(r.stored)}</td>
              <td className="py-2 pr-3 text-right text-muted-foreground">{fmtNumber(r.duplicates)}</td>
              <td className="py-2 pr-3 text-right text-muted-foreground">{fmtNumber(r.rejected)}</td>
              <td className="py-2 pr-3 text-xs text-muted-foreground">{fmtRelative(r.started_at)}</td>
              <td className="py-2 text-xs text-muted-foreground">{r.duration_s === null ? "—" : `${r.duration_s.toFixed(1)} s`}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 4. Recent activity — the latest ingestion runs. */
export function RecentRuns() {
  const { data, isPending, isError, error, refetch } = useRecent(6);
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Recent ingestion runs</CardTitle>
          <CardDescription>Latest 10 crawl runs recorded by the pipeline</CardDescription>
        </div>
        <Link href="/operations/ingestion" className="shrink-0 text-xs text-accent hover:underline">Ingestion monitor</Link>
      </CardHeader>
      <CardContent>
        {isError ? <ErrorState error={error} onRetry={() => void refetch()} /> : isPending ? <ListSkeleton rows={6} /> : (data?.runs.length ?? 0) === 0 ? (
          <EmptyState title="No ingestion runs recorded" />
        ) : (
          <RunsTable runs={data?.runs ?? []} />
        )}
      </CardContent>
    </Card>
  );
}

function HealthRow({ h }: { h: SourceHealth }) {
  const problems = [h.rate_limited_runs > 0 && `${h.rate_limited_runs} rate-limited`, h.access_denied_runs > 0 && `${h.access_denied_runs} access denied`].filter(Boolean);
  return (
    <li className="flex items-start gap-2 py-2">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="truncate text-sm font-medium">{h.source}</span>
          <Badge>{h.vertical.toLowerCase().replace("_", " ")}</Badge>
        </div>
        <div className="text-xs text-muted-foreground">
          last run {fmtRelative(h.last_run_at)} · last success {h.last_success_at ? fmtRelative(h.last_success_at) : "never"}
          {problems.length > 0 && <span className="text-warning"> · {problems.join(" · ")}</span>}
        </div>
        {h.health !== "HEALTHY" && (h.health_detail || h.last_error) && (
          <Tooltip content={h.health === "DISABLED" ? h.health_detail : h.last_error ?? h.health_detail}>
            <div className={`truncate text-xs ${h.health === "DISABLED" ? "text-muted-foreground" : "text-danger"}`}>
              {h.health === "DISABLED" ? h.health_detail : h.last_error ?? h.health_detail}
            </div>
          </Tooltip>
        )}
      </div>
      <HealthBadge health={h.health} />
    </li>
  );
}

/** Source health overview — status of each source's most recent run. */
export function SourceHealthOverview() {
  const { data, isPending, isError, error, refetch } = useSourceHealth();
  const counts = (data ?? []).reduce<Record<string, number>>((acc, h) => ({ ...acc, [h.health]: (acc[h.health] ?? 0) + 1 }), {});
  return (
    <Card className="flex flex-col">
      <CardHeader>
        <div>
          <CardTitle>Source health</CardTitle>
          <CardDescription>Health per source · <Link href="/operations/sources" className="text-accent hover:underline">details</Link></CardDescription>
        </div>
        {data && (
          <div className="flex flex-wrap justify-end gap-1">
            {Object.entries(counts).map(([s, n]) => (
              <span key={s} className="inline-flex items-center gap-1 text-xs text-muted-foreground"><HealthBadge health={s} />{n}</span>
            ))}
          </div>
        )}
      </CardHeader>
      <CardContent className="flex-1">
        {isError ? <ErrorState error={error} onRetry={() => void refetch()} /> : isPending ? <ListSkeleton rows={6} /> : (data?.length ?? 0) === 0 ? (
          <EmptyState title="No sources have run yet" />
        ) : (
          <ul className="max-h-[420px] divide-y divide-border/60 overflow-y-auto pr-1">{data?.map((h) => <HealthRow key={h.source} h={h} />)}</ul>
        )}
      </CardContent>
    </Card>
  );
}
