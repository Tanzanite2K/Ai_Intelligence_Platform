"""AI topic taxonomy and a transparent keyword matcher.

Topics are assigned by explicit, case-insensitive whole-word patterns, never by a model. Every assignment keeps the
term that matched so the UI can show *why* a record counts toward a topic. Matching is deliberately conservative:
ambiguous single words (e.g. "agent", "model", "AI") are not patterns on their own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Topic:
    id: str
    label: str
    patterns: tuple[str, ...]
    # Literals that every match contains, for patterns whose literal parts are all inside groups (pre-filter only).
    required_any: tuple[str, ...] = ()

    def regex(self) -> re.Pattern[str]:
        return re.compile(r"(?<![\w-])(?:" + "|".join(self.patterns) + r")(?![\w-])", re.IGNORECASE)


TOPICS: tuple[Topic, ...] = (
    Topic("llm", "Large language models", (r"large language models?", r"LLMs?", r"language models?", r"GPT-?\d[\w.]*", r"chatbots?", r"text-generation")),
    Topic("agents", "AI agents", (r"agentic", r"AI agents?", r"autonomous agents?", r"multi-agent", r"agent(?:ic)? workflows?", r"tool[- ]use", r"computer[- ]use", r"agents? framework")),
    Topic("retrieval", "Retrieval, RAG & embeddings", (r"retrieval[- ]augmented(?: generation)?", r"RAG", r"vector (?:database|search|store)s?", r"semantic search", r"embeddings?", r"sentence-similarity", r"feature-extraction", r"rerank(?:er|ing)?")),
    Topic("multimodal", "Multimodal & vision-language", (r"multi-?modal", r"vision[- ]language", r"VLMs?", r"image-text-to-text", r"any-to-any", r"video-text-to-text", r"visual question answering")),
    Topic("generative_media", "Image & video generation", (r"text-to-image", r"image generation", r"diffusion(?: models?)?", r"text-to-video", r"video generation", r"image-to-image", r"image-to-video", r"generative art")),
    Topic("vision", "Computer vision", (r"computer vision", r"object detection", r"object-detection", r"image classification", r"image-classification", r"(?:image |semantic |instance )?segmentation", r"image-segmentation", r"depth estimation", r"depth-estimation", r"OCR", r"pose estimation")),
    Topic("speech_audio", "Speech & audio", (r"speech", r"text-to-speech", r"automatic-speech-recognition", r"ASR", r"TTS", r"voice(?: AI| agents?| assistants?)?", r"audio", r"text-to-audio", r"audio-classification")),
    Topic("robotics", "Robotics & embodied AI", (r"robots?", r"robotics?", r"embodied", r"humanoids?", r"vision-language-action", r"VLA", r"(?:robotic|dexterous|bimanual) manipulation", r"manipulation tasks?")),
    Topic("reinforcement_learning", "Reinforcement learning", (r"reinforcement learning", r"RLHF", r"RLVR", r"reward models?", r"policy optimi[sz]ation", r"GRPO", r"PPO", r"DPO")),
    Topic("reasoning", "Reasoning & math", (r"reasoning", r"chain[- ]of[- ]thought", r"mathematical", r"math(?:ematics)? problems?", r"theorem proving", r"test-time (?:compute|scaling)")),
    Topic("code", "Code generation & developer tools", (r"code generation", r"coding (?:assistants?|agents?)", r"software engineering agents?", r"developer tools?", r"program synthesis", r"code LLMs?", r"code models?", r"SWE-bench")),
    Topic("safety", "AI safety, alignment & trust", (r"(?:AI|safety|value|human|preference|LLM) alignment", r"aligned with human (?:values|preferences)", r"AI safety", r"jailbreak(?:s|ing)?", r"red[- ]teaming", r"guardrails?", r"hallucinations?", r"interpretability")),
    Topic("efficiency", "Efficient training & inference", (r"quanti[sz]ation", r"quanti[sz]ed", r"distillation", r"pruning", r"speculative decoding", r"KV[- ]cache", r"LoRA", r"mixture[- ]of[- ]experts", r"MoE", r"sparse attention")),
    Topic("infrastructure", "AI infrastructure & compute", (r"GPUs?", r"model serving", r"inference (?:platform|infrastructure|engine|serving)", r"MLOps", r"AI infrastructure", r"data cent(?:er|re)s?", r"accelerators?", r"AI chips?", r"Infrastructure")),
    Topic("health_bio", "Healthcare & life sciences", (r"health ?care", r"health tech", r"medical", r"clinical", r"biotech", r"drug discovery", r"proteins?", r"genomics?", r"Healthcare IT")),
    Topic("finance", "Finance & fintech", (r"fintech", r"financial", r"banking", r"insurance", r"trading", r"payments?")),
    Topic("open_models", "Open-source & open-weight models", (r"open[- ]source", r"open[- ]weights?", r"open models?")),
    # Nearly every paper *uses* a benchmark; this topic counts records whose contribution IS a benchmark/dataset.
    Topic("evaluation", "New benchmarks & datasets", (r"(?:introduce|present|release|propose|construct|build|curate)[sd]? (?:a |an |the )?(?:[\w-]+ ){0,3}(?:benchmark|dataset)s?",
                                                        r"(?:new|novel|comprehensive|large-scale) (?:benchmark|dataset)s?", r"synthetic data", r"data label(?:l)?ing", r"evaluation (?:framework|suite|harness)"),
          required_any=("benchmark", "dataset")),
    Topic("autonomy", "Autonomous vehicles & drones", (r"autonomous (?:driving|vehicles?)", r"self-driving", r"drones?", r"autonomous systems?")),
    Topic("security", "AI & cybersecurity", (r"cyber ?security", r"security", r"threat detection", r"fraud detection", r"vulnerabilit(?:y|ies)")),
)
TOPIC_BY_ID: dict[str, Topic] = {t.id: t for t in TOPICS}


def anchor(pattern: str) -> str | None:
    """A lowercase literal that every match of ``pattern`` must contain, or None if there is none.

    Groups, character classes, escapes and optional characters are removed; the longest remaining letter run is kept.
    Used only as a fast pre-filter: when no anchor of a topic occurs in the text, its regex cannot match.
    """
    p = pattern.lower()
    p = re.sub(r"\((?:[^()]|\([^()]*\))*\)[?*]?(?:\{\d*,?\d*\})?", " ", p)  # (?:...) groups, optional or not
    p = re.sub(r"\[[^\]]*\][?*]?", " ", p)                                    # [...] classes
    p = re.sub(r"\\.", " ", p)                                                  # escapes like \d
    p = re.sub(r".[?*]", " ", p)                                                 # optional single characters
    runs = re.findall(r"[a-z]{3,}", p)
    return max(runs, key=len) if runs else None


_COMPILED: tuple[tuple[Topic, re.Pattern[str], tuple[str, ...] | None], ...] = tuple(
    (t, t.regex(), None if any(anchor(p) is None for p in t.patterns) and not t.required_any
     else tuple({a for p in t.patterns if (a := anchor(p))} | set(t.required_any)))
    for t in TOPICS
)


def match_topics(text: str) -> dict[str, str]:
    """{topic_id: first matching term} for the given text."""
    out: dict[str, str] = {}
    if not text:
        return out
    low = text.lower()
    for t, rx, anchors in _COMPILED:
        if anchors is not None and not any(a in low for a in anchors):
            continue
        m = rx.search(text)
        if m:
            out[t.id] = m.group(0)
    return out
