---
name: cjk-text-integrity
description: Use when CJK file text shows U+FFFD or patch can't match it.
---

# CJK Text Integrity

## Trigger

- You wrote a file containing Chinese (CJK) text via `write_file` and the on-disk content has `U+FFFD` replacement characters (rendered as `` / `\ufffd`) where a Chinese character should be.
- `patch` fails with "Could not find a match" even though `read_file` / `search_files` clearly show the line containing the text you want to fix — the corrupted bytes don't match your source text.
- Any task producing Chinese-language deliverables (HTML reports, dashboards, docs) that must pass validation.

## Symptom pattern (observed on this platform)

1. `write_file` succeeds (`verified: true`), but a CJK character was mangled into `U+FFFD` during write.
2. `read_file` renders it as `\ufffd`; `search_files` shows the same; the visible output cannot be copied as a reliable `old_string`.
3. `patch` (mode=replace) cannot match the corrupted fragment, even with unique surrounding context — the corrupted bytes are not the text you type.
4. `validate_html.py` may still pass if the placeholder/structure checks are clean, so a broken char can silently ship.

## Repair workflow (proven)

Do NOT fight `patch` — repair with an exact Python replace:

```bash
python3 - <<'EOF'
p = "<output-file>"
data = open(p, encoding="utf-8", errors="replace").read()
# 1) Locate the corrupted fragment verbatim with repr()
print(repr([l for l in data.splitlines() if "<anchor-text>" in l]))
# 2) Replace the exact corrupted fragment (copy from the repr output)
open(p, "w", encoding="utf-8").write(data.replace("<corrupted fragment>", "<fixed text>"))
print("fixed")
EOF
```

Key details:
- Read with `encoding="utf-8", errors="replace"` so the file never raises on the damaged bytes.
- Use `repr()` to see the true byte-level fragment — do not retype it from `read_file` output.
- Use `str.replace` with the verbatim fragment; asserts (`assert bad in data`) protect against typos.

## Verification (mandatory after repair)

- Re-check no replacement chars remain: `'\ufffd' in open(p, encoding="utf-8").read()` must be `False`.
- Re-run the deliverable validator if one exists (e.g. `python3 <skill-dir>/scripts/validate_html.py <file>` for HTML deliverables).
- Spot-check the repaired substring is present via `search_files`.

## Pitfalls

- Never author `patch` old_strings from `read_file`/`search_files` output when `\ufffd` is visible — guaranteed no-match.
- `read_file`'s pagination hint ("Re-read the whole file before overwriting") is worth following after corruption, but the repr+replace approach works without it.
- This is a repair pattern, NOT a claim that `write_file` is broken: files usually write fine; when they don't, fix bytes, don't blame the tool.

## Related

- `aegis/html-deliverable` (user-owned) governs the HTML deliverable template workflow this pitfall was found in; recommend `hermes curator adopt html-deliverable` so consolidation can merge this reference into it.