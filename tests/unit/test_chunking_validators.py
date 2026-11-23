import pytest

from src.extraction.chunking import bisect_chunk, clean_document, density, estimate_tokens, plan_chunks, with_lead
from src.extraction.providers.base import MalformedResponseError
from src.extraction.validators import parse_json_loose, quote_in_source, validate_fields, value_supported


def doc(n: int, filler: str = "The committee met and discussed several general topics at length without decisions.") -> str:
    return "Acme AI launches Atlas\n\n" + "\n\n".join(f"{filler} Item {i}." for i in range(n))


def test_estimate_is_conservative() -> None:
    text = "word " * 1000  # ~1000-1250 real BPE tokens
    assert estimate_tokens(text) >= 1250
    assert estimate_tokens("模型" * 100) >= 200  # CJK ~1 token/char


def test_every_chunk_fits_budget_including_overhead() -> None:
    plan = plan_chunks(doc(400), budget_tokens=2000, prompt_overhead_tokens=500, max_chunks=50)
    assert len(plan.chunks) > 3
    for c in plan.chunks:
        assert estimate_tokens(with_lead(plan.lead, c)) + 500 <= 2000


def test_giant_unbroken_text_is_hard_split() -> None:
    plan = plan_chunks("x" * 5 + " " + ("abcdefghij " * 6000), budget_tokens=1500, prompt_overhead_tokens=300, max_chunks=100)
    assert all(estimate_tokens(c) <= 1500 - 300 for c in plan.chunks)


def test_boilerplate_and_repeats_removed() -> None:
    t = "Accept all cookies\nHome\nHome\nReal content sentence that matters a lot.\nSubscribe to our newsletter"
    out = clean_document(t)
    assert "cookies" not in out and "Subscribe" not in out and out.count("Home") == 1


def test_max_chunks_drops_low_density_keeps_dense_and_reports() -> None:
    dense = "Pricing: the Pro plan costs $29 per month; Enterprise requires contact sales. Founded 2023, 45 employees, Series A."
    text = doc(300) + "\n\n" + dense + "\n\n" + doc(300)
    plan = plan_chunks(text, budget_tokens=1500, prompt_overhead_tokens=300, max_chunks=2)
    assert len(plan.chunks) <= 2 and plan.dropped_units > 0 and plan.notes
    assert any("$29 per month" in c for c in plan.chunks)
    assert density(dense) > density("The committee met and discussed several general topics.")


def test_bisect_on_boundary() -> None:
    a, b = bisect_chunk("First paragraph here.\n\nSecond paragraph here.")
    assert a == "First paragraph here." and b == "Second paragraph here."


def test_budget_too_small_raises() -> None:
    with pytest.raises(ValueError):
        plan_chunks(doc(3), budget_tokens=500, prompt_overhead_tokens=480)


# ------------------------------------------------------------ JSON parsing
@pytest.mark.parametrize(
    "raw",
    ['{"fields": {}}', '```json\n{"fields": {}}\n```', 'Sure! Here it is: {"fields": {"a": {"value": "}"}}} hope it helps'],
)
def test_parse_json_loose(raw: str) -> None:
    assert "fields" in parse_json_loose(raw)


@pytest.mark.parametrize("raw", ["no json here", '{"fields": {"a": ', "[1,2,3]"])
def test_parse_json_malformed(raw: str) -> None:
    with pytest.raises(MalformedResponseError):
        parse_json_loose(raw)


# ------------------------------------------------------------- evidence
SRC = "Atlas by Acme Robotics is free to start; upgrade to the Pro plan for $20/month. The team has 1,200 employees. Fully remote."


def test_evidence_must_be_in_source() -> None:
    assert quote_in_source("upgrade to the Pro plan for $20/month", SRC)
    assert quote_in_source("UPGRADE   to the pro plan for $20/month", SRC)
    assert not quote_in_source("Atlas is used by 3 million developers", SRC)


def test_value_support_rules() -> None:
    assert value_supported("pricing_model", "FREEMIUM", "free to start; upgrade to the Pro plan", SRC)
    assert not value_supported("pricing_model", "ENTERPRISE", "free to start; upgrade to the Pro plan", SRC)
    assert value_supported("employee_count", 1200, "The team has 1,200 employees", SRC)
    assert not value_supported("employee_count", 1500, "The team has 1,200 employees", SRC)
    assert value_supported("is_remote", True, "Fully remote", SRC)


