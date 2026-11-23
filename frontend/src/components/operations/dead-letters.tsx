"use client";

import { ExternalLink, RotateCcw } from "lucide-react";
import { useDeadLetters } from "@/lib/api/hooks";
import { fmtDateTime, fmtNumber } from "@/lib/format";
import { useUrlState } from "@/lib/url-state";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState, ErrorState, ListSkeleton } from "@/components/common/states";
import { Field, Pagination, TextFilter } from "@/components/explore/controls";
import { pageParams } from "@/components/explore/explorer";

export function DeadLetters() {
  const { get, all, set, reset } = useUrlState();
  const { page, page_size } = pageParams(all);
  const stage = get("stage");
  const { data, isPending, isError, error, isFetching, refetch } = useDeadLetters({ stage: stage || undefined, page, page_size });
  const stages = [...new Set((data?.data ?? []).map((d) => d.stage))];
  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Dead letter queue</h1>
          <p className="text-sm text-muted-foreground">Items that failed processing after retries and were set aside instead of being dropped.</p>
        </div>
        <span className="tabular text-sm text-muted-foreground" aria-live="polite">{data ? `${fmtNumber(data.meta.total)} items` : ""}</span>
      </div>
      <Card className="flex flex-wrap items-end gap-3 p-4">
        <div className="w-full sm:w-72">
          <Field label="Stage (exact)" htmlFor="dl-stage">
            <TextFilter id="dl-stage" value={stage} onCommit={(v) => set({ stage: v })} placeholder="e.g. llm.news_entities" />
          </Field>
        </div>
        {stages.length > 0 && !stage && (
          <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
            Stages on this page:
            {stages.map((s) => <button key={s} type="button" onClick={() => set({ stage: s })} className="rounded-md border border-border px-1.5 py-0.5 hover:text-foreground">{s}</button>)}
          </div>
        )}
        {stage && <Button variant="ghost" size="sm" onClick={reset}><RotateCcw /> Reset</Button>}
        <p className="w-full text-[11px] text-muted-foreground">
          Retrying is not available from this interface. Dead-lettered news items are re-processed by the command-line enrichment job (<code>python -m src.main enrich --news</code>), which must be run deliberately.
        </p>
      </Card>
      {isError ? <Card><ErrorState error={error} onRetry={() => void refetch()} /></Card> : isPending ? <Card className="p-4"><ListSkeleton rows={6} /></Card> : data.data.length === 0 ? (
        <Card><EmptyState title={stage ? `No dead letters for stage “${stage}”` : "The dead letter queue is empty"} /></Card>
      ) : (
        <ul className={cn("flex flex-col gap-3", isFetching && "opacity-60 transition-opacity")}>
          {data.data.map((d) => (
            <li key={d.id}>
              <Card className="flex flex-col gap-2 p-4">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <Badge tone="danger">#{d.id}</Badge>
                  <code className="rounded bg-surface-2 px-1.5 py-0.5">{d.stage}</code>
                  <span className="text-muted-foreground">{fmtDateTime(d.created_at)} · {d.attempts} attempt{d.attempts === 1 ? "" : "s"}</span>
                  <Badge>{d.retry_supported ? "retry available" : "no automatic retry"}</Badge>
                </div>
                {d.key && (/^https?:\/\//.test(d.key) ? (
                  <a href={d.key} target="_blank" rel="noopener noreferrer" className="inline-flex max-w-full items-center gap-1 text-sm text-accent hover:underline">
                    <span className="truncate">{d.key}</span><ExternalLink className="size-3 shrink-0" />
                  </a>
                ) : <div className="text-sm break-all">{d.key}</div>)}
                <details className="group">
                  <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
                    <span className="line-clamp-2 text-danger group-open:hidden">{d.error}</span>
                    <span className="hidden group-open:inline">Hide details</span>
                  </summary>
                  <div className="mt-2 flex flex-col gap-2">
                    <div><div className="mb-1 text-[11px] font-semibold text-muted-foreground uppercase">Failure reason</div><pre className="max-h-64 overflow-auto rounded-md bg-surface-2 p-2 text-xs whitespace-pre-wrap text-danger">{d.error}</pre></div>
                    <div><div className="mb-1 text-[11px] font-semibold text-muted-foreground uppercase">Payload (preview)</div><pre className="max-h-48 overflow-auto rounded-md bg-surface-2 p-2 text-xs whitespace-pre-wrap">{d.payload_preview}</pre></div>
                  </div>
                </details>
              </Card>
            </li>
          ))}
        </ul>
      )}
      {data && <Pagination page={data.meta.page} pageSize={data.meta.page_size} total={data.meta.total} totalPages={data.meta.total_pages} onPage={(p) => set({ page: p })} onPageSize={(s) => set({ page_size: s })} />}
    </div>
  );
}
