/**
 * Runtime schemas for the FastAPI /api/v1 responses (mirrors src/api/v1/schemas.py).
 * Every response is validated, so a backend contract change surfaces as a clear error
 * instead of silently rendering wrong data.
 */
import { z } from "zod";

const dt = z.string().min(1); // ISO-8601 timestamps from the API
const nullableDt = dt.nullable();

export const RecordType = z.enum(["STARTUP", "PRODUCT", "RESEARCH_PAPER", "JOB", "NEWS"]);
export type RecordType = z.infer<typeof RecordType>;

export const TypeSlug = z.enum(["startups", "products", "papers", "jobs", "news"]);
export type TypeSlug = z.infer<typeof TypeSlug>;

export const PageMeta = z.object({
  page: z.number().int(),
  page_size: z.number().int(),
  total: z.number().int(),
  total_pages: z.number().int(),
});

export const RecordSummary = z.object({
  id: z.number().int(),
  type: RecordType,
  name: z.string().nullable(),
  description: z.string().nullable(),
  organisation: z.string().nullable(),
  url: z.string().nullable(),
  source_name: z.string(),
  source_url: z.string(),
  published_at: nullableDt,
  collected_at: dt,
  date_verified: z.boolean().nullable(),
  validation_status: z.string(),
  extraction_method: z.string(),
  github_stars: z.number().int().nullable(),
  tags: z.array(z.string()),
  location: z.string().nullable(),
  pricing_model: z.string().nullable(),
  is_remote: z.boolean().nullable(),
  role_family: z.string().nullable(),
  authors: z.array(z.string()),
  organizations: z.array(z.string()),
});
export type RecordSummary = z.infer<typeof RecordSummary>;

export const Overview = z.object({
  data: z.object({
    records: z.record(z.string(), z.number().int()),
    total_records: z.number().int(),
    organisations: z.number().int(),
    seed_organisations: z.number().int(),
    entity_mappings: z.number().int(),
    pending_reviews: z.number().int(),
    dead_letters: z.number().int(),
    failed_runs: z.number().int(),
    ingestion_runs: z.number().int(),
    last_run_at: nullableDt,
    generated_at: dt,
  }),
});
export type Overview = z.infer<typeof Overview>["data"];

const DayPoint = z.object({ date: z.string() }).catchall(z.number());
export const Activity = z.object({
  data: z.object({
    collected_per_day: z.array(DayPoint),
    published_per_day: z.array(DayPoint),
    runs_per_day: z.array(z.object({ date: z.string(), runs: z.number(), stored: z.number() })),
  }),
  meta: z.object({ days: z.number(), collection_days: z.number() }),
  note: z.string().nullable(),
});
export type Activity = z.infer<typeof Activity>;

export const SourceContribution = z.object({
  data: z.array(z.object({ source: z.string(), type: z.string(), records: z.number().int() })),
});
export type SourceContribution = z.infer<typeof SourceContribution>["data"];

export const Freshness = z.object({
  data: z.array(
    z.object({
      type: z.string(),
      total: z.number().int(),
      undated: z.number().int(),
      within_24h: z.number().int(),
      within_7d: z.number().int(),
      within_30d: z.number().int(),
      within_365d: z.number().int(),
      older_than_365d: z.number().int(),
    }),
  ),
  meta: z.object({ as_of: dt, basis: z.string() }),
});
export type Freshness = z.infer<typeof Freshness>;

export const RunItem = z.object({
  id: z.number().int(),
  source: z.string(),
  vertical: z.string(),
  status: z.string(),
  started_at: dt,
  finished_at: nullableDt,
  duration_s: z.number().nullable(),
  fetched: z.number().int(),
  stored: z.number().int(),
  duplicates: z.number().int(),
  rejected: z.number().int(),
  error: z.string().nullable(),
});
export type RunItem = z.infer<typeof RunItem>;

export const Recent = z.object({
  data: z.object({
    startups: z.array(RecordSummary),
    products: z.array(RecordSummary),
    papers: z.array(RecordSummary),
    jobs: z.array(RecordSummary),
    news: z.array(RecordSummary),
    runs: z.array(RunItem),
  }),
});
export type Recent = z.infer<typeof Recent>["data"];