def test_validate_fields_rejects_hallucination_and_keeps_verified() -> None:
    raw = {"fields": {
        "pricing_model": {"value": "FREEMIUM", "evidence": "free to start; upgrade to the Pro plan for $20/month"},
        "employee_count": {"value": 5000, "evidence": "The company has 5,000 employees worldwide"},  # fabricated quote
        "startup_name": {"value": "Acme Robotics", "evidence": None},  # no evidence
        "ceo": {"value": "Jane", "evidence": "Jane"},  # unexpected field
    }}
    rep = validate_fields(raw, {"pricing_model", "employee_count", "startup_name"}, SRC)
    assert rep.accepted == {"pricing_model": "FREEMIUM"}
    assert "hallucination" in rep.rejected["employee_count"]
    assert rep.rejected["startup_name"] == "no evidence quote"
    assert "ceo" in rep.rejected


def test_list_field_keeps_supported_items_and_drops_unsupported() -> None:
    src = "Baseten and Hugging Face partnered with Google on Gemma decision models."
    raw = {"fields": {"organizations": {"value": ["Baseten", "Hugging Face", "OpenAI"], "evidence": "Baseten and Hugging Face partnered with Google"}}}
    rep = validate_fields(raw, {"organizations"}, src)
    assert rep.accepted == {"organizations": ["Baseten", "Hugging Face"]}  # 'OpenAI' is not in the source -> dropped
    assert "dropped unsupported" in rep.fields["organizations"].reason


def test_list_field_all_unsupported_is_rejected() -> None:
    raw = {"fields": {"organizations": {"value": ["OpenAI", "Anthropic"], "evidence": "OpenAI and Anthropic announced"}}}
    rep = validate_fields(raw, {"organizations"}, "Baseten released a model.")
    assert rep.accepted == {} and "hallucination" in rep.rejected["organizations"]


def test_only_gemini_configured_chain_has_no_failure() -> None:
    from pydantic import SecretStr

    from src.config.settings import Settings
    from src.extraction.providers.vendors import build_providers

    s = Settings(_env_file=None, gemini_api_key=SecretStr("k"), groq_api_key="", deepseek_api_key="")  # type: ignore[call-arg]
    provs = build_providers(s)
    assert [p.name for p in provs if p.configured] == ["gemini"]  # optional providers simply absent from the chain


# --- regression: 1b live check (real MarkTechPost article, Gemini answered, accepted={}) -------------------------------
from src.extraction.orchestrator import LLMOrchestrator, normalise_object_list  # noqa: E402
from src.extraction.tasks import NEWS_ENTITIES  # noqa: E402
from src.extraction.validators import split_quotes  # noqa: E402

ARTICLE = ("Baseten Releases Bison-1 and Groundhog-1\n\nBaseten released two decision models built on Gemma 4 from Google. "
           "The weights are on Hugging Face.\nLiquid AI and Base Labs contributed evaluation data.")
VALID = ["Baseten", "Google", "Hugging Face", "Liquid AI"]
EVIDENCE_SHAPES = {
    "pipe": "Baseten released two decision models | Gemma 4 from Google | The weights are on Hugging Face | Liquid AI and Base Labs contributed",
    "semicolon": "Baseten released two decision models; Gemma 4 from Google; The weights are on Hugging Face; Liquid AI and Base Labs contributed",
    "newline": "Baseten released two decision models\nGemma 4 from Google\nThe weights are on Hugging Face\nLiquid AI and Base Labs contributed",
    "ellipsis": "Baseten released two decision models ... Gemma 4 from Google ... The weights are on Hugging Face ... Liquid AI and Base Labs",
}


def _validate(fields: dict) -> object:
    s = __import__("src.config.settings", fromlist=["get_settings"]).get_settings()
    return LLMOrchestrator([], s)._validate(NEWS_ENTITIES, {"fields": fields}, ARTICLE)


@pytest.mark.parametrize("shape", list(EVIDENCE_SHAPES))
def test_valid_orgs_accepted_for_every_evidence_separator(shape: str) -> None:
    rep = _validate({"organizations": {"value": VALID, "evidence": EVIDENCE_SHAPES[shape]}})
    assert rep.accepted.get("organizations") == VALID, (shape, rep.rejected)


@pytest.mark.parametrize("shape", list(EVIDENCE_SHAPES))
def test_unverifiable_orgs_dropped_for_every_separator(shape: str) -> None:
    rep = _validate({"organizations": {"value": [*VALID, "OpenAI", "Anthropic"], "evidence": EVIDENCE_SHAPES[shape]}})
    assert rep.accepted["organizations"] == VALID  # names absent from the article never survive


