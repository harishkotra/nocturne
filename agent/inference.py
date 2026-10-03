"""Plain-fetch inference for Nocturne.

Every LLM call in the app goes through here: `{baseUrl}/chat/completions` with
`response_format: {type: "json_object"}`. Nothing else -- no SDK, no retries that hide
failures, no canned responses.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

PRESETS: dict[str, dict[str, str]] = {
    "particle": {
        "label": "Particle.ai",
        "baseUrl": "https://api.particle.ai/v1",
        "model": "deepseek-v4.1-flash",
        "needsKey": "true",
    },
    "lmstudio": {
        "label": "LM Studio (local)",
        "baseUrl": "http://localhost:1234/v1",
        "model": "google/gemma-4-e4b",
        "needsKey": "false",
    },
    "ollama": {
        "label": "Ollama (local)",
        "baseUrl": "http://localhost:11434/v1",
        "model": "llama3.2",
        "needsKey": "false",
    },
    "gemini": {
        "label": "Gemini",
        "baseUrl": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-2.5-flash",
        "needsKey": "true",
    },
    "custom": {"label": "Custom", "baseUrl": "", "model": "", "needsKey": "true"},
}


class InferenceError(RuntimeError):
    """A real provider failure: status, message and body are preserved verbatim."""

    def __init__(self, message: str, *, status: int | None = None, body: str = "", url: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body
        self.url = url

    def to_dict(self) -> dict[str, Any]:
        return {"error": str(self), "status": self.status, "body": self.body[:2000], "url": self.url}


@dataclass
class ProviderConfig:
    base_url: str
    model: str
    api_key: str = ""
    temperature: float = 0.0
    timeout: float = 180.0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def completions_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    @property
    def models_url(self) -> str:
        return self.base_url.rstrip("/") + "/models"

    def headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h


def config_from_env() -> ProviderConfig:
    return ProviderConfig(
        base_url=os.environ.get("OPENAI_BASE_URL", "http://localhost:1234/v1"),
        model=os.environ.get("MODEL", "google/gemma-4-e4b"),
        api_key=os.environ.get("OPENAI_API_KEY", ""),
    )


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


def extract_json(raw: str) -> Any:
    """Pull a JSON object out of a model reply, tolerating fences and prose."""
    if not raw or not raw.strip():
        raise InferenceError("model returned an empty completion")
    text = raw.strip()
    fenced = _JSON_FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start == -1:
        raise InferenceError(f"model reply is not JSON: {text[:200]!r}")
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError as exc:
                    raise InferenceError(f"model reply has invalid JSON: {exc}") from exc
    raise InferenceError(f"model reply has unbalanced JSON braces: {text[:200]!r}")


def _post(cfg: ProviderConfig, payload: dict[str, Any]) -> httpx.Response:
    try:
        with httpx.Client(timeout=cfg.timeout) as client:
            return client.post(cfg.completions_url, headers=cfg.headers(), json=payload)
    except httpx.HTTPError as exc:
        raise InferenceError(
            f"cannot reach {cfg.completions_url}: {type(exc).__name__}: {exc}", url=cfg.completions_url
        ) from exc


# Some OpenAI-compatible servers (LM Studio, older llama.cpp wrappers) reject
# response_format={"type":"json_object"}. Remember that per base URL so we don't pay for a
# failing request on every pass, and report the degradation honestly.
_JSON_OBJECT_UNSUPPORTED: set[str] = set()


def _rejects_json_mode(status: int, body: str) -> bool:
    return status >= 400 and "response_format" in body.lower()


def chat(cfg: ProviderConfig, messages: list[dict[str, str]], *, want_json: bool = False) -> str:
    """One chat completion. Falls back to non-JSON mode when the server rejects it."""
    payload: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "temperature": cfg.temperature,
        **cfg.extra,
    }
    use_json_mode = want_json and cfg.base_url not in _JSON_OBJECT_UNSUPPORTED
    if use_json_mode:
        payload["response_format"] = {"type": "json_object"}
    resp = _post(cfg, payload)
    if use_json_mode and _rejects_json_mode(resp.status_code, resp.text):
        _JSON_OBJECT_UNSUPPORTED.add(cfg.base_url)
        payload.pop("response_format", None)
        resp = _post(cfg, payload)
    if resp.status_code >= 400:
        raise InferenceError(
            f"{cfg.completions_url} responded {resp.status_code}",
            status=resp.status_code,
            body=resp.text,
            url=cfg.completions_url,
        )
    try:
        data = resp.json()
    except json.JSONDecodeError as exc:
        raise InferenceError(
            f"{cfg.completions_url} returned a non-JSON body", status=resp.status_code, body=resp.text
        ) from exc
    choices = data.get("choices") or []
    if not choices:
        raise InferenceError(
            f"{cfg.completions_url} returned no choices", status=resp.status_code, body=resp.text
        )
    message = choices[0].get("message") or {}
    content = message.get("content")
    if content is None:
        content = choices[0].get("text") or ""
    # Reasoning models may put the answer in a separate field.
    if not str(content).strip() and message.get("reasoning"):
        content = message["reasoning"]
    return str(content)


def chat_json(cfg: ProviderConfig, messages: list[dict[str, str]], *, schema_hint: str = "") -> Any:
    raw = chat(cfg, messages, want_json=True)
    try:
        return extract_json(raw)
    except InferenceError as exc:
        exc.body = (exc.body or "") + f"\n--- raw completion ---\n{raw[:2000]}"
        raise


def chat_stream(cfg: ProviderConfig, messages: list[dict[str, str]]):
    """Yield text deltas from a streamed completion (used by the chat pane)."""
    payload = {
        "model": cfg.model,
        "messages": messages,
        "temperature": cfg.temperature,
        "stream": True,
        **cfg.extra,
    }
    try:
        with httpx.Client(timeout=cfg.timeout) as client:
            with client.stream(
                "POST", cfg.completions_url, headers=cfg.headers(), json=payload
            ) as resp:
                if resp.status_code >= 400:
                    body = resp.read().decode("utf-8", "replace")
                    raise InferenceError(
                        f"{cfg.completions_url} responded {resp.status_code}",
                        status=resp.status_code,
                        body=body,
                        url=cfg.completions_url,
                    )
                for line in resp.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        data = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    for choice in data.get("choices") or []:
                        delta = (choice.get("delta") or {}).get("content")
                        if delta:
                            yield str(delta)
    except httpx.HTTPError as exc:
        raise InferenceError(
            f"cannot reach {cfg.completions_url}: {type(exc).__name__}: {exc}", url=cfg.completions_url
        ) from exc


def test_connection(cfg: ProviderConfig) -> dict[str, Any]:
    """Hit the provider for real and report the actual status + body."""
    out: dict[str, Any] = {
        "baseUrl": cfg.base_url,
        "model": cfg.model,
        "ok": False,
        "steps": [],
    }
    try:
        with httpx.Client(timeout=min(cfg.timeout, 20.0)) as client:
            resp = client.get(cfg.models_url, headers=cfg.headers())
        out["steps"].append(
            {
                "url": cfg.models_url,
                "status": resp.status_code,
                "body": _redact(resp.text, cfg.api_key)[:1200],
            }
        )
        models_ok = resp.status_code < 400
    except httpx.HTTPError as exc:
        out["steps"].append(
            {"url": cfg.models_url, "status": None, "body": f"{type(exc).__name__}: {exc}"}
        )
        models_ok = False

    probe = [
        {"role": "system", "content": 'Reply with exactly the JSON object: {"ok":true}'},
        {"role": "user", "content": "ping"},
    ]
    probe_body: dict[str, Any] = {
        "model": cfg.model,
        "messages": probe,
        "temperature": 0,
        # reasoning models spend output tokens on hidden thinking; 16 is not enough to
        # produce a visible completion, so the probe asks for room and reports what came back
        "max_tokens": 200,
        "response_format": {"type": "json_object"},
    }
    try:
        with httpx.Client(timeout=min(cfg.timeout, 60.0)) as client:
            resp = client.post(cfg.completions_url, headers=cfg.headers(), json=probe_body)
            if resp.status_code >= 400 and "response_format" in resp.text.lower():
                # Server does not implement json_object mode: retry without it and say so.
                out["jsonModeAccepted"] = False
                body = _redact(resp.text, cfg.api_key)
                out["steps"].append(
                    {"url": cfg.completions_url, "status": resp.status_code, "body": body[:600]}
                )
                probe_body.pop("response_format")
                resp = client.post(cfg.completions_url, headers=cfg.headers(), json=probe_body)
        body = _redact(resp.text, cfg.api_key)
        out["steps"].append({"url": cfg.completions_url, "status": resp.status_code, "body": body[:1200]})
        if resp.status_code < 400:
            try:
                message = resp.json()["choices"][0]["message"]
                content = message.get("content") or message.get("reasoning") or ""
                out["ok"] = True
                out["completion"] = str(content)[:200]
                out.setdefault("jsonModeAccepted", True)
            except Exception as exc:  # noqa: BLE001 - report the real shape problem
                out["error"] = f"200 but unparsable completion: {exc}"
        elif not models_ok and resp.status_code is None:
            out["error"] = "provider unreachable"
        else:
            out["error"] = f"chat/completions failed with HTTP {resp.status_code}"
            if not models_ok:
                out["error"] += " (and /models also failed, so the base URL or key is likely wrong)"
    except httpx.HTTPError as exc:
        out["steps"].append({"url": cfg.completions_url, "status": None, "body": f"{type(exc).__name__}: {exc}"})
        out["error"] = f"chat/completions unreachable: {type(exc).__name__}: {exc}"

    if not out["ok"] and "error" not in out:
        out["error"] = "connection test failed"
    return out


def _redact(text: str, secret: str) -> str:
    if secret and len(secret) > 4:
        return text.replace(secret, "<api-key>")
    return text
