---
name: feishu-cloud-document-fetch
description: "Use to read a Feishu/Lark doc/sheet/wiki by URL."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [feishu, lark, integration, data-source, aegis]
    category: aegis
    related_skills: [aegis-wiki-maintenance, feishu-openid-to-userid]
---

# Feishu / Lark 云文档读取（feishu-cloud-document-fetch）

凭一个 Feishu/Lark 云文档 URL（或裸 token）读取其内容，无需 Hermes 依赖。支持：

- `docx` 新版文档 → `raw_content` 直读
- `sheets` 电子表格 → 每个子表渲染为 Markdown 表格（默认前 500 行）
- `wiki` 知识库节点 → 自动 `get_node` 解析真实 doc 再读

旧版 `docs`/`doc` 无 `raw_content` 接口，需先在飞书中转换为新版文档。

## When to Use / 何时激活

- 用户粘贴或提供 `*.feishu.cn` / `*.larksuite.com` 的 `docx`、`sheets`、`wiki` 链接
- 用户要求「读取 / 总结 / 提取 / 归档」某份飞书表格或文档的内容
- 需要把飞书文档内容拉进本地做后续处理（归档到 Wiki、分析等）

不确定用户意图（只是读取 vs. 归档 vs. 分析）时，先用 clarify 问清楚再动手。

## 前提：凭证

脚本从环境变量读取凭证（运行时动态生效）：

- `FEISHU_APP_ID` / `FEISHU_APP_SECRET`（必填，除非提供 user token）
- `FEISHU_DOMAIN`：`feishu` | `lark`（本机 `.env` 当前为 `lark`）
- `FEISHU_USER_ACCESS_TOKEN`（可选；以用户身份调用，可读「应用不可见但用户可见」的文档）

**关键坑（已验证）**：这些变量通常**不在**当前 shell 环境里，而在 Aegis profile 的 `.env`（`$HERMES_HOME/.env`）。必须先把该文件 source 进子 shell 再运行脚本，且**绝不读取 / 打印 / 复述任何密钥值**（红线）。用 `set -a; . <env>; set +a` 一次性载入即可，不要 echo 变量值。

## 用法

**始终运行本技能自带的脚本** `scripts/feishu_doc_fetch.py` —— 它随技能分发、自包含，是唯一权威运行目标。不要引用 `$HERMES_HOME/scripts/` 或其他外部路径下的同名脚本。用 `skill_view(name='feishu-cloud-document-fetch')` 返回的 `skill_dir` 拼出绝对路径 `<skill_dir>/scripts/feishu_doc_fetch.py`。

### 1. 先验证连通性（可选）

```bash
set -a && . $HERMES_HOME/.env 2>/dev/null && set +a && \
python3 <skill_dir>/scripts/feishu_doc_fetch.py --test-conn
```

成功输出形如 `OK tenant_access_token acquired via lark (...)`。

### 2. 读取文档 / 表格

```bash
set -a && . $HERMES_HOME.env 2>/dev/null && set +a && \
python3 <skill_dir>/scripts/feishu_doc_fetch.py "<完整URL>"
```

- URL 里的 `?sheet=<id>` 会被识别，只读该子表；不带则读全部子表。
- 内容默认打到 stdout（Markdown）。
- `--out FILE` 写入文件而非 stdout；`--json` 输出含 `token`/`obj_type` 的 JSON。
- 表格默认前 500 行；需要更多设 `FEISHU_SHEET_MAX_ROWS=<n>`。

### 3. 只解析 URL（不调 API）

```bash
python3 <skill_dir>/scripts/feishu_doc_fetch.py --parse "<URL>"
```

返回 `{token, obj_type, sheet}`，用于调试链接解析。

## 常见错误与处置

- `需要环境变量 FEISHU_APP_ID / FEISHU_APP_SECRET`：没 source `.env`。按上面的 `set -a; . <env>; set +a` 载入后重试。
- `获取表格元信息失败 ...`（权限类）：把文档分享给本应用/机器人，或配置 `FEISHU_USER_ACCESS_TOKEN`。
- `旧版文档(doc)无 raw_content 接口`：在飞书中「转换为新版文档」后重试。
- `无法从输入中解析出文档 token`：URL 不含可识别前缀（docx/sheets/wiki/...）或不是裸 token。

## 读取之后（衔接下游）

读到内容后按用户意图继续：只需总结就直接整理输出；要归档到 Wiki 则遵循 `aegis-wiki-maintenance` 的 Raw → LLM Compile → Wiki 流程（音视频/图片类不做知识化编译）。

## Pitfalls

- **绝不读/打印/转发任何密钥**：只 source `.env`，不要读取或 echo 其中的值。这是 Aegis 红线。
- **多行 heredoc 执行 Python 会触发防护并超时**：不要 `python3 << 'EOF'`。脚本已是文件，直接 `python3 <file>` 单行运行。
- **不要把凭证写进命令行参数**：脚本只从环境变量取值，命令行不该出现任何 token/secret。
- **只跑技能内脚本**：运行目标恒为 `<skill_dir>/scripts/feishu_doc_fetch.py`，不要改用外部 `$HERMES_HOME/scripts/` 的同名文件。二者出现分歧时以技能内为准；如需吸收外部改动，把外部内容拷进技能内脚本后再运行，而非反向引用。