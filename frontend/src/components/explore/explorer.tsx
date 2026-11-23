"use client";

import { Filter, LayoutGrid, List, RotateCcw } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import type { Query } from "@/lib/api/client";
import { useRecordList, useSourcesByType } from "@/lib/api/hooks";
import type { RecordSummary, TypeSlug } from "@/lib/api/schemas";
import { fmtNumber } from "@/lib/format";
import { useUrlState } from "@/lib/url-state";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/common/states";
import { Field, Pagination, PAGE_SIZES, SearchInput, Select, TextFilter, inputCls } from "./controls";
import { RecordRow, StartupCard } from "./record-items";
import { NewsTagOptions, NewsTagSummary } from "./news-tags";

export type FilterKey =
  | "source" | "category" | "location" | "organisation" | "pricing_model" | "remote"
  | "min_stars" | "date_from" | "date_to" | "fresh_hours" | "verified_dates" | "tagged";

export interface ExplorerConfig {
  slug: TypeSlug;
  title: string;
  description: string;
  searchPlaceholder: string;
  filters: FilterKey[];
  /** Per-type labels for the generic `category` / `organisation` filters (they map to different stored fields). */
  labels?: Partial<Record<FilterKey, { label: string; placeholder?: string; hint?: string }>>;
  sorts: { value: string; label: string }[];
  grid?: boolean;
  note?: string;
}

const API_KEYS: FilterKey[] = ["source", "category", "location", "organisation", "pricing_model", "remote", "min_stars", "date_from", "date_to", "fresh_hours", "verified_dates", "tagged"];

/** URL state -> API query (dates widened to whole days in UTC). */
export function toApiQuery(all: Record<string, string>, keys: string[]): Query {
  const q: Query = {};
  for (const k of keys) {
    const v = all[k];
    if (!v) continue;
    if (k === "date_from") q[k] = `${v}T00:00:00Z`;
    else if (k === "date_to") q[k] = `${v}T23:59:59Z`;
    else q[k] = v;
  }
  return q;
}

export function pageParams(all: Record<string, string>): { page: number; page_size: number } {
  const page = Math.max(1, Number.parseInt(all.page ?? "1", 10) || 1);
  const ps = Number.parseInt(all.page_size ?? "", 10);
  return { page, page_size: PAGE_SIZES.includes(ps) ? ps : 24 };
}

