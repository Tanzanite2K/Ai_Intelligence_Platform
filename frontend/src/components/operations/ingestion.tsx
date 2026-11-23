"use client";

import { RotateCcw } from "lucide-react";
import { useMemo } from "react";
import { useActivity, useOperationsHealth, useRuns } from "@/lib/api/hooks";
import { fmtDate, fmtNumber } from "@/lib/format";
import { useUrlState } from "@/lib/url-state";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorState, LimitationNote, ListSkeleton } from "@/components/common/states";
import { StatusBadge } from "@/components/common/status-badge";
import { Field, Pagination, Select } from "@/components/explore/controls";
import { pageParams } from "@/components/explore/explorer";
import { RunsTable } from "@/components/dashboard/lists";

const STATUSES = ["OK", "PARTIAL", "FAILED", "INACCESSIBLE", "SKIPPED"];

function Kpi({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: "danger" | "warning" | "success" }) {
  return (
    <Card className="p-3.5">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={cn("tabular text-2xl font-semibold", tone === "danger" && "text-danger", tone === "warning" && "text-warning", tone === "success" && "text-success")}>{value}</div>
      {sub && <div className="text-[11px] text-muted-foreground">{sub}</div>}
    </Card>
  );
}

/** Execution statistics aggregated from per-source health (all runs) and daily run aggregates. */
function RunStats() {
  const health = useOperationsHealth();
  const activity = useActivity(3650);
  const s = useMemo(() => {
    const h = health.data ?? [];
    const byStatus: Record<string, number> = {};
    for (const x of h) for (const [k, v] of Object.entries(x.runs_by_status)) byStatus[k] = (byStatus[k] ?? 0) + v;
    const runs = h.reduce((a, x) => a + x.runs, 0);
    return {
      runs, byStatus, sources: h.length,
      ok: byStatus.OK ?? 0,
      neverOk: h.filter((x) => !x.last_success_at).length,
      rateLimited: h.reduce((a, x) => a + x.rate_limited_runs, 0),
      denied: h.reduce((a, x) => a + x.access_denied_runs, 0),
    };
  }, [health.data]);
  if (health.isError) return <Card><ErrorState error={health.error} onRetry={() => void health.refetch()} /></Card>;
  if (health.isPending) return <Card className="p-4"><ListSkeleton rows={3} /></Card>;
  const days = activity.data?.data.runs_per_day ?? [];
  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Kpi label="Crawl runs recorded" value={fmtNumber(s.runs)} sub={`${fmtNumber(s.sources)} sources`} />
        <Kpi label="Successful runs" value={s.runs ? `${Math.round((s.ok / s.runs) * 100)}%` : "—"} sub={`${fmtNumber(s.ok)} of ${fmtNumber(s.runs)} with status OK`} tone="success" />
        <Kpi label="Partial runs" value={fmtNumber(s.byStatus.PARTIAL ?? 0)} sub="stored data, with some errors" tone={(s.byStatus.PARTIAL ?? 0) > 0 ? "warning" : undefined} />
        <Kpi label="Failed / access denied" value={fmtNumber((s.byStatus.FAILED ?? 0) + (s.byStatus.INACCESSIBLE ?? 0))} tone={(s.byStatus.FAILED ?? 0) + (s.byStatus.INACCESSIBLE ?? 0) > 0 ? "danger" : undefined} />
        <Kpi label="Rate-limited runs" value={fmtNumber(s.rateLimited)} sub="HTTP 429 or rate-limit errors" />
        <Kpi label="Sources never successful" value={fmtNumber(s.neverOk)} tone={s.neverOk > 0 ? "danger" : undefined} />
      </div>
      <Card>
        <CardHeader><div><CardTitle>Runs per day</CardTitle><CardDescription>Crawl runs started and records stored per day</CardDescription></div></CardHeader>
        <CardContent>
          {activity.isPending ? <ListSkeleton rows={2} /> : days.length === 0 ? <EmptyState title="No runs recorded" /> : (
            <div className="flex flex-col gap-2">
              {days.length <= 1 && <LimitationNote>All recorded runs happened on {days[0] ? fmtDate(days[0].date) : "one day"}; scheduled ingestion has not run on other days yet, so there is no trend to show.</LimitationNote>}
              <table className="w-full max-w-md text-sm"><tbody className="tabular">
                {days.map((d) => <tr key={d.date} className="border-b border-border/60 last:border-0"><td className="py-1.5">{fmtDate(d.date)}</td><td className="py-1.5 text-right">{fmtNumber(d.runs)} runs</td><td className="py-1.5 text-right">{fmtNumber(d.stored)} stored</td></tr>)}
              </tbody></table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

export function IngestionMonitor() {
  const { get, all, set, reset } = useUrlState();
  const { page, page_size } = pageParams(all);
  const status = STATUSES.includes(get("status")) ? get("status") : "";
  const source = get("source");
  const health = useOperationsHealth();
  const runs = useRuns({ status: status || undefined, source: source || undefined, page, page_size });
  const shown = runs.data?.data ?? [];
  const totals = shown.reduce((a, r) => ({ fetched: a.fetched + r.fetched, stored: a.stored + r.stored, dup: a.dup + r.duplicates, rej: a.rej + r.rejected }), { fetched: 0, stored: 0, dup: 0, rej: 0 });
  const durations = shown.map((r) => r.duration_s).filter((d): d is number => d !== null);
  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Ingestion monitor</h1>
        <p className="text-sm text-muted-foreground">Crawl history recorded by the pipeline. Read-only: crawls cannot be started from this interface.</p>
      </div>
      <RunStats />
      <Card>
        <CardHeader>
          <div><CardTitle>Crawl history</CardTitle><CardDescription>Newest first{runs.data ? ` · ${fmtNumber(runs.data.meta.total)} runs match` : ""}</CardDescription></div>
          {(status || source) && <Button variant="ghost" size="sm" onClick={reset}><RotateCcw /> Reset</Button>}
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="grid gap-3 sm:grid-cols-2 lg:max-w-xl">
            <Field label="Source" htmlFor="run-source">
              <Select id="run-source" value={source} onChange={(v) => set({ source: v })} placeholder="All sources" options={(health.data ?? []).map((h) => ({ value: h.source, label: h.source })).sort((a, b) => a.label.localeCompare(b.label))} />
            </Field>
            <Field label="Status" htmlFor="run-status">
              <Select id="run-status" value={status} onChange={(v) => set({ status: v })} placeholder="Any status" options={STATUSES.map((s) => ({ value: s, label: s === "INACCESSIBLE" ? "Access denied" : s.charAt(0) + s.slice(1).toLowerCase() }))} />
            </Field>
          </div>
          {runs.isError ? <ErrorState error={runs.error} onRetry={() => void runs.refetch()} /> : runs.isPending ? <ListSkeleton rows={8} /> : shown.length === 0 ? (
            <EmptyState title="No runs match these filters" />
          ) : (
            <>
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground tabular">
                <span>Shown runs: {fmtNumber(shown.length)}</span>
                <span>fetched {fmtNumber(totals.fetched)}</span><span>stored {fmtNumber(totals.stored)}</span>
                <span>duplicates {fmtNumber(totals.dup)}</span><span>rejected {fmtNumber(totals.rej)}</span>
                {durations.length > 0 && <span>median duration {[...durations].sort((a, b) => a - b)[Math.floor(durations.length / 2)]?.toFixed(1)} s</span>}
              </div>
              <div className={cn(runs.isFetching && "opacity-60 transition-opacity")}><RunsTable runs={shown} /></div>
            </>
          )}
          {runs.data && <Pagination page={runs.data.meta.page} pageSize={runs.data.meta.page_size} total={runs.data.meta.total} totalPages={runs.data.meta.total_pages} onPage={(p) => set({ page: p })} onPageSize={(s) => set({ page_size: s })} />}
          <p className="text-[11px] text-muted-foreground">
            “Rejected” counts items a source returned that failed validation or the 24 h freshness window (e.g. older job postings); they are not errors. <StatusBadge status="PARTIAL" /> = data stored but some requests failed.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
