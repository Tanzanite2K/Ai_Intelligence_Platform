"use client";

import Link from "next/link";
import { useRelationships } from "@/lib/api/hooks";
import { TYPE_LABEL, TYPE_ORDER, fmtNumber } from "@/lib/format";
import { entityHref } from "@/lib/entities";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState, LimitationNote, ListSkeleton } from "@/components/common/states";
import { MethodBadge } from "./common";

function Kpi({ label, value, sub }: { label: string; value: number; sub?: string }) {
  return (
    <Card className="p-3.5">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="tabular text-2xl font-semibold">{fmtNumber(value)}</div>
      {sub && <div className="text-[11px] text-muted-foreground">{sub}</div>}
    </Card>
  );
}

function TypeChips({ counts }: { counts: Record<string, number> }) {
  return (
    <span className="flex flex-wrap gap-1">
      {TYPE_ORDER.filter((t) => counts[t]).map((t) => <Badge key={t}>{TYPE_LABEL[t]} {fmtNumber(counts[t] ?? 0)}</Badge>)}
    </span>
  );
}

export function RelationshipOverview() {
  const { data, isPending, isError, error, refetch } = useRelationships();
  if (isError) return <Card><ErrorState error={error} onRetry={() => void refetch()} /></Card>;
  if (isPending) return <Card className="p-4"><ListSkeleton rows={8} /></Card>;
  const d = data.data;
  const maxMethod = Math.max(...d.match_methods.map((m) => m.mappings), 1);
  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Entity relationships</h1>
        <p className="text-sm text-muted-foreground">What entity resolution actually connects in the stored data. Record links use verified mappings only.</p>
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Kpi label="Organisations" value={d.organisations} sub={`${fmtNumber(d.seed_organisations)} curated seed entries`} />
        <Kpi label="With linked records" value={d.organisations_with_records} />
        <Kpi label="Linked to ≥ 2 record types" value={d.multi_type_organisations.total} />
        <Kpi label="Merged name variants" value={d.alias_merges.total} sub="organisations with >1 source name" />
        <Kpi label="Pending reviews" value={d.pending_reviews} />
        <Kpi label="Mappings without a record" value={d.unmatched_mappings} sub="source URL not found among records" />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><div><CardTitle>How names were matched</CardTitle><CardDescription>Stored match method and the confidence range recorded for it</CardDescription></div></CardHeader>
          <CardContent className="flex flex-col gap-2.5">
            {d.match_methods.map((m) => (
              <div key={m.method} className="flex flex-col gap-1">
                <div className="flex items-center justify-between text-sm">
                  <MethodBadge method={m.method} />
                  <span className="tabular text-xs text-muted-foreground">
                    {fmtNumber(m.mappings)} · confidence {m.min_confidence === m.max_confidence ? m.min_confidence : `${m.min_confidence}–${m.max_confidence}`}
                  </span>
                </div>
                <div className="h-1.5 rounded-full bg-surface-2"><div className="h-1.5 rounded-full bg-accent" style={{ width: `${(m.mappings / maxMethod) * 100}%` }} /></div>
              </div>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><div><CardTitle>Organisations by number of linked record types</CardTitle><CardDescription>e.g. an organisation with both products and jobs counts as 2</CardDescription></div></CardHeader>
          <CardContent className="flex flex-col gap-2">
            {Object.entries(d.organisations_by_linked_types).map(([k, v]) => (
              <div key={k} className="flex items-center justify-between text-sm"><span>{k} type{k === "1" ? "" : "s"}</span><span className="tabular text-muted-foreground">{fmtNumber(v)} organisations</span></div>
            ))}
            <div className="mt-2 flex flex-col gap-1.5">
              {Object.values(data.unavailable).map((u) => <LimitationNote key={u}>{u}</LimitationNote>)}
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader><div><CardTitle>Most connected organisations</CardTitle><CardDescription>Linked to the most record types, then records</CardDescription></div></CardHeader>
          <CardContent><ul className="flex flex-col gap-2">{d.multi_type_organisations.top.map((o) => (
            <li key={o.id} className="flex flex-col gap-0.5"><Link href={entityHref(o.id, true)} className="text-sm font-medium hover:text-accent">{o.name}</Link><TypeChips counts={o.records_by_type} /></li>
          ))}</ul></CardContent>
        </Card>
        <Card>
          <CardHeader><div><CardTitle>Most linked records</CardTitle><CardDescription>Verified records per organisation</CardDescription></div></CardHeader>
          <CardContent><ul className="flex flex-col gap-2">{d.top_organisations.map((o) => (
            <li key={o.id} className="flex items-start justify-between gap-2"><span className="flex flex-col gap-0.5"><Link href={entityHref(o.id, true)} className="text-sm font-medium hover:text-accent">{o.name}</Link><TypeChips counts={o.records_by_type} /></span><span className="tabular text-sm text-muted-foreground">{fmtNumber(o.records)}</span></li>
          ))}</ul></CardContent>
        </Card>
        <Card>
          <CardHeader><div><CardTitle>Merged name variants</CardTitle><CardDescription>Different source names resolved to one organisation</CardDescription></div></CardHeader>
          <CardContent><ul className="flex flex-col gap-2">{d.alias_merges.top.map((o) => (
            <li key={o.id} className="flex flex-col gap-0.5"><Link href={entityHref(o.id, true)} className="text-sm font-medium hover:text-accent">{o.name}</Link>
              <span className="flex flex-wrap gap-1">{o.original_names.map((n) => <Badge key={n}>{n}</Badge>)}</span></li>
          ))}</ul></CardContent>
        </Card>
      </div>
    </div>
  );
}
