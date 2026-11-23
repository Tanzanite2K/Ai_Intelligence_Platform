from src.entity_resolution.normalize import compact_key, display_name, normalize_name
from src.entity_resolution.resolver import EntityResolver
from src.entity_resolution.seed import SEED_ENTITIES
from tests.er_cases import LIVE_REGRESSIONS, NEGATIVE, POSITIVE


def test_seed_has_at_least_50_unique_entities() -> None:
    assert len(SEED_ENTITIES) >= 50
    assert len({compact_key(e.canonical) for e in SEED_ENTITIES}) == len(SEED_ENTITIES)


def test_normalisation_layers() -> None:
    assert normalize_name("  ＯｐｅｎＡＩ,   Inc. ") == "openai"  # unicode width + suffix + whitespace
    assert normalize_name("Société Générale S.A.") == "societe generale"  # accents + legal suffix
    assert normalize_name("Weights & Biases") == "weights and biases"
    assert compact_key("Open AI") == compact_key("OpenAI") == compact_key("OPENAI")
    assert display_name("Acme Robotics, Inc.") == "Acme Robotics"


def evaluate() -> dict[str, float]:
    r = EntityResolver()
    tp = fn = fp = tn = 0
    for raw, site, exp in POSITIVE:
        got = r.resolve(raw, website=site).canonical
        if exp is None:
            continue
        tp += got == exp
        fn += got != exp
    seed_names = {e.canonical for e in SEED_ENTITIES}
    for raw, site in NEGATIVE:
        res = EntityResolver().resolve(raw, website=site)
        merged = res.canonical in seed_names and res.method in ("FUZZY", "SEMANTIC", "ALIAS", "EXACT", "DOMAIN")
        fp += merged
        tn += not merged
    return {"precision": tp / (tp + fp) if tp + fp else 1.0, "recall": tp / (tp + fn), "false_merges": fp, "tp": tp, "fn": fn}


def test_resolution_quality_metrics() -> None:
    m = evaluate()
    assert m["false_merges"] == 0, m  # never merge different organisations
    assert m["precision"] == 1.0
    assert m["recall"] >= 0.95, m


def test_canonical_examples_from_assessment() -> None:
    r = EntityResolver()
    for raw in ("OpenAI", "OpenAI, Inc.", "Open AI", "OpenAI Inc.", "OPENAI"):
        assert r.resolve(raw).canonical == "OpenAI"


def test_methods_and_mapping_log() -> None:
    r = EntityResolver()
    assert r.resolve("OpenAI").method == "EXACT"
    assert r.resolve("meta-llama").method == "ALIAS"
    assert r.resolve("Mistrall AI").method == "REVIEW"  # single-word brand typo is reviewed, not merged
    r.resolve("Unsloth AI")
    res = r.resolve("Unsloth")
    assert (res.canonical, res.method) == ("Unsloth AI", "FUZZY")  # generic-suffix variant: same core brand
    assert r.resolve("Foo Labs", website="https://huggingface.co/foo").method == "NEW"  # shared host is not identity
    log = r.log[-1].model_dump(by_alias=True, mode="json")
    assert set(log) >= {"originalName", "canonicalName", "matchMethod", "confidence", "resolvedAt", "sourceUrl"}


def test_dynamic_learning_of_new_entities() -> None:
    r = EntityResolver()
    first = r.resolve("Acme Robotics, Inc.")
    assert (first.canonical, first.method) == ("Acme Robotics", "NEW")
    again = r.resolve("ACME ROBOTICS")
    assert (again.canonical, again.method) == ("Acme Robotics", "EXACT")
    typo = r.resolve("Acme Robotic")
    assert typo.canonical == "Acme Robotics" and typo.method == "FUZZY"


def test_domain_conflict_goes_to_review() -> None:
    res = EntityResolver().resolve("Writer", website="https://writerapp.io")
    assert res.needs_review and res.method == "REVIEW"


def test_deterministic() -> None:
    names = [n for n, _, _ in POSITIVE] + [n for n, _ in NEGATIVE]
    a = [EntityResolver().resolve(n).canonical for n in names]
    b = [EntityResolver().resolve(n).canonical for n in names]
    assert a == b


def test_live_false_merges_never_recur() -> None:
    for first, later in LIVE_REGRESSIONS:
        r = EntityResolver()
        r.resolve(first)
        res = r.resolve(later)
        assert res.canonical != first, (first, later, res)
        assert res.method in ("NEW", "REVIEW")


def test_same_name_different_companies_get_different_keys() -> None:
    from src.core.schemas import StartupRecord

    def s(url: str, site: str) -> StartupRecord:
        return StartupRecord.model_validate({"source": {"name": "YC", "url": url}, "content": {"entityName": "Contour", "website": site},
                                             "provenance": {"extractionMethod": "STRUCTURED_API"}})

    a = s("https://www.ycombinator.com/companies/contour", "https://contour.dev")
    b = s("https://www.ycombinator.com/companies/contourholdings", "https://contour.com")
    c = s("https://huggingface.co/contour", "https://huggingface.co/contour")  # shared host -> name-only key
    assert a.dedup_hash() != b.dedup_hash() and c.natural_key() == "org:contour"


def test_handle_confirms_known_entity_but_never_creates_one() -> None:
    r = EntityResolver()
    assert r.resolve_with_handle("AI at Meta", "meta-llama").canonical == "Meta"
    assert r.resolve_with_handle("Beijing Academy of Artificial Intelligence", "BAAI").canonical == "Beijing Academy of Artificial Intelligence"
    assert r.lookup("BAAI") is None  # the handle was not registered as an entity
    assert r.resolve_with_handle("Mistral AI_", "mistralai").canonical == "Mistral AI"
