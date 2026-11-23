"""Concrete providers: Google Gemini Flash, Groq (Llama), DeepSeek."""

from __future__ import annotations

from typing import Any

from src.config.settings import Settings
from src.extraction.providers.base import (
    BaseProvider,
    IncompleteResponseError,
    InvalidRequestError,
    LLMRequest,
    LLMResponse,
    MalformedResponseError,
)


class GeminiProvider(BaseProvider):
    """Gemini generateContent.

    Newer Flash models "think" before answering and those hidden tokens count against
    ``maxOutputTokens``: a small budget then ends in ``MAX_TOKENS`` with no visible text. We therefore
    (a) request ``thinkingBudget`` (default 0) and drop it automatically if a model rejects the field,
    (b) when thinking cannot be disabled, reserve output headroom, and (c) ignore ``thought`` parts.
    """

    name = "gemini"
    BASE = "https://generativelanguage.googleapis.com/v1beta/models"
    MIN_OUTPUT_WITH_THINKING = 4096

    def __init__(self, *args: Any, thinking_budget: int | None = 0, **kw: Any) -> None:
        super().__init__(*args, **kw)
        self.thinking_budget = thinking_budget

    async def complete(self, req: LLMRequest) -> LLMResponse:
        try:
            return await super().complete(req)
        except InvalidRequestError as e:
            msg = str(e)
            # some models reject thinkingConfig with a generic "invalid argument": retry ONCE without it
            if self.thinking_budget is not None and ("think" in msg.lower() or "INVALID_ARGUMENT" in msg or "invalid argument" in msg.lower()):
                self.thinking_budget = None  # model does not accept thinkingConfig/budget: retry once without it
                return await super().complete(req)
            raise

    def build_request(self, req: LLMRequest) -> tuple[str, dict[str, str], dict[str, Any]]:
        max_out = req.max_output_tokens if self.thinking_budget == 0 else max(req.max_output_tokens, self.MIN_OUTPUT_WITH_THINKING)
        cfg: dict[str, Any] = {"temperature": req.temperature, "maxOutputTokens": max_out}
        if self.thinking_budget is not None:
            cfg["thinkingConfig"] = {"thinkingBudget": self.thinking_budget}
        if req.json_mode:
            cfg["responseMimeType"] = "application/json"
        payload = {
            "systemInstruction": {"parts": [{"text": req.system}]},
            "contents": [{"role": "user", "parts": [{"text": req.user}]}],
            "generationConfig": cfg,
        }
        # key in a header, never in the URL (URLs end up in proxy/access logs)
        return f"{self.BASE}/{self.model}:generateContent", {"x-goog-api-key": self._api_key or "", "Content-Type": "application/json"}, payload

    def parse_response(self, data: dict[str, Any], latency: float) -> LLMResponse:
        cands = data.get("candidates") or []
        if not cands:
            fb = (data.get("promptFeedback") or {}).get("blockReason")
            raise MalformedResponseError(f"gemini: no candidates (blockReason={fb})", provider=self.name)
        c = cands[0]
        parts = (c.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))  # never treat hidden reasoning as the answer
        usage = data.get("usageMetadata") or {}
        if c.get("finishReason") == "MAX_TOKENS":
            if usage.get("thoughtsTokenCount") and self.thinking_budget is not None:
                self.thinking_budget = None  # budget was ignored: next call reserves headroom instead
            raise IncompleteResponseError(
                f"gemini: output truncated (MAX_TOKENS; thoughts={usage.get('thoughtsTokenCount', 0)}, visible_chars={len(text)})", provider=self.name)
        if c.get("finishReason") not in (None, "STOP") and not text:
            raise MalformedResponseError(f"gemini: no text (finishReason={c.get('finishReason')})", provider=self.name)
        return LLMResponse(text, self.name, self.model, usage.get("promptTokenCount"), usage.get("candidatesTokenCount"), latency)


class OpenAICompatibleProvider(BaseProvider):
    endpoint: str = ""

    def build_request(self, req: LLMRequest) -> tuple[str, dict[str, str], dict[str, Any]]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": req.system}, {"role": "user", "content": req.user}],
            "temperature": req.temperature,
            "max_tokens": req.max_output_tokens,
        }
        if req.json_mode:
            payload["response_format"] = {"type": "json_object"}
        return self.endpoint, {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}, payload

    def parse_response(self, data: dict[str, Any], latency: float) -> LLMResponse:
        choices = data.get("choices") or []
        if not choices:
            raise MalformedResponseError(f"{self.name}: no choices", provider=self.name)
        ch = choices[0]
        if ch.get("finish_reason") == "length":
            raise IncompleteResponseError(f"{self.name}: output truncated (finish_reason=length)", provider=self.name)
        text = (ch.get("message") or {}).get("content") or ""
        usage = data.get("usage") or {}
        return LLMResponse(text, self.name, self.model, usage.get("prompt_tokens"), usage.get("completion_tokens"), latency)


class GroqProvider(OpenAICompatibleProvider):
    name = "groq"
    endpoint = "https://api.groq.com/openai/v1/chat/completions"


class DeepSeekProvider(OpenAICompatibleProvider):
    name = "deepseek"
    endpoint = "https://api.deepseek.com/chat/completions"


def build_providers(s: Settings) -> list[BaseProvider]:
    def key(v: Any) -> str | None:
        return v.get_secret_value() if v else None

    registry: dict[str, BaseProvider] = {
        "gemini": GeminiProvider(api_key=key(s.gemini_api_key), model=s.gemini_model, input_token_budget=s.gemini_input_token_budget, rpm=s.gemini_rpm,
                                 timeout_s=s.llm_timeout_s, thinking_budget=s.gemini_thinking_budget),
        "groq": GroqProvider(api_key=key(s.groq_api_key), model=s.groq_model, input_token_budget=s.groq_input_token_budget, rpm=s.groq_rpm, timeout_s=s.llm_timeout_s),
        "deepseek": DeepSeekProvider(api_key=key(s.deepseek_api_key), model=s.deepseek_model, input_token_budget=s.deepseek_input_token_budget, rpm=s.deepseek_rpm, timeout_s=s.llm_timeout_s),
    }
    return [registry[n] for n in s.providers_order if n in registry]
