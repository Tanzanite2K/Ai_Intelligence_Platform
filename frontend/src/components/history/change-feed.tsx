"use client";

import Link from "next/link";
import { Database, FilePlus2, FileX2, GitCommitHorizontal, RefreshCcw } from "lucide-react";
import { useBatches, useChanges, type Change } from "@/lib/api/history";
import { TYPE_LABEL, TYPE_ORDER, fmtDate, fmtDateTime, fmtNumber } from "@/lib/format";
import { useUrlState } from "@/lib/url-state";
import { cn } from "@/lib/utils";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorState, LimitationNote, ListSkeleton } from "@/components/common/states";
import { IntelHeader } from "@/components/intelligence/common";

type KindInfo = { label: string; tone: BadgeProps["tone"]; icon: typeof FilePlus2 };
const MODIFIED: KindInfo = { label: "Modified", tone: "info", icon: GitCommitHorizontal };
const KIND: Record<string, KindInfo> = {
  new: { label: "New", tone: "success", icon: FilePlus2 },
  modified: MODIFIED,
  disappeared: { label: "Disappeared", tone: "danger", icon: FileX2 },
  reappeared: { label: "Reappeared", tone: "warning", icon: RefreshCcw },
};

function Kpi({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card className="px-4 py-3"><div className="text-xs text-muted-foreground">{label}</div><div className="tabular text-xl font-semibold">{value}</div>{hint && <div className="text-[11px] text-muted-foreground">{hint}</div>}</Card>
  );
}

function ChangeRow({ c }: { c: Change }) {
  const k = KIND[c.kind] ?? MODIFIED;
  const Icon = k.icon;
  return (
    <li className="flex items-start gap-3 py-2.5">
      <Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Badge tone={k.tone}>{k.label}</Badge><Badge tone="neutral">{TYPE_LABEL[c.type] ?? c.type}</Badge>
          <span className="text-muted-foreground">{c.at ? fmtDateTime(c.at) : ""}{c.source ? ` · ${c.source}` : ""}{c.trigger ? ` · ${c.trigger}` : ""}</span>
        </div>
        {c.record_id ? <Link href={`/${c.type}/${c.record_id}?tab=history`} className="mt-0.5 block truncate text-sm hover:text-accent">{c.name ?? `Record #${c.record_id}`}</Link>
          : <span className="mt-0.5 block text-sm text-muted-foreground">Record no longer stored</span>}
        {c.changed_fields && c.changed_fields.length > 0 && <div className="text-xs text-muted-foreground">v{c.version}: {c.changed_fields.join(", ")}</div>}
      </div>
    </li>
  );
}

