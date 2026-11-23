"use client";

import Link from "next/link";
import { Building2, RotateCcw, Search } from "lucide-react";
import { entityHref } from "@/lib/entities";
import { useCallback, useMemo } from "react";
import type { Query } from "@/lib/api/client";
import { useSearch, useSourcesByType } from "@/lib/api/hooks";
import type { TypeSlug } from "@/lib/api/schemas";
import { TYPE_LABEL, TYPE_ORDER, fmtNumber } from "@/lib/format";
import { useUrlState } from "@/lib/url-state";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Tooltip } from "@/components/ui/tooltip";
import { EmptyState, ErrorState } from "@/components/common/states";
import { Field, Pagination, SearchInput, Select, TextFilter, inputCls } from "./controls";
import { ListSkeletonRows, pageParams, toApiQuery } from "./explorer";
import { RecordRow } from "./record-items";

const FILTER_KEYS = ["source", "organisation", "date_from", "date_to", "fresh_hours"];

export function GlobalSearch() {
  const { get, all, set, reset } = useUrlState();
  const types = (get("types") || "").split(",").filter((t): t is TypeSlug => (TYPE_ORDER as readonly string[]).includes(t));
  const onlyPapers = types.length === 1 && types[0] === "papers";
  const sorts = [
    { value: "newest", label: "Newest first" },
    { value: "oldest", label: "Oldest first" },
    { value: "name", label: "Name A–Z" },
    ...(onlyPapers ? [{ value: "stars", label: "Most GitHub stars" }] : []),
  ];
  const sort = sorts.some((s) => s.value === get("sort")) ? get("sort") : "newest";
  const { page, page_size } = pageParams(all);
  const query: Query = { ...toApiQuery(all, ["q", ...FILTER_KEYS]), types: types.join(",") || undefined, sort, page, page_size };
  const { data, isPending, isError, error, isFetching, refetch } = useSearch(query);
  const sources = useSourcesByType();
  const sourceOptions = useMemo(
    () => [...new Set((sources.data ?? []).filter((s) => !types.length || types.includes(s.type as TypeSlug)).map((s) => s.source))].sort().map((s) => ({ value: s, label: s })),
    [sources.data, types],
  );
  const onQ = useCallback((v: string) => set({ q: v }), [set]);
  const active = FILTER_KEYS.filter((k) => get(k)).length + (get("q") ? 1 : 0) + (types.length ? 1 : 0);

  const toggleType = (t: TypeSlug) => {
    const next = types.includes(t) ? types.filter((x) => x !== t) : [...types, t];
    set({ types: next.join(","), sort: next.length === 1 && next[0] === "papers" ? get("sort") : get("sort") === "stars" ? null : get("sort") });
  };

  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Explore intelligence</h1>
        <p className="text-sm text-muted-foreground">One search across startups, products, research papers, jobs and news.</p>
      </div>

      <Card className="flex flex-col gap-3 p-4">
        <SearchInput value={get("q")} onChange={onQ} placeholder="Search names, descriptions, abstracts, authors, companies, tags…" busy={isFetching} />
        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Record types">
          <span className="mr-1 text-xs text-muted-foreground">In:</span>
          {TYPE_ORDER.map((t) => {
            const on = types.includes(t);
            const n = data?.counts_by_type[t] ?? 0;
            return (
              <button
                key={t}
                type="button"
                aria-pressed={on}
                onClick={() => toggleType(t)}
                className={cn(
                  "inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-xs transition-colors",
                  on ? "border-accent/50 bg-accent-soft text-accent" : "border-border text-muted-foreground hover:border-border-strong hover:text-foreground",
                )}
              >
                {TYPE_LABEL[t]}
                {data && <span className="tabular rounded-full bg-surface-2 px-1.5 text-[10px] text-muted-foreground">{fmtNumber(n)}</span>}
              </button>
            );
          })}
          {types.length > 0 && <span className="text-[11px] text-muted-foreground">({types.length} selected)</span>}
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <Field label="Source" htmlFor="g-source">
            <Select id="g-source" value={get("source")} onChange={(v) => set({ source: v })} options={sourceOptions} placeholder="All sources" />
          </Field>
          <Field label="Organisation" htmlFor="g-org">
            <TextFilter id="g-org" value={get("organisation")} onCommit={(v) => set({ organisation: v })} placeholder="Maker, employer, startup…" />
          </Field>
          <Field label="Published within" htmlFor="g-fresh">
            <Select id="g-fresh" value={get("fresh_hours")} onChange={(v) => set({ fresh_hours: v })} placeholder="Any time"
              options={[{ value: "24", label: "Last 24 hours" }, { value: "168", label: "Last 7 days" }, { value: "720", label: "Last 30 days" }]} />
          </Field>
          <Field label="From" htmlFor="g-from">
            <input id="g-from" type="date" value={get("date_from")} max={get("date_to") || undefined} onChange={(e) => set({ date_from: e.target.value })} className={inputCls} />
          </Field>
          <Field label="To" htmlFor="g-to">
            <input id="g-to" type="date" value={get("date_to")} min={get("date_from") || undefined} onChange={(e) => set({ date_to: e.target.value })} className={inputCls} />
          </Field>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3">
          <span className="tabular text-sm text-muted-foreground" aria-live="polite">
            {data ? `${fmtNumber(data.meta.total)} ${data.meta.total === 1 ? "record" : "records"}${data.query ? ` for “${data.query}”` : ""}` : "Searching…"}
          </span>
          <div className="flex items-center gap-2">
            {active > 0 && <Button variant="ghost" size="sm" onClick={reset}><RotateCcw /> Reset</Button>}
            <label className="sr-only" htmlFor="g-sort">Sort by</label>
            <select id="g-sort" value={sort} onChange={(e) => set({ sort: e.target.value })} className={cn(inputCls, "h-8 w-auto cursor-pointer")}>
              {sorts.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
          </div>
        </div>
        <p className="text-[11px] text-muted-foreground">
          Matching is case-insensitive substring search; every word must appear. Results are not relevance-ranked. Date filters use the
          publication date, or the collection date for undated records (startups, products).
        </p>
      </Card>

      {data && data.organisations.length > 0 && (
        <Card className="p-4">
          <div className="mb-2 flex items-center gap-1.5 text-sm font-semibold"><Building2 className="size-4" /> Matching organisations</div>
          <div className="flex flex-wrap gap-1.5">
            {data.organisations.map((o) => (
              <Tooltip key={o.id} content={`${fmtNumber(o.mapping_count)} entity mappings${o.needs_review ? " · includes a mapping pending human review" : ""}`}>
                <Link href={entityHref(o.id, true)}>
                  <Badge tone={o.needs_review ? "warning" : "neutral"} className="hover:border-border-strong hover:text-foreground">{o.canonical_name}{o.needs_review ? " · review" : ""}</Badge>
                </Link>
              </Tooltip>
            ))}
          </div>
        </Card>
      )}

      {isError ? (
        <Card><ErrorState error={error} onRetry={() => void refetch()} /></Card>
      ) : isPending ? (
        <Card><ListSkeletonRows /></Card>
      ) : data.data.length === 0 ? (
        <Card>
          <EmptyState title={get("q") ? `No records match “${get("q")}”` : "No records match these filters"}>
            {active > 0 ? <Button variant="ghost" size="sm" onClick={reset} className="mt-1"><RotateCcw /> Clear search and filters</Button> : null}
          </EmptyState>
        </Card>
      ) : (
        <Card className={cn(isFetching && "opacity-60 transition-opacity")}>
          <ul>{data.data.map((r) => <RecordRow key={r.id} r={r} showType />)}</ul>
        </Card>
      )}
      {data && (
        <Pagination page={data.meta.page} pageSize={data.meta.page_size} total={data.meta.total} totalPages={data.meta.total_pages}
          onPage={(p) => { set({ page: p }); window.scrollTo({ top: 0, behavior: "smooth" }); }} onPageSize={(s) => set({ page_size: s })} />
      )}
      {!get("q") && !isPending && (
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground"><Search className="size-3.5" /> Showing all records; type to search.</p>
      )}
    </div>
  );
}
