# Frontier Intelligence API (`/api/v1/intelligence`)

Read-only endpoints behind the **Frontier Intelligence** section (Brief, Trend Radar, Market Signals, Innovation Map,
Historical Trends). Interactive schema: `http://127.0.0.1:8000/docs` → tag **intelligence**.

## How results are computed

* **One read-only scan.** The API selects only the fields the topic rules need from `records` (via JSON extraction) and the
  `entity_mappings` rows, builds an in-memory index, and caches it. The cache is rebuilt only when the database fingerprint
  (row counts, max ids, last `updated_at`, pending reviews) changes. The index is warmed at API start-up.
  No tables, indexes or migrations are added; nothing is written.
* **Topics** come from a transparent keyword taxonomy (`src/intelligence/topics.py`, 20 topics, see `/topics-catalog`).
  Fields searched: startups = description + tags; products = name + Hugging Face task; papers = title + abstract;
  jobs = title + role family; news = title + summary. Every record keeps the term that matched.
* **Time** uses publication dates only. Records without one (all startups and products) are never placed on a timeline and
  are excluded (and counted as `undated_excluded`) when a date filter is set.
* **Emerging topics** compare a topic's *share* of a category in the last 28 days of publication dates with the preceding
  56 days. Assessable only if each window has ≥ 100 records and records on ≥ 50% of its days. "Rising" needs share ratio
  ≥ 1.5, ≥ 10 recent records and a two-proportion z ≥ 2 ("declining" mirrors it). Otherwise the response says why not.
* **Organisations.** Verified link = record mapped to the organisation by an entity mapping with `needs_review = false`.
  News organisation tags are **co-mentions**, reported separately; no partnership, investment or ownership is inferred.
  Research papers carry no organisation affiliation.
* **Gaps.** Empty buckets are returned with `has_records: false` and listed in `gaps`; nothing is interpolated.

## Common query parameters

| Parameter | Used by | Meaning |
|---|---|---|
| `types` | topics, topics/{id}, signals, innovation-map | Comma list of `startups,products,papers,jobs,news` (default all). 422 `invalid_type` otherwise. |
| `date_from`, `date_to` | same + timeline | Publication-date range (inclusive). 422 `invalid_range` if reversed. |
| `page`, `page_size` | topics/{id}, innovation-map | Pagination (page_size ≤ 100), echoed in `meta`. |

All responses: `{"data": …, "meta": {"index_built_at", "records_indexed", …}}`. Errors: `{"error": {"code", "message", "details"}}`.

## Endpoints

| Endpoint | Returns |
|---|---|
| `GET /coverage` | Per category: records, records with a publication date, first/last date, span, days with records, missing days, five largest gaps, collection days, sources, granularity support (`day` ≥ 7 days, `week` ≥ 4 weeks, `month` ≥ 3 months), topic fields. Plus global `notes`. |
| `GET /topics` | Trend Radar: `topics[]` (records, categories present, `by_type`, `share_by_type`, `top_terms`), `emerging.by_type` with method, windows and per-topic status, `undated_excluded`. |
| `GET /topics/{topic_id}` | One topic: frequency, organisations (`verified` / `mentioned`), weekly timeline with topic share, paginated supporting `records` (newest first, `matched_term`). 404 for unknown topic. |
| `GET /signals` | Market Signals per category: records, dated / topic / verified-organisation coverage, sources, collection days, publication range, top topics with share, top verified organisations, most-mentioned organisations (news). `notes` state that counts measure collection volume, not market growth. |
| `GET /timeline?type=papers&granularity=week&topic=` | Historical Trends: buckets (`start`, `end`, `records`, `has_records`, `partial`, optional `topic_records`, `topic_share`), `gaps`, `supported` + `reason`, `undated_excluded`. `type` may be `all`. |
| `GET /innovation-map?topic=&q=&relation=all\|verified\|mentioned` | Organisation rows (verified records by category, topics, news mentions, co-mentioned organisations, sample records) and an organisation × topic matrix (top 12 × 10), with `semantics` text. |
| `GET /brief` | Insights. Each has `observation`, `metrics`, `organisations`, `topics`, `period` (+ `basis`), `evidence` (up to 5 linked records, total, explore link) and `confidence` (`high`/`medium`/`low` + explanation). |
| `GET /topics-catalog` | Topic ids, labels and exact patterns; searched fields per category. |

## Data limits as of 2026-11-22 (from `/coverage`)

* All 6,708 records were collected on 2026-11-21: counts describe collection, not change over time.
* Only research papers have a usable publication history (2026-05-19 → 2026-11-19, 134 days with papers). Their monthly
  counts mainly reflect how far back the Hugging Face daily-paper lists were crawled, which is why trends compare shares.
* Jobs and news cover about one day (24-hour freshness window); startups and products have no dates.
