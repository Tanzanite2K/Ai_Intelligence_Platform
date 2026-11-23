/** Phase 7 history, evidence and change APIs (read-only). Responses are validated with Zod like the rest of the client. */
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { apiGet, type Query } from "./client";

const str = z.string().nullable();
const Note = z.string().nullable().optional();

export const Version = z.object({
  version: z.number(), origin: z.string(), content_hash: z.string(), valid_from: str, valid_to: str, current: z.boolean(),
  batch_id: z.string(), trigger: str, changed_fields: z.array(z.string()),
});
export const Observation = z.object({
  batch_id: z.string(), trigger: str, source: z.string(), observed_at: str, origin: z.string(), content_hash: z.string(), published_at: str,
});
export const RecordHistory = z.object({
  data: z.object({
    record: z.object({ id: z.number(), type: z.string(), name: z.string(), source: z.string(), published_at: str }),
    first_seen: z.object({ at: str, bound: z.string(), label: z.string() }),
    last_seen: str, in_baseline: z.boolean(), newly_discovered: z.boolean(),
    versions: z.array(Version), observations: z.array(Observation),
    presence: z.array(z.object({ event: z.string(), source: z.string(), observed_at: str, batch_id: z.string(), scan_id: str })),
    counts: z.object({ versions: z.number(), observations: z.number(), live_observations: z.number(), sources: z.number() }),
  }),
  note: Note,
});
export type RecordHistory = z.infer<typeof RecordHistory>;

const VersionMeta = z.object({ version: z.number(), origin: z.string(), valid_from: str, content_hash: z.string() });
export const Compare = z.object({
  data: z.object({
    from: VersionMeta, to: VersionMeta, available_versions: z.array(z.number()),
    changes: z.array(z.object({ field: z.string(), change: z.string(), before: z.unknown(), after: z.unknown() })),
  }).nullable(),
  note: Note,
});
export const Metrics = z.object({
  data: z.object({
    series: z.array(z.object({
      metric: z.string(),
      points: z.array(z.object({ at: str, value: z.number(), source: z.string(), batch_id: str, kind: z.string() })),
    })),
  }),
  note: Note,
});
export type Metrics = z.infer<typeof Metrics>;

export const Coverage = z.object({
  available: z.boolean(), baseline_date: str.optional(), batches: z.number().optional(), live_batches: z.number().optional(),
  live_collection_days: z.number().optional(), first_live_day: str.optional(), last_live_day: str.optional(),
  observations: z.record(z.string(), z.number()).optional(), emerging_labels_enabled: z.boolean().optional(), emerging_min_days: z.number().optional(),
});
export type Coverage = z.infer<typeof Coverage>;
export const Change = z.object({
  kind: z.string(), type: z.string(), record_id: z.number().nullable(), name: str, at: str, batch_id: z.string().nullable(), trigger: str,
  source: str, changed_fields: z.array(z.string()).nullable(), version: z.number().nullable(),
});
export type Change = z.infer<typeof Change>;
export const Changes = z.object({
  data: z.array(Change),
  meta: z.object({ total: z.number(), page: z.number().optional(), page_size: z.number().optional(), by_kind: z.record(z.string(), z.number()).optional() }),
  coverage: Coverage.optional(), note: Note,
});
export const Batch = z.object({
  batch_id: z.string(), trigger: z.string(), scheduled_for: str, started_at: str, finished_at: str, status: z.string(), observations: z.number(),
  new: z.number(), modified: z.number(), disappeared: z.number(), metric_samples: z.number(), notes: str,
});
export type Batch = z.infer<typeof Batch>;
export const Batches = z.object({ data: z.array(Batch), note: Note });

export const ReliabilityDay = z.object({
  date: z.string(), runs: z.number(), ok: z.number(), partial: z.number(), failed: z.number(), inaccessible: z.number(),
  fetched: z.number(), stored: z.number(), avg_seconds: z.number().nullable(),
});
export const Reliability = z.object({
  data: z.array(z.object({
    source: z.string(), days: z.array(ReliabilityDay), runs: z.number(), ok: z.number(), partial: z.number(), failed: z.number(),
    inaccessible: z.number(), success_rate: z.number().nullable(),
  })),
  meta: z.object({ days: z.number(), days_with_runs: z.array(z.string()), sources: z.number() }), definition: z.string(), note: Note,
});
export type Reliability = z.infer<typeof Reliability>;

export const TimelineEvent = z.object({
  date: str, date_basis: z.string(), kind: z.string(), title: z.string(), detail: z.string().nullable().optional(),
  record: z.object({ id: z.number(), type: z.string() }), source: z.string(), url: z.string(),
});
export type TimelineEvent = z.infer<typeof TimelineEvent>;
export const Timeline = z.object({
  data: z.object({
    organisation: z.string(), events: z.array(TimelineEvent), counts: z.record(z.string(), z.number()),
    published_per_month: z.array(z.object({ month: z.string(), events: z.number() })),
  }),
  meta: z.object({ records: z.number(), history_available: z.boolean() }), note: Note,
});
export const EvidenceItem = z.object({
  record_id: z.number(), record_type: z.string(), source: z.string(), group: z.string(), kind: z.string(), snippet: z.string(),
  url: z.string().nullable(), date: z.string().nullable(),
});
export const Claim = z.object({
  kind: z.string(), predicate: z.string(), object: z.string(), value: z.string(), support_tier: z.string(),
  independent_source_groups: z.array(z.string()), evidence_count: z.number(), syndicated_copies: z.number(), strongest_evidence: z.string(),
  confidence: z.number(), needs_review: z.boolean(), conflicting_values: z.array(z.string()), evidence: z.array(EvidenceItem),
});
export type Claim = z.infer<typeof Claim>;
export const Evidence = z.object({
  data: z.object({
    claims: z.array(Claim), summary: z.record(z.string(), z.number()),
    co_mentions: z.array(z.object({ organisation: z.string(), articles: z.number() })), co_mentions_note: z.string(), confidence_formula: z.string(),
    review: z.object({ pending_names: z.array(z.string()), note: z.string() }).nullable(),
  }),
  meta: z.object({ records: z.number() }),
});
export type Evidence = z.infer<typeof Evidence>;