export const SourceHealth = z.object({
  source: z.string(),
  vertical: z.string(),
  status: z.string(),
  last_run_at: nullableDt,
  last_success_at: nullableDt,
  runs: z.number().int(),
  runs_by_status: z.record(z.string(), z.number().int()),
  rate_limited_runs: z.number().int(),
  access_denied_runs: z.number().int(),
  last_error: z.string().nullable(),
  items_seen: z.number().int().nullable(),
  disabled_reason: z.string().nullable().default(null),
  action_required: z.string().nullable().default(null),
  health: z.string().default("HEALTHY"),
  health_detail: z.string().nullable().default(null),
  next_attempt_at: nullableDt.default(null),
  live_check: z
    .object({
      verdict: z.string().nullable(),
      checked_at: z.string().nullable(),
      report: z.string().nullable().optional(),
      http_status: z.number().int().nullable().optional(),
      requests_made: z.number().int().nullable().optional(),
    })
    .nullable()
    .default(null),
});
export type SourceHealth = z.infer<typeof SourceHealth>;
export const SourceHealthList = z.object({ data: z.array(SourceHealth) });

export const SearchResult = z.object({
  data: z.array(RecordSummary),
  meta: PageMeta,
  counts_by_type: z.record(z.string(), z.number().int()),
  organisations: z.array(
    z.object({ id: z.string(), canonical_name: z.string(), mapping_count: z.number().int(), needs_review: z.boolean() }),
  ),
  query: z.string().nullable(),
  sort: z.string(),
});
export type SearchResult = z.infer<typeof SearchResult>;

export const Health = z.object({ status: z.string() });

export const ApiErrorBody = z.object({
  error: z.object({ code: z.string(), message: z.string(), details: z.unknown().optional() }),
});

export const RecordPage = z.object({ data: z.array(RecordSummary), meta: PageMeta });
export type RecordPage = z.infer<typeof RecordPage>;

export const Provenance = z.enum(["source", "enriched", "entity_resolution", "llm_validated", "missing"]);
export type Provenance = z.infer<typeof Provenance>;

export const FieldInfo = z.object({
  key: z.string(),
  value: z.unknown(),
  provenance: Provenance,
  evidence: z.string().nullable(),
  note: z.string().nullable(),
});
export type FieldInfo = z.infer<typeof FieldInfo>;

export const OrgRef = z.object({
  canonical_name: z.string(),
  match_method: z.string().nullable(),
  confidence: z.number().nullable(),
  needs_review: z.boolean(),
});

export const OrgTag = z.object({
  name: z.string(),
  entity_id: z.string(),
  mapped: z.boolean(),
  match_method: z.string().nullable(),
  confidence: z.number().nullable(),
  needs_review: z.boolean(),
});
export type OrgTag = z.infer<typeof OrgTag>;

export const NewsFacets = z.object({
  data: z.object({
    total: z.number().int(),
    tagged: z.number().int(),
    untagged: z.number().int(),
    organisations: z.array(z.object({ name: z.string(), id: z.string(), articles: z.number().int() })),
  }),
});
export type NewsFacets = z.infer<typeof NewsFacets>["data"];

export const RecordDetail = RecordSummary.extend({
  fields: z.array(FieldInfo),
  provenance: z.record(z.string(), z.unknown()),
  resolved_organisation: OrgRef.nullable(),
  organisation_tags: z.array(OrgTag).default([]),
  related: z.array(RecordSummary),
  freshness_hours: z.number().nullable(),
  created_at: nullableDt,
  updated_at: nullableDt,
});
export type RecordDetail = z.infer<typeof RecordDetail>;
export const RecordDetailResponse = z.object({ data: RecordDetail });

// ------------------------------------------------------------------ organisations (entity resolution)
export const EntitySummary = z.object({
  id: z.string(),
  canonical_name: z.string(),
  seed_id: z.number().int().nullable(),
  origin: z.enum(["SEED", "DISCOVERED"]),
  domain: z.string().nullable(),
  aliases: z.array(z.string()),
  mapping_count: z.number().int(),
  record_count: z.number().int(),
  review_record_count: z.number().int(),
  needs_review: z.boolean(),
  match_methods: z.record(z.string(), z.number().int()),
});
export type EntitySummary = z.infer<typeof EntitySummary>;
export const EntityPage = z.object({ data: z.array(EntitySummary), meta: PageMeta });

