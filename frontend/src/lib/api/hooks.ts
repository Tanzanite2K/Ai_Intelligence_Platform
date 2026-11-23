"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api, type Query } from "./client";
import type { TypeSlug } from "./schemas";

export const qk = {
  health: ["health"] as const,
  overview: ["dashboard", "overview"] as const,
  activity: (days: number) => ["dashboard", "activity", days] as const,
  contribution: ["dashboard", "source-contribution"] as const,
  freshness: ["dashboard", "freshness"] as const,
  recent: (limit: number) => ["dashboard", "recent", limit] as const,
  sourceHealth: ["dashboard", "source-health"] as const,
  quickSearch: (q: string) => ["search", "quick", q] as const,
  search: (params: Query) => ["search", "page", params] as const,
  list: (slug: TypeSlug, params: Query) => ["list", slug, params] as const,
  record: (slug: TypeSlug, id: number) => ["record", slug, id] as const,
};

export const useHealth = () => useQuery({ queryKey: qk.health, queryFn: ({ signal }) => api.health(signal), refetchInterval: 30_000, retry: 0 });
export const useOverview = () => useQuery({ queryKey: qk.overview, queryFn: ({ signal }) => api.overview(signal) });
export const useActivity = (days = 90) => useQuery({ queryKey: qk.activity(days), queryFn: ({ signal }) => api.activity(days, signal) });
export const useSourceContribution = () => useQuery({ queryKey: qk.contribution, queryFn: ({ signal }) => api.sourceContribution(signal) });
export const useFreshness = () => useQuery({ queryKey: qk.freshness, queryFn: ({ signal }) => api.freshness(signal) });
export const useRecent = (limit = 5) => useQuery({ queryKey: qk.recent(limit), queryFn: ({ signal }) => api.recent(limit, signal) });
export const useSourceHealth = () => useQuery({ queryKey: qk.sourceHealth, queryFn: ({ signal }) => api.sourceHealth(signal) });

export const useQuickSearch = (q: string) =>
  useQuery({
    queryKey: qk.quickSearch(q),
    queryFn: ({ signal }) => api.search({ q, page_size: 6 }, signal),
    enabled: q.trim().length >= 2,
    staleTime: 60_000,
    placeholderData: (prev) => prev,
  });

export const useSearch = (params: Query) =>
  useQuery({ queryKey: qk.search(params), queryFn: ({ signal }) => api.search(params, signal), placeholderData: keepPreviousData });

export const useRecordList = (slug: TypeSlug, params: Query) =>
  useQuery({ queryKey: qk.list(slug, params), queryFn: ({ signal }) => api.list(slug, params, signal), placeholderData: keepPreviousData });

export const useRecord = (slug: TypeSlug, id: number) =>
  useQuery({ queryKey: qk.record(slug, id), queryFn: ({ signal }) => api.record(slug, id, signal), enabled: Number.isInteger(id) && id > 0 });

/** Sources per record type, derived from the real per-source counts (no hardcoded lists). */
export const useSourcesByType = () =>
  useQuery({ queryKey: qk.contribution, queryFn: ({ signal }) => api.sourceContribution(signal), staleTime: 5 * 60_000 });

export const useEntities = (params: Query) =>
  useQuery({ queryKey: ["entities", params], queryFn: ({ signal }) => api.entities(params, signal), placeholderData: keepPreviousData });

export const useEntity = (id: string) =>
  useQuery({ queryKey: ["entity", id], queryFn: ({ signal }) => api.entity(id, signal), enabled: id.startsWith("e_") });

export const useEntityRecords = (id: string, params: Query, enabled = true) =>
  useQuery({
    queryKey: ["entity", id, "records", params],
    queryFn: ({ signal }) => api.entityRecords(id, params, signal),
    enabled: enabled && id.startsWith("e_"),
    placeholderData: keepPreviousData,
  });

export const useEntityRelated = (id: string) =>
  useQuery({ queryKey: ["entity", id, "related"], queryFn: ({ signal }) => api.entityRelated(id, signal), enabled: id.startsWith("e_") });

export const useReviews = () => useQuery({ queryKey: ["reviews"], queryFn: ({ signal }) => api.reviews({ page_size: 100 }, signal) });
export const useRelationships = () => useQuery({ queryKey: ["relationships"], queryFn: ({ signal }) => api.relationships(signal) });
export const useQuality = () => useQuery({ queryKey: ["dashboard", "quality"], queryFn: ({ signal }) => api.quality(signal) });

export const useRuns = (params: Query) =>
  useQuery({ queryKey: ["operations", "runs", params], queryFn: ({ signal }) => api.runs(params, signal), placeholderData: keepPreviousData });
export const useOperationsHealth = () =>
  useQuery({ queryKey: ["operations", "health"], queryFn: ({ signal }) => api.operationsHealth(signal) });
export const useDeadLetters = (params: Query) =>
  useQuery({ queryKey: ["operations", "dead-letters", params], queryFn: ({ signal }) => api.deadLetters(params, signal), placeholderData: keepPreviousData });

/** Tagged/untagged counts and organisation tags, computed by the API from stored validated tags. */
export const useNewsFacets = () => useQuery({ queryKey: ["news", "facets"], queryFn: ({ signal }) => api.newsFacets(signal) });

/* Frontier Intelligence */
const keep = { placeholderData: keepPreviousData, staleTime: 60_000 } as const;
export const useIntelCoverage = () => useQuery({ queryKey: ["intel", "coverage"], queryFn: ({ signal }) => api.intelCoverage(signal), staleTime: 60_000 });
export const useIntelTopics = (params: Query) => useQuery({ queryKey: ["intel", "topics", params], queryFn: ({ signal }) => api.intelTopics(params, signal), ...keep });
export const useIntelTopic = (id: string, params: Query) =>
  useQuery({ queryKey: ["intel", "topic", id, params], queryFn: ({ signal }) => api.intelTopic(id, params, signal), ...keep });
export const useIntelSignals = (params: Query) => useQuery({ queryKey: ["intel", "signals", params], queryFn: ({ signal }) => api.intelSignals(params, signal), ...keep });
export const useIntelTimeline = (params: Query) => useQuery({ queryKey: ["intel", "timeline", params], queryFn: ({ signal }) => api.intelTimeline(params, signal), ...keep });
export const useIntelMap = (params: Query) => useQuery({ queryKey: ["intel", "map", params], queryFn: ({ signal }) => api.intelMap(params, signal), ...keep });
export const useIntelBrief = () => useQuery({ queryKey: ["intel", "brief"], queryFn: ({ signal }) => api.intelBrief(signal), staleTime: 60_000 });
