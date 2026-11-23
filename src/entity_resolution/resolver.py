"""Deterministic entity resolution engine.

Layers (first decisive layer wins):

1-4. normalisation (Unicode/NFKC+accents, case, punctuation/whitespace, legal suffixes)
5.   EXACT      – compact key equals a canonical key                      conf 1.00
6.   ALIAS      – compact key equals a curated/learned alias              conf 0.99
     DOMAIN     – registrable website domain equals the entity's domain   conf 0.98
7.   FUZZY      – rapidfuzz score >= accept threshold, clear margin over
                  the runner-up, and *no guard violated*                 conf = score/100
8.   SEMANTIC   – ambiguous fuzzy band: character-n-gram TF-IDF cosine
                  (pluggable embedding backend) must also agree           conf = cosine
9.   REVIEW     – still ambiguous: kept as its own entity, queued for a human
     NEW        – no candidate: registered as a new canonical entity (dynamic)

Guards that block a merge even at high string similarity ("avoid merging
different organisations merely because names are similar"):
* conflicting known domains;
* a distinguishing token on either side that is not generic
  ("Mistral AI" vs "Mistral Health", "Scale AI" vs "Scale Computing");
* differing numbers ("GPT 4" vs "GPT 5", "01.AI" vs "02 AI").

Resolution is deterministic: same input order and seed -> same output.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from rapidfuzz import fuzz, process

from src.core.schemas import EntityMapping
from src.core.urls import registrable_domain
from src.entity_resolution.normalize import compact_key, display_name, normalize_name
from src.entity_resolution.seed import SEED_ENTITIES, SeedEntity
from src.monitoring.metrics import ER_DECISIONS

GENERIC_TOKENS = {
    "ai", "a i", "labs", "lab", "technologies", "technology", "tech", "systems", "software", "research", "the", "and",
    "io", "hq", "app", "apps", "platform", "global", "intelligence", "artificial", "solutions", "studio", "studios", "cloud", "data",
}


@dataclass(slots=True)
class Entity:
    canonical: str
    key: str
    domain: str | None
    aliases: set[str] = field(default_factory=set)
    origin: str = "SEED"


@dataclass(frozen=True, slots=True)
class Resolution:
    original: str
    canonical: str
    method: str
    confidence: float
    needs_review: bool = False
    candidate: str | None = None

    def to_mapping(self, source_url: str | None, kind: str = "ORGANIZATION") -> EntityMapping:
        return EntityMapping(
            originalName=self.original, canonicalName=self.canonical, matchMethod=self.method, confidence=self.confidence,
            sourceUrl=source_url, entityKind=kind, needsReview=self.needs_review, resolvedAt=datetime.now(UTC),
        )


def _ngrams(s: str, n: int = 3) -> Counter[str]:
    s = f"  {s}  "
    return Counter(s[i : i + n] for i in range(len(s) - n + 1))


def cosine_ngrams(a: str, b: str, idf: dict[str, float] | None = None) -> float:
    va, vb = _ngrams(a), _ngrams(b)
    w = (lambda g: idf.get(g, 1.0)) if idf else (lambda g: 1.0)
    dot = sum(va[g] * vb[g] * w(g) ** 2 for g in va.keys() & vb.keys())
    na = math.sqrt(sum((c * w(g)) ** 2 for g, c in va.items()))
    nb = math.sqrt(sum((c * w(g)) ** 2 for g, c in vb.items()))
    return dot / (na * nb) if na and nb else 0.0


SHARED_HOSTS = {
    "github.io", "github.com", "huggingface.co", "vercel.app", "netlify.app", "notion.site", "medium.com", "substack.com",
    "linkedin.com", "twitter.com", "google.com", "sites.google.com", "wixsite.com", "webflow.io", "framer.website", "carrd.co",
    "ycombinator.com", "producthunt.com", "apple.com", "play.google.com",
}


def org_domain(url_or_domain: str | None) -> str | None:
    """Registrable domain usable as identity evidence (None for shared hosting / platforms)."""
    if not url_or_domain:
        return None
    d = registrable_domain(url_or_domain)
    return None if d in SHARED_HOSTS or not d or "." not in d else d


def _tokens(norm: str) -> set[str]:
    return {t for t in norm.split() if t}


def core_tokens(norm: str) -> list[str]:
    return [t for t in norm.split() if t not in GENERIC_TOKENS]


def _distinct_tokens(a: str, b: str) -> tuple[set[str], set[str]]:
    """Non-generic tokens present on one side only. A token that is a near-spelling of a token on the
    other side (typo, plural: 'mistrall'~'mistral', 'robotic'~'robotics') is not distinguishing."""
    ta, tb = _tokens(a), _tokens(b)
    oa = {t for t in ta - tb if t not in GENERIC_TOKENS}
    ob = {t for t in tb - ta if t not in GENERIC_TOKENS}

    def has_twin(t: str, pool: set[str]) -> bool:
        return len(t) >= 5 and any(len(u) >= 5 and fuzz.ratio(t, u) >= 85 and not (re.search(r"\d", t) or re.search(r"\d", u)) for u in pool)

    da = {t for t in oa if not has_twin(t, tb - ta)}
    db = {t for t in ob if not has_twin(t, ta - tb)}
    return da, db


class EntityResolver:
    def __init__(
        self,
        seed: Iterable[SeedEntity] = SEED_ENTITIES,
        *,
        fuzzy_accept: float = 92.0,
        fuzzy_review: float = 80.0,
        semantic_accept: float = 0.90,
        embedder: Callable[[str, str], float] | None = None,
        learn: bool = True,
    ) -> None:
        self.fuzzy_accept, self.fuzzy_review, self.semantic_accept = fuzzy_accept, fuzzy_review, semantic_accept
        self.embedder = embedder
        self.learn = learn
        self.entities: dict[str, Entity] = {}
        self.alias_index: dict[str, str] = {}
        self.domain_index: dict[str, str] = {}
        self._norm_by_key: dict[str, str] = {}
        self.log: list[EntityMapping] = []
        for s in seed:
            self._register(s.canonical, s.domain, s.aliases, origin="SEED")

    # ---------------------------------------------------------- registry
    def _register(self, canonical: str, domain: str | None, aliases: Iterable[str] = (), origin: str = "DISCOVERED") -> Entity:
        key = compact_key(canonical)
        ent = self.entities.get(key)
        if ent is None:
            ent = Entity(canonical, key, domain, set(), origin)
            self.entities[key] = ent
            self._norm_by_key[key] = normalize_name(canonical)
        if domain and not ent.domain:
            ent.domain = domain
        od = org_domain(ent.domain)
        if od:
            self.domain_index.setdefault(od, key)
        for a in aliases:
            ak = compact_key(a)
            if ak and ak not in self.entities:
                self.alias_index.setdefault(ak, key)
                ent.aliases.add(a)
        return ent

    @property
    def seed_count(self) -> int:
        return sum(1 for e in self.entities.values() if e.origin == "SEED")

    # ------------------------------------------------------------ resolve
    def resolve(self, raw: str, *, website: str | None = None, source_url: str | None = None, kind: str = "ORGANIZATION") -> Resolution:
        res = self._resolve(raw, website)
        ER_DECISIONS.labels(res.method).inc()
        self.log.append(res.to_mapping(source_url, kind))
        return res

    def _resolve(self, raw: str, website: str | None) -> Resolution:
        raw = (raw or "").strip()
        if not raw:
            raise ValueError("cannot resolve an empty name")
        norm = normalize_name(raw)
        key = norm.replace(" ", "")
        dom = org_domain(website)
        if not key:
            return Resolution(raw, display_name(raw), "NEW", 1.0)

        if key in self.entities:
            ent = self.entities[key]
            if dom and org_domain(ent.domain) and org_domain(ent.domain) != dom and ent.origin == "SEED":
                return self._new_or_review(raw, norm, dom, candidate=ent.canonical, reason="domain_conflict")
            return Resolution(raw, ent.canonical, "EXACT", 1.0)
        if key in self.alias_index:
            ent = self.entities[self.alias_index[key]]
            if dom and org_domain(ent.domain) and org_domain(ent.domain) != dom:
                return self._new_or_review(raw, norm, dom, candidate=ent.canonical, reason="domain_conflict")
            return Resolution(raw, ent.canonical, "ALIAS", 0.99)
        if dom and dom in self.domain_index:
            ent = self.entities[self.domain_index[dom]]
            self._learn_alias(ent, raw)
            return Resolution(raw, ent.canonical, "DOMAIN", 0.98)

        # ---- fuzzy over canonical + alias keys (normalised, spaced forms)
        choices: dict[str, str] = {}
        for k, n in self._norm_by_key.items():
            choices[f"c:{k}"] = n
        for ak in self.alias_index:
            choices[f"a:{ak}"] = ak
        top = process.extract(norm, choices, scorer=fuzz.WRatio, limit=3)
        if not top:
            return self._new(raw, dom)
        (best_norm, best_score, best_id) = top[0]
        best_key = best_id[2:] if best_id.startswith("c:") else self.alias_index[best_id[2:]]
        runner = next((s for _, s, i in top[1:] if (i[2:] if i.startswith("c:") else self.alias_index[i[2:]]) != best_key), 0.0)
        ent = self.entities[best_key]
        cand_norm = self._norm_by_key[best_key]

        guard = self._guard(norm, cand_norm, dom, ent)
        if best_score >= self.fuzzy_accept and best_score - runner >= 3 and guard is None:
            self._learn_alias(ent, raw)
            return Resolution(raw, ent.canonical, "FUZZY", round(best_score / 100, 4))
        if best_score >= self.fuzzy_review and guard is None:
            sem = self.embedder(norm, cand_norm) if self.embedder else cosine_ngrams(norm, best_norm)
            if sem >= self.semantic_accept:
                self._learn_alias(ent, raw)
                return Resolution(raw, ent.canonical, "SEMANTIC", round(sem, 4))
            return self._new_or_review(raw, norm, dom, candidate=ent.canonical, reason=f"ambiguous fuzzy={best_score:.1f} sem={sem:.2f}")
        if best_score >= self.fuzzy_review and guard is not None:
            # similar strings but evidence says different orgs: keep separate; flag for a human only when very close
            typo_level = fuzz.ratio("".join(core_tokens(norm)), "".join(core_tokens(cand_norm))) >= 85
            if best_score >= self.fuzzy_accept and (guard != "single_token_brand_mismatch" or typo_level):
                return self._new_or_review(raw, norm, dom, candidate=ent.canonical, reason=guard)
        return self._new(raw, dom)

    def _guard(self, a: str, b: str, dom: str | None, ent: Entity) -> str | None:
        if dom and org_domain(ent.domain) and org_domain(ent.domain) != dom:
            return "domain_conflict"
        ca, cb = core_tokens(a), core_tokens(b)
        if "".join(ca) != "".join(cb) and (len(ca) <= 1 or len(cb) <= 1):
            # single-word brands differing by a letter are usually DIFFERENT companies
            # ('Conntour' vs 'Contour', 'Datasite' vs 'DataSuite'): never auto-merge, send to review
            return "single_token_brand_mismatch"
        da, db = _distinct_tokens(a, b)
        if da or db:
            # allow pure concatenation/spacing variants: 'deep mind' vs 'deepmind'
            if a.replace(" ", "") != b.replace(" ", ""):
                return f"distinct_tokens:{sorted(da | db)}"
        if re.findall(r"\d+", a) != re.findall(r"\d+", b):
            return "numeric_mismatch"
        return None

    def _learn_alias(self, ent: Entity, raw: str) -> None:
        if self.learn:
            k = compact_key(raw)
            if k and k not in self.entities:
                self.alias_index.setdefault(k, ent.key)
                ent.aliases.add(raw)

    def _new(self, raw: str, dom: str | None) -> Resolution:
        name = display_name(raw)
        if self.learn:
            self._register(name, dom, (raw,) if compact_key(raw) != compact_key(name) else (), origin="DISCOVERED")
        return Resolution(raw, name, "NEW", 1.0)

    def _new_or_review(self, raw: str, norm: str, dom: str | None, *, candidate: str, reason: str) -> Resolution:
        name = display_name(raw)
        if self.learn and compact_key(name) not in self.entities:
            self._register(name, dom, origin="DISCOVERED")
        return Resolution(raw, name, "REVIEW", 0.5, needs_review=True, candidate=f"{candidate} ({reason})")

    def lookup(self, raw: str) -> str | None:
        """Side-effect-free exact/alias lookup (no logging, no learning). Used for secondary names such as
        Hugging Face handles: they may *confirm* a known entity but must never create one."""
        key = compact_key(raw or "")
        if key in self.entities:
            return self.entities[key].canonical
        if key in self.alias_index:
            return self.entities[self.alias_index[key]].canonical
        return None

    def resolve_with_handle(self, name: str, handle: str | None, *, source_url: str | None = None) -> Resolution:
        """Resolve a display name; if it is unknown (NEW) but its platform handle is a known alias, prefer that entity."""
        known = self.lookup(handle) if handle else None
        if known and self.lookup(name) is None:
            res = Resolution(name, known, "ALIAS", 0.97)  # e.g. 'AI at Meta' (handle meta-llama) -> Meta
            ER_DECISIONS.labels(res.method).inc()
            self.log.append(res.to_mapping(source_url))
            if self.learn:
                self._learn_alias(self.entities[compact_key(known)], name)
            return res
        return self.resolve(name, source_url=source_url)

    def canonical_rows(self) -> list[dict]:
        return [
            {"canonical_name": e.canonical, "normalized_key": e.key, "domain": e.domain, "aliases": sorted(e.aliases), "kind": "ORGANIZATION", "origin": e.origin}
            for e in self.entities.values()
        ]
