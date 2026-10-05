[English](#english) | [中文](#中文)

---

<a name="english"></a>

# Fanfic Assistant Backend — Django REST API

> Backend service for a Chinese fanfiction long-form writing assistant. Provides REST APIs for character card management, AU Mod management, relationship entities, user authentication, multi-provider LLM generation, Phase 2 example library (pgvector), and consistency evaluation.

**🌐 API Base URL: [https://web-production-29e7.up.railway.app](https://web-production-29e7.up.railway.app)**

- Web Frontend: [Fanfic-Assistant](https://github.com/YennieP/Fanfic-Assistant)
- Mobile Client: [Fanfic-Assistant-Mobile](https://github.com/YennieP/Fanfic-Assistant-Mobile)
- Design Docs: [docs/](https://github.com/YennieP/Fanfic-Assistant/tree/main/docs) (in frontend repo)

---

## Phase 1 Experiment Results

3-group × 5-scene controlled experiment. Full report: [EXPERIMENT.md](./EXPERIMENT.md)

| Group | Description | Avg Score |
|---|---|---|
| Control A | No character information | 7.8 |
| Control B | Natural language summary (2–3 sentences) | 8.4 |
| **Experimental** | **Full structured character card system** | **9.8** |

Key finding: "switch mechanism" scene (character fully lets loose in entertainment contexts) — Control A/B: 2/10, Experimental: 9/10.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Framework | Django 6 + Django REST Framework |
| Auth | JWT (djangorestframework-simplejwt) |
| Database | SQLite (dev) / PostgreSQL (prod, Railway) |
| Vector DB | pgvector (PostgreSQL extension) |
| LLM | Anthropic SDK / Google Gen AI SDK / Groq SDK / Cerebras (OpenAI-compatible SDK) / OpenRouter (OpenAI-compatible SDK) |
| Encryption | cryptography (Fernet symmetric) |
| Async Logging | QueueHandler + QueueListener |
| Naming | djangorestframework-camel-case |
| Deployment | Railway (auto-deploy on push) |

---

## Project Structure

```
fanfic-assistant-backend/
  core/
    settings.py, urls.py, wsgi.py
    taxonomy.py       # Global TAXONOMY tag table (single source of truth)
  characters/
    models.py         # BaseCard, AUMod, Relationship, RelationshipMembership, LabelHistory
  users/
    models.py         # UserProviderKey, UserLLMConfig
    encryption.py     # Fernet encrypt/decrypt
  logs/
    models.py         # RestApiLog, LlmCallLog
    context.py        # request_id ContextVar
    middleware.py     # Auto REST API logging
    decorators.py     # @log_llm_call
    queue.py          # QueueHandler + QueueListener async writes
  generation/
    providers/
      base.py         # BaseProvider, UsageInfo, CompleteResult
      anthropic.py    # claude-sonnet-4-20250514
      gemini.py       # gemini-2.5-flash (real streaming via generate_content_stream + first-chunk retry)
      groq.py         # llama-3.3-70b-versatile (free tier)
      cerebras.py     # llama3.1-8b, OpenAI-compatible (~1B tokens/month free)
      openrouter.py   # meta-llama/llama-3.3-70b-instruct:free (200 req/day free)
    prompt.py         # Character card + style fragments → system/user prompt
    views.py          # POST /api/generate/stream/ SSE endpoint
  examples/           # Phase 2
    models.py         # Article, Fragment (pgvector VectorField 768 dims)
    embedding.py      # gemini-embedding-001, MRL truncation to 768 dims
    llm_pipeline.py   # Line-number segmentation, TAXONOMY tag inference
    views.py          # Full CRUD + segment + infer-tags + confirm endpoints
  evaluation/
    models.py         # ConsistencyScore
    views.py          # POST /api/evaluation/score/
  EXPERIMENT.md
  requirements.txt
```

---

## API Reference

### Auth

| Method | Path | Description | Auth |
|---|---|---|---|
| POST | `/api/auth/register/` | Register | Public |
| POST | `/api/token/` | Login (JWT) | Public |
| POST | `/api/token/refresh/` | Refresh token | Public |
| GET | `/api/auth/me/` | Current user info | Required |
| GET | `/api/auth/llm-config/` | Get provider config + hasKey status | Required |
| POST | `/api/auth/llm-config/` | Save/update a provider's API Key | Required |
| PATCH | `/api/auth/llm-config/` | Switch active provider | Required |

**LLM Config — POST body:**
```json
{ "provider": "groq", "api_key": "gsk_..." }
```
Supported providers: `anthropic`, `gemini`, `groq`, `cerebras`, `openrouter`. Keys are Fernet-encrypted, never returned in plaintext. Saving a key automatically switches to that provider.

**LLM Config — PATCH body (switch without re-entering key):**
```json
{ "provider": "gemini" }
```

**LLM Config — GET response:**
```json
{
  "activeProvider": "groq",
  "providers": {
    "anthropic": { "hasKey": true, "capabilities": { "video": false, "embedding": false } },
    "gemini": { "hasKey": true, "capabilities": { "video": true, "embedding": true } },
    "groq": { "hasKey": true, "capabilities": { "video": false, "embedding": false } },
    "cerebras": { "hasKey": false, "capabilities": { "video": false, "embedding": false } },
    "openrouter": { "hasKey": false, "capabilities": { "video": false, "embedding": false } }
  }
}
```

---

### Character Cards

| Method | Path | Description |
|---|---|---|
| GET | `/api/characters/` | List (lightweight) |
| POST | `/api/characters/` | Create |
| GET | `/api/characters/:id/` | Detail (full data) |
| PATCH | `/api/characters/:id/` | Update |
| DELETE | `/api/characters/:id/` | Delete |

---

### AU Mods

| Method | Path | Description |
|---|---|---|
| GET | `/api/characters/:id/mods/` | List |
| POST | `/api/characters/:id/mods/` | Create |
| GET | `/api/characters/:id/mods/:modId/` | Detail |
| PATCH | `/api/characters/:id/mods/:modId/` | Update |
| DELETE | `/api/characters/:id/mods/:modId/` | Delete |

---

### Generation (SSE)

| Method | Path | Description |
|---|---|---|
| POST | `/api/generate/stream/` | SSE streaming generation |

**Request:**
```json
{
  "characterId": "uuid",
  "auModId": "uuid",
  "sceneInput": {
    "location": "convenience store entrance",
    "intent": "Chen Mo questions Lin Yu's decision; Lin Yu accepts but is hurt",
    "characters": ["Lin Yu", "Chen Mo"],
    "time": "late night",
    "tone": "oppressive",
    "perspective": "Lin Yu's POV",
    "secondaryCharacters": ["passerby"],
    "sceneRole": "escalate conflict",
    "targetState": "relationship more tense",
    "desiredLength": "medium",
    "sceneRestrictions": "no physical contact"
  }
}
```

**SSE events:**
```
data: {"type": "chunk", "content": "..."}
data: {"type": "done", "generationId": "uuid", "styleInjected": true, "styleFragmentCount": 3}
data: {"type": "error", "code": "provider_key_invalid"}
```
Error `code` is machine-readable (`provider_key_invalid`, `provider_rate_limit`, `provider_quota_daily`, `provider_quota_monthly`, `rate_limited`, `no_api_key`, `character_not_found`, `validation_error`, `generation_failed`, ...); the frontend maps it to a localized message.

Rate limit: 10 req/user/min. Style injection (Phase 2) fires automatically when confirmed fragments exist for the character and the user has a Gemini Key.

---

### Example Library (Phase 2)

**Critical constraint:** LLM calls (segmentation, tag inference) use the active provider. Vectorization (embedding) always uses Gemini `gemini-embedding-001` — Groq/Anthropic have no embedding APIs.

| Method | Path | Description |
|---|---|---|
| GET | `/api/examples/articles/` | List articles |
| POST | `/api/examples/articles/` | Upload article |
| GET | `/api/examples/articles/:id/` | Detail (with fragments) |
| PATCH | `/api/examples/articles/:id/` | Update title/content |
| DELETE | `/api/examples/articles/:id/` | Delete |
| POST | `/api/examples/articles/:id/segment/` | LLM segmentation (line-number approach) |
| POST | `/api/examples/articles/:id/confirm-all/` | Batch vectorize all tagged fragments |
| GET | `/api/examples/fragments/` | List fragments |
| GET | `/api/examples/fragments/:id/` | Fragment detail |
| PATCH | `/api/examples/fragments/:id/` | Update text or tags |
| DELETE | `/api/examples/fragments/:id/` | Delete |
| POST | `/api/examples/fragments/:id/infer-tags/` | LLM tag inference |
| POST | `/api/examples/fragments/:id/confirm/` | Vectorize and index |

---

### Consistency Evaluation

| Method | Path | Description |
|---|---|---|
| POST | `/api/evaluation/score/` | LLM-as-judge scoring |

**Request:**
```json
{ "generationId": "uuid", "generatedText": "...", "characterId": "uuid", "auModId": "uuid" }
```

**Response:**
```json
{ "score": 8, "reasoning": "...", "evaluationId": "uuid" }
```

---

### Relationships

| Method | Path | Description |
|---|---|---|
| GET | `/api/relationships/` | List |
| POST | `/api/relationships/` | Create (requires `participant_ids`, min 2) |
| GET | `/api/relationships/:id/` | Detail (with memberships) |
| PATCH | `/api/relationships/:id/` | Update overall tone |
| DELETE | `/api/relationships/:id/` | Delete |
| PATCH | `/api/relationships/:id/memberships/:mId/` | Update participant mod |

---

### TAXONOMY & Label History

| Method | Path | Description |
|---|---|---|
| GET | `/api/taxonomy/` | Global tag table (read-only) |
| GET | `/api/label-history/?field_type=xxx` | Per-user label suggestions |
| POST | `/api/label-history/` | Add/refresh label (upsert) |

---

## Data Models

### UserProviderKey + UserLLMConfig (Phase 2 multi-key)
```python
class UserProviderKey(models.Model):
    user, provider              # unique_together
    api_key_encrypted           # Fernet encrypted, never returned in plaintext

class UserLLMConfig(models.Model):
    user                        # OneToOneField → User
    provider                    # active provider: anthropic / gemini / groq
```

### Article + Fragment (Phase 2)
```python
class Article(models.Model):
    owner, character, title, content, created_at, updated_at

class Fragment(models.Model):
    owner, article, character
    text                        # fragment text
    tags                        # JSONField (TAXONOMY selections)
    embedding                   # VectorField(dimensions=768) — gemini-embedding-001 MRL
    is_confirmed                # bool — user-reviewed and indexed
    order, created_at, updated_at
```

### BaseCard
```python
class BaseCard(models.Model):
    owner, name, fandom
    gender                      # 他/她/它/祂/other
    gender_type, gender_pronoun # only for "other"
    mbti, mbti_notes
    core_values, core_fears, key_experiences  # JSONField
    quick_labels                # JSONField
    behavioral_patterns         # JSONField (trigger 4-dim + response 3-dim: immediate/follow_up/internal)
    forbidden_behaviors         # JSONField
    default_state, emotion_expression_style, recovery_pattern  # text
    emotional_triggers, conditions, physical_traits  # JSONField
    speech_style_custom_tags    # JSONField
```

### AUMod
```python
class AUMod(models.Model):
    character, au_name, setting, role_title, role_age, role_current_situation
    quick_labels, forbidden_behaviors  # JSONField
    inherit_exclude             # JSONField: {"quick_labels": ["id1"], "forbidden_behaviors": ["id2"]}
```

### Relationship / RelationshipMembership
```python
class Relationship(models.Model):
    owner, overall_tone
    participants                # ManyToManyField through RelationshipMembership

class RelationshipMembership(models.Model):
    relationship, character
    nicknames_for_others        # JSONField: [{"calls": "Chen Mo", "as": ["Lao Chen"]}]
    quick_labels, forbidden_behaviors, inherit_exclude  # JSONField
```

### LabelHistory
```python
class LabelHistory(models.Model):
    user, field_type, label     # unique_together (user, field_type, label)
    used_at                     # auto_now — refreshed on every POST for recency ranking
```

---

## Architecture Status

| Item | Status | Notes |
|---|---|---|
| BaseCard + AUMod CRUD | ✅ | `inherit_exclude` inheritance |
| Relationship entity + UI | ✅ | Independent model; list + detail pages |
| Label history autocomplete | ✅ | DB storage, cross-device sync |
| TAXONOMY | ✅ | `core/taxonomy.py` + `GET /api/taxonomy/` |
| LLM-as-judge evaluation | ✅ | `evaluation/` app; 0–10 score; `generation_id` chain |
| Phase 1 experiment | ✅ | 9.8 vs 8.4 vs 7.8; see EXPERIMENT.md |
| Multi-provider key storage | ✅ | `UserProviderKey`; Anthropic/Gemini/Groq/Cerebras/OpenRouter |
| Groq provider | ✅ | llama-3.3-70b-versatile; free tier |
| Cerebras provider | ✅ | `llama3.1-8b`, OpenAI-compatible SDK; ~1B tokens/month free (originally planned `llama-3.3-70b`, not available on free tier — see `6a0c7bb`) |
| OpenRouter provider | ✅ | `meta-llama/llama-3.3-70b-instruct:free`, OpenAI-compatible SDK; 200 req/day free |
| Article + Fragment models | ✅ | pgvector VectorField(768 dims) |
| LLM segmentation | ✅ | Line-number approach; chunked; ~200 output tokens |
| TAXONOMY tag inference | ✅ | LLM infers; all editable |
| pgvector embedding | ✅ | gemini-embedding-001; MRL to 768 dims |
| Example Library API | ✅ | Full CRUD + segment + infer-tags + confirm |
| Style injection in generation | ✅ | Cosine similarity; few-shot system prompt injection |
| VectorSearchLog | ✅ | Query text, result count, latency; feeds the ablation study |
| Phase 2 ablation study | ❌ | Core deliverable; see frontend `docs/ToDo.md` for current blockers |
| Celery + Redis | ⚠️ Deferred | Re-evaluate as needed |

---

## Local Development

The project uses a repository-local uv, Python 3.12, virtual environment, cache,
temporary directory, and configuration. It does not install or replace a
system Python and does not modify `PATH` or shell profiles.

**Apple Silicon macOS:**

```bash
git clone https://github.com/YennieP/Fanfic-Assistant-Backend.git
cd fanfic-assistant-backend
./scripts/bootstrap-macos.sh
./scripts/python-project.sh manage.py migrate
./scripts/python-project.sh manage.py runserver 127.0.0.1:8000
```

**64-bit x86 Windows (PowerShell):**

```powershell
git clone https://github.com/YennieP/Fanfic-Assistant-Backend.git
cd Fanfic-Assistant-Backend
.\scripts\bootstrap-windows.ps1
.\scripts\python-project.ps1 manage.py migrate
.\scripts\python-project.ps1 manage.py runserver 127.0.0.1:8000
```

Both bootstrap scripts verify a pinned uv download, install dependencies from
`uv.lock`, and create a local `.env` with fresh development-only secrets when
one does not already exist. The local database is SQLite. Do not copy Railway
production environment variables into this checkout. Delete `.runtime/` and
`.venv/` to remove the project Python and all Python dependencies completely.

Naming: backend `snake_case` → API `camelCase` (djangorestframework-camel-case auto-converts).

---

---

<a name="中文"></a>

# Fanfic Assistant 后端 — Django REST API

> 中文同人文长文写作辅助工具的后端服务。提供角色卡管理、AU Mod、关系实体、用户认证、多 provider LLM 生成、Phase 2 示例库（pgvector）和一致性评估的 REST API。

**🌐 API 地址：[https://web-production-29e7.up.railway.app](https://web-production-29e7.up.railway.app)**

- 前端仓库：[Fanfic-Assistant](https://github.com/YennieP/Fanfic-Assistant)
- 设计文档：[docs/](https://github.com/YennieP/Fanfic-Assistant/tree/main/docs)（位于前端仓库）

---

## Phase 1 实验结论

3 组 × 5 场景对照实验，完整报告见 [EXPERIMENT.md](./EXPERIMENT.md)。

| 组别 | 说明 | 平均分 |
|---|---|---|
| Control A | 零角色信息 | 7.8 |
| Control B | 自然语言角色简介（2-3 句话）| 8.4 |
| **Experimental** | **完整结构化角色卡系统** | **9.8** |

核心发现：「开关机制」场景（进入娱乐状态时完全放开）——Control A/B 均只有 2 分，角色卡系统 9 分。

---

## 技术栈

| 层级 | 技术 |
|---|---|
| 框架 | Django 6 + Django REST Framework |
| 认证 | JWT（djangorestframework-simplejwt）|
| 数据库 | SQLite（开发）/ PostgreSQL（生产，Railway）|
| 向量数据库 | pgvector（PostgreSQL 扩展）|
| LLM | Anthropic SDK / Google Gen AI SDK / Groq SDK / Cerebras (OpenAI-compatible SDK) / OpenRouter (OpenAI-compatible SDK) |
| 加密 | cryptography（Fernet 对称加密）|
| 异步日志 | QueueHandler + QueueListener |
| 命名转换 | djangorestframework-camel-case |
| 部署 | Railway（push 即自动更新）|

---

## API 参考

### 认证

| 方法 | 路径 | 说明 | 认证 |
|---|---|---|---|
| POST | `/api/auth/register/` | 注册 | 公开 |
| POST | `/api/token/` | 登录获取 JWT | 公开 |
| POST | `/api/token/refresh/` | 刷新 token | 公开 |
| GET | `/api/auth/me/` | 当前用户信息 | 必须 |
| GET | `/api/auth/llm-config/` | 获取 provider 配置和 Key 状态 | 必须 |
| POST | `/api/auth/llm-config/` | 保存/更新某 provider 的 API Key | 必须 |
| PATCH | `/api/auth/llm-config/` | 切换当前激活的 provider | 必须 |

**POST 请求体：**
```json
{ "provider": "groq", "api_key": "gsk_..." }
```
支持的 provider：`anthropic`、`gemini`、`groq`、`cerebras`、`openrouter`。Key Fernet 加密存储，永不明文返回。保存 Key 后自动切换到该 provider。

**GET 响应：**
```json
{
  "activeProvider": "groq",
  "providers": {
    "anthropic": { "hasKey": true, "capabilities": { "video": false, "embedding": false } },
    "gemini": { "hasKey": true, "capabilities": { "video": true, "embedding": true } },
    "groq": { "hasKey": true, "capabilities": { "video": false, "embedding": false } },
    "cerebras": { "hasKey": false, "capabilities": { "video": false, "embedding": false } },
    "openrouter": { "hasKey": false, "capabilities": { "video": false, "embedding": false } }
  }
}
```

---

### 角色卡

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/characters/` | 列表（轻量）|
| POST | `/api/characters/` | 新建 |
| GET | `/api/characters/:id/` | 详情（完整数据）|
| PATCH | `/api/characters/:id/` | 更新 |
| DELETE | `/api/characters/:id/` | 删除 |

---

### AU Mod

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/characters/:id/mods/` | 列表 |
| POST | `/api/characters/:id/mods/` | 新建 |
| GET | `/api/characters/:id/mods/:modId/` | 详情 |
| PATCH | `/api/characters/:id/mods/:modId/` | 更新 |
| DELETE | `/api/characters/:id/mods/:modId/` | 删除 |

---

### 生成（SSE）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/generate/stream/` | SSE 流式生成 |

SSE 事件格式：
```
data: {"type": "chunk", "content": "..."}
data: {"type": "done", "generationId": "uuid", "styleInjected": true, "styleFragmentCount": 3}
data: {"type": "error", "code": "provider_key_invalid"}
```
`code` 为机器可读错误码（`provider_key_invalid`、`provider_rate_limit`、`provider_quota_daily`、`provider_quota_monthly`、`rate_limited`、`no_api_key`、`character_not_found`、`validation_error`、`generation_failed` 等），前端据此查 i18n 表展示本地化文案。

限流：每用户每分钟 10 次。Phase 2 风格注入在有已入库片段且有 Gemini Key 时自动触发。

---

### 示例库（Phase 2）

**重要约束：** LLM 调用（切割/标签推断）使用当前激活的 provider；向量化固定使用 Gemini `gemini-embedding-001`（Groq/Anthropic 无 embedding API）。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/examples/articles/` | 上传文章 |
| POST | `/api/examples/articles/:id/segment/` | LLM 情节切割（行号边界方案）|
| POST | `/api/examples/articles/:id/confirm-all/` | 批量向量化入库 |
| POST | `/api/examples/fragments/:id/infer-tags/` | LLM 标签推断 |
| POST | `/api/examples/fragments/:id/confirm/` | 单片段向量化入库 |
| PATCH | `/api/examples/fragments/:id/` | 更新文本或标签 |

---

### 一致性评估

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/evaluation/score/` | LLM-as-judge 一致性评分 |

---

### 关系实体

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/api/relationships/` | 列表 / 新建 |
| GET/PATCH/DELETE | `/api/relationships/:id/` | 详情 / 更新基调 / 删除 |
| PATCH | `/api/relationships/:id/memberships/:mId/` | 更新参与者 mod |

---

## 架构状态

| 功能 | 状态 | 说明 |
|---|---|---|
| BaseCard + AUMod CRUD | ✅ | `inherit_exclude` 继承机制 |
| 关系实体 + 前端 UI | ✅ | 独立 model；列表页 + 详情页 |
| 标签历史 autocomplete | ✅ | 数据库存储，跨设备同步 |
| TAXONOMY 全局标签表 | ✅ | `core/taxonomy.py` + `GET /api/taxonomy/` |
| LLM-as-judge 一致性评估 | ✅ | `evaluation/` app；0-10 分；`generation_id` 串联链路 |
| Phase 1 对比实验 | ✅ | 9.8 vs 8.4 vs 7.8；见 EXPERIMENT.md |
| 多 provider Key 存储 | ✅ | `UserProviderKey`；Anthropic/Gemini/Groq/Cerebras/OpenRouter 独立 Key |
| Groq provider | ✅ | llama-3.3-70b-versatile；免费 tier |
| Cerebras provider | ✅ | `llama3.1-8b`，OpenAI 兼容 SDK；~10 亿 token/月免费（原计划用 `llama-3.3-70b`，免费 tier 账号权限不支持，见 `6a0c7bb`）|
| OpenRouter provider | ✅ | `meta-llama/llama-3.3-70b-instruct:free`，OpenAI 兼容 SDK；200 req/天免费 |
| Article + Fragment 模型 | ✅ | pgvector VectorField(768 维) |
| LLM 情节切割 | ✅ | 行号边界方案；分块处理；~200 token 输出 |
| TAXONOMY 标签推断 | ✅ | LLM 推断；全部可编辑 |
| pgvector 向量化 | ✅ | gemini-embedding-001；MRL 截断至 768 维 |
| 示例库 API | ✅ | 完整 CRUD + segment + infer-tags + confirm |
| 生成时风格注入 | ✅ | cosine similarity 检索；few-shot 注入 |
| VectorSearchLog | ✅ | 查询文字、结果数、延迟；供 ablation study 分析使用 |
| Phase 2 ablation study | ❌ | 核心 deliverable；当前阻塞项见前端仓库 `docs/ToDo.md` |
| Celery + Redis | ⚠️ 延期 | 按需评估 |

---

## 本地开发

本项目将 uv、Python 3.12、虚拟环境、缓存、临时目录和工具配置全部放在仓库内，
不会安装或替换系统 Python，也不会修改 `PATH` 或 shell 配置。

**Apple Silicon macOS：**

```bash
git clone https://github.com/YennieP/Fanfic-Assistant-Backend.git
cd fanfic-assistant-backend
./scripts/bootstrap-macos.sh
./scripts/python-project.sh manage.py migrate
./scripts/python-project.sh manage.py runserver 127.0.0.1:8000
```

**64 位 x86 Windows（PowerShell）：**

```powershell
git clone https://github.com/YennieP/Fanfic-Assistant-Backend.git
cd Fanfic-Assistant-Backend
.\scripts\bootstrap-windows.ps1
.\scripts\python-project.ps1 manage.py migrate
.\scripts\python-project.ps1 manage.py runserver 127.0.0.1:8000
```

两个 bootstrap 脚本都会校验固定版本 uv 的下载文件、严格按 `uv.lock` 安装依赖；
若 `.env` 不存在，还会生成仅供本地开发使用的新密钥。本地数据库默认使用 SQLite。
不要把 Railway 生产环境变量复制到本仓库。删除 `.runtime/` 和 `.venv/` 即可完整移除
项目 Python 及全部 Python 依赖。

命名约定：后端存储 `snake_case`，API 输出自动转换为 `camelCase`（djangorestframework-camel-case）。

---

## 作者

**Yanxi Pan** — CS Master's Student @ Northeastern University (Silicon Valley Campus, Class of 2027)
目标方向：Applied ML Engineer — Generative AI / Content AI
