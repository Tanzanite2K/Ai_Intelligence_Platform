"use client";

import { AlertTriangle, CheckCircle2, Radar, ShieldX, Timer, Wrench } from "lucide-react";
import { useOperationsHealth } from "@/lib/api/hooks";
import type { SourceHealth } from "@/lib/api/schemas";
import { fmtDateTime, fmtNumber, fmtRelative } from "@/lib/format";
import { useUrlState } from "@/lib/url-state";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Tooltip } from "@/components/ui/tooltip";
import { EmptyState, ErrorState, ListSkeleton } from "@/components/common/states";
import { HealthBadge, StatusBadge } from "@/components/common/status-badge";

const FILTERS = [
  { key: "", label: "All" },
  { key: "HEALTHY", label: "Healthy" },
  { key: "RATE_LIMITED", label: "Rate limited" },
  { key: "CREDENTIAL_REQUIRED", label: "Credential required" },
  { key: "PARTIAL", label: "Partial" },
  { key: "FAILED", label: "Failed" },
  { key: "DISABLED", label: "Disabled" },
] as const;

function matches(h: SourceHealth, f: string): boolean {
  return f === "" || h.health === f;
}

const LIVE_TONE = (v: string | null) => (v === "PASS" ? "text-success" : v?.startsWith("DISABLED") ? "text-muted-foreground" : "text-warning");

function LiveCheck({ h }: { h: SourceHealth }) {
  const l = h.live_check;
  if (!l) return <span className="text-muted-foreground">not checked</span>;
  return (
    <Tooltip content={`${l.report ?? "diagnostic report"} · ${l.requests_made === 0 ? "no request made" : l.requests_made ? `${l.requests_made} request(s)` : ""}`}>
      <span tabIndex={0} className="inline-flex flex-col" data-testid="live-check">
        <span className={cn("inline-flex items-center gap-1 font-medium", LIVE_TONE(l.verdict))}><Radar className="size-3.5" />{l.verdict ?? "unknown"}</span>
        <span className="text-muted-foreground">{l.http_status ? `HTTP ${l.http_status} · ` : ""}{l.checked_at ? fmtRelative(l.checked_at) : ""}</span>
      </span>
    </Tooltip>
  );
}

