"""Shared HTTP helpers for the providers."""
from __future__ import annotations

import json
from typing import Any, Iterator

import requests

from ..base import BackendError

CONNECT_TIMEOUT = 8
READ_TIMEOUT = 600


def post_stream(url: str, headers: dict[str, str], payload: dict[str, Any],
                unreachable: str) -> requests.Response:
    """POST *payload* and return the streaming response.

    Connection problems are converted to :class:`BackendError` using the
    *unreachable* message. HTTP status handling is left to the caller.
    """
    try:
        return requests.post(url, headers=headers, json=payload, stream=True,
                             timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    except requests.exceptions.ConnectionError as exc:
        raise BackendError(unreachable) from exc
    except requests.exceptions.Timeout as exc:
        raise BackendError("The model server took too long to answer.") from exc
    except requests.exceptions.RequestException as exc:
        raise BackendError(f"Request failed: {type(exc).__name__}") from exc


def error_text(response: requests.Response) -> str:
    """Best-effort extraction of an error message from a failed response."""
    try:
        data = response.json()
    except ValueError:
        return (response.text or "").strip()[:300]
    err = data.get("error", data) if isinstance(data, dict) else data
    if isinstance(err, dict):
        return str(err.get("message") or err)[:300]
    return str(err)[:300]


def iter_sse(response: requests.Response) -> Iterator[tuple[str, str]]:
    """Yield ``(event, data)`` pairs from a server-sent-events stream."""
    event = ""
    try:
        for raw in response.iter_lines(decode_unicode=False):
            if raw is None:
                continue
            line = raw.decode("utf-8", errors="replace")
            if not line:
                event = ""
                continue
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                yield event, line[5:].strip()
    except requests.exceptions.RequestException as exc:
        raise BackendError("The connection dropped while I was answering.") from exc


def loads(text: str) -> dict[str, Any]:
    """Parse a JSON object, returning an empty dict on failure."""
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}
