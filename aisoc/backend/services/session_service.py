"""Session service adapters."""

from __future__ import annotations

from typing import Any
import re
import time

from hermes_state import SessionDB

_TOOL_OUTPUT_TRUNCATE_CHARS = 500


def _strip_sensitive_session_fields(item: dict[str, Any]) -> dict[str, Any]:
    """Remove heavy/sensitive fields that should not be exposed in list APIs."""
    item.pop("system_prompt", None)
    return item


def _is_web_session(session: dict[str, Any]) -> bool:
    """Return True for AISOC web-UI sessions, which are the only channel
    scoped per authenticated account. All other channels (slack/feishu/
    telegram/cli/cron/tui/api...) are shared and visible to every caller."""
    return str(session.get("source") or "") == "aisoc_web"


def _owns_session(session: dict[str, Any], *, user_id: str) -> bool:
    """Return True when ``user_id`` may modify (not just view) ``session``.

    Mutation stays strict and owner-scoped: the caller must match the
    session's stored ``user_id`` exactly. Ownerless sessions (legacy rows,
    or rows from platforms with no per-user account concept) are never
    mutable through this check, since nobody has claimed them yet. Read
    access is governed separately by ``_is_readable`` (non-``aisoc_web``
    channels are shared reads by design).
    """
    owner = str(session.get("user_id") or "").strip()
    return bool(owner) and owner == user_id


def _is_readable(session: dict[str, Any], *, user_id: str) -> bool:
    """Return True when ``user_id`` may view (not modify) ``session``.

    Only ``aisoc_web`` sessions are private: owner must match exactly (or,
    for unclaimed legacy web rows, public reads are allowed). All other
    channels (slack/feishu/telegram/cli/cron/tui/discord/api/...) are shared
    by design — the browse/search page's whole purpose is to surface history
    across platforms, and most of it never carries a per-user owner.
    """
    if not _is_web_session(session):
        return True
    owner = str(session.get("user_id") or "").strip()
    return not owner or owner == user_id


def _build_message_search_query(query: str) -> str:
    """Translate a user query into an FTS5 query for ``search_messages``.

    ASCII tokens become ``<token> OR <token>*``: a prefix-only query
    (``<token>*``) floods the result window with long-word prefix hits and
    buries the exact token past the result limit — e.g. searching ``hi``
    matched 13k rows and pushed the exact ``hi`` message out of view. The
    OR keeps the exact token (ranked first) plus prefix coverage. CJK
    tokens are passed through verbatim: ``search_messages`` routes them to
    its trigram/LIKE paths where ``*`` is a literal character, so appending
    it would prevent any match. Already-quoted phrases and explicit ``*``
    prefixes are left untouched.
    """
    terms: list[str] = []
    for token in re.findall(r'"[^"]*"|\S+', query.strip()):
        if token.startswith('"') or token.endswith("*"):
            terms.append(token)
        elif SessionDB._contains_cjk(token):
            terms.append(token)
        else:
            terms.append(f"{token} OR {token}*")
    return " ".join(terms)


