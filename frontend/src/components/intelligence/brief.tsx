"use client";

import Link from "next/link";
import { ArrowRight, CalendarRange, FileSearch } from "lucide-react";
import { useIntelBrief } from "@/lib/api/hooks";
import type { Insight } from "@/lib/api/schemas";
import { entityHref } from "@/lib/entities";
import { TYPE_LABEL, fmtDate, fmtDateTime, fmtNumber } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorState, ListSkeleton } from "@/components/common/states";
import { ConfidenceBadge, CoverageNotes, IntelHeader, fmtShare, recordLink } from "./common";

function metricValue(label: string, v: number | null): string {
  if (v === null) return "—";
  if (/ratio/i.test(label)) return `${v.toFixed(2)}×`;
  if (label === "z") return v.toFixed(2);
  if (/share/i.test(label)) return fmtShare(v);
  return fmtNumber(v);
}

function period(p: Insight["period"]) {
  if (!p.from && !p.to) return "No dated evidence";
  return p.from === p.to ? fmtDate(p.from) : `${fmtDate(p.from)} – ${fmtDate(p.to)}`;
}

function InsightCard({ i }: { i: Insight }) {
  return (
    <Card data-testid="insight" data-insight={i.id}>
      <CardHeader className="pb-2">
        <div className="min-w-0">
          <CardTitle className="text-sm leading-snug">{i.title}</CardTitle>
          <CardDescription className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="inline-flex items-center gap-1"><CalendarRange className="size-3.5" />{period(i.period)}</span>
            <span className="text-muted-foreground/80">{i.period.basis}</span>
          </CardDescription>
        </div>
        <ConfidenceBadge level={i.confidence.level} explanation={i.confidence.explanation} />
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="text-sm leading-relaxed break-words" data-testid="observation">{i.observation}</p>
        {i.metrics.length > 0 && (
          <dl className="flex flex-wrap gap-1.5">
            {i.metrics.map((m) => (
              <div key={m.label} className="rounded-md border border-border bg-surface-2/60 px-2 py-1 text-xs">
                <dt className="text-muted-foreground">{m.label}</dt>
                <dd className="tabular font-medium">{metricValue(m.label, m.value)}</dd>
              </div>
            ))}
          </dl>
        )}
        {(i.organisations.length > 0 || i.topics.length > 0) && (
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            {i.topics.map((t) => (
              <Link key={t.id} href={`/intelligence/trends/${t.id}`} className="rounded-md bg-accent-soft px-1.5 py-0.5 font-medium text-accent hover:underline">{t.label}</Link>
            ))}
            {/* An organisation can legitimately appear more than once (e.g. it leads two topics), so the key includes its position. */}
            {i.organisations.map((o, idx) => (
              <Link key={`${o.id}-${idx}`} href={entityHref(o.id, true)} className="rounded-md border border-border px-1.5 py-0.5 hover:border-border-strong hover:underline">{o.name}</Link>
            ))}
          </div>
        )}
        <div className="rounded-md border border-border/70 p-2.5">
          <div className="mb-1.5 flex items-center justify-between gap-2 text-xs">
            <span className="inline-flex items-center gap-1 font-medium"><FileSearch className="size-3.5" /> Supporting records ({fmtNumber(i.evidence.total_supporting)})</span>
            <Link href={i.evidence.explore_href} className="inline-flex items-center gap-0.5 text-accent hover:underline">Explore <ArrowRight className="size-3" /></Link>
          </div>
          {i.evidence.records.length ? (
            <ul className="flex flex-col gap-1" data-testid="evidence">
              {i.evidence.records.map((r) => (
                <li key={`${r.type}-${r.id}`} className="flex min-w-0 items-center gap-2 text-xs">
                  <Badge>{TYPE_LABEL[r.type]}</Badge>
                  <Link href={recordLink(r.type, r.id)} className="min-w-0 truncate hover:text-accent hover:underline" title={r.name}>{r.name}</Link>
                  {r.published && <span className="ml-auto shrink-0 text-muted-foreground">{fmtDate(r.published)}</span>}
                </li>
              ))}
            </ul>
          ) : <p className="text-xs text-muted-foreground">Aggregate measurement — see the linked page for the underlying data.</p>}
        </div>
      </CardContent>
    </Card>
  );
}

export function IntelligenceBrief() {
  const { data, isPending, isError, error, refetch } = useIntelBrief();
  return (
    <div className="mx-auto flex max-w-[1200px] flex-col gap-4">
      <IntelHeader title="Intelligence brief"
        description="Observations computed from stored records. Each one shows its counts, period, supporting records and how much weight it can bear. No predictions are made." />
      <CoverageNotes />
      {isError ? <Card><ErrorState error={error} onRetry={() => void refetch()} /></Card> : isPending ? <Card className="p-4"><ListSkeleton rows={6} /></Card> : data.data.length === 0 ? (
        <Card><EmptyState title="No insights">There are no stored records to analyse.</EmptyState></Card>
      ) : (
        <>
          <div className="grid gap-3 lg:grid-cols-2">{data.data.map((i) => <InsightCard key={i.id} i={i} />)}</div>
          <p className="text-[11px] text-muted-foreground">Computed from {fmtNumber(data.meta.records_indexed)} records · index built {fmtDateTime(data.meta.index_built_at)}</p>
        </>
      )}
    </div>
  );
}