def test_list_of_objects_with_per_item_evidence_accepted() -> None:
    items = [{"name": "Baseten", "evidence": "Baseten released two decision models"},
             {"name": "Google", "evidence": "Gemma 4 from Google"},
             {"organization": "Hugging Face", "evidence": "The weights are on Hugging Face"},
             {"name": "Mistral AI", "evidence": "Mistral AI also released a model"}]  # fabricated item + fabricated quote
    rep = _validate({"organizations": {"value": items}})
    assert rep.accepted["organizations"] == ["Baseten", "Google", "Hugging Face"]


def test_all_fabricated_names_with_fabricated_quotes_rejected() -> None:
    rep = _validate({"organizations": {"value": ["OpenAI", "Anthropic"], "evidence": "OpenAI; Anthropic partnered with OpenAI"}})
    assert "organizations" not in rep.accepted and "hallucination" in rep.rejected["organizations"]


def test_real_names_with_fabricated_quotes_rejected() -> None:
    # names occur in the article but no quote is verbatim -> no grounding evidence -> rejected
    rep = _validate({"organizations": {"value": ["Baseten", "Google"], "evidence": "Baseten was acquired by Google last year"}})
    assert "organizations" not in rep.accepted


def test_fabricated_names_with_real_quotes_rejected() -> None:
    rep = _validate({"organizations": {"value": ["OpenAI", "Meta"], "evidence": "Baseten released two decision models"}})
    assert "organizations" not in rep.accepted and "name does not occur in the source" in rep.rejected["organizations"]


def test_split_quotes_and_object_normalisation_are_conservative() -> None:
    assert split_quotes("a | bb | Baseten; Google…Hugging Face") == ["Baseten", "Google", "Hugging Face"]  # <3 chars dropped
    assert normalise_object_list({"value": [{"name": "X Corp", "evidence": "q1"}, "Y Inc", {"foo": 1}]}) == {"value": ["X Corp", "Y Inc"], "evidence": "q1"}


def test_comma_joined_evidence_is_not_split_into_bare_name_quotes() -> None:
    # ', ' is not a quote separator (commas occur inside real quotes); a comma-joined blob is not verbatim -> rejected
    ev = "Baseten released two decision models, Gemma 4 from Google, The weights are on Hugging Face"
    rep = _validate({"organizations": {"value": ["Baseten", "Google", "Hugging Face"], "evidence": ev}})
    assert "organizations" not in rep.accepted


# --- 1b confirmed root cause: list value returned as ONE comma-separated string ---------------------------------------
from src.extraction.orchestrator import FieldSpec, ListFormatError, coerce  # noqa: E402
from src.extraction.tasks import PRODUCT_PRICING  # noqa: E402

FOOTER_ARTICLE = (
    "Baseten Releases Bison-1 and Groundhog-1\n\nBaseten has released bison-1 and groundhog-1, built on Google’s \nGemma 4\n. "
    "Runs on Baseten Model APIs, plus open BF16 weights on Hugging Face. bison-1 is 1 point above Liquid AI’s d1.2-ultra (75).\n"
    "Connect with us\n\nThe post \nBaseten Releases Bison-1 and Groundhog-1\n appeared first on \nMarkTechPost\n."
)
GOOD_EV = "Baseten has released bison-1 and groundhog-1 | built on Google’s Gemma 4 | Liquid AI’s d1.2-ultra | open BF16 weights on Hugging Face"


def _v(fields: dict, src: str = FOOTER_ARTICLE, split_fallback: bool = False) -> object:
    s = __import__("src.config.settings", fromlist=["get_settings"]).get_settings()
    return LLMOrchestrator([], s)._validate(NEWS_ENTITIES, {"fields": fields}, src, split_fallback=split_fallback)


def test_valid_json_array_with_individual_quotes_accepted() -> None:
    rep = _v({"organizations": {"value": ["Baseten", "Google", "Liquid AI", "Hugging Face"], "evidence": GOOD_EV}})
    assert rep.accepted["organizations"] == ["Baseten", "Google", "Liquid AI", "Hugging Face"]
    assert set(rep.fields["organizations"].item_reasons.values()) == {"verified"}


def test_comma_separated_string_is_never_wrapped_as_one_item() -> None:
    spec = FieldSpec("string_list", "orgs")
    with pytest.raises(ListFormatError):
        coerce(spec, "Baseten, Google")
    with pytest.raises(MalformedResponseError, match="JSON array"):  # -> structured repair retry in _call
        _v({"organizations": {"value": "Baseten, Google", "evidence": GOOD_EV}})


