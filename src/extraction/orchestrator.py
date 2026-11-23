"""Multi-tier LLM extraction engine: Gemini Flash -> Groq Llama -> DeepSeek.

Per chunk the orchestrator walks the provider chain. Inside one provider:

* 429 / RESOURCE_EXHAUSTED: wait ``Retry-After`` (or exponential backoff with
  full jitter), the provider's token bucket is drained so *concurrent* calls
  back off too; after ``max_retries`` or a wait longer than ``max_wait_s`` ->
  fall back to the next provider.
* 413 / context overflow / truncated output: the chunk is bisected on a
  semantic boundary and both halves retried (depth-limited) — the same
  oversize payload is never re-sent.
* timeout / 5xx: backoff retry, then fall back.
* malformed JSON or schema-invalid output: one repair retry, then fall back.
* auth / missing key: provider skipped immediately; a per-provider circuit
  breaker skips persistently failing providers for a cooldown.

When falling back to a provider with a *smaller* input budget the chunk is
re-split to that budget first. Chunk-level results are validated against
their own source text (evidence quotes) and merged deterministically.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from src.config.settings import Settings, get_settings
from src.core.ratelimit import CircuitBreaker
from src.core.retry import backoff_delay
from src.core.schemas import ValidationStatus
from src.extraction.chunking import bisect_chunk, estimate_tokens, plan_chunks, with_lead
from src.extraction.providers.base import (
    AuthError,
    BaseProvider,
    IncompleteResponseError,
    InvalidRequestError,
    LLMRequest,
    MalformedResponseError,
    PayloadTooLargeError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
)
from src.extraction.validators import ValidationReport, norm, parse_json_loose, split_name_list, validate_fields
from src.monitoring.logging import get_logger
from src.monitoring.metrics import LLM_CALLS, LLM_CHUNKS, LLM_FALLBACKS, LLM_LATENCY

log = get_logger(__name__)

SYSTEM_PROMPT = (
    "You are a precise information-extraction engine. Extract facts ONLY from the DOCUMENT given by the user. "
    "Never use outside knowledge, never guess, never compute values that are not written in the document. "
    'Respond with a single JSON object of the form {"fields": {"<field>": {"value": <value or null>, '
    '"evidence": "<exact verbatim quote from the DOCUMENT, max 200 characters, or null>"}}}. '
    "If a field is not explicitly stated, set value and evidence to null. "
    'For list fields (type "string_list") the value MUST be a JSON array of separate strings, e.g. ["Acme AI", "Globex"]; '
    'never a single comma-separated string such as "Acme AI, Globex". Their evidence must give, for EACH item, its own '
    "verbatim quote that contains that item's name, joined with ' | ' in the same order as the items."
)


@dataclass(frozen=True, slots=True)
class FieldSpec:
    type: str  # string | integer | boolean | enum | string_list
    description: str
    enum: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExtractionTask:
    name: str
    instructions: str
    fields: dict[str, FieldSpec]
    max_output_tokens: int = 1024

    def render(self, document: str, repair_hint: str | None = None) -> str:
        spec = {
            k: {"type": v.type, "description": v.description, **({"allowed": list(v.enum)} if v.enum else {}),
                **({"format": "JSON array of separate strings, one per item; one verbatim quote per item in evidence"} if v.type == "string_list" else {})}
            for k, v in self.fields.items()
        }
        hint = f"\n\nYOUR PREVIOUS ANSWER WAS REJECTED: {repair_hint}. Return valid JSON exactly in the required format." if repair_hint else ""
        return f"TASK: {self.instructions}\n\nFIELDS:\n{json.dumps(spec, indent=1)}{hint}\n\nDOCUMENT:\n<<<\n{document}\n>>>"

    def prompt_overhead(self) -> int:
        return estimate_tokens(SYSTEM_PROMPT) + estimate_tokens(self.render("")) + 120


class ProviderFailed(Exception):
    def __init__(self, provider: str, reason: str) -> None:
        super().__init__(f"{provider}: {reason}")
        self.provider, self.reason = provider, reason


class ExtractionFailed(Exception):
    pass


class NoProviderAvailable(ExtractionFailed):
    pass


@dataclass
class Attempt:
    chunk: int
    provider: str
    outcome: str
    detail: str = ""
    raw: str | None = None


@dataclass
class ExtractionResult:
    data: dict[str, Any]
    evidence: dict[str, str]
    confidence: float
    status: ValidationStatus
    providers_used: list[str]
    chunks_total: int
    chunks_failed: int
    rejected_fields: dict[str, str] = field(default_factory=dict)
    conflicts: dict[str, list[Any]] = field(default_factory=dict)
    attempts: list[Attempt] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


_NAME_KEYS = ("name", "organization", "organisation", "entity", "value", "text")


def normalise_object_list(item: dict[str, Any]) -> dict[str, Any]:
    """Accept ``{"value": [{"name": "X", "evidence": "..."}, ...]}`` (a common model variant of the requested
    ``{"value": ["X"], "evidence": "q1 | q2"}``). Names and per-item quotes are carried over unchanged and are
    validated exactly like the canonical form."""
    names: list[str] = []
    quotes: list[str] = []
    for v in item["value"]:
        if isinstance(v, dict):
            n = next((v[k] for k in _NAME_KEYS if isinstance(v.get(k), str) and v[k].strip()), None)
            if n:
                names.append(n)
            if isinstance(v.get("evidence"), str):
                quotes.append(v["evidence"])
        elif isinstance(v, str):
            names.append(v)
    if isinstance(item.get("evidence"), str):
        quotes.append(item["evidence"])
    return {"value": names, "evidence": " | ".join(quotes) or None}


class ListFormatError(TypeError):
    """A list field arrived as one string; recoverable through the structured repair retry."""


def coerce(spec: FieldSpec, value: Any, *, split_fallback: bool = False) -> Any:
    if value is None:
        return None
    t = spec.type
    if t == "string":
        if isinstance(value, (dict, list)):
            raise TypeError("expected string")
        return str(value).strip() or None
    if t == "integer":
        if isinstance(value, bool):
            raise TypeError("expected integer")
        if isinstance(value, (int, float)):
            return int(value)
        digits = str(value).replace(",", "").replace("_", "").strip()
        return int(digits)
    if t == "boolean":
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if s in ("true", "yes"):
            return True
        if s in ("false", "no"):
            return False
        raise TypeError("expected boolean")
    if t == "enum":
        v = str(value).strip().upper()
        if v not in spec.enum:
            raise TypeError(f"'{value}' not in {spec.enum}")
        return v
    if t == "string_list":
        if isinstance(value, str):
            # never wrap "A, B, C" as one item; the caller asks the model to repair (or, on the repair attempt,
            # splits conservatively and validates every name independently)
            if not split_fallback:
                raise ListFormatError("must be a JSON array of separate strings, not a single string")
            value = split_name_list(value)
        if not isinstance(value, list):
            raise TypeError("expected list")
        if any(isinstance(x, (dict, list)) for x in value):
            raise TypeError("list items must be strings")
        return [str(x).strip() for x in value if str(x).strip()]
    raise TypeError(f"unknown type {t}")


class LLMOrchestrator:
    def __init__(
        self,
        providers: list[BaseProvider],
        settings: Settings | None = None,
        *,
        max_wait_s: float = 45.0,
        max_split_depth: int = 3,
        max_chunks: int = 8,
        sleep: Any = asyncio.sleep,
        capture_raw: bool = False,
    ) -> None:
        self.s = settings or get_settings()
        self.providers = providers
        self.max_retries = self.s.llm_max_retries_per_provider
        self.max_wait_s = max_wait_s
        self.max_split_depth = max_split_depth
        self.max_chunks = max_chunks
        self._sleep = sleep
        self.capture_raw = capture_raw  # diagnostics: keep raw model output (no secrets) in Attempt.raw
        self._sem = asyncio.Semaphore(self.s.llm_max_concurrency)
        self.breakers = {p.name: CircuitBreaker(threshold=5, cooldown_s=120) for p in providers}

    def available(self) -> list[BaseProvider]:
        return [p for p in self.providers if p.configured and self.breakers[p.name].allow()]

    async def close(self) -> None:
        for p in self.providers:
            await p.close()

    # ------------------------------------------------------------------ API
    async def extract(self, task: ExtractionTask, document: str) -> ExtractionResult:
        chain = self.available()
        if not chain:
            raise NoProviderAvailable("no LLM provider configured/available (set GEMINI_API_KEY, GROQ_API_KEY or DEEPSEEK_API_KEY)")
        plan = plan_chunks(document, budget_tokens=chain[0].input_token_budget, prompt_overhead_tokens=task.prompt_overhead(), max_chunks=self.max_chunks)
        if not plan.chunks:
            raise ExtractionFailed("empty document after cleaning")
        attempts: list[Attempt] = []
        results = await asyncio.gather(*(self._run_chunk(task, plan.lead, c, i, attempts) for i, c in enumerate(plan.chunks)), return_exceptions=True)
        reports: list[tuple[int, ValidationReport]] = []
        used: list[str] = []
        failed = 0
        for i, r in enumerate(results):
            if isinstance(r, BaseException):
                failed += 1
                attempts.append(Attempt(i, "*", "chunk_failed", str(r)[:300]))
                continue
            reps, prov = r
            used.append(prov)
            reports.extend((i, rep) for rep in reps)
        if not reports:
            raise ExtractionFailed(f"all {len(plan.chunks)} chunks failed on every provider: " + "; ".join(a.detail for a in attempts[-3:]))
        res = self._merge(task, reports, len(plan.chunks), failed)
        res.providers_used = sorted(set(used), key=used.index)
        res.attempts = attempts
        res.notes.extend(plan.notes)
        return res

    # ------------------------------------------------------------ internals
    def _fit(self, text: str, budget: int, overhead: int) -> list[str]:
        if estimate_tokens(text) + overhead <= budget:
            return [text]
        a, b = bisect_chunk(text)
        if not a or not b:
            return [text]
        return self._fit(a, budget, overhead) + self._fit(b, budget, overhead)

    async def _run_chunk(self, task: ExtractionTask, lead: str, chunk: str, idx: int, attempts: list[Attempt]) -> tuple[list[ValidationReport], str]:
        last = "no provider"
        for prov in self.available():
            pieces = self._fit(with_lead(lead, chunk), prov.input_token_budget, task.prompt_overhead())
            try:
                reps: list[ValidationReport] = []
                for piece in pieces:
                    reps.extend(await self._call(prov, task, piece, idx, attempts, depth=0))
                self.breakers[prov.name].record_success()
                LLM_CHUNKS.inc(len(pieces))
                return reps, prov.name
            except ProviderFailed as e:
                last = str(e)
                self.breakers[prov.name].record_failure()
                LLM_FALLBACKS.labels(prov.name, e.reason.split(":")[0]).inc()
                log.warning("llm.fallback", provider=prov.name, chunk=idx, reason=e.reason[:200])
        raise ExtractionFailed(f"chunk {idx}: every provider failed (last: {last})")

    async def _call(self, prov: BaseProvider, task: ExtractionTask, text: str, idx: int, attempts: list[Attempt], depth: int) -> list[ValidationReport]:
        repair: str | None = None
        for attempt in range(self.max_retries + 1):
            req = LLMRequest(system=SYSTEM_PROMPT, user=task.render(text, repair), max_output_tokens=task.max_output_tokens)
            try:
                async with self._sem:
                    with LLM_LATENCY.labels(prov.name).time():
                        resp = await prov.complete(req)
                raw = parse_json_loose(resp.text)
                report = self._validate(task, raw, text, split_fallback=repair is not None)
                LLM_CALLS.labels(prov.name, "ok").inc()
                verdicts = {k: v.reason for k, v in report.fields.items()}
                attempts.append(Attempt(idx, prov.name, "ok", f"in={resp.input_tokens} out={resp.output_tokens} verdicts={verdicts}",
                                        raw=resp.text[:6000] if self.capture_raw else None))
                return [report]
            except RateLimitError as e:
                LLM_CALLS.labels(prov.name, "rate_limited").inc()
                delay = e.retry_after if e.retry_after is not None else backoff_delay(attempt, self.s.llm_backoff_base_s, self.s.llm_backoff_max_s)
                attempts.append(Attempt(idx, prov.name, "429", f"wait={delay:.2f}s"))
                if delay > self.max_wait_s or attempt == self.max_retries:
                    raise ProviderFailed(prov.name, f"rate_limited: retry budget exhausted (next wait {delay:.1f}s)") from e
                await self._sleep(delay)
            except (PayloadTooLargeError, IncompleteResponseError) as e:
                kind = "413" if isinstance(e, PayloadTooLargeError) else "truncated"
                LLM_CALLS.labels(prov.name, kind).inc()
                attempts.append(Attempt(idx, prov.name, kind, f"depth={depth} tokens~{estimate_tokens(text)}"))
                if depth >= self.max_split_depth:
                    raise ProviderFailed(prov.name, f"payload_too_large: still too large after {depth} splits") from e
                left, right = bisect_chunk(text)
                if not left or not right:
                    raise ProviderFailed(prov.name, "payload_too_large: cannot split further") from e
                return await self._call(prov, task, left, idx, attempts, depth + 1) + await self._call(prov, task, right, idx, attempts, depth + 1)
            except (ProviderTimeoutError, ProviderUnavailableError) as e:
                LLM_CALLS.labels(prov.name, "unavailable").inc()
                attempts.append(Attempt(idx, prov.name, "unavailable", str(e)[:120]))
                if attempt == self.max_retries:
                    raise ProviderFailed(prov.name, f"unavailable: {e}") from e
                await self._sleep(backoff_delay(attempt, self.s.llm_backoff_base_s, self.s.llm_backoff_max_s))
            except MalformedResponseError as e:
                LLM_CALLS.labels(prov.name, "malformed").inc()
                attempts.append(Attempt(idx, prov.name, "malformed", str(e)[:120]))
                if repair is not None:
                    raise ProviderFailed(prov.name, f"malformed: {e}") from e
                repair = str(e)[:200]
            except (AuthError, InvalidRequestError) as e:
                LLM_CALLS.labels(prov.name, "client_error").inc()
                attempts.append(Attempt(idx, prov.name, "client_error", str(e)[:120]))
                raise ProviderFailed(prov.name, f"client_error: {e}") from e
            except ProviderError as e:  # pragma: no cover - future subclasses
                raise ProviderFailed(prov.name, f"error: {e}") from e
        raise ProviderFailed(prov.name, "retries exhausted")

    def _validate(self, task: ExtractionTask, raw: dict[str, Any], source: str, *, split_fallback: bool = False) -> ValidationReport:
        fields = raw.get("fields", raw)
        if not isinstance(fields, dict):
            raise MalformedResponseError("'fields' must be an object")
        coerced: dict[str, Any] = {}
        type_errors: dict[str, str] = {}
        for name, spec in task.fields.items():
            item = fields.get(name)
            if spec.type == "string_list" and isinstance(item, dict) and isinstance(item.get("value"), list) and any(isinstance(v, dict) for v in item["value"]):
                item = normalise_object_list(item)
            if isinstance(item, dict) and "value" in item:
                try:
                    coerced[name] = {"value": coerce(spec, item.get("value"), split_fallback=split_fallback), "evidence": item.get("evidence")}
                except ListFormatError as e:
                    raise MalformedResponseError(f"field '{name}' {e} (e.g. [\"A\", \"B\"])") from e
                except (TypeError, ValueError) as e:
                    type_errors[name] = f"type error: {e}"
            elif item is not None:
                coerced[name] = item
        rep = validate_fields({"fields": coerced}, set(task.fields), source)
        from src.extraction.validators import FieldVerdict

        for name, why in type_errors.items():
            rep.fields[name] = FieldVerdict(None, None, False, why)
        return rep

    def _merge(self, task: ExtractionTask, reports: list[tuple[int, ValidationReport]], chunks_total: int, chunks_failed: int) -> ExtractionResult:
        data: dict[str, Any] = {}
        evidence: dict[str, str] = {}
        rejected: dict[str, str] = {}
        conflicts: dict[str, list[Any]] = {}
        for name, spec in task.fields.items():
            cands = [(i, r.fields[name]) for i, r in reports if name in r.fields and r.fields[name].accepted and r.fields[name].value is not None]
            rej = [r.fields[name].reason for _, r in reports if name in r.fields and not r.fields[name].accepted]
            if rej and not cands:
                rejected[name] = rej[0]
            if not cands:
                continue
            if spec.type == "string_list":
                merged: list[str] = []
                seen: set[str] = set()
                for _, v in sorted(cands, key=lambda c: c[0]):
                    for item in v.value:
                        if norm(item) not in seen:
                            seen.add(norm(item))
                            merged.append(item)
                data[name] = merged
                evidence[name] = " | ".join(v.evidence or "" for _, v in cands)[:600]
                continue
            votes = Counter(norm(str(v.value)) for _, v in cands)
            if len(votes) > 1:
                conflicts[name] = [v.value for _, v in cands]
            top = max(votes.values())
            winner_key = next(k for k in (norm(str(v.value)) for _, v in sorted(cands, key=lambda c: c[0])) if votes[k] == top)
            i, v = next((i, v) for i, v in sorted(cands, key=lambda c: c[0]) if norm(str(v.value)) == winner_key)
            data[name], evidence[name] = v.value, v.evidence or ""
        attempted = len(data) + len(rejected)
        precision = len(data) / attempted if attempted else 1.0
        coverage = (chunks_total - chunks_failed) / chunks_total
        conflict_penalty = 0.85 if conflicts else 1.0
        confidence = round(precision * coverage * conflict_penalty, 4)
        if chunks_failed or rejected or conflicts:
            status = ValidationStatus.PARTIAL
        else:
            status = ValidationStatus.VERIFIED
        return ExtractionResult(data, evidence, confidence, status, [], chunks_total, chunks_failed, rejected, conflicts)
