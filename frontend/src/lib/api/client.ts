/**
 * Central typed API client. All calls go to the FastAPI backend (NEXT_PUBLIC_API_BASE_URL);
 * responses are validated with Zod. No secrets live in the frontend.
 */
import type { z } from "zod";
import * as S from "./schemas";

export const API_BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");

export type ApiErrorKind = "network" | "http" | "contract" | "rate_limited";

export class ApiError extends Error {
  constructor(
    public readonly kind: ApiErrorKind,
    message: string,
    public readonly status?: number,
    public readonly code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export type Query = Record<string, string | number | boolean | null | undefined>;

function buildUrl(path: string, query?: Query): string {
  const url = new URL(`${API_BASE_URL}${path}`);
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
  }
  return url.toString();
}

export async function apiGet<T extends z.ZodType>(path: string, schema: T, query?: Query, signal?: AbortSignal): Promise<z.infer<T>> {
  let res: Response;
  try {
    res = await fetch(buildUrl(path, query), { signal, headers: { Accept: "application/json" }, cache: "no-store" });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError("network", `Cannot reach the API at ${API_BASE_URL}. Is the FastAPI server running?`);
  }
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  if (!res.ok) {
    const parsed = S.ApiErrorBody.safeParse(body);
    const msg = parsed.success ? parsed.data.error.message : `Request failed (HTTP ${res.status})`;
    throw new ApiError(res.status === 429 ? "rate_limited" : "http", msg, res.status, parsed.success ? parsed.data.error.code : undefined);
  }
  const parsed = schema.safeParse(body);
  if (!parsed.success) {
    const first = parsed.error.issues[0];
    throw new ApiError("contract", `Unexpected response from ${path}: ${first ? `${first.path.join(".")} ${first.message}` : "invalid shape"}`);
  }
  return parsed.data;
}

export const api = {
  health: (signal?: AbortSignal) => apiGet("/health", S.Health, undefined, signal),
  overview: (signal?: AbortSignal) => apiGet("/api/v1/dashboard/overview", S.Overview, undefined, signal).then((r) => r.data),
  activity: (days = 90, signal?: AbortSignal) => apiGet("/api/v1/dashboard/activity", S.Activity, { days }, signal),
  sourceContribution: (signal?: AbortSignal) => apiGet("/api/v1/dashboard/source-contribution", S.SourceContribution, undefined, signal).then((r) => r.data),
  freshness: (signal?: AbortSignal) => apiGet("/api/v1/dashboard/freshness", S.Freshness, undefined, signal),
  recent: (limit = 5, signal?: AbortSignal) => apiGet("/api/v1/dashboard/recent", S.Recent, { limit }, signal).then((r) => r.data),
  sourceHealth: (signal?: AbortSignal) => apiGet("/api/v1/dashboard/source-health", S.SourceHealthList, undefined, signal).then((r) => r.data),
  search: (params: Query, signal?: AbortSignal) => apiGet("/api/v1/search", S.SearchResult, params, signal),
  intelCoverage: (signal?: AbortSignal) => apiGet("/api/v1/intelligence/coverage", S.IntelCoverage, undefined, signal),
  intelTopics: (params: Query, signal?: AbortSignal) => apiGet("/api/v1/intelligence/topics", S.IntelTopics, params, signal),
  intelTopic: (id: string, params: Query, signal?: AbortSignal) => apiGet(`/api/v1/intelligence/topics/${encodeURIComponent(id)}`, S.IntelTopicDetail, params, signal),
  intelSignals: (params: Query, signal?: AbortSignal) => apiGet("/api/v1/intelligence/signals", S.IntelSignals, params, signal),
  intelTimeline: (params: Query, signal?: AbortSignal) => apiGet("/api/v1/intelligence/timeline", S.IntelTimeline, params, signal),
  intelMap: (params: Query, signal?: AbortSignal) => apiGet("/api/v1/intelligence/innovation-map", S.IntelMap, params, signal),
  intelBrief: (signal?: AbortSignal) => apiGet("/api/v1/intelligence/brief", S.IntelBrief, undefined, signal),
  list: (slug: S.TypeSlug, params: Query, signal?: AbortSignal) => apiGet(`/api/v1/${slug}`, S.RecordPage, params, signal),
  entities: (params: Query, signal?: AbortSignal) => apiGet("/api/v1/entities", S.EntityPage, params, signal),
  entity: (id: string, signal?: AbortSignal) =>
    apiGet(`/api/v1/entities/${encodeURIComponent(id)}`, S.EntityDetailResponse, undefined, signal).then((r) => r.data),
  entityRecords: (id: string, params: Query, signal?: AbortSignal) =>
    apiGet(`/api/v1/entities/${encodeURIComponent(id)}/records`, S.RecordPage, params, signal),
  entityRelated: (id: string, signal?: AbortSignal) => apiGet(`/api/v1/entities/${encodeURIComponent(id)}/related`, S.RelatedEntities, undefined, signal),
  reviews: (params: Query, signal?: AbortSignal) => apiGet("/api/v1/reviews", S.ReviewPage, params, signal),
  relationships: (signal?: AbortSignal) => apiGet("/api/v1/relationships/overview", S.RelationshipsOverview, { top: 15 }, signal),
  quality: (signal?: AbortSignal) => apiGet("/api/v1/dashboard/quality", S.Quality, undefined, signal),
  runs: (params: Query, signal?: AbortSignal) => apiGet("/api/v1/operations/ingestion-runs", S.RunPage, params, signal),
  operationsHealth: (signal?: AbortSignal) => apiGet("/api/v1/operations/source-health", S.SourceHealthList, undefined, signal).then((r) => r.data),
  deadLetters: (params: Query, signal?: AbortSignal) => apiGet("/api/v1/operations/dead-letters", S.DeadLetterPage, params, signal),
  newsFacets: (signal?: AbortSignal) => apiGet("/api/v1/news/facets", S.NewsFacets, { top: 200 }, signal).then((r) => r.data),
  record: (slug: S.TypeSlug, id: number, signal?: AbortSignal) =>
    apiGet(`/api/v1/${slug}/${id}`, S.RecordDetailResponse, undefined, signal).then((r) => r.data),
};