def test_comma_string_split_fallback_only_on_repair_attempt_and_per_name() -> None:
    rep = _v({"organizations": {"value": "Baseten, Google, OpenAI", "evidence": GOOD_EV}}, split_fallback=True)
    assert rep.accepted["organizations"] == ["Baseten", "Google"]
    assert rep.fields["organizations"].item_reasons["OpenAI"] == "name does not occur in the source"


def test_string_with_one_valid_and_one_unsupported_name() -> None:
    rep = _v({"organizations": {"value": "Baseten; Anthropic", "evidence": GOOD_EV}}, split_fallback=True)
    assert rep.accepted["organizations"] == ["Baseten"]


def test_name_in_article_but_without_its_own_quote_is_dropped() -> None:
    # Hugging Face occurs in the article, but no evidence quote names it
    rep = _v({"organizations": {"value": ["Baseten", "Hugging Face"], "evidence": "Baseten has released bison-1 and groundhog-1"}})
    assert rep.accepted["organizations"] == ["Baseten"]
    assert rep.fields["organizations"].item_reasons["Hugging Face"] == "no evidence quote mentions this name"


def test_valid_quote_for_another_org_never_validates_this_org() -> None:
    rep = _v({"organizations": {"value": ["Google"], "evidence": "open BF16 weights on Hugging Face"}})
    assert "organizations" not in rep.accepted
    assert rep.fields["organizations"].item_reasons["Google"] == "no evidence quote mentions this name"


def test_fabricated_quote_rejected_even_when_name_is_real() -> None:
    # near-verbatim (fuzzy-similar) but not in the article; per-item quotes need an exact normalised match
    rep = _v({"organizations": {"value": ["Google"], "evidence": "Baseten has released bison-1 with Google"}})
    assert "organizations" not in rep.accepted
    assert "not verbatim" in rep.fields["organizations"].item_reasons["Google"]


def test_bare_name_is_not_a_quote() -> None:
    rep = _v({"organizations": {"value": ["Google"], "evidence": "Google"}})
    assert "organizations" not in rep.accepted


def test_publisher_only_in_footer_is_rejected() -> None:
    for ev in ("The post appeared first on MarkTechPost",          # the captured, non-verbatim quote
               "appeared first on \nMarkTechPost"):                # verbatim footer text
        rep = _v({"organizations": {"value": ["Baseten", "MarkTechPost"], "evidence": GOOD_EV + " | " + ev}})
        assert rep.accepted["organizations"] == ["Baseten"]
        assert rep.fields["organizations"].item_reasons["MarkTechPost"] in (
            "its quote is not verbatim in the source (fabricated or paraphrased)",
            "only supported by publisher/attribution text, not by the article content")


def test_article_about_a_publisher_is_accepted_on_body_evidence() -> None:
    src = ("MarkTechPost launches an AI research newsletter\n\nMarkTechPost announced a weekly newsletter covering AI papers. "
           "The post MarkTechPost launches an AI research newsletter appeared first on MarkTechPost.")
    rep = _v({"organizations": {"value": ["MarkTechPost"], "evidence": "MarkTechPost announced a weekly newsletter covering AI papers"}}, src)
    assert rep.accepted["organizations"] == ["MarkTechPost"]


def test_captured_1b_response_replay_offline() -> None:
    raw = ('{"fields": {"organizations": {"value": "Baseten, Google, Liquid AI, Hugging Face, MarkTechPost", "evidence": '
           '"Baseten has released bison-1 and groundhog-1 | built on Google’s Gemma 4 | Liquid AI’s d1.2-ultra | '
           'open BF16 weights on Hugging Face | The post appeared first on MarkTechPost"}}}')
    with pytest.raises(MalformedResponseError):  # first attempt -> repair request
        _v(parse_json_loose(raw)["fields"])
    rep = _v(parse_json_loose(raw)["fields"], split_fallback=True)  # repair attempt returning the same shape
    assert rep.accepted["organizations"] == ["Baseten", "Google", "Liquid AI", "Hugging Face"]


def test_pricing_extraction_unchanged() -> None:
    src = "Atlas\n\nAtlas is free to start; upgrade to the Pro plan for $20/month. Enterprise plans: contact sales."
    s = __import__("src.config.settings", fromlist=["get_settings"]).get_settings()
    rep = LLMOrchestrator([], s)._validate(PRODUCT_PRICING, {"fields": {"pricing_model": {"value": "FREEMIUM",
                                           "evidence": "Atlas is free to start; upgrade to the Pro plan for $20/month"}}}, src)
    assert rep.accepted == {"pricing_model": "FREEMIUM"}
