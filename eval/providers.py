"""Model providers for the evaluation harness (PLAN.md phase N3).

Three providers are implemented, and none of them is required to run the
harness:

``mock``
    Deterministic canned answers, including failures. Used by the test suite
    and for dry runs; it never touches the network and never needs credentials.

``replay``
    Re-reads answers that were saved earlier (JSONL). This keeps the existing
    offline workflow of `eval/evaluate.py` and makes old runs reproducible.

``openai-compatible``
    Any server that implements the OpenAI chat-completions schema. The base URL
    and model are arguments; the API key is read from an environment variable
    named by ``--api-key-env`` and is never written to a record, a log line or
    an error message.

Rules that the harness depends on:

* a value that the provider does not report is ``None``, never ``0`` and never
  an estimate;
* an error is recorded as an error with its class and message, not as an empty
  answer;
* the request parameters (model, temperature, max tokens, streaming) are
  recorded verbatim so a run can be described exactly.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional


@dataclass
class Request:
    """One prompt sent to a provider, recorded exactly as sent."""

    prompt: str
    system: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    stream: bool = False
    #: Extra request fields a server understands, e.g. a GBNF grammar for
    #: constrained decoding. Recorded verbatim; `None` when nothing extra is sent.
    extra: Optional[dict] = None

    @property
    def prompt_sha256(self) -> str:
        material = (self.system or "") + "\x00" + self.prompt
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass
class Response:
    """What came back, including what was *not* reported."""

    text: str = ""
    model: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    ttft_ms: Optional[float] = None
    wall_ms: Optional[float] = None
    finish_reason: Optional[str] = None
    error: Optional[str] = None
    error_class: Optional[str] = None
    attempts: int = 1

    @property
    def ok(self) -> bool:
        return self.error is None

    def report(self) -> dict:
        document = asdict(self)
        document["ok"] = self.ok
        document["prompt_tokens_reported"] = self.prompt_tokens is not None
        document["completion_tokens_reported"] = self.completion_tokens is not None
        return document


class ProviderError(RuntimeError):
    """Raised by providers when the transport fails; recorded on the response."""


class Provider:
    name = "base"

    def complete(self, request: Request) -> Response:  # pragma: no cover - interface
        raise NotImplementedError

    def describe(self) -> dict:
        return {"provider": self.name}


class MockProvider(Provider):
    """Deterministic provider: a script of answers or callables, consumed in order.

    A scripted entry may be a string (the answer text), a `Response`, or a
    callable taking the request and returning either. When the script is
    exhausted the last entry repeats, so a study run of any length is possible.
    """

    name = "mock"

    def __init__(self, script, label: str = "mock"):
        if not script:
            raise ValueError("MockProvider needs at least one scripted answer")
        self.script = list(script)
        self.label = label
        self.calls: list[Request] = []

    def complete(self, request: Request) -> Response:
        self.calls.append(request)
        index = min(len(self.calls) - 1, len(self.script) - 1)
        entry = self.script[index]
        if callable(entry) and not isinstance(entry, Response):
            entry = entry(request)
        if isinstance(entry, Response):
            return entry
        # A mock measures nothing: timings and token counts stay missing rather
        # than being filled with a plausible-looking number.
        return Response(
            text=str(entry),
            model=self.label,
            prompt_tokens=None,
            completion_tokens=None,
            wall_ms=None,
            ttft_ms=None,
        )

    def describe(self) -> dict:
        return {"provider": self.name, "label": self.label, "scripted_answers": len(self.script)}


class ReplayProvider(Provider):
    """Replays saved answers keyed by the SHA-256 of the exact prompt."""

    name = "replay"

    def __init__(self, path: Path):
        self.path = Path(path)
        self.answers: dict[str, dict] = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            key = record.get("prompt_sha256") or hashlib.sha256(
                ((record.get("system") or "") + "\x00" + record.get("prompt", "")).encode("utf-8")
            ).hexdigest()
            self.answers[key] = record
        self.misses = 0

    def complete(self, request: Request) -> Response:
        record = self.answers.get(request.prompt_sha256)
        if record is None:
            self.misses += 1
            return Response(error="no saved answer for this prompt",
                            error_class="ReplayMiss")
        return Response(
            text=record.get("response", ""),
            model=record.get("model"),
            prompt_tokens=record.get("prompt_tokens"),
            completion_tokens=record.get("completion_tokens"),
            wall_ms=record.get("wall_ms"),
            ttft_ms=record.get("ttft_ms"),
            finish_reason=record.get("finish_reason"),
        )

    def describe(self) -> dict:
        return {"provider": self.name, "path": str(self.path), "entries": len(self.answers)}


#: A gateway may reject the default urllib User-Agent outright: Groq answers
#: `Python-urllib/3.13` with HTTP 403 and Cloudflare's `error code: 1010`.
DEFAULT_USER_AGENT = "nava-study/1.0"

#: Status codes worth retrying: rate limits and transient server-side failures.
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504, 529})


class OpenAICompatibleProvider(Provider):
    """Chat-completions client for any OpenAI-compatible endpoint.

    The API key is read from the environment at call time. It is never stored on
    the instance, never returned in a record and redacted from error messages.

    A rate limit is not a model failure. When the server answers 429 (or a
    transient 5xx) the call is retried after the delay the server asks for —
    `retry-after` or `x-ratelimit-reset-tokens` when present, exponential
    backoff otherwise — and the number of HTTP attempts is recorded on the
    response. Without this a token-per-minute limit would silently turn every
    attempt into a provider error and the aggregates would be meaningless.
    """

    name = "openai-compatible"

    def __init__(self, base_url: str, model: str, api_key_env: str = "OPENAI_API_KEY",
                 extra_headers: Optional[dict] = None, organisation: Optional[str] = None,
                 user_agent: str = DEFAULT_USER_AGENT, max_retries: int = 5,
                 max_retry_wait: float = 300.0, timeout: float = 120.0, sleep=time.sleep):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.extra_headers = dict(extra_headers or {})
        self.organisation = organisation
        self.user_agent = user_agent
        self.max_retries = max_retries
        self.max_retry_wait = max_retry_wait
        self.timeout = timeout
        self._sleep = sleep
        #: Redacted durations of the waits this provider performed (for the record).
        self.waits: list[float] = []

    def _headers(self) -> dict:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise ProviderError(f"environment variable {self.api_key_env} is not set")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "User-Agent": self.user_agent,
        }
        if self.organisation:
            headers["OpenAI-Organization"] = self.organisation
        headers.update(self.extra_headers)
        return headers

    def _payload(self, request: Request) -> dict:
        messages = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})
        payload = {"model": request.model or self.model, "messages": messages,
                   "stream": bool(request.stream)}
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.stream:
            payload["stream_options"] = {"include_usage": True}
        if request.extra:
            payload.update(request.extra)
        return payload

    def complete(self, request: Request) -> Response:  # pragma: no cover - network
        try:
            headers = self._headers()
        except ProviderError as error:
            return Response(error=str(error), error_class="ProviderError")
        payload = self._payload(request)
        target = f"{self.base_url}/chat/completions"
        http_request = urllib.request.Request(
            target, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        start = time.perf_counter()
        http_attempts = 0
        delay = 1.0
        while True:
            http_attempts += 1
            try:
                with urllib.request.urlopen(http_request, timeout=self.timeout) as handle:
                    if request.stream:
                        response = self._read_stream(handle, start)
                        response.attempts = http_attempts
                        return response
                    body = json.loads(handle.read().decode("utf-8"))
                wall_ms = round((time.perf_counter() - start) * 1000, 3)
                response = self._from_body(body, wall_ms)
                response.attempts = http_attempts
                return response
            except urllib.error.HTTPError as error:
                detail = _redact(error.read().decode("utf-8", "replace")[:500], headers)
                # A daily cap is not a transient limit: the server says when it
                # resets (hours), and retrying would burn the whole run while
                # every attempt turns into a provider error anyway.
                blocking = _blocking_class(detail, error.headers)
                if blocking:
                    # Retrying cannot fix a daily cap or a request that asks for
                    # more output than the whole per-minute budget allows.
                    return Response(error=f"HTTP {error.code}: {detail}",
                                    error_class=blocking,
                                    wall_ms=round((time.perf_counter() - start) * 1000, 3),
                                    attempts=http_attempts)
                if error.code in RETRYABLE_STATUS and http_attempts <= self.max_retries:
                    wait = _retry_delay(error.headers, delay, self.max_retry_wait)
                    self.waits.append(wait)
                    self._sleep(wait)
                    delay = min(delay * 2, self.max_retry_wait)
                    continue
                return Response(error=f"HTTP {error.code}: {detail}", error_class="HTTPError",
                                wall_ms=round((time.perf_counter() - start) * 1000, 3),
                                attempts=http_attempts)
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                if http_attempts <= self.max_retries:
                    wait = min(delay, self.max_retry_wait)
                    self.waits.append(wait)
                    self._sleep(wait)
                    delay = min(delay * 2, self.max_retry_wait)
                    continue
                return Response(error=_redact(str(error), headers),
                                error_class=type(error).__name__,
                                wall_ms=round((time.perf_counter() - start) * 1000, 3),
                                attempts=http_attempts)

    def _from_body(self, body: dict, wall_ms: Optional[float]) -> Response:
        try:
            choice = body["choices"][0]
            text = choice.get("message", {}).get("content") or ""
            finish = choice.get("finish_reason")
        except (KeyError, IndexError, TypeError) as error:
            return Response(error=f"unexpected response shape: {error}", error_class="SchemaError",
                            wall_ms=wall_ms)
        usage = body.get("usage") or {}
        return Response(
            text=text,
            model=body.get("model"),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            wall_ms=wall_ms,
            finish_reason=finish,
        )

    def _read_stream(self, handle, start: float) -> Response:  # pragma: no cover - network
        text_parts: list[str] = []
        ttft_ms: Optional[float] = None
        usage: dict = {}
        finish: Optional[str] = None
        model: Optional[str] = None
        for raw_line in handle:
            line = raw_line.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            model = chunk.get("model", model)
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices", []):
                delta = choice.get("delta", {}).get("content")
                if delta:
                    if ttft_ms is None:
                        ttft_ms = round((time.perf_counter() - start) * 1000, 3)
                    text_parts.append(delta)
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
        return Response(
            text="".join(text_parts),
            model=model,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            ttft_ms=ttft_ms,
            wall_ms=round((time.perf_counter() - start) * 1000, 3),
            finish_reason=finish,
        )

    def describe(self) -> dict:
        return {"provider": self.name, "base_url": self.base_url, "model": self.model,
                "api_key_env": self.api_key_env,
                "user_agent": self.user_agent,
                "max_retries": self.max_retries,
                # Names only: a header value may be a credential.
                "extra_header_names": sorted(self.extra_headers)}


def _blocking_class(detail: str, headers) -> Optional[str]:
    """The error class for a 429 that no retry can resolve, else ``None``.

    Two shapes matter, and confusing them makes a record lie:

    * a daily cap — Groq says `tokens per day (TPD)` and the reset is hours
      away, so retrying would turn every attempt into a provider error after a
      long stall (`DailyQuotaExceeded`);
    * a request larger than an entire per-minute budget — Groq says "Request
      too large ... on output tokens per minute (OTPM)"; waiting cannot help
      either, because the *request* is the problem, not the current usage
      (`RequestTooLarge`). The fix is a smaller `--max-tokens`.
    """
    text = (detail or "").lower()
    if "per day" in text or "(tpd)" in text:
        return "DailyQuotaExceeded"
    # "Request too large ... expected output tokens exceed the enforced limit"
    # means no retry can succeed: the request itself is above the ceiling. An
    # OTPM message that instead says "Used 285, Requested 900, try again in
    # 11.1s" is a *transient* per-minute limit and must be retried.
    if "request too large" in text and "exceed the enforced limit" in text:
        return "RequestTooLarge"
    # `x-should-retry: false` is deliberately ignored: Groq sets it on the
    # transient OTPM message too ("Used 285, Requested 900, try again in 11.1s").
    # The bound that protects a run from a retry storm is `--retries`.
    return None


def _retry_delay(headers, fallback: float, cap: float) -> float:
    """How long to wait before retrying, preferring what the server asked for."""
    for name in ("retry-after", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        value = headers.get(name) if headers is not None else None
        if not value:
            continue
        seconds = _seconds(value)
        if seconds is not None:
            return max(0.0, min(seconds, cap))
    return max(0.0, min(fallback, cap))


def _seconds(value: str) -> Optional[float]:
    """Parse `1.5`, `690ms`, `12.015s`, `1m26.4s` — the shapes Groq returns."""
    text = str(value).strip().lower()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    parts = re.findall(r"(\d+(?:\.\d+)?)\s*(ms|s|m)", text)
    if not parts:
        return None
    total, seen = 0.0, 0
    for number, unit in parts:
        seen += len(number) + len(unit)
        total += float(number) * {"ms": 0.001, "s": 1.0, "m": 60.0}[unit]
    # Refuse to guess at text that is not made of those pieces.
    if seen < len(re.sub(r"\s", "", text)):
        return None
    return total


def _redact(text: str, headers: dict) -> str:
    """Remove anything that looks like a credential from an error message."""
    redacted = text
    for name, value in headers.items():
        if name.lower() == "authorization":
            token = value.split(" ", 1)[-1]
            if token:
                redacted = redacted.replace(token, "[redacted]")
    return redacted


def build_provider(name: str, *, model: Optional[str] = None, base_url: Optional[str] = None,
                   api_key_env: str = "OPENAI_API_KEY", replay_path: Optional[Path] = None,
                   script=None, headers: Optional[dict] = None,
                   max_retries: int = 5) -> Provider:
    if name == "mock":
        return MockProvider(script or ["fn main() { print(0) }\n"], label="mock")
    if name == "replay":
        if replay_path is None:
            raise ValueError("the replay provider needs --replay-file")
        return ReplayProvider(replay_path)
    if name == "openai":
        if not base_url or not model:
            raise ValueError("the openai provider needs --base-url and --model")
        return OpenAICompatibleProvider(base_url, model, api_key_env=api_key_env,
                                        extra_headers=headers, max_retries=max_retries)
    raise ValueError(f"unknown provider {name!r}; use mock, replay or openai")