export const MappingRow = z.object({
  id: z.number().int(),
  original_name: z.string(),
  match_method: z.string(),
  confidence: z.number(),
  needs_review: z.boolean(),
  source_url: z.string(),
  resolved_at: nullableDt,
});
export type MappingRow = z.infer<typeof MappingRow>;

export const EntityDetail = EntitySummary.extend({
  records_by_type: z.record(z.string(), z.number().int()),
  review_records_by_type: z.record(z.string(), z.number().int()),
  original_names: z.array(z.string()),
  mappings: z.array(MappingRow),
  unavailable: z.record(z.string(), z.string()),
});
export type EntityDetail = z.infer<typeof EntityDetail>;
export const EntityDetailResponse = z.object({ data: EntityDetail });

export const RelatedEntities = z.object({
  data: z.array(z.object({ name: z.string(), id: z.string(), co_mentions: z.number().int(), basis: z.string() })),
  meta: z.object({ news_articles_considered: z.number().int() }),
  note: z.string().nullable(),
});
export type RelatedEntities = z.infer<typeof RelatedEntities>;

export const ReviewItem = z.object({
  id: z.number().int(),
  original_name: z.string(),
  canonical_name: z.string(),
  match_method: z.string(),
  confidence: z.number(),
  source_url: z.string(),
  resolved_at: dt,
  status: z.literal("PENDING"),
  proposed_candidate: z.string().nullable(),
  reason: z.string().nullable(),
  audit: z.record(z.string(), z.string()).nullable(),
  candidate_entity: z.object({ id: z.string(), canonical_name: z.string(), verified_mappings: z.number().int(), records: z.number().int() }).nullable(),
  shares_canonical_with: z.number().int(),
  source_record: RecordSummary.nullable(),
  note: z.string(),
});
export type ReviewItem = z.infer<typeof ReviewItem>;
export const ReviewPage = z.object({ data: z.array(ReviewItem), meta: PageMeta });

const OrgLink = z.object({ id: z.string(), name: z.string(), records_by_type: z.record(z.string(), z.number().int()), records: z.number().int() });
export const RelationshipsOverview = z.object({
  data: z.object({
    organisations: z.number().int(),
    organisations_with_records: z.number().int(),
    seed_organisations: z.number().int(),
    seed_organisations_with_mappings: z.number().int(),
    match_methods: z.array(z.object({ method: z.string(), mappings: z.number().int(), min_confidence: z.number(), max_confidence: z.number() })),
    organisations_by_linked_types: z.record(z.string(), z.number().int()),
    multi_type_organisations: z.object({ total: z.number().int(), top: z.array(OrgLink) }),
    top_organisations: z.array(OrgLink),
    alias_merges: z.object({ total: z.number().int(), top: z.array(z.object({ id: z.string(), name: z.string(), original_names: z.array(z.string()) })) }),
    unmatched_mappings: z.number().int(),
    pending_reviews: z.number().int(),
    news_articles_with_validated_tags: z.number().int(),
  }),
  unavailable: z.record(z.string(), z.string()),
});
export type RelationshipsOverview = z.infer<typeof RelationshipsOverview>;

export const Quality = z.object({
  data: z.object({
    validation_status: z.record(z.string(), z.record(z.string(), z.number().int())),
    dates: z.array(z.object({ type: z.string(), total: z.number().int(), dated: z.number().int(), date_verified: z.number().int() })),
  }),
  meta: z.object({ statuses: z.record(z.string(), z.string()) }),
});
export type Quality = z.infer<typeof Quality>;

// ------------------------------------------------------------------ operations
export const RunPage = z.object({ data: z.array(RunItem), meta: PageMeta });
export type RunPage = z.infer<typeof RunPage>;