function FilterControls({ cfg, get, set }: { cfg: ExplorerConfig; get: (k: string) => string; set: (p: Record<string, string | null>) => void }) {
  const sources = useSourcesByType();
  const sourceOptions = useMemo(
    () => [...new Set((sources.data ?? []).filter((s) => s.type === cfg.slug).map((s) => s.source))].sort().map((s) => ({ value: s, label: s })),
    [sources.data, cfg.slug],
  );
  const L = (k: FilterKey, label: string) => cfg.labels?.[k]?.label ?? label;
  const P = (k: FilterKey) => cfg.labels?.[k]?.placeholder;
  const H = (k: FilterKey) => cfg.labels?.[k]?.hint;
  const id = (k: string) => `${cfg.slug}-${k}`;
  return (
    <div className="flex flex-col gap-3.5">
      {cfg.filters.includes("source") && (
        <Field label={L("source", "Source")} htmlFor={id("source")}>
          <Select id={id("source")} value={get("source")} onChange={(v) => set({ source: v })} options={sourceOptions} placeholder="All sources" />
        </Field>
      )}
      {cfg.filters.includes("category") && (
        <Field label={L("category", "Category")} htmlFor={id("category")} hint={H("category")}>
          <TextFilter id={id("category")} value={get("category")} onCommit={(v) => set({ category: v })} placeholder={P("category")} />
        </Field>
      )}
      {cfg.filters.includes("organisation") && (
        <Field label={L("organisation", "Organisation")} htmlFor={id("organisation")} hint={H("organisation")}>
          <TextFilter id={id("organisation")} value={get("organisation")} onCommit={(v) => set({ organisation: v })} placeholder={P("organisation")}
            list={cfg.slug === "news" ? `${id("organisation")}-tags` : undefined} />
          {cfg.slug === "news" && <NewsTagOptions id={`${id("organisation")}-tags`} />}
        </Field>
      )}
      {cfg.filters.includes("tagged") && (
        <Field label="Organisation tags" htmlFor={id("tagged")}>
          <Select id={id("tagged")} value={get("tagged")} onChange={(v) => set({ tagged: v })} placeholder="All articles"
            options={[{ value: "true", label: "Tagged articles" }, { value: "false", label: "Untagged articles" }]} />
        </Field>
      )}
      {cfg.filters.includes("location") && (
        <Field label="Location" htmlFor={id("location")}>
          <TextFilter id={id("location")} value={get("location")} onCommit={(v) => set({ location: v })} placeholder="e.g. London" />
        </Field>
      )}
      {cfg.filters.includes("pricing_model") && (
        <Field label="Pricing model" htmlFor={id("pricing")} hint="FREE = open-weight licence tag on the Hub; other values only with verified text evidence">
          <Select id={id("pricing")} value={get("pricing_model")} onChange={(v) => set({ pricing_model: v })} placeholder="Any"
            options={["FREE", "FREEMIUM", "PAID", "ENTERPRISE"].map((p) => ({ value: p, label: p.charAt(0) + p.slice(1).toLowerCase() }))} />
        </Field>
      )}
      {cfg.filters.includes("remote") && (
        <Field label="Work arrangement" htmlFor={id("remote")}>
          <Select id={id("remote")} value={get("remote")} onChange={(v) => set({ remote: v })} placeholder="Any"
            options={[{ value: "true", label: "Remote" }, { value: "false", label: "Not marked remote" }]} />
        </Field>
      )}
      {cfg.filters.includes("min_stars") && (
        <Field label="Minimum GitHub stars" htmlFor={id("stars")} hint="Live counts from the GitHub API">
          <TextFilter id={id("stars")} value={get("min_stars")} onCommit={(v) => set({ min_stars: /^\d+$/.test(v) ? v : null })} placeholder="e.g. 100" />
        </Field>
      )}
      {cfg.filters.includes("fresh_hours") && (
        <Field label="Published within" htmlFor={id("fresh")}>
          <Select id={id("fresh")} value={get("fresh_hours")} onChange={(v) => set({ fresh_hours: v })} placeholder="Any time"
            options={[{ value: "24", label: "Last 24 hours" }, { value: "168", label: "Last 7 days" }, { value: "720", label: "Last 30 days" }, { value: "8760", label: "Last 12 months" }]} />
        </Field>
      )}
      {cfg.filters.includes("date_from") && (
        <div className="grid grid-cols-2 gap-2">
          <Field label={L("date_from", "From")} htmlFor={id("from")}>
            <input id={id("from")} type="date" value={get("date_from")} max={get("date_to") || undefined} onChange={(e) => set({ date_from: e.target.value })} className={inputCls} />
          </Field>
          <Field label="To" htmlFor={id("to")}>
            <input id={id("to")} type="date" value={get("date_to")} min={get("date_from") || undefined} onChange={(e) => set({ date_to: e.target.value })} className={inputCls} />
          </Field>
        </div>
      )}
      {cfg.filters.includes("verified_dates") && (
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={get("verified_dates") === "true"} onChange={(e) => set({ verified_dates: e.target.checked ? "true" : null })} className="size-4 accent-[var(--accent)]" />
          Verified publication dates only
        </label>
      )}
    </div>
  );
}

export function ListSkeletonRows({ rows = 8 }: { rows?: number }) {
  return (
    <ul aria-busy="true" aria-label="Loading results">
      {Array.from({ length: rows }, (_, i) => (
        <li key={i} className="flex flex-col gap-2 border-b border-border/60 px-4 py-3.5 last:border-0">
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-3 w-1/3" />
        </li>
      ))}
    </ul>
  );
}

