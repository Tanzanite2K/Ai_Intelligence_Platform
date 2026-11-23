"""Deterministic job classification: role family, remote eligibility, AI relevance."""

from __future__ import annotations

import re

_FAMILIES: list[tuple[str, re.Pattern[str]]] = [
    ("Research", re.compile(r"\b(research(er)?|scientist|phd|postdoc)\b", re.I)),
    ("Data", re.compile(r"\b(data (scientist|engineer|analyst)|analytics|bi\b|business intelligence|statistician)", re.I)),
    ("Engineering", re.compile(r"\b(engineer(ing)?|developer|swe|sre|devops|architect|programmer|ml ops|mlops|infrastructure|security|technical staff|full[- ]?stack|backend|frontend|software)\b", re.I)),
    ("Product", re.compile(r"\b(product manager|product management|product lead|program manager|tpm|product owner)\b", re.I)),
    ("Design", re.compile(r"\b(design(er)?|ux|ui|user research)\b", re.I)),
    ("Sales", re.compile(r"\b(sales|account (executive|manager)|business development|bdr|sdr|partnerships?|solutions? (engineer|architect)|go[- ]to[- ]market|gtm)\b", re.I)),
    ("Marketing", re.compile(r"\b(marketing|growth|brand|content|communications|pr\b|community|developer relations|devrel)\b", re.I)),
    ("Customer Success", re.compile(r"\b(customer (success|support|experience)|support (engineer|specialist)|technical support)\b", re.I)),
    ("People", re.compile(r"\b(recruit(er|ing)|talent|people|hr\b|human resources)\b", re.I)),
    ("Finance", re.compile(r"\b(finance|accounting|accountant|controller|tax|treasury|fp&a|revenue)\b", re.I)),
    ("Legal", re.compile(r"\b(legal|counsel|attorney|paralegal|compliance|policy)\b", re.I)),
    ("Operations", re.compile(r"\b(operations|ops\b|chief of staff|office|workplace|procurement|logistics|program)\b", re.I)),
]
# Precedence fixes: "Solutions Engineer" is Sales, "Research Engineer" is Research.
_OVERRIDES: list[tuple[str, re.Pattern[str]]] = [
    ("Sales", re.compile(r"\bsolutions? (engineer|architect)\b", re.I)),
    ("Research", re.compile(r"\bresearch (engineer|scientist)\b", re.I)),
    ("Data", re.compile(r"\bdata (scientist|engineer|analyst)\b", re.I)),
    ("Customer Success", re.compile(r"\bsupport engineer\b", re.I)),
]

_AI_RE = re.compile(
    r"\b(ai|a\.i\.|artificial intelligence|machine learning|ml|deep learning|llms?|large language models?|nlp|natural language|"
    r"computer vision|generative|genai|gen ai|neural|pytorch|tensorflow|transformers?|reinforcement learning|rlhf|mlops|"
    r"data scientist|prompt engineer|diffusion|rag)\b",
    re.I,
)


def role_family(title: str, department: str | None = None) -> str:
    for fam, pat in _OVERRIDES:
        if pat.search(title):
            return fam
    for fam, pat in _FAMILIES:
        if pat.search(title):
            return fam
    for fam, pat in _FAMILIES:
        if department and pat.search(department):
            return fam
    return "Other"


def remote_from_text(*texts: str | None) -> bool | None:
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return None
    if re.search(r"\b(remote|anywhere|distributed|work from home|wfh)\b", blob):
        return True
    if re.search(r"\b(on[- ]?site|in[- ]office|hybrid)\b", blob):
        return False
    return None


def is_ai_related(*texts: str | None) -> bool:
    return bool(_AI_RE.search(" ".join(t for t in texts if t)))