export const DeadLetterItem = z.object({
  id: z.number().int(),
  stage: z.string(),
  key: z.string().nullable(),
  error: z.string(),
  attempts: z.number().int(),
  created_at: dt,
  payload_preview: z.string(),
  retry_supported: z.boolean(),
});
export type DeadLetterItem = z.infer<typeof DeadLetterItem>;
export const DeadLetterPage = z.object({ data: z.array(DeadLetterItem), meta: PageMeta });

/* ------------------------------------------------------------------ Frontier Intelligence (/api/v1/intelligence) */
const IntelMeta = z.object({ index_built_at: z.string().nullable(), records_indexed: z.number().int() }).passthrough();
const IntelOrg = z.object({ name: z.string(), id: z.string() });
const CountMap = z.record(z.string(), z.number());

export const TopicFreq = z.object({
  id: z.string(),
  label: z.string(),
  records: z.number().int(),
  categories: z.number().int(),
  by_type: CountMap,
  share_by_type: z.record(z.string(), z.number().nullable()),
  top_terms: z.array(z.object({ term: z.string(), records: z.number().int() })),
});
export type TopicFreq = z.infer<typeof TopicFreq>;

const Window = z.object({ from: z.string(), to: z.string(), records: z.number().int(), day_coverage: z.number() });
export const EmergingTopic = z.object({
  id: z.string(), label: z.string(), recent_records: z.number().int(), baseline_records: z.number().int(),
  recent_share: z.number(), baseline_share: z.number(), share_ratio: z.number().nullable(), z: z.number(),
  status: z.enum(["rising", "declining", "stable", "too_few_records"]),
});
export type EmergingTopic = z.infer<typeof EmergingTopic>;
export const EmergingType = z.object({
  assessable: z.boolean(),
  reason: z.string().optional(),
  windows: z.object({ recent: Window, baseline: Window }).optional(),
  topics: z.array(EmergingTopic).optional(),
});
export type EmergingType = z.infer<typeof EmergingType>;

export const IntelTopics = z.object({
  data: z.object({
    topics: z.array(TopicFreq),
    records: z.number().int(),
    by_type: CountMap,
    undated_excluded: z.number().int(),
    emerging: z.object({ method: z.string(), by_type: z.record(z.string(), EmergingType) }),
    topic_fields: z.record(z.string(), z.array(z.string())),
  }),
  meta: IntelMeta,
});
export type IntelTopics = z.infer<typeof IntelTopics>;

const Granularity = z.record(z.string(), z.object({ supported: z.boolean(), reason: z.string().nullable() }));
export const CoverageCategory = z.object({
  type: z.string(), label: z.string(), records: z.number().int(), with_publication_date: z.number().int(),
  publication_first: z.string().nullable(), publication_last: z.string().nullable(), span_days: z.number().int(),
  days_with_records: z.number().int(), missing_days: z.number().int(),
  largest_gaps: z.array(z.object({ from: z.string(), to: z.string(), days: z.number().int() })),
  collection_days: z.array(z.string()), sources: CountMap, granularity: Granularity, topic_fields: z.array(z.string()), with_topic: z.number().int(),
});
export type CoverageCategory = z.infer<typeof CoverageCategory>;
export const IntelCoverage = z.object({
  data: z.object({ categories: z.array(CoverageCategory), collection_days: z.array(z.string()), total_records: z.number().int(), notes: z.array(z.string()) }),
  meta: IntelMeta,
});
export type IntelCoverage = z.infer<typeof IntelCoverage>;

export const TimelineBucket = z.object({
  start: z.string(), end: z.string(), records: z.number().int(), has_records: z.boolean(), partial: z.boolean(),
  topic_records: z.number().int().optional(), topic_share: z.number().nullable().optional(),
});
export type TimelineBucket = z.infer<typeof TimelineBucket>;
export const Timeline = z.object({
  granularity: z.string(), supported: z.boolean(), reason: z.string().nullable(),
  first: z.string().nullable().optional(), last: z.string().nullable().optional(),
  buckets: z.array(TimelineBucket), gaps: z.array(z.object({ from: z.string(), to: z.string(), buckets: z.number().int() })),
  undated_excluded: z.number().int(), records: z.number().int().optional(),
});
export type Timeline = z.infer<typeof Timeline>;
export const IntelTimeline = z.object({ data: Timeline, meta: IntelMeta });

