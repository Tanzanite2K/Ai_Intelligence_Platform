"""Provider abstraction + normalised error taxonomy.

Every vendor's failure modes are mapped onto a small set of exceptions so the
orchestrator can apply one policy:

=======================  =========================================  ==================================
Exception                Typical trigger                            Orchestrator policy
=======================  =========================================  ==================================
RateLimitError           429, Gemini RESOURCE_EXHAUSTED             wait Retry-After / backoff+jitter,
                                                                    then fall back
PayloadTooLargeError     413, Groq TPM "request too large",         split the chunk (never resend
                         400 "context length" / "too many tokens"   the same payload) and retry
ProviderTimeoutError     client timeout                             backoff retry, then fall back
ProviderUnavailableError 5xx / overloaded                           backoff retry, then fall back
IncompleteResponseError  finish_reason=length / MAX_TOKENS          split chunk (output too big)
MalformedResponseError   non-JSON / schema-invalid output           one repair retry, then fall back
AuthError                401/403, missing key                       skip provider (not retryable)
InvalidRequestError      other 4xx                                  fall back
=======================  =========================================  ==================================
"""

from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import aiohttp

from src.core.ratelimit import TokenBucket
from src.core.retry import parse_retry_after
from src.monitoring.logging import get_logger, redact

log = get_logger(__name__)


class ProviderError(Exception):
    retryable: bool = False

    def __init__(self, message: str, *, provider: str = "", status: int | None = None) -> None:
        super().__init__(redact(message))
        self.provider, self.status = provider, status


class RateLimitError(ProviderError):
    retryable = True

    def __init__(self, message: str, *, retry_after: float | None = None, **kw: Any) -> None:
        super().__init__(message, **kw)
        self.retry_after = retry_after


class PayloadTooLargeError(ProviderError):
    retryable = True  # retryable *after splitting*


class IncompleteResponseError(ProviderError):
    retryable = True


class ProviderTimeoutError(ProviderError):
    retryable = True


class ProviderUnavailableError(ProviderError):
    retryable = True


class MalformedResponseError(ProviderError):
    retryable = True


class AuthError(ProviderError):
    pass


class InvalidRequestError(ProviderError):
    pass


@dataclass(slots=True)
class LLMRequest:
    system: str
    user: str
    max_output_tokens: int = 2048
    temperature: float = 0.0
    json_mode: bool = True


@dataclass(slots=True)
class LLMResponse:
    text: str
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_s: float


_CONTEXT_OVERFLOW = re.compile(
    r"(context.{0,20}(length|window)|maximum context|too many tokens|exceeds the maximum number of tokens|"
    r"input is too long|request too large|prompt is too long|reduce the length|token limit)",
    re.I,
)
_RETRY_DELAY_IN_BODY = re.compile(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"|try again in (\d+(?:\.\d+)?)\s*s', re.I)


def classify_http_error(provider: str, status: int, body: str, headers: dict[str, str]) -> ProviderError:
    snippet = body[:400]
    if status == 429 or "RESOURCE_EXHAUSTED" in body:
        # Groq/OpenAI report TPM breaches for one oversize request as 413; some gateways use 429 + "too large"
        if "request too large" in body.lower():
            return PayloadTooLargeError(f"{provider} 429 request too large: {snippet}", provider=provider, status=status)
        ra = parse_retry_after(headers.get("retry-after") or headers.get("Retry-After"))
        if ra is None:
            m = _RETRY_DELAY_IN_BODY.search(body)
            if m:
                ra = float(m.group(1) or m.group(2))
        return RateLimitError(f"{provider} rate limited: {snippet}", retry_after=ra, provider=provider, status=status)
    if status == 413 or (status == 400 and _CONTEXT_OVERFLOW.search(body)):
        return PayloadTooLargeError(f"{provider} payload too large: {snippet}", provider=provider, status=status)
    if status in (401, 403):
        return AuthError(f"{provider} auth error {status}", provider=provider, status=status)
    if status in (408, 504):
        return ProviderTimeoutError(f"{provider} timeout {status}", provider=provider, status=status)
    if status >= 500:
        return ProviderUnavailableError(f"{provider} unavailable {status}: {snippet}", provider=provider, status=status)
    return InvalidRequestError(f"{provider} invalid request {status}: {snippet}", provider=provider, status=status)


class BaseProvider(ABC):
    name: str = "base"

    def __init__(self, *, api_key: str | None, model: str, input_token_budget: int, rpm: int, timeout_s: float = 60.0) -> None:
        self._api_key = api_key
        self.model = model
        self.input_token_budget = input_token_budget
        self.timeout_s = timeout_s
        self.limiter = TokenBucket(rate_per_s=rpm / 60.0, burst=max(1, rpm // 6))
        self._session: aiohttp.ClientSession | None = None

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout_s), trust_env=True)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def complete(self, req: LLMRequest) -> LLMResponse:
        if not self.configured:
            raise AuthError(f"{self.name}: API key not configured", provider=self.name)
        await self.limiter.acquire()
        url, headers, payload = self.build_request(req)
        t0 = time.monotonic()
        session = await self._http()
        try:
            async with session.post(url, headers=headers, json=payload) as resp:
                body = await resp.text()
                if resp.status >= 400:
                    err = classify_http_error(self.name, resp.status, body, dict(resp.headers))
                    if isinstance(err, RateLimitError) and err.retry_after:
                        self.limiter.penalize(err.retry_after)
                    raise err
        except (TimeoutError, aiohttp.ServerTimeoutError) as e:
            raise ProviderTimeoutError(f"{self.name} timeout after {self.timeout_s}s", provider=self.name) from e
        except aiohttp.ClientError as e:
            raise ProviderUnavailableError(f"{self.name} connection error: {type(e).__name__}", provider=self.name) from e
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            raise MalformedResponseError(f"{self.name} returned non-JSON envelope", provider=self.name) from e
        return self.parse_response(data, time.monotonic() - t0)

    @abstractmethod
    def build_request(self, req: LLMRequest) -> tuple[str, dict[str, str], dict[str, Any]]: ...

    @abstractmethod
    def parse_response(self, data: dict[str, Any], latency: float) -> LLMResponse: ...