const RecRef = z.object({ id: z.number(), type: z.string(), name: z.string().nullable().optional(), published: z.string().nullable().optional() });
export const Section = z.object({
  id: z.string(), title: z.string(), status: z.string(), label: z.string().optional(), basis: z.string().optional(), explanation: z.string().optional(),
  period: z.object({
    recent: z.array(z.string()), prior: z.array(z.string()),
    papers: z.object({ recent: z.number(), prior: z.number() }), days_with_papers: z.object({ recent: z.number(), prior: z.number() }),
  }).optional(),
  statements: z.array(z.object({
    statement: z.string(), evidence: z.array(RecRef).optional(), label: z.string().optional(), change_pp: z.number().optional(),
    recent_share: z.number().optional(), prior_share: z.number().optional(), recent: z.number().optional(), prior: z.number().optional(),
  })).default([]),
  series: z.array(z.record(z.string(), z.union([z.string(), z.number()]))).optional(),
  per_day: z.array(z.object({ date: z.string(), jobs: z.number() })).optional(),
  roles: z.array(z.object({ role_family: z.string(), jobs: z.number() })).optional(),
  jobs: z.number().optional(),
  snapshot: z.object({
    papers_with_stars: z.number(), median: z.number().nullable(),
    top: z.array(z.object({ id: z.number(), type: z.string(), name: z.string().nullable(), stars: z.number() })),
  }).optional(),
});
export type Section = z.infer<typeof Section>;
export const IntelChanges = z.object({
  data: z.object({ sections: z.array(Section), coverage: Coverage }),
  meta: z.object({ emerging_labels_enabled: z.boolean(), rule: z.string() }),
});

export const historyApi = {
  record: (id: number, s?: AbortSignal) => apiGet(`/api/v1/records/${id}/history`, RecordHistory, undefined, s),
  compare: (id: number, q: Query, s?: AbortSignal) => apiGet(`/api/v1/records/${id}/history/compare`, Compare, q, s),
  metrics: (id: number, s?: AbortSignal) => apiGet(`/api/v1/records/${id}/history/metrics`, Metrics, undefined, s),
  changes: (q: Query, s?: AbortSignal) => apiGet("/api/v1/history/changes", Changes, q, s),
  batches: (s?: AbortSignal) => apiGet("/api/v1/history/batches", Batches, undefined, s),
  reliability: (q: Query, s?: AbortSignal) => apiGet("/api/v1/operations/source-reliability", Reliability, q, s),
  timeline: (id: string, s?: AbortSignal) => apiGet(`/api/v1/entities/${encodeURIComponent(id)}/timeline`, Timeline, undefined, s),
  evidence: (id: string, s?: AbortSignal) => apiGet(`/api/v1/entities/${encodeURIComponent(id)}/evidence`, Evidence, undefined, s),
  intelChanges: (s?: AbortSignal) => apiGet("/api/v1/intelligence/changes", IntelChanges, undefined, s),
};

const ok = (id: number) => Number.isInteger(id) && id > 0;
export const useRecordHistory = (id: number) => useQuery({ queryKey: ["history", id], queryFn: ({ signal }) => historyApi.record(id, signal), enabled: ok(id) });
export const useCompare = (id: number, q: Query) =>
  useQuery({ queryKey: ["history", id, "compare", q], queryFn: ({ signal }) => historyApi.compare(id, q, signal), enabled: ok(id), placeholderData: keepPreviousData });
export const useMetrics = (id: number) => useQuery({ queryKey: ["history", id, "metrics"], queryFn: ({ signal }) => historyApi.metrics(id, signal), enabled: ok(id) });
export const useChanges = (q: Query) => useQuery({ queryKey: ["changes", q], queryFn: ({ signal }) => historyApi.changes(q, signal), placeholderData: keepPreviousData });
export const useBatches = () => useQuery({ queryKey: ["batches"], queryFn: ({ signal }) => historyApi.batches(signal) });
export const useReliability = (q: Query) => useQuery({ queryKey: ["reliability", q], queryFn: ({ signal }) => historyApi.reliability(q, signal) });
export const useTimeline = (id: string) => useQuery({ queryKey: ["entity", id, "timeline"], queryFn: ({ signal }) => historyApi.timeline(id, signal), enabled: id.startsWith("e_") });
export const useEvidence = (id: string) => useQuery({ queryKey: ["entity", id, "evidence"], queryFn: ({ signal }) => historyApi.evidence(id, signal), enabled: id.startsWith("e_") });
export const useIntelChanges = () => useQuery({ queryKey: ["intel", "changes"], queryFn: ({ signal }) => historyApi.intelChanges(signal), staleTime: 60_000 });
