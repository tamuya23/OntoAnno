from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any


DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
RETRYABLE_HTTP_STATUS = {408, 409, 429, 500, 502, 503, 504}


class OpenAIRequestError(RuntimeError):
    pass


def chat_completions_url(base_url: str | None) -> str:
    base = str(base_url or os.getenv("OPENAI_BASE_URL") or DEFAULT_OPENAI_BASE_URL).rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _sanitize_error_body(body: str) -> str:
    text = body.strip()
    text = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text)
    text = re.sub(r"(api[_-]?key['\"]?\s*[:=]\s*['\"]?)[^'\"\\s,}]+", r"\1[REDACTED]", text, flags=re.IGNORECASE)
    return text[:4000]


def _retry_delay(exc: urllib.error.HTTPError, attempt: int) -> float:
    retry_after = exc.headers.get("Retry-After") if exc.headers else None
    if retry_after:
        try:
            return min(float(retry_after), 30.0)
        except ValueError:
            pass
    return min(2.0 ** attempt, 30.0)


def post_openai_json(
    *,
    url: str,
    payload: dict[str, Any],
    api_key: str,
    timeout: int = 180,
    max_retries: int = 3,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            last_error = OpenAIRequestError(f"LLM API HTTP {exc.code}: {_sanitize_error_body(body)}")
            if exc.code in RETRYABLE_HTTP_STATUS and attempt < max_retries:
                time.sleep(_retry_delay(exc, attempt))
                continue
            raise last_error from exc
        except urllib.error.URLError as exc:
            last_error = OpenAIRequestError(f"LLM API request failed: {exc.reason}")
            if attempt < max_retries:
                time.sleep(min(2.0 ** attempt, 30.0))
                continue
            raise last_error from exc
        except json.JSONDecodeError as exc:
            raise OpenAIRequestError(f"LLM API response was not valid JSON: {exc}") from exc

    raise OpenAIRequestError(str(last_error or "LLM API request failed"))
