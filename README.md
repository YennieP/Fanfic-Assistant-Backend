[English](#english) | [中文](#中文)

---

<a name="english"></a>

# Fanfic Assistant Backend — Django REST API

> Backend service for a Chinese fanfiction long-form writing assistant, providing REST APIs for character card management, AU Mod management, relationship entities, user authentication, and AI text generation.

**🌐 API Base URL: [https://web-production-29e7.up.railway.app](https://web-production-29e7.up.railway.app)**

- Web Frontend: [Fanfic-Assistant](https://github.com/YennieP/Fanfic-Assistant)
- Mobile Client: [Fanfic-Assistant-Mobile](https://github.com/YennieP/Fanfic-Assistant-Mobile)

---

## Tech Stack

| Layer | Technology |
|---|---|
| Framework | Django 6 + Django REST Framework |
| Auth | JWT (djangorestframework-simplejwt) |
| Database | SQLite (dev) / PostgreSQL (prod) |
| LLM | Anthropic SDK / Google Gen AI SDK |
| Encryption | cryptography (Fernet symmetric encryption) |
| Async Logging | QueueHandler + QueueListener |
| Cross-origin | django-cors-headers |
| Naming | djangorestframework-camel-case |
| Deployment | Railway |

---

## Project Structure

```
fanfic-assistant-backend/
  core/
    settings.py
    urls.py
    wsgi.py
  characters/
    models.py         # BaseCard, AUMod, Relationship, RelationshipMembership
    serializers.py
    views.py
    urls.py
    admin.py
    migrations/
  users/
    models.py         # UserLLMConfig (provider + encrypted API Key)
    serializers.py    # LLMConfigSerializer (api_key never returned)
    views.py          # LLMConfigView
    urls.py
    encryption.py     # Fernet encrypt/decrypt utilities
  logs/
    models.py         # RestApiLog, LlmCallLog
    context.py        # request_id ContextVar
    middleware.py     # Automatic REST API request logging
    decorators.py     # @log_llm_call decorator (supports both regular and generator returns)
    queue.py          # QueueHandler + QueueListener async writes
    admin.py          # Admin dashboard
    migrations/
  generation/
    providers/
      base.py         # Abstract base class + UsageInfo dataclass
      anthropic.py    # Anthropic Claude implementation
      gemini.py       # Google Gemini implementation
    prompt.py         # Character card → prompt builder
    views.py          # POST /api/generate/stream/ SSE endpoint
    urls.py
  .env
  requirements.txt
  manage.py
```

---

## API Reference

### Auth

| Method | Path | Description | Auth |
|---|---|---|---|
| POST | `/api/auth/register/` | Register new user | Public |
| POST | `/api/token/` | Login, obtain JWT tokens | Public |
| POST | `/api/token/refresh/` | Refresh access token | Public |
| GET | `/api/auth/me/` | Get current user info | Required |

All authenticated requests must include:
```
Authorization: Bearer <access_token>
```

---

### Character Cards

| Method | Path | Description |
|---|---|---|
| GET | `/api/characters/` | List all character cards (lightweight) |
| POST | `/api/characters/` | Create character card |
| GET | `/api/characters/:id/` | Get character card detail (full data) |
| PATCH | `/api/characters/:id/` | Update character card |
| DELETE | `/api/characters/:id/` | Delete character card |

---

### AU Mods

| Method | Path | Description |
|---|---|---|
| GET | `/api/characters/:id/mods/` | List all AU Mods for a character |
| POST | `/api/characters/:id/mods/` | Create AU Mod |
| GET | `/api/characters/:id/mods/:modId/` | Get AU Mod detail |
| PATCH | `/api/characters/:id/mods/:modId/` | Update AU Mod |
| DELETE | `/api/characters/:id/mods/:modId/` | Delete AU Mod |

---

### LLM Config

| Method | Path | Description |
|---|---|---|
| GET | `/api/auth/llm-config/` | Get current user LLM config (returns hasKey + provider only, never the key plaintext) |
| POST | `/api/auth/llm-config/` | Save or update LLM config |

**Request:**
```json
{ "provider": "anthropic", "api_key": "sk-ant-..." }
```

**Response:**
```json
{ "hasKey": true, "provider": "anthropic" }
```

Supported providers: `anthropic` (Claude), `gemini` (Google Gemini). API Keys are stored with Fernet symmetric encryption and never appear in any API response.

---

### Generation

| Method | Path | Description |
|---|---|---|
| POST | `/api/generate/stream/` | SSE streaming generation, returns `text/event-stream` |

**Request body:**
```json
{
  "characterId": "uuid",
  "auModId": "uuid",
  "sceneInput": {
    "location": "convenience store entrance",
    "intent": "Chen Mo questions Lin Yu's decision; Lin Yu accepts on the surface but is hurt inside",
    "characters": ["Lin Yu", "Chen Mo"],
    "time": "late night",
    "tone": "oppressive",
    "perspective": "Lin Yu's POV"
  }
}
```

**SSE event format:**
```
data: {"type": "chunk", "content": "generated text fragment"}
data: {"type": "done"}
data: {"type": "error", "message": "error info"}
```

Rate limit: 10 requests per user per minute.

---

### Relationship Entities

Relationships are independent entities, not owned by any single character. They activate when all participant characters are present in the scene.

| Method | Path | Description |
|---|---|---|
| GET | `/api/relationships/` | List all relationship entities for current user |
| POST | `/api/relationships/` | Create relationship entity |
| GET | `/api/relationships/:id/` | Get detail (including memberships) |
| PATCH | `/api/relationships/:id/` | Update overall tone |
| DELETE | `/api/relationships/:id/` | Delete relationship entity |
| GET | `/api/relationships/:id/memberships/` | List all participant mods |
| GET | `/api/relationships/:id/memberships/:mId/` | Get single participant mod |
| PATCH | `/api/relationships/:id/memberships/:mId/` | Update participant mod |

> `participant_ids` is only accepted at creation time (minimum 2 participants, must belong to current user). The system automatically creates an empty membership for each participant. Memberships are created/deleted with the relationship entity and cannot be added or removed individually.

---

## Data Models

### BaseCard
```python
class BaseCard(models.Model):
    owner                    # FK → User
    name                     # character name
    fandom                   # source work
    mbti                     # MBTI type
    core_values              # JSONField
    core_fears               # JSONField
    key_experiences          # JSONField
    quick_labels             # JSONField
    behavioral_patterns      # JSONField (trigger 4-dim + response 3-dim)
    forbidden_behaviors      # JSONField
    default_state            # default emotional baseline
    emotional_triggers       # JSONField
    emotion_expression_style # text
    recovery_pattern         # text
    conditions               # JSONField
    physical_traits          # JSONField
    speech_style_custom_tags # JSONField
```

### AUMod
```python
class AUMod(models.Model):
    character                # FK → BaseCard
    au_name                  # AU name
    setting                  # world/background setting
    role_title               # occupation/identity
    role_age                 # age in this AU
    role_current_situation   # current life situation
    quick_labels             # JSONField
    forbidden_behaviors      # JSONField
    inherit_exclude          # JSONField — excluded BaseCard entry IDs
                             # structure: {"quick_labels": ["id1"], "forbidden_behaviors": ["id2"]}
```

### Relationship / RelationshipMembership
```python
class Relationship(models.Model):
    owner                    # FK → User (access control)
    overall_tone             # objective description of relationship dynamic
    participants             # ManyToManyField through RelationshipMembership

class RelationshipMembership(models.Model):
    relationship             # FK → Relationship
    character                # FK → BaseCard
    nicknames_for_others     # JSONField — [{"calls": "Chen Mo", "as": ["Lao Chen"]}]
    quick_labels             # JSONField
    forbidden_behaviors      # JSONField
    inherit_exclude          # JSONField
```

### UserLLMConfig
```python
class UserLLMConfig(models.Model):
    user                     # OneToOneField → User
    provider                 # enum: anthropic / gemini
    api_key_encrypted        # Fernet encrypted, never returned in plaintext
```

---

## Generation Pipeline

```
generation/
  providers/
    base.py       # Abstract BaseProvider + UsageInfo dataclass
    anthropic.py  # claude-sonnet-4-20250514
    gemini.py     # gemini-2.5-flash
  prompt.py       # Character card → system/user prompt construction
  views.py        # POST /api/generate/stream/ SSE endpoint
```

**LlmCallLog is integrated into the generation pipeline.** Latency, token usage, and success/failure for every LLM call are automatically recorded via `@log_llm_call(feature="character_generate")`.

Implementation: providers yield a `UsageInfo` sentinel after all text chunks; the decorator wraps the generator, transparently passes text chunks, captures `UsageInfo`, and writes `LlmCallLog` after iteration completes. The view layer and frontend are unaware of `UsageInfo`.

---

## Naming Convention

Backend uses `snake_case`; API output is automatically converted to `camelCase` by `djangorestframework-camel-case`:

```
quick_labels    → quickLabels
au_name         → auName
inherit_exclude → inheritExclude
overall_tone    → overallTone
```

---

## Local Development

### Requirements
- Python 3.12+
- pip

### Setup

```bash
git clone https://github.com/YennieP/Fanfic-Assistant-Backend.git
cd fanfic-assistant-backend

python -m venv venv
source venv/bin/activate        # macOS/Linux
venv\Scripts\activate           # Windows

pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Server runs at `http://localhost:8000`

### Environment Variables (`.env`)

```
SECRET_KEY=django-insecure-xxx
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
DATABASE_URL=sqlite:///db.sqlite3
CORS_ALLOWED_ORIGINS=http://localhost:5173
ENCRYPTION_KEY=<generate with Fernet.generate_key()>
```

---

## Architecture Status

| Item | Status | Notes |
|---|---|---|
| Relationship independent model | ✅ Done | — |
| AUMod `inherit_exclude` field | ✅ Done | Field complete; UI pending |
| LlmCallLog in generation pipeline | ✅ Done | — |
| TAXONOMY backend storage | ⚠️ Pending | Before writing UI |
| Vector database integration | ❌ Pending | Required before Phase 2 |
| Celery + Redis async queue | ⚠️ Deferred | Re-evaluate at Phase 2 |

---

## Pending Features

- [ ] Relationship entity frontend UI (backend complete)
- [ ] TAXONOMY global tag table backend storage
- [ ] Example library API (depends on vector database)
- [ ] Vector database integration (pgvector / Chroma / Qdrant)
- [ ] Celery + Redis async queue (deferred to Phase 2 evaluation)
- [ ] LLM-as-judge consistency evaluation API
- [x] `@log_llm_call` decorator integrated into generation pipeline

---

## Author

**Yanxi Pan**
CS Master's Student @ Northeastern University (Silicon Valley Campus, Class of 2027)
Target Role: Applied ML Engineer — Generative AI / Content AI

---

---

<a name="中文"></a>

# Fanfic Assistant Backend — Django REST API

> 中文同人文长文写作辅助工具的后端服务，提供角色卡管理、AU Mod 管理、关系实体管理、用户认证等 API 接口。

**🌐 后端 API 地址：[https://web-production-29e7.up.railway.app](https://web-production-29e7.up.railway.app)**

- 前端仓库：[Fanfic-Assistant](https://github.com/YennieP/Fanfic-Assistant)
- 移动端仓库：[Fanfic-Assistant-Mobile](https://github.com/YennieP/Fanfic-Assistant-Mobile)

---

## 技术栈

| 层级 | 技术 |
|---|---|
| 后端框架 | Django 6 + Django REST Framework |
| 认证 | JWT（djangorestframework-simplejwt）|
| 数据库 | SQLite（开发）/ PostgreSQL（生产）|
| LLM | Anthropic SDK / Google Gen AI SDK |
| 加密 | cryptography（Fernet 对称加密）|
| 异步日志 | QueueHandler + QueueListener |
| 跨域 | django-cors-headers |
| 命名转换 | djangorestframework-camel-case |
| 部署 | Railway |

---

## 项目结构

```
fanfic-assistant-backend/
  core/
    settings.py
    urls.py
    wsgi.py
  characters/
    models.py         # BaseCard、AUMod、Relationship、RelationshipMembership
    serializers.py
    views.py
    urls.py
    admin.py
    migrations/
  users/
    models.py         # UserLLMConfig（provider + 加密 API Key）
    serializers.py    # LLMConfigSerializer（api_key 永不返回）
    views.py          # LLMConfigView
    urls.py
    encryption.py     # Fernet 加密/解密工具
  logs/
    models.py         # RestApiLog、LlmCallLog
    context.py        # request_id ContextVar
    middleware.py     # REST API 请求自动记录
    decorators.py     # @log_llm_call 装饰器（支持普通返回值和 generator 两种路径）
    queue.py          # QueueHandler + QueueListener 异步写入
    admin.py          # Dashboard
    migrations/
  generation/
    providers/
      base.py         # 抽象基类 + UsageInfo dataclass
      anthropic.py    # Anthropic Claude 实现
      gemini.py       # Google Gemini 实现
    prompt.py         # 角色卡 → prompt 构建逻辑
    views.py          # SSE streaming endpoint
    urls.py
  .env
  requirements.txt
  manage.py
```

---

## API 接口文档

### 认证

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| POST | `/api/auth/register/` | 注册新用户 | 公开 |
| POST | `/api/token/` | 登录，获取 JWT token | 公开 |
| POST | `/api/token/refresh/` | 刷新 access token | 公开 |
| GET | `/api/auth/me/` | 获取当前用户信息 | 需登录 |

所有需要登录的接口请求头携带：
```
Authorization: Bearer <access_token>
```

---

### 角色卡

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/characters/` | 获取当前用户所有角色卡（轻量列表）|
| POST | `/api/characters/` | 创建角色卡 |
| GET | `/api/characters/:id/` | 获取角色卡详情（完整数据）|
| PATCH | `/api/characters/:id/` | 更新角色卡 |
| DELETE | `/api/characters/:id/` | 删除角色卡 |

---

### AU Mod

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/characters/:id/mods/` | 获取某角色的所有 AU Mod |
| POST | `/api/characters/:id/mods/` | 创建 AU Mod |
| GET | `/api/characters/:id/mods/:modId/` | 获取 AU Mod 详情 |
| PATCH | `/api/characters/:id/mods/:modId/` | 更新 AU Mod |
| DELETE | `/api/characters/:id/mods/:modId/` | 删除 AU Mod |

---

### LLM 配置

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/auth/llm-config/` | 获取当前用户 LLM 配置（不返回 key 明文，只返回 hasKey + provider）|
| POST | `/api/auth/llm-config/` | 保存或更新 LLM 配置 |

**请求体：**
```json
{ "provider": "anthropic", "api_key": "sk-ant-..." }
```

**响应体：**
```json
{ "hasKey": true, "provider": "anthropic" }
```

支持的 provider：`anthropic`（Claude）、`gemini`（Google Gemini）。API Key 使用 Fernet 对称加密存储，明文永不出现在任何 API response 中。

---

### 生成

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/generate/stream/` | SSE streaming 生成，返回 `text/event-stream` |

**请求体：**
```json
{
  "characterId": "uuid",
  "auModId": "uuid",
  "sceneInput": {
    "location": "便利店门口",
    "intent": "陈默质疑林宇的决定，林宇表面接受但内心受伤",
    "characters": ["林宇", "陈默"],
    "time": "深夜",
    "tone": "压抑",
    "perspective": "林宇视角"
  }
}
```

**SSE 事件格式：**
```
data: {"type": "chunk", "content": "生成的文字片段"}
data: {"type": "done"}
data: {"type": "error", "message": "错误信息"}
```

速率限制：每用户每分钟最多 10 次请求。

---

### 关系实体

关系实体是独立存在的实体，不从属于任何单一角色。激活条件为参与者中的角色同时在场。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/relationships/` | 获取当前用户的所有关系实体 |
| POST | `/api/relationships/` | 创建关系实体 |
| GET | `/api/relationships/:id/` | 获取关系实体详情（含 memberships）|
| PATCH | `/api/relationships/:id/` | 更新关系基调 |
| DELETE | `/api/relationships/:id/` | 删除关系实体 |
| GET | `/api/relationships/:id/memberships/` | 获取所有参与者 mod |
| GET | `/api/relationships/:id/memberships/:mId/` | 获取单个参与者 mod 详情 |
| PATCH | `/api/relationships/:id/memberships/:mId/` | 更新参与者 mod |

> `participant_ids` 仅在创建时有效，至少需要 2 个参与者且必须属于当前用户。创建后系统自动为每个参与者建立空 membership。Membership 随关系实体创建/删除，不支持单独新增或删除。

---

## 数据模型

### BaseCard
```python
class BaseCard(models.Model):
    owner                    # 关联用户
    name                     # 角色名
    fandom                   # 来源作品
    mbti                     # MBTI 类型
    core_values              # 核心价值观（JSONField）
    core_fears               # 核心恐惧（JSONField）
    key_experiences          # 重要经历（JSONField）
    quick_labels             # 性格标签（JSONField）
    behavioral_patterns      # 行为模式（JSONField，trigger 四维度 + response 三维度）
    forbidden_behaviors      # 人设红线（JSONField）
    default_state            # 日常情绪基调
    emotional_triggers       # 情绪触发点（JSONField）
    emotion_expression_style # 情绪表达方式
    recovery_pattern         # 情绪恢复方式
    conditions               # 疾病（JSONField）
    physical_traits          # 特殊体征（JSONField）
    speech_style_custom_tags # 台词风格自定义标签（JSONField）
```

### AUMod
```python
class AUMod(models.Model):
    character                # 关联角色卡（ForeignKey）
    au_name                  # AU 名称
    setting                  # 世界观/背景设定
    role_title               # 职业/身份名称
    role_age                 # 年龄
    role_current_situation   # 当前人生处境
    quick_labels             # AU 专属性格标签（JSONField）
    forbidden_behaviors      # AU 专属人设红线（JSONField）
    inherit_exclude          # 从 BaseCard 排除的条目 ID（JSONField）
                             # 结构: {"quick_labels": ["id1"], "forbidden_behaviors": ["id2"]}
```

### Relationship / RelationshipMembership
```python
class Relationship(models.Model):
    owner                    # 关联用户（访问控制）
    overall_tone             # 关系整体基调（客观描述）
    participants             # ManyToManyField through RelationshipMembership

class RelationshipMembership(models.Model):
    relationship             # FK → Relationship
    character                # FK → BaseCard
    nicknames_for_others     # JSONField — [{"calls": "陈默", "as": ["老陈", "陈队"]}]
    quick_labels             # JSONField
    forbidden_behaviors      # JSONField
    inherit_exclude          # JSONField
```

### UserLLMConfig
```python
class UserLLMConfig(models.Model):
    user                     # OneToOneField → User
    provider                 # 枚举：anthropic / gemini
    api_key_encrypted        # Fernet 加密存储，永不明文返回
```

---

## Generation Pipeline

```
generation/
  providers/
    base.py       # 抽象基类 BaseProvider + UsageInfo dataclass
    anthropic.py  # claude-sonnet-4-20250514
    gemini.py     # gemini-2.5-flash
  prompt.py       # 角色卡 → system/user prompt 构建
  views.py        # POST /api/generate/stream/ SSE endpoint
```

**LlmCallLog 已接入生成 pipeline。** 每次 LLM 调用的 latency、token 用量、成功/失败均通过 `@log_llm_call(feature="character_generate")` 自动记录。

实现机制：provider 在所有文字 chunk yield 完后，最后 yield 一个 `UsageInfo` sentinel；decorator 包装整个 generator，透传文字 chunk，捕获 `UsageInfo`，迭代结束后写入 `LlmCallLog`。view 层和前端均不感知 `UsageInfo` 的存在。

---

## 命名转换规则

后端存储使用 `snake_case`，API 输出自动转换为 `camelCase`：

```
quick_labels    → quickLabels
au_name         → auName
inherit_exclude → inheritExclude
overall_tone    → overallTone
```

---

## 本地开发

### 环境要求
- Python 3.12+
- pip

### 安装步骤

```bash
git clone https://github.com/YennieP/Fanfic-Assistant-Backend.git
cd fanfic-assistant-backend

python -m venv venv
source venv/bin/activate        # macOS/Linux
venv\Scripts\activate           # Windows

pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

服务器运行在 `http://localhost:8000`

### 环境变量（.env）

```
SECRET_KEY=django-insecure-xxx
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
DATABASE_URL=sqlite:///db.sqlite3
CORS_ALLOWED_ORIGINS=http://localhost:5173
ENCRYPTION_KEY=你用 Fernet.generate_key() 生成的密钥
```

---

## 架构审查与待修正问题

| 问题 | 状态 | 解决时间节点 |
|---|---|---|
| Relationship 独立 model | ✅ 已修正 | — |
| AUMod `inherit_exclude` 字段缺失 | ✅ 已修正（字段已加，前端继承选择 UI 待实现）| — |
| LlmCallLog 接入生成 pipeline | ✅ 已完成 | — |
| TAXONOMY 全局标签表缺少后端存储 | ⚠️ 待修正 | 写作界面开发前 |
| 向量数据库选型接入 | ❌ 待修正 | Phase 2 前（必须）|
| Celery + Redis 异步队列 | ⚠️ 已延期 | Phase 2 前按需评估 |

---

## 待开发功能

- [ ] 关系实体前端 UI（model 已就绪，API 端点已完成）
- [ ] TAXONOMY 全局标签表后端存储
- [ ] 示例库 API（台词示例片段，依赖向量数据库）
- [ ] 向量数据库选型接入（pgvector / Chroma / Qdrant）
- [ ] Celery + Redis 异步队列（延期至 Phase 2 评估）
- [ ] LLM-as-judge 一致性评估 API
- [x] `@log_llm_call` decorator 接入生成 pipeline

---

## 作者

**Yanxi Pan**
CS Master's Student @ Northeastern University (Silicon Valley Campus, Class of 2027)
目标方向：Applied ML Engineer — Generative AI / Content AI