export function ChangeFeed() {
  const { get, set } = useUrlState();
  const kind = get("kind"), type = get("type"), days = Number(get("days") || 90), page = Number(get("page") || 1);
  const { data, isPending, isError, error, refetch, isFetching } = useChanges({ kind, type, days, page, page_size: 25 });
  const batches = useBatches();
  const cov = data?.coverage;
  const pages = data ? Math.max(1, Math.ceil(data.meta.total / 25)) : 1;
  const chip = (active: boolean) => cn("rounded-full border px-2.5 py-0.5 text-xs", active ? "border-accent/50 bg-accent-soft text-accent" : "border-border text-muted-foreground hover:text-foreground");
  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-4">
      <IntelHeader title="Intelligence Change Feed" description="Records that FrontierAtlas saw appear, change, disappear or return between collection batches. The baseline snapshot is never reported as a change." />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Kpi label="Baseline snapshot" value={cov?.baseline_date ? fmtDate(cov.baseline_date) : "—"} hint={cov?.observations ? `${fmtNumber(cov.observations.baseline_backfill ?? 0)} records` : undefined} />
        <Kpi label="Live collection batches" value={fmtNumber(cov?.live_batches ?? 0)} hint={`${cov?.live_collection_days ?? 0} collection day(s)`} />
        <Kpi label="Changes in view" value={fmtNumber(data?.meta.total ?? 0)} hint={`last ${days} days`} />
        <Kpi label="Emerging-trend labels" value={cov?.emerging_labels_enabled ? "Enabled" : "Not yet"} hint={`need ${cov?.emerging_min_days ?? 30} days of live collection`} />
      </div>
      <Card>
        <CardHeader className="pb-2"><div><CardTitle>Changes</CardTitle><CardDescription>Newest first · open a record to see its full history</CardDescription></div></CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Filters">
            <button className={chip(!kind)} onClick={() => set({ kind: null })}>All kinds</button>
            {Object.entries(KIND).map(([k, v]) => <button key={k} className={chip(kind === k)} onClick={() => set({ kind: k })}>{v.label}{data?.meta.by_kind?.[k] ? ` ${data.meta.by_kind[k]}` : ""}</button>)}
            <span className="mx-1 h-4 w-px bg-border" />
            <button className={chip(!type)} onClick={() => set({ type: null })}>All types</button>
            {TYPE_ORDER.map((t) => <button key={t} className={chip(type === t)} onClick={() => set({ type: t })}>{TYPE_LABEL[t]}</button>)}
            <span className="mx-1 h-4 w-px bg-border" />
            {[7, 30, 90, 365].map((d) => <button key={d} className={chip(days === d)} onClick={() => set({ days: d === 90 ? null : d })}>{d}d</button>)}
          </div>
          {isError ? <ErrorState error={error} onRetry={() => void refetch()} /> : isPending ? <ListSkeleton rows={6} /> : data.data.length === 0 ? (
            <EmptyState title="No changes recorded yet">{data.note ?? "No change matches these filters."}</EmptyState>
          ) : (
            <>
              <ul className={cn("divide-y divide-border/60", isFetching && "opacity-60")} data-testid="change-list">{data.data.map((c, i) => <ChangeRow key={`${c.batch_id}-${c.record_id}-${c.kind}-${i}`} c={c} />)}</ul>
              {pages > 1 && (
                <div className="flex items-center justify-end gap-2 text-xs">
                  <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => set({ page: page - 1 })}>Previous</Button>
                  <span className="tabular">Page {page} of {pages}</span>
                  <Button variant="outline" size="sm" disabled={page >= pages} onClick={() => set({ page: page + 1 })}>Next</Button>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="pb-2"><div><CardTitle className="flex items-center gap-1.5"><Database className="size-4" /> Collection batches</CardTitle><CardDescription>What each batch recorded</CardDescription></div></CardHeader>
        <CardContent>
          {batches.isError ? <ErrorState error={batches.error} onRetry={() => void batches.refetch()} /> : batches.isPending ? <ListSkeleton rows={2} /> : batches.data.data.length === 0 ? <EmptyState title="No batches" /> : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs" data-testid="batch-table">
                <thead className="text-muted-foreground"><tr>{["Batch", "Trigger", "Slot", "Status", "Observed", "New", "Modified", "Disappeared", "Metric samples"].map((h) => <th key={h} className="py-1.5 pr-3 font-medium">{h}</th>)}</tr></thead>
                <tbody className="divide-y divide-border/60">
                  {batches.data.data.map((b) => (
                    <tr key={b.batch_id}>
                      <td className="py-1.5 pr-3 font-mono text-[10px]">{b.batch_id.slice(0, 8)}</td><td className="py-1.5 pr-3">{b.trigger}</td>
                      <td className="tabular py-1.5 pr-3">{b.scheduled_for ? fmtDate(b.scheduled_for) : "—"}</td>
                      <td className="py-1.5 pr-3"><Badge tone={b.status === "completed" ? "success" : b.status === "running" ? "info" : "warning"}>{b.status}</Badge></td>
                      {[b.observations, b.new, b.modified, b.disappeared, b.metric_samples].map((n, i) => <td key={i} className="tabular py-1.5 pr-3">{fmtNumber(n)}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
              {batches.data.data.some((b) => b.trigger === "baseline_backfill") && (
                <p className="mt-2 text-[11px] text-muted-foreground">The baseline batch copied the records stored on 2026-11-21; it is not a reconstruction of earlier history.</p>
              )}
            </div>
          )}
        </CardContent>
      </Card>
      {cov && !cov.emerging_labels_enabled && <LimitationNote>Snapshot-based emerging-trend labels stay off until {cov.emerging_min_days ?? 30} days of reliable live collection exist ({cov.live_collection_days ?? 0} so far).</LimitationNote>}
    </div>
  );
}
