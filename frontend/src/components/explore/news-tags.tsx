"use client";

import { useNewsFacets } from "@/lib/api/hooks";
import { fmtNumber } from "@/lib/format";
import { Skeleton } from "@/components/ui/skeleton";

/** Live tagged/untagged counts for the News explorer (from GET /api/v1/news/facets — never hardcoded). */
export function NewsTagSummary() {
  const { data, isPending, isError, refetch } = useNewsFacets();
  return (
    <div className="mt-4 border-t border-border pt-3 text-xs" aria-live="polite" data-testid="news-tag-summary">
      <div className="mb-1 font-medium">Organisation tags</div>
      {isPending ? (
        <div aria-busy="true" aria-label="Loading tag counts" className="flex flex-col gap-1.5"><Skeleton className="h-3 w-40" /><Skeleton className="h-3 w-28" /></div>
      ) : isError ? (
        <p className="text-danger">
          Tag counts unavailable.{" "}
          <button type="button" className="underline" onClick={() => void refetch()}>Retry</button>
        </p>
      ) : data.total === 0 ? (
        <p className="text-muted-foreground">No news articles are stored.</p>
      ) : (
        <p className="text-muted-foreground">
          <span className="tabular font-medium text-foreground">{fmtNumber(data.tagged)}</span> of {fmtNumber(data.total)} articles have
          validated organisation tags · <span className="tabular font-medium text-foreground">{fmtNumber(data.untagged)}</span> untagged
          {data.organisations.length > 0 && <> · {fmtNumber(data.organisations.length)} distinct organisations</>}
        </p>
      )}
    </div>
  );
}

/** Datalist suggestions: the organisation tags that actually exist on stored articles. */
export function NewsTagOptions({ id }: { id: string }) {
  const { data } = useNewsFacets();
  return (
    <datalist id={id}>
      {(data?.organisations ?? []).map((o) => (
        <option key={o.id} value={o.name}>{`${o.articles} article${o.articles === 1 ? "" : "s"}`}</option>
      ))}
    </datalist>
  );
}