def list_sessions(
    limit: int = 20,
    offset: int = 0,
    source: str | None = None,
    *,
    user_id: str,
    strict_owner: bool = False,
) -> dict[str, Any]:
    """List sessions visible to ``user_id``.

    By default, ownerless sessions (legacy rows, tui/discord/cron/etc.) are
    included alongside the user's own — this is the browse/search page's
    behavior. Pass ``strict_owner=True`` for a private listing (e.g. the
    per-user chat sidebar) that must show only sessions this account
    actually owns, not history nobody has claimed.
    """
    db = SessionDB()
    try:
        include_ownerless = not strict_owner
        src = str(source or "").strip() or None
        # Only the AISOC web-UI channel is scoped per authenticated account.
        # All other channels (or the unfiltered "all channels" view, which the
        # web UI drives with source=None) are shared: do NOT constrain the SQL
        # by user_id for them, otherwise owned non-web sessions (e.g. feishu
        # user_id=2e97629d) would be hidden from everyone else.
        if src == "aisoc_web":
            sessions = db.list_sessions_rich(
                source=src,
                limit=limit,
                offset=offset,
                user_id=user_id,
                include_ownerless=include_ownerless,
                include_continuation_children=True,
                project_compression_tips=False,
            )
            total = db.session_count(
                source=src, user_id=user_id, include_ownerless=include_ownerless
            )
        elif src is None:
            # "All channels" view: shared channels are visible to everyone, but
            # aisoc_web sessions must stay user-scoped. Merge the two
            # populations (rather than dropping the SQL filter entirely) so
            # other users' web sessions never leak into the unfiltered list.
            shared = db.list_sessions_rich(
                exclude_sources=["aisoc_web"],
                limit=limit + offset,
                offset=0,
                user_id=None,
                include_ownerless=True,
                include_continuation_children=True,
                project_compression_tips=False,
            )
            web_own = db.list_sessions_rich(
                source="aisoc_web",
                limit=limit + offset,
                offset=0,
                user_id=user_id,
                include_ownerless=include_ownerless,
                include_continuation_children=True,
                project_compression_tips=False,
            )
            merged = sorted(
                web_own + shared,
                key=lambda s: s.get("last_active") or s.get("started_at") or 0,
                reverse=True,
            )
            sessions = merged[offset : offset + limit]
            total = db.session_count(
                exclude_sources=["aisoc_web"], user_id=None, include_ownerless=True
            ) + db.session_count(
                source="aisoc_web", user_id=user_id, include_ownerless=include_ownerless
            )
        else:
            # Any other single channel (slack/feishu/telegram/...): shared.
            sessions = db.list_sessions_rich(
                source=src,
                limit=limit,
                offset=offset,
                user_id=None,
                include_ownerless=True,
                include_continuation_children=True,
                project_compression_tips=False,
            )
            total = db.session_count(source=src, user_id=None, include_ownerless=True)
        now = time.time()
        for item in sessions:
            item["is_active"] = (
                item.get("ended_at") is None
                and (now - item.get("last_active", item.get("started_at", 0))) < 300
            )
            _strip_sensitive_session_fields(item)
        return {"sessions": sessions, "total": total, "limit": limit, "offset": offset}
    finally:
        db.close()


def search_sessions(query: str, limit: int = 20, *, user_id: str) -> dict[str, Any]:
    if not query or not query.strip():
        return {"results": []}

    db = SessionDB()
    try:
        prefix_query = _build_message_search_query(query)
        matches = db.search_messages(query=prefix_query, limit=limit)
        seen: dict[str, dict[str, Any]] = {}
        for match in matches:
            sid = match["session_id"]
            if sid in seen:
                continue
            session = db.get_session(sid)
            if not session or not _is_readable(session, user_id=user_id):
                continue
            seen[sid] = {
                "session_id": sid,
                "snippet": match.get("snippet", ""),
                "role": match.get("role"),
                "source": match.get("source"),
                "model": match.get("model"),
                "session_started": match.get("session_started"),
            }
        return {"results": list(seen.values())}
    finally:
        db.close()


def _session_latest_descendant(session_id: str) -> tuple[str | None, list[str]]:
    def row_get(row: Any, key: str, index: int):
        if isinstance(row, dict):
            return row.get(key)
        try:
            return row[key]
        except Exception:
            try:
                return row[index]
            except Exception:
                return None

    db = SessionDB()
    try:
        sid = db.resolve_session_id(session_id)
        if not sid or not db.get_session(sid):
            return None, []

        conn = (
            getattr(db, "conn", None)
            or getattr(db, "_conn", None)
            or getattr(db, "connection", None)
            or getattr(db, "_connection", None)
        )
        rows = []
        if conn is not None:
            raw_rows = conn.execute(
                "SELECT id, parent_session_id, started_at FROM sessions"
            ).fetchall()
            for row in raw_rows:
                rows.append(
                    {
                        "id": row_get(row, "id", 0),
                        "parent_session_id": row_get(row, "parent_session_id", 1),
                        "started_at": row_get(row, "started_at", 2),
                    }
                )
        else:
            rows = db.list_sessions_rich(limit=10000, offset=0)

        children: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            rid = row.get("id")
            parent = row.get("parent_session_id")
            if rid and parent:
                children.setdefault(parent, []).append(row)

        def started(row: dict[str, Any]) -> float:
            try:
                return float(row.get("started_at") or 0)
            except Exception:
                return 0.0

        current = sid
        path = [sid]
        seen = {sid}
        while children.get(current):
            candidates = [r for r in children[current] if r.get("id") not in seen]
            if not candidates:
                break
            candidates.sort(key=started, reverse=True)
            current = candidates[0]["id"]
            path.append(current)
            seen.add(current)
        return current, path
    finally:
        db.close()