export function SourceHealthPage() {
  const { get, set } = useUrlState();
  const f = FILTERS.some((x) => x.key === get("view")) ? get("view") : "";
  const { data, isPending, isError, error, refetch } = useOperationsHealth();
  const rows = (data ?? []).filter((h) => matches(h, f));
  const counts = Object.fromEntries(FILTERS.map((x) => [x.key, (data ?? []).filter((h) => matches(h, x.key)).length]));
  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Source health</h1>
        <p className="text-sm text-muted-foreground">
          Health combines configuration, rate-limit cooldowns, rejected credentials and each source’s most recent run. “Live check” is the latest
          read-only diagnostic (RUN_SOURCE_CHECK), which does not write runs.
        </p>
      </div>
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter sources">
        {FILTERS.map((x) => (
          <button key={x.key} type="button" aria-pressed={f === x.key} onClick={() => set({ view: x.key })}
            className={cn("inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-xs", f === x.key ? "border-accent/50 bg-accent-soft text-accent" : "border-border text-muted-foreground hover:text-foreground")}>
            {x.label}{data && <span className="tabular rounded-full bg-surface-2 px-1.5 text-[10px]">{counts[x.key]}</span>}
          </button>
        ))}
      </div>
      {isError ? <Card><ErrorState error={error} onRetry={() => void refetch()} /></Card> : isPending ? <Card className="p-4"><ListSkeleton rows={8} /></Card> : rows.length === 0 ? (
        <Card><EmptyState title="No sources in this view" /></Card>
      ) : (
        <Card className="overflow-x-auto">
          <table className="w-full min-w-[1100px] text-sm">
            <thead><tr className="border-b border-border text-left text-xs text-muted-foreground">
              <th scope="col" className="px-4 py-2.5 font-medium">Source</th>
              <th scope="col" className="px-3 py-2.5 font-medium">Health</th>
              <th scope="col" className="px-3 py-2.5 font-medium">Live check</th>
              <th scope="col" className="px-3 py-2.5 font-medium">Last success</th>
              <th scope="col" className="px-3 py-2.5 font-medium">Runs by status</th>
              <th scope="col" className="px-3 py-2.5 font-medium">Restrictions</th>
              <th scope="col" className="px-3 py-2.5 text-right font-medium">Items seen</th>
              <th scope="col" className="px-3 py-2.5 font-medium">Last error</th>
            </tr></thead>
            <tbody>
              {rows.map((h) => (
                <tr key={h.source} className="border-b border-border/60 align-top last:border-0">
                  <td className="px-4 py-2.5"><div className="font-medium">{h.source}</div><Badge>{h.vertical.toLowerCase().replace("_", " ")}</Badge></td>
                  <td className="max-w-[260px] px-3 py-2.5">
                    {h.health_detail ? (
                      <Tooltip content={h.health_detail}><HealthBadge health={h.health} tabIndex={0} data-testid="health-badge" /></Tooltip>
                    ) : <HealthBadge health={h.health} data-testid="health-badge" />}
                    <div className="mt-1 flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
                      last run {fmtRelative(h.last_run_at)} {h.health !== "DISABLED" && <StatusBadge status={h.status} />}
                    </div>
                    {h.next_attempt_at && (
                      <div className="mt-0.5 text-xs text-warning" data-testid="next-attempt">no requests before {fmtDateTime(h.next_attempt_at)}</div>
                    )}
                    {h.health_detail && <p className="mt-0.5 text-[11px] leading-snug break-words text-muted-foreground">{h.health_detail}</p>}
                  </td>
                  <td className="px-3 py-2.5 text-xs"><LiveCheck h={h} /></td>
                  <td className="px-3 py-2.5 text-xs">
                    {h.last_success_at ? (
                      <Tooltip content={fmtDateTime(h.last_success_at)}><span tabIndex={0} className="inline-flex items-center gap-1 text-success"><CheckCircle2 className="size-3.5" />{fmtRelative(h.last_success_at)}</span></Tooltip>
                    ) : <span className="inline-flex items-center gap-1 text-danger"><AlertTriangle className="size-3.5" /> never</span>}
                  </td>
                  <td className="px-3 py-2.5"><div className="flex flex-wrap gap-1">{Object.entries(h.runs_by_status).map(([s, n]) => <span key={s} className="inline-flex items-center gap-1 text-xs"><StatusBadge status={s} /><span className="tabular">{n}</span></span>)}</div></td>
                  <td className="px-3 py-2.5 text-xs">
                    {h.rate_limited_runs > 0 && <div className="inline-flex items-center gap-1 text-warning"><Timer className="size-3.5" /> {h.rate_limited_runs} rate-limited</div>}
                    {h.access_denied_runs > 0 && <div className="inline-flex items-center gap-1 text-danger"><ShieldX className="size-3.5" /> {h.access_denied_runs} access denied</div>}
                    {h.rate_limited_runs === 0 && h.access_denied_runs === 0 && <span className="text-muted-foreground">none</span>}
                  </td>
                  <td className="tabular px-3 py-2.5 text-right">{h.items_seen === null ? <span className="text-muted-foreground">—</span> : fmtNumber(h.items_seen)}</td>
                  <td className="max-w-[360px] px-3 py-2.5 text-xs">
                    {h.last_error ? (
                      h.health === "DISABLED" ? (
                        <span className="line-clamp-3 break-words text-muted-foreground">Before it was disabled: {h.last_error}</span>
                      ) : <span className="line-clamp-3 break-words text-danger">{h.last_error}</span>
                    ) : <span className="text-muted-foreground">none recorded</span>}
                    {h.action_required && (
                      <p className="mt-1.5 flex gap-1 break-words text-foreground" data-testid="action-required">
                        <Wrench className="mt-0.5 size-3 shrink-0 text-warning" /><span><span className="font-medium">Action required:</span> {h.action_required}</span>
                      </p>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      <p className="text-[11px] text-muted-foreground">“Items seen” is the crawl checkpoint counter (only for sources that keep one). Errors are shown with secrets redacted by the API.</p>
    </div>
  );
}
