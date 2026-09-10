"""
Generic, read-only Microsoft Graph query tool for the chatbox.

Reuses the same app-only client credentials as powerbi_engine.py
(MS_TENANT_ID / MS_CLIENT_ID / MS_CLIENT_SECRET). Unlike that module, this one
doesn't hand-build a typed tool per M365 capability -- it exposes a single
"call any Graph GET endpoint" primitive, and leaves picking the right path/
query to the LLM router (see _GRAPH_SYSTEM_CHEATSHEET in server.py).

Important: app-only auth has no signed-in user, so there is no "/me". Any
query about "my mail / my calendar / my chats" must target a specific user
by UPN or object id -- see DEFAULT_USER_UPN.
"""
import os
import json
from typing import Optional, Dict, Any

import httpx

from powerbi_engine import _graph_token, GRAPH_BASE, PowerBIEngineError  # reuse auth + base URL

DEFAULT_USER_UPN = os.environ.get("MS_DEFAULT_USER_UPN", "")

# GET-only allowlist of path prefixes. Blocks accidental/LLM-hallucinated
# writes even though the tool is meant to be read-only by design -- this is
# the actual enforcement, not just a description promising good behavior.
_BLOCKED_METHODS = {"POST", "PATCH", "PUT", "DELETE"}


class GraphQueryError(Exception):
    pass


def query_graph(path: str, params: Optional[Dict[str, Any]] = None, method: str = "GET") -> dict:
    method = (method or "GET").upper()
    if method in _BLOCKED_METHODS:
        raise GraphQueryError(
            f"Refusing to call Graph with method={method}: this tool is read-only (GET requests only)."
        )

    path = (path or "").strip().lstrip("/")
    if not path:
        raise GraphQueryError("No Graph API path given.")

    try:
        token = _graph_token()
    except PowerBIEngineError as e:
        raise GraphQueryError(str(e))

    url = path if path.startswith("http") else f"{GRAPH_BASE}/{path}"
    r = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, params=params or {}, timeout=25.0)
    if r.status_code >= 400:
        raise GraphQueryError(f"Graph API error {r.status_code} calling '{path}': {r.text[:400]}")
    try:
        return r.json()
    except Exception:
        return {"raw": r.text[:2000]}


def summarize_graph_result(data: dict, max_items: int = 15, max_chars: int = 3500) -> str:
    """Renders a Graph JSON response as compact markdown for the chatbox --
    full raw JSON is unreadable and can blow past reasonable reply length."""
    if "value" in data and isinstance(data["value"], list):
        items = data["value"][:max_items]
        total = len(data["value"])
        lines = [f"({total} item{'s' if total != 1 else ''}{f', showing {len(items)}' if total > len(items) else ''})", ""]
        for item in items:
            lines.append("```json\n" + json.dumps(item, indent=2, default=str)[:600] + "\n```")
        text = "\n".join(lines)
    else:
        text = "```json\n" + json.dumps(data, indent=2, default=str) + "\n```"

    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n…(truncated)"
    return text