export function RecordExplorer({ cfg }: { cfg: ExplorerConfig }) {
  const { get, all, set, reset } = useUrlState();
  const [filtersOpen, setFiltersOpen] = useState(false);
  const view = cfg.grid && get("view") !== "list" ? "grid" : "list";
  const sort = cfg.sorts.some((s) => s.value === get("sort")) ? get("sort") : (cfg.sorts[0]?.value ?? "newest");
  const { page, page_size } = pageParams(all);
  const query: Query = { ...toApiQuery(all, ["q", ...API_KEYS.filter((k) => cfg.filters.includes(k) || (k === "date_to" && cfg.filters.includes("date_from")))]), sort, page, page_size };
  const { data, isPending, isError, error, isFetching, refetch } = useRecordList(cfg.slug, query);
  const active = API_KEYS.filter((k) => get(k)).length + (get("q") ? 1 : 0);
  const onQ = useCallback((v: string) => set({ q: v }), [set]);
  const rows: RecordSummary[] = data?.data ?? [];

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">{cfg.title}</h1>
          <p className="text-sm text-muted-foreground">{cfg.description}</p>
        </div>
        <span className="tabular text-sm text-muted-foreground" aria-live="polite">
          {data ? `${fmtNumber(data.meta.total)} ${data.meta.total === 1 ? "result" : "results"}` : ""}
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="min-w-[220px] flex-1">
          <SearchInput value={get("q")} onChange={onQ} placeholder={cfg.searchPlaceholder} busy={isFetching} />
        </div>
        <label className="sr-only" htmlFor={`${cfg.slug}-sort`}>Sort by</label>
        <select id={`${cfg.slug}-sort`} value={sort} onChange={(e) => set({ sort: e.target.value })} className={cn(inputCls, "w-auto cursor-pointer")}>
          {cfg.sorts.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
        </select>
        {cfg.grid && (
          <div className="flex rounded-md border border-border p-0.5" role="group" aria-label="View">
            <Button variant="ghost" size="sm" aria-pressed={view === "grid"} aria-label="Grid view" onClick={() => set({ view: null, page })} className={cn(view === "grid" && "bg-surface-2 text-foreground")}>
              <LayoutGrid />
            </Button>
            <Button variant="ghost" size="sm" aria-pressed={view === "list"} aria-label="List view" onClick={() => set({ view: "list", page })} className={cn(view === "list" && "bg-surface-2 text-foreground")}>
              <List />
            </Button>
          </div>
        )}
        <Button variant="outline" size="sm" className="lg:hidden" onClick={() => setFiltersOpen((o) => !o)} aria-expanded={filtersOpen}>
          <Filter /> Filters{active ? ` (${active})` : ""}
        </Button>
      </div>

      <div className="grid gap-4 lg:grid-cols-[240px_1fr]">
        <aside className={cn(filtersOpen ? "block" : "hidden", "lg:block")} aria-label="Filters">
          <Card className="p-4">
            <div className="mb-3 flex items-center justify-between">
              <span className="text-sm font-semibold">Filters</span>
              {active > 0 && (
                <Button variant="ghost" size="sm" onClick={reset}>
                  <RotateCcw /> Reset
                </Button>
              )}
            </div>
            <FilterControls cfg={cfg} get={get} set={set} />
            {cfg.slug === "news" && <NewsTagSummary />}
            {cfg.note && <p className="mt-4 border-t border-border pt-3 text-[11px] text-muted-foreground">{cfg.note}</p>}
          </Card>
        </aside>

        <section aria-label="Results" className="flex min-w-0 flex-col gap-3">
          {isError ? (
            <Card>
              <ErrorState error={error} onRetry={() => void refetch()} />
              {active > 0 && (
                <div className="-mt-4 pb-6 text-center">
                  <Button variant="ghost" size="sm" onClick={reset}><RotateCcw /> Reset filters</Button>
                </div>
              )}
            </Card>
          ) : isPending ? (
            <Card><ListSkeletonRows /></Card>
          ) : rows.length === 0 ? (
            <Card>
              <EmptyState title={data && data.meta.total > 0 ? "No results on this page" : "No records match these filters"}>
                {active > 0 ? (
                  <Button variant="ghost" size="sm" onClick={reset} className="mt-1"><RotateCcw /> Clear all filters</Button>
                ) : "Nothing is stored for this category yet."}
              </EmptyState>
            </Card>
          ) : view === "grid" ? (
            <div className={cn("grid gap-3 sm:grid-cols-2 xl:grid-cols-3", isFetching && "opacity-60 transition-opacity")}>
              {rows.map((r) => <StartupCard key={r.id} r={r} />)}
            </div>
          ) : (
            <Card className={cn(isFetching && "opacity-60 transition-opacity")}>
              <ul>{rows.map((r) => <RecordRow key={r.id} r={r} />)}</ul>
            </Card>
          )}
          {data && (
            <Pagination
              page={data.meta.page}
              pageSize={data.meta.page_size}
              total={data.meta.total}
              totalPages={data.meta.total_pages}
              onPage={(p) => {
                set({ page: p });
                window.scrollTo({ top: 0, behavior: "smooth" });
              }}
              onPageSize={(s) => set({ page_size: s })}
            />
          )}
        </section>
      </div>
    </div>
  );
}