def get_session_detail(session_id: str, *, user_id: str) -> dict[str, Any] | None:
    db = SessionDB()
    try:
        sid = db.resolve_session_id(session_id)
        if not sid:
            return None
        session = db.get_session(sid)
        if not session or not _is_readable(session, user_id=user_id):
            return None
        return session
    finally:
        db.close()


def get_session_detail_with_messages(session_id: str, *, user_id: str) -> dict[str, Any] | None:
    db = SessionDB()
    try:
        sid = db.resolve_session_id(session_id)
        if not sid:
            return None

        session = db.get_session(sid)
        if not session or not _is_readable(session, user_id=user_id):
            return None

        raw_messages = db.get_messages(sid)
    finally:
        db.close()

    messages: list[dict[str, Any]] = []
    for msg in raw_messages:
        role = str(msg.get("role") or "")
        content = msg.get("content")
        if content is None:
            text = ""
        elif isinstance(content, str):
            text = content
        else:
            text = str(content)

        if role == "tool" and len(text) > _TOOL_OUTPUT_TRUNCATE_CHARS:
            text = text[:_TOOL_OUTPUT_TRUNCATE_CHARS] + "...[truncated]"
        if role == "assistant" and not text:
            continue

        messages.append(
            {
                "role": role,
                "content": text,
                "tool_name": msg.get("tool_name"),
                "tool_call_id": msg.get("tool_call_id"),
                "timestamp": msg.get("timestamp"),
            }
        )

    return {
        "session_id": sid,
        "source": str(session.get("source") or ""),
        "model": str(session.get("model") or ""),
        "started_at": session.get("started_at"),
        "ended_at": session.get("ended_at"),
        "message_count": int(session.get("message_count") or 0),
        "tokens": int(session.get("input_tokens") or 0) + int(session.get("output_tokens") or 0),
        "messages": messages,
    }


def get_latest_descendant(session_id: str, *, user_id: str) -> dict[str, Any] | None:
    db = SessionDB()
    try:
        sid = db.resolve_session_id(session_id)
        if not sid:
            return None
        session = db.get_session(sid)
        if not session or not _is_readable(session, user_id=user_id):
            return None
    finally:
        db.close()

    latest, path = _session_latest_descendant(session_id)
    if not latest:
        return None
    return {
        "requested_session_id": path[0] if path else session_id,
        "session_id": latest,
        "path": path,
        "changed": bool(path and latest != path[0]),
    }


def get_session_messages(session_id: str, *, user_id: str) -> dict[str, Any] | None:
    db = SessionDB()
    try:
        sid = db.resolve_session_id(session_id)
        if not sid:
            return None
        session = db.get_session(sid)
        if not session or not _is_readable(session, user_id=user_id):
            return None
        return {"session_id": sid, "messages": db.get_messages(sid)}
    finally:
        db.close()


def delete_session(session_id: str, *, user_id: str) -> bool:
    db = SessionDB()
    try:
        sid = db.resolve_session_id(session_id)
        if not sid:
            return False
        session = db.get_session(sid)
        if not session or not _owns_session(session, user_id=user_id):
            return False
        return bool(db.delete_session(sid))
    finally:
        db.close()
