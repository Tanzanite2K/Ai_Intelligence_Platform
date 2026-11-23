"use client";

import Link from "next/link";
import { Globe, RotateCcw } from "lucide-react";
import { useCallback } from "react";
import { useEntities } from "@/lib/api/hooks";
import { fmtNumber } from "@/lib/format";
import { useUrlState } from "@/lib/url-state";
import { entityHref } from "@/lib/entities";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState, ErrorState } from "@/components/common/states";
import { Field, Pagination, SearchInput, Select, inputCls } from "@/components/explore/controls";
import { ListSkeletonRows, pageParams } from "@/components/explore/explorer";
import { MethodBadge, OriginBadge, ReviewBadge } from "./common";

export function OrganisationExplorer() {
  const { get, all, set, reset } = useUrlState();
  const sort = get("sort") === "name" ? "name" : "records";
  const { page, page_size } = pageParams(all);
  const origin = ["SEED", "DISCOVERED"].includes(get("origin")) ? get("origin") : "";
  const review = ["true", "false"].includes(get("needs_review")) ? get("needs_review") : "";
  const { data, isPending, isError, error, isFetching, refetch } = useEntities({ q: get("q") || undefined, origin: origin || undefined, needs_review: review || undefined, sort, page, page_size });
  const onQ = useCallback((v: string) => set({ q: v }), [set]);
  const active = ["q", "origin", "needs_review"].filter((k) => get(k)).length;

  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Organisations</h1>
          <p className="text-sm text-muted-foreground">Canonical organisations produced by entity resolution, with the records linked to each.</p>
        </div>
        <span className="tabular text-sm text-muted-foreground" aria-live="polite">{data ? `${fmtNumber(data.meta.total)} organisations` : ""}</span>
      </div>

      <Card className="grid gap-3 p-4 md:grid-cols-[1fr_180px_180px_180px] md:items-end">
        <Field label="Search" htmlFor="org-q">
          <SearchInput value={get("q")} onChange={onQ} placeholder="Canonical or original (raw) name…" busy={isFetching} />
        </Field>
        <Field label="Origin" htmlFor="org-origin">
          <Select id="org-origin" value={origin} onChange={(v) => set({ origin: v })} placeholder="All"
            options={[{ value: "SEED", label: "Curated seed list" }, { value: "DISCOVERED", label: "Discovered" }]} />
        </Field>
        <Field label="Review status" htmlFor="org-review">
          <Select id="org-review" value={review} onChange={(v) => set({ needs_review: v })} placeholder="Any"
            options={[{ value: "true", label: "Has pending review" }, { value: "false", label: "No pending review" }]} />
        </Field>
        <Field label="Sort" htmlFor="org-sort">
          <select id="org-sort" value={sort} onChange={(e) => set({ sort: e.target.value === "records" ? null : e.target.value })} className={cn(inputCls, "cursor-pointer")}>
            <option value="records">Most linked records</option>
            <option value="name">Name A–Z</option>
          </select>
        </Field>
      </Card>

      {isError ? (
        <Card><ErrorState error={error} onRetry={() => void refetch()} /></Card>
      ) : isPending ? (
        <Card><ListSkeletonRows /></Card>
      ) : data.data.length === 0 ? (
        <Card>
          <EmptyState title="No organisations match">
            {active > 0 && <Button variant="ghost" size="sm" onClick={reset} className="mt-1"><RotateCcw /> Clear filters</Button>}
          </EmptyState>
        </Card>
      ) : (
        <Card className={cn("overflow-x-auto", isFetching && "opacity-60 transition-opacity")}>
          <table className="w-full min-w-[760px] text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-foreground">
                <th scope="col" className="px-4 py-2.5 font-medium">Organisation</th>
                <th scope="col" className="px-3 py-2.5 font-medium">Origin</th>
                <th scope="col" className="px-3 py-2.5 text-right font-medium">Verified records</th>
                <th scope="col" className="px-3 py-2.5 text-right font-medium">Mappings</th>
                <th scope="col" className="px-3 py-2.5 font-medium">How names were matched</th>
              </tr>
            </thead>
            <tbody>
              {data.data.map((e) => (
                <tr key={e.id} className="group relative border-b border-border/60 last:border-0 hover:bg-surface-2/50">
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-1.5">
                      <Link href={entityHref(e.id, true)} className="font-medium after:absolute after:inset-0 hover:text-accent">{e.canonical_name}</Link>
                      {e.needs_review && <span className="relative z-10"><ReviewBadge /></span>}
                    </div>
                    <div className="flex flex-wrap gap-x-3 text-xs text-muted-foreground">
                      {e.domain && <span className="inline-flex items-center gap-1"><Globe className="size-3" />{e.domain}</span>}
                      {e.aliases.length > 0 && <span>{e.aliases.length} alias{e.aliases.length > 1 ? "es" : ""}</span>}
                    </div>
                  </td>
                  <td className="px-3 py-2.5"><span className="relative z-10"><OriginBadge origin={e.origin} /></span></td>
                  <td className="tabular px-3 py-2.5 text-right">
                    {fmtNumber(e.record_count)}
                    {e.review_record_count > 0 && <div className="text-[11px] text-warning">+{fmtNumber(e.review_record_count)} pending review</div>}
                  </td>
                  <td className="tabular px-3 py-2.5 text-right text-muted-foreground">{fmtNumber(e.mapping_count)}</td>
                  <td className="px-3 py-2.5">
                    <div className="relative z-10 flex flex-wrap gap-1">
                      {Object.entries(e.match_methods).sort((a, b) => b[1] - a[1]).map(([m, n]) => <MethodBadge key={m} method={m} count={n} />)}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      {data && (
        <Pagination page={data.meta.page} pageSize={data.meta.page_size} total={data.meta.total} totalPages={data.meta.total_pages}
          onPage={(p) => { set({ page: p }); window.scrollTo({ top: 0, behavior: "smooth" }); }} onPageSize={(s) => set({ page_size: s })} />
      )}
      <p className="text-[11px] text-muted-foreground">
        Verified records are linked through confirmed mappings only. Organisation descriptions are not stored by the pipeline.
      </p>
    </div>
  );
}
