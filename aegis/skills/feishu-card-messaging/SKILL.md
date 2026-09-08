---
name: feishu-card-messaging
description: Use when sending/updating Feishu/Lark cards via lark_oapi.
---

# Feishu/Lark Card Messaging (lark_oapi)

## When to use
- Sending an interactive card to a Feishu/Lark chat from Python (SDK scripts, gateway adapter work, demos).
- Streaming / progressive card content ("AI typing" effect, todo rows appearing line by line).
- Element-level updates of an already-sent card (mark todo done, append rows, refresh a region).

## Environment facts (verified 2026-09)
- Credentials: `FEISHU_APP_ID`, `FEISHU_APP_SECRET` in `~/.hermes/.env`. Load them, never print values.
- `FEISHU_DOMAIN=lark` → pass `LARK_DOMAIN` from `lark_oapi.core.const` when building the client.
- Client: `lark.Client.builder().app_id(...).app_secret(...).domain(LARK_DOMAIN).log_level(lark.LogLevel.WARNING).build()`. Lazy imports guard `lark_oapi` availability.
- Target chat: `FEISHU_HOME_CHANNEL` env; this user's Home chat is `oc_70a26ae08ab74593c040cf501d992a7d` (`receive_id_type="chat_id"`).
- Full research notes live in `~/wiki/01-Raw/AIAgent/hermes-feishu-card-output.md` — consult it before implementing adapter work.

## Two paths — pick per need

### Path A: JSON 1.0 card via im/v1 send (simple, no CardKit)
- `POST /open-apis/im/v1/messages`, `msg_type="interactive"`, `content` = card JSON string.
- VERIFIED JSON 1.0 component rules:
  - Markdown/rich text: `{"tag":"div","text":{"tag":"lark_md","content":"..."}}` — NOT top-level `markdown`.
  - Checkbox: `{"tag":"checker", "name":..., "label":{"tag":"plain_text","content":...}, "checked":bool}` — NOT `checkbox` (rejected: `not support tag: checkbox`).
  - Foldable panel: `collapsible_panel` REQUIRES `header.title` (else `no collapsible panel header`, 10002); children in `elements`.
- Update already-sent cards via `PATCH /im/v1/messages/:message_id` (14-day window, needs `update_multi:true`).

### Path B: CardKit v2 card entities (streaming + element updates) — use for dynamic cards
Lifecycle (every step verified code 0 with this user's app):
1. **Create**: `POST /open-apis/cardkit/v1/cards`, body `{"type":"card_json","data":<JSON-2.0 card>}` — types `raw`/`json` are INVALID. Returns `card_id`.
2. **Deliver**: `POST /open-apis/im/v1/messages`, `msg_type="interactive"`, content = `{"type":"card","data":{"card_id":"..."}}` — NOT the card JSON itself.
3. **Full update**: `PUT /open-apis/cardkit/v1/cards/:card_id` — body `card(type=card_json,data=...)`; requires `uuid` + `sequence`.
4. **Settings**: `PATCH /open-apis/cardkit/v1/cards/:card_id/settings` — `settings` = JSON string `{"config":{...}}` (summary, streaming_mode toggling).
5. **Stream text**: `PUT /open-apis/cardkit/v1/cards/:card_id/elements/:element_id/content` — body `content={"text":"..."}`.
6. **Element ops**: `POST`(insert) / `PUT`(update) / `PATCH`(partial) / `DELETE` on `.../cards/:card_id/elements/:element_id`.
7. **Batch**: `POST .../batch_update` — `actions` = JSON Patch array `[{"method":"update","path":"/body/elements/0","value":{...}}]`.

## Critical gotchas (each one cost real API errors)
1. **`content` is FULL-TEXT REPLACE, not append.** Official semantics: old text as prefix of new text → typewriter append; otherwise whole text replaces on screen. The caller MUST maintain cumulative text (`acc += fragment`) to emulate progressive output. Sending incremental fragments yields replacement (user-verified behavior).
2. **Row-level "append" (todos)**: use `POST elements` with `type=insert_after|insert_before|append` + `target_element_id` anchor; `elements` field must be an ARRAY (single dict fails with type-mismatch).
3. **`sequence` required on every CardKit update request** — missing ⇒ 99992402 `field validation failed`.
4. **`streaming_mode:true` needed for `content` streaming** — otherwise 300309 `streaming mode is closed`. Set at create (`config.streaming_mode`), toggle later via `settings`.
5. **`checker`, not `checkbox`** — even in JSON 2.0 CardKit create; `checkbox` and `form_container` rejected.
6. **`collapsible_panel` needs `header.title`** in both 1.0 and 2.0 forms.
7. **im/v1 send ignores `"schema":"2.0"`** — always parses as JSON 1.0. JSON 2.0-only components require the CardKit path.
8. **`id_convert` (message_id→card_id)** only works for CardKit-delivered cards; plain JSON 1.0 cards return `queried result is empty`.
9. **14-day update window** after card-entity creation; recreate past that.
10. Reading a card back via im get returns the rendered form (img keys) even with `card_msg_content_type="raw"` — don't rely on it to inspect markdown state; use controlled A/B experiments to pin down API semantics.

## Minimal verified snippet
```python
import os, json
from uuid import uuid4
env = {}
for line in open(os.path.expanduser("~/.hermes/.env")):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); env[k.strip()] = v.strip().strip('"').strip("'")
from lark_oapi import Client as LarkClient
from lark_oapi.core.const import LARK_DOMAIN
client = LarkClient.builder().app_id(env["FEISHU_APP_ID"]).app_secret(env["FEISHU_APP_SECRET"]).domain(LARK_DOMAIN).build()

card = {"schema":"2.0","config":{"streaming_mode":True,"update_multi":True,"summary":{"content":"🤔 生成中…"}},
        "header":{"title":{"tag":"plain_text","content":"标题"},"template":"blue"},
        "body":{"elements":[{"tag":"markdown","element_id":"md_1","content":""}]}}
body = CreateCardRequestBody.builder().type("card_json").data(json.dumps(card, ensure_ascii=False)).build()
resp = client.cardkit.v1.card.create(CreateCardRequest.builder().request_body(body).build())
card_id = resp.data.card_id
# deliver
content = json.dumps({"type":"card","data":{"card_id":card_id}}, ensure_ascii=False)
# stream: acc += fragment each round, then
ContentCardElementRequestBody.builder().content(json.dumps({"text": acc})).uuid(str(uuid4())).sequence(n).build()
```

See `references/cardkit-api-notes.md` for the full interface/error table.