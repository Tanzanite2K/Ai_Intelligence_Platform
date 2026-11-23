"""Prometheus metrics shared by crawlers, the LLM engine and pipelines."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter("aip_http_requests_total", "Outbound HTTP requests", ["domain", "status"])
HTTP_LATENCY = Histogram("aip_http_latency_seconds", "Outbound HTTP latency", ["domain"])
CIRCUIT_OPEN = Gauge("aip_circuit_open", "1 when the domain circuit breaker is open", ["domain"])

RECORDS_COLLECTED = Counter("aip_records_collected_total", "Raw records emitted by crawlers", ["vertical", "source"])
RECORDS_STORED = Counter("aip_records_stored_total", "Records persisted (new)", ["vertical"])
RECORDS_DUPLICATE = Counter("aip_records_duplicate_total", "Records skipped as duplicates", ["vertical"])
RECORDS_REJECTED = Counter("aip_records_rejected_total", "Records rejected", ["vertical", "reason"])

LLM_CALLS = Counter("aip_llm_calls_total", "LLM provider calls", ["provider", "outcome"])
LLM_LATENCY = Histogram("aip_llm_latency_seconds", "LLM call latency", ["provider"])
LLM_FALLBACKS = Counter("aip_llm_fallbacks_total", "Fallbacks to the next provider", ["from_provider", "reason"])
LLM_CHUNKS = Counter("aip_llm_chunks_total", "Chunks sent to LLMs")

ER_DECISIONS = Counter("aip_entity_resolution_total", "Entity resolution decisions", ["method"])
SOURCE_STATUS = Gauge("aip_source_accessible", "1 if the source was reachable on last run", ["source"])