export const IntelRecord = z.object({
  type: TypeSlug, id: z.number().int(), name: z.string(), published: z.string().nullable(), source: z.string(),
  organisations: z.array(IntelOrg), mentioned: z.array(IntelOrg), matched_term: z.string().nullable(), topics: z.array(z.string()),
});
export type IntelRecord = z.infer<typeof IntelRecord>;
export const IntelTopicDetail = z.object({
  data: z.object({
    topic: z.object({ id: z.string(), label: z.string(), patterns: z.array(z.string()) }),
    frequency: TopicFreq,
    organisations: z.object({
      verified: z.array(IntelOrg.extend({ records: z.number().int() })),
      mentioned: z.array(IntelOrg.extend({ articles: z.number().int() })),
    }),
    timeline: Timeline,
    records: z.array(IntelRecord),
    undated_excluded: z.number().int(),
  }),
  meta: IntelMeta.extend({ page: z.number().int(), page_size: z.number().int(), total: z.number().int(), total_pages: z.number().int() }),
});
export type IntelTopicDetail = z.infer<typeof IntelTopicDetail>;

export const SignalCategory = z.object({
  type: z.string(), label: z.string(), records: z.number().int(), with_publication_date: z.number().int(), with_topic: z.number().int(),
  with_verified_organisation: z.number().int(), sources: CountMap, collection_days: z.array(z.string()),
  publication_first: z.string().nullable(), publication_last: z.string().nullable(),
  top_topics: z.array(z.object({ id: z.string(), label: z.string(), records: z.number().int(), share: z.number().nullable() })),
  top_organisations: z.array(IntelOrg.extend({ records: z.number().int() })),
  most_mentioned: z.array(IntelOrg.extend({ articles: z.number().int() })),
});
export type SignalCategory = z.infer<typeof SignalCategory>;
export const IntelSignals = z.object({
  data: z.object({ categories: z.array(SignalCategory), records: z.number().int(), undated_excluded: z.number().int(), notes: z.array(z.string()) }),
  meta: IntelMeta,
});

export const MapOrg = z.object({
  name: z.string(), id: z.string(), verified_records: z.number().int(), by_type: CountMap,
  topics: z.array(z.object({ id: z.string(), label: z.string(), records: z.number().int() })),
  news_mentions: z.number().int(), co_mentioned_with: z.array(IntelOrg.extend({ articles: z.number().int() })),
  sample_records: z.array(z.object({ type: TypeSlug, id: z.number().int(), name: z.string() })),
});
export type MapOrg = z.infer<typeof MapOrg>;
export const IntelMap = z.object({
  data: z.object({
    organisations: z.array(MapOrg),
    matrix: z.object({ organisations: z.array(z.string()), topics: z.array(z.object({ id: z.string(), label: z.string() })), cells: z.array(z.array(z.number().int())) }),
    undated_excluded: z.number().int(),
    semantics: z.record(z.string(), z.string()),
  }),
  meta: IntelMeta.extend({ page: z.number().int(), page_size: z.number().int(), total: z.number().int(), total_pages: z.number().int() }),
});
export type IntelMap = z.infer<typeof IntelMap>;

export const Insight = z.object({
  id: z.string(), title: z.string(), observation: z.string(),
  metrics: z.array(z.object({ label: z.string(), value: z.number().nullable() })),
  organisations: z.array(IntelOrg), topics: z.array(z.object({ id: z.string(), label: z.string() })),
  period: z.object({ from: z.string().nullable(), to: z.string().nullable(), basis: z.string() }),
  evidence: z.object({
    records: z.array(z.object({ type: TypeSlug, id: z.number().int(), name: z.string(), published: z.string().nullable() })),
    total_supporting: z.number().int(), explore_href: z.string(),
  }),
  confidence: z.object({ level: z.enum(["high", "medium", "low"]), explanation: z.string() }),
});
export type Insight = z.infer<typeof Insight>;
export const IntelBrief = z.object({ data: z.array(Insight), meta: IntelMeta });
