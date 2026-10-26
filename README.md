# Fanfic Assistant Backend

Django REST API for [Fanfic Assistant](https://github.com/YennieP/Fanfic-Assistant). It provides authentication, character and relationship data, multi-provider generation, consistency evaluation, the example-library pipeline, and operational logging.

中文说明为主；endpoint 名称和命令保留英文，避免与代码产生第二套翻译协议。

## 文档入口

- [后端文档职责](./docs/README.md)
- [Phase 1 实验报告](./EXPERIMENT.md)
- [仓库开发约束](./AGENTS.md)
- [跨系统设计、实现状态、待办与审计](https://github.com/YennieP/Fanfic-Assistant/tree/main/docs)

跨系统状态只在前端仓库的 `docs/IMPLEMENTATION.md` 维护，本 README 不再复制一份“已完成能力”清单。

## 技术栈

- Python 3.12, Django 6, Django REST Framework
- SimpleJWT, django-cors-headers, django-rest-framework-camel-case
- PostgreSQL + pgvector（生产与完整风格检索）；SQLite（基础本地开发与测试）
- Gemini、Groq 当前启用；Anthropic、Cerebras、OpenRouter 集成与已保存 Key 保留，但当前由 catalog 暂时下线
- Gunicorn + WhiteNoise，部署于 Railway
- pytest, pytest-django, factory-boy, pytest-cov

## 本地开发

仓库自带隔离的 uv `0.12.23` 和 Python 3.12 启动脚本。uv、Python、虚拟环境、缓存和临时文件都只写入本仓库的 `.runtime/` / `.venv/`，不会修改系统 Python、全局 `PATH`、shell profile 或其他仓库。删除这两个目录即可完整回滚本地运行环境。

Apple Silicon macOS：

```bash
./scripts/bootstrap-macos.sh
./scripts/python-project.sh manage.py migrate
./scripts/python-project.sh manage.py runserver 127.0.0.1:8000
```

64 位 x86 Windows PowerShell：

```powershell
.\scripts\bootstrap-windows.ps1
.\scripts\python-project.ps1 manage.py migrate
.\scripts\python-project.ps1 manage.py runserver 127.0.0.1:8000
```

两个 bootstrap 脚本都会校验固定版本 uv 的下载文件，安装仓库内的 Python 3.12，并严格按 `uv.lock` 创建 `.venv/`。若 `.env` 不存在，脚本还会生成仅供本地开发使用的新密钥；已有 `.env` 永不覆盖。本地数据库默认使用 SQLite，禁止复制 Railway 的数据库 URL、密钥或生产数据到本地。

## 统一验证

macOS：

```bash
./scripts/python-project.sh scripts/verify.py
```

Windows：

```powershell
.\scripts\python-project.ps1 scripts\verify.py
```

该命令与 GitHub Actions 一致，依次执行：

1. Markdown 本地链接、文件名大小写和 whitespace 检查
2. Python 版本及 `requirements*.txt` / `pyproject.toml` / `uv.lock` 直接依赖一致性检查
3. `manage.py check`
4. `manage.py makemigrations --check --dry-run`
5. 全部 pytest 测试与 coverage 报告

脚本只在子进程中为缺失的 `SECRET_KEY` / `ENCRYPTION_KEY` 提供固定的非生产验证值，使干净 CI 无需保存 secrets；已有环境变量不会被覆盖。任何真实 schema 变更仍须单独审查 migration，并遵守跨系统 `ToDo.md` 中的生产数据库核验边界。

## API 总览

除健康检查、注册和 JWT 获取/刷新外，API 默认要求 Bearer token。JSON 通过 camel-case parser/renderer 与前端交互。

| 领域 | 路径 | 说明 |
|---|---|---|
| Health | `/health/`, `/health/live/` | 数据库就绪检查与不访问外部依赖的进程存活检查；无需鉴权 |
| Auth | `/api/auth/register/`, `/api/auth/me/` | 注册与当前用户 |
| JWT | `/api/token/`, `/api/token/refresh/` | 获取与刷新 token |
| LLM config | `/api/auth/llm-config/`, `/api/auth/llm-config/verify/` | 候选 Key 真实验证后保存、已存 Key 显式验证与当前 provider；响应包含 catalog `enabled` / `disabledReason`，不返回明文 Key |
| Characters | `/api/characters/` | BaseCard CRUD |
| Character suggestions | `/api/characters/suggest-completions/` | 仅返回可审阅的补全建议，不写入 BaseCard；缺少角色名返回 400 `missing_required_fields`，空生成结果返回 500 `suggestion_result_invalid` |
| AU Mods | `/api/characters/{character_id}/mods/` | 角色下的 AU Mod CRUD |
| Translation | `/api/characters/{canonical_id}/translate/`, `/versions/` | 翻译预览与多语言版本查询；源版本不存在返回 404 `character_not_found`，空或非对象翻译结果返回 500 `translation_result_invalid` |
| Relationships | `/api/relationships/` | 关系实体与 nested memberships；membership PATCH 支持标签、红线、称呼及 `inheritExclude` |
| Taxonomy | `/api/taxonomy/`, `/api/label-history/` | 双语标签体系与历史 |
| Generation | `/api/generate/stream/` | SSE 流式生成；可选 `providerOverride` 仅覆盖本次请求 |
| Evaluation | `/api/evaluation/score/`, `/score/{id}/rate/` | LLM judge 与人工评分；显式失败响应使用 `code` / `detail` |
| Articles | `/api/examples/articles/` | 文章 CRUD、segment，以及只处理显式片段 ID 的 confirm-selected；创建时缺少角色或正文返回 400 `missing_required_fields` 且不写入。segment 可选 `providerOverride`，会在解析 provider 前忽略纯空白缺口，再先完成、验证全部实质缺口结果并用单个短事务替换旧草稿；空正文、无效结果、保存失败分别返回 `article_content_empty`、`segmentation_result_invalid`、`segmentation_save_failed`。confirm-selected 的选择范围、片段状态、标签和 Gemini Key 显式失败分别返回 `fragment_selection_invalid`、`fragment_state_conflict`、`fragment_tags_invalid`、`no_api_key`；embedding 返回后仅在片段未变化时确认，并发冲突计入既有部分失败汇总 |
| Fragments | `/api/examples/fragments/` | 片段列表；同路径 POST 创建已弃用并处于观察期；详情、infer-tags、confirm 继续支持。详情 PATCH 必须同时提供 `updatedAt` 和至少一个 `text` / `tags` 字段，用字段级条件更新避免覆盖并发状态或在删除后复活片段；详情 DELETE 也必须提供 `updatedAt`，并在短事务中锁定目标、核对版本后删除，陈旧版本或存在子片段均返回 409 `fragment_state_conflict` 且零删除。两种写入缺少或无效版本时均返回 400。infer-tags 在 LLM 返回后仅在片段输入、状态和版本未变化时写入标签，并发冲突返回相同 409，不会覆盖并发编辑、撤销并发确认或复活已删除片段。confirm 的 skip 状态、缺少或无有效值标签、缺 Gemini Key 显式失败均保持 400，分别返回 `fragment_state_conflict`、`fragment_tags_invalid`、`no_api_key`；embedding 返回后仅在片段未变化时确认，并发冲突返回 409 `fragment_state_conflict` |
| Fragment merge | `/api/examples/fragments/merge/` | 带版本校验地原子更新保留片段并删除另一片段；跨文章选择返回 400 `fragment_selection_invalid`，任一片段版本冲突返回 409 `fragment_state_conflict` |
| Conflict resolution | `/api/examples/fragments/resolve-conflict/`, `/resolve-conflicts/` | 单组与批量处理均要求双方版本；批量接口在一个事务中采用全部新版，任一冲突无效则整体不写入 |

路由细节以各 app 的 `urls.py` 与 serializer 为准。新增或修改 endpoint 时，应同步本表和跨系统实现稿。

> **弃用通知（2026-10-16）**：仅 `POST /api/examples/fragments/` 进入弃用观察期；同路径 GET 及 fragment detail、infer-tags、confirm、merge、resolve-conflict、resolve-conflicts 均不在本次弃用范围。官方 Web/Mobile 当前不调用该 POST：单组冲突残余片段由原子 `POST /api/examples/fragments/resolve-conflict/` 创建，批量采用新版由 `POST /api/examples/fragments/resolve-conflicts/` 处理，自动切割则使用 article segment endpoint。该 POST 在观察期内仍保持现有行为；观察期从本通知合并到默认分支并公开后开始计算，至少 14 天、建议 30 天。只有可用生产日志窗口内继续没有 collection POST 调用，才会在后续独立 PR 中仅移除 `FragmentListView.post()`；GET 将继续保留。

## 关键运行边界

- `POST /api/generate/stream/` 返回 `text/event-stream`，事件类型包括 `chunk`、`done` 和 `error`；错误事件使用机器可读 `code`。
- `POST /api/generate/stream/` 与 `POST /api/examples/articles/{article_id}/segment/` 接受可选 `providerOverride`。未提供时继续使用当前 active provider；提供时只使用当前用户为该 catalog-enabled provider 保存的 Key，且只对本次请求生效，不读取其他用户的 Key、不修改 active provider。未知、已下线或未保存 Key 的 provider 会在外部调用前 fail closed。
- 当前启用的文本模型为 Gemini `gemini-2.5-flash` 与 Groq `openai/gpt-oss-120b`，两者已在 2026-10-15 通过生产“只验证、不切换”确认可用。Anthropic `claude-sonnet-4-6` 因尚无有效 Key、Cerebras `gpt-oss-120b` 因当前账户没有可用额度，暂时由 provider catalog 禁用；OpenRouter `google/gemma-4-31b-it:free` 继续因免费上游连续跨日 429 保持禁用。三个 provider 的集成、模型定义、已保存 Key 与验证历史均保留，恢复前分别需要新的真实验证与标准切割证据；不会动态路由到未披露模型或自动改用付费模型。独立的 `openrouter-model-monitor` 定时/手动 workflow 仍使用无 Key 公开目录检查该模型存在、文本价格为零且支持 `response_format`，为后续恢复提供信号；它不在普通 push/PR 上运行。
- 保存候选 provider Key 和切换已有 provider 都会先执行一次低输出预算的真实 text completion；验证失败不会覆盖旧 Key或改变 active provider。`POST /api/auth/llm-config/verify/` 可显式复查已存 Key，GET 和页面加载不会自动调用 provider 或消耗额度。响应只返回是否已存 Key、catalog 模型、`enabled` / `disabledReason`、最近一次安全验证状态/时间及请求和实际模型，不返回明文 Key。catalog 中禁用的 provider 会在 Key 解密和外部网络调用前以 `provider_disabled` 拒绝保存、验证、切换和实际 LLM 调用；已有 Key 与验证历史原样保留，不需要 migration。
- GPT-OSS 同步 JSON 调用使用低 reasoning 强度，避免推理 token 挤占结构化输出预算；provider 的 401、402、404、429、其他状态、timeout 和 connection error 会转换为安全错误码，vendor 原始响应不会写入 API/SSE 响应或 `LlmCallLog.error_message`。Cerebras 402 单独表示账户没有可用额度或计费权限，不得误报为 Key 无效或短暂服务故障。
- 对 SSE，`RestApiLog.status_code=200` 只表示流式响应已建立，`latency_ms` 只记录响应准备耗时；完整 provider 流耗时与最终成功/失败记录在同一 request ID 的 `LlmCallLog`。客户端提前断开按 error 记录，错误信息为 `stream interrupted before completion`。
- 生成请求支持 `characterId`、`auModId`、`activeRelationshipIds`、结构化 `sceneInput`、`outputLanguage` 和可选 `forcedFragmentId`。当前网页还会传入 `resolvedRelationshipIds`：它是用户从自动激活集合中明确仲裁出的有序子集；评估请求必须复用生成快照中的这两个列表。
- 评估 score/rate 的显式业务失败统一返回 `{"code": "...", "detail": "..."}`，当前机器码为 `missing_required_fields`、`generation_not_found`、`character_not_found`、`evaluation_result_invalid`、`missing_user_rating`、`invalid_user_rating` 与 `evaluation_not_found`；原 HTTP 状态和中文 `detail` 语义保持不变。该批次不涉及 SSE，provider 失败继续沿用既有安全错误码。
- LLM 配置的保存、切换与显式验证入口中，不支持的 provider 和缺少 API Key 的显式失败统一返回 `{"code": "...", "detail": "..."}`，机器码分别为 `unsupported_provider` 与 `no_api_key`；原 400 状态和中文 `detail` 语义保持不变。成功响应、Key 加密与写入时机、active provider 切换条件及既有 `ProviderError` 安全错误码均未改变。
- 标签历史 POST 缺少 `fieldType` 或非空 `label` 时返回 400 `{"code": "missing_required_fields", "detail": "fieldType and label are required"}`。合法请求仍按当前用户、字段和标准化标签执行 upsert 并刷新 `usedAt`；GET 的用户/字段隔离、最近使用倒序和最多 100 条行为不变。
- 生成与评估会先把 BaseCard、可选 AU Mod 和当前角色实际参与的 active relationships 解析为同一个 `EffectiveCharacterContext`。AU/relationship 的 `inheritExclude` 即使没有追加项也生效；空白或损坏列表项会被忽略；更高特异性来源按 ID 或规范化内容去重。未提供 `resolvedRelationshipIds` 的旧客户端继续按参与人数升序、同人数按创建时间和 UUID 稳定排序；提供该字段时只采用同时属于自动激活集合、当前用户且当前角色实际参与的关系，显式空列表也有效。跨特异性仍以参与人数更少者优先，用户顺序只在同一参与人数内生效；当采用多个关系时，generation/evaluation prompt 都明确声明靠前关系在无法同时满足时优先。两条调用路径共享有效数据，但 generation 保留称呼与创作模板，evaluation 保留独立 judge 模板。
- BaseCard、AU Mod 和 relationship membership 的 `quickLabels` / `forbiddenBehaviors` 写入值必须是数组，且每个数组元素必须是 JSON object；顶层类型或元素类型错误时 serializer 返回 400，不再把字符串等旧格式写入 JSONField。该边界只固定容器与元素类型；`EffectiveCharacterContext` 仍负责忽略缺少有效 `content` 的对象。
- 文章自动切割要求 provider 复制输入中标注的绝对行号，`start` / `end` 均为 inclusive；每个非空行必须恰好覆盖一次，空白行可以并入相邻片段或省略，但纯空白片段、重叠和越出当前 gap 的范围均会被拒绝。已确认片段之间若只剩纯空白行，会在解析 provider 前按已完成状态忽略；过滤后没有实质缺口时返回既有 200、空片段列表和“无需重新切割”提示，不调用 LLM 或改写 Fragment。编号后文本按包含换行符的 3000 字符或 100 行上限分块，以先达到者为准；接近硬上限时优先退回最近 20 行内的空白行边界，避免人为切断相邻情节。单个超长原文行不会从行内拆开，以免破坏绝对行号协议。当前不猜测性修正 EOF 多一行等越界结果。
- 文章自动切割当前使用 4000 输出 token 上限；生产容量验收确认 2000 会在接近 chunk 上限的高行密度输入上耗尽并截断 JSON。该值是调用上限，不代表每次固定消耗 4000 token。
- Gemini 同步调用会向应用控制台日志记录容量指标（model、finish reason、prompt/completion/total/unattributed token 和输出预算），不记录 prompt、模型正文或 API Key；`unattributed` 只表示 SDK 未分类的 token，不直接等同于 thinking token。
- `confirm-selected` 只处理请求中显式列出的、属于当前文章、状态仍可确认且包含可向量化有效标签的片段 ID；选择范围无效、片段状态变化、标签无效和缺少 Gemini Key 会在调用 embedding 前分别返回 `fragment_selection_invalid`、`fragment_state_conflict`、`fragment_tags_invalid`、`no_api_key`，原 HTTP 状态和中文 `detail` 语义保持不变。批量和单片段 confirm 都不会在等待外部 embedding 时持有数据库锁；embedding 返回后，只在正文、标签、类型、确认状态和版本均未变化且片段仍存在时条件写入 embedding 与确认状态，因此不会覆盖并发编辑、覆盖另一确认结果或复活已删除片段。条件不满足时，批量路径把该 ID 计入既有 200 部分失败汇总并继续处理后续选择，单片段路径返回 409 `fragment_state_conflict`。逐片段向量化异常仍使用既有失败语义。旧 `confirm-all` 已在前端切换完成后退役，避免绕过显式范围约束。
- `PATCH /api/examples/fragments/{id}/` 继续只识别 `text` 与 `tags`，并强制要求客户端同时提交 `updatedAt` 和至少一个上述可变字段；任一可变字段存在都会撤销确认并清空 embedding，成功响应仍返回推进版本后的完整 Fragment。缺少或格式无效的 `updatedAt`、空 PATCH 及仅版本 PATCH 均返回 400 且不写入；版本不匹配或读取后片段被删除返回 409 `fragment_state_conflict`，不会写入任何字段或复活记录。请求开始前已不存在及越权仍保持 404，GET 行为不变。
- `DELETE /api/examples/fragments/{id}/` 强制要求客户端提交 `updatedAt`。后端在短事务中用 `select_for_update()` 锁定属于当前用户的目标，并在锁内核对版本和子片段关系；版本不匹配或存在 `child_fragments` 时返回 409 `fragment_state_conflict` 且不删除任何记录，当前版本且无子片段时返回 204。缺少或格式无效的版本返回 400；请求开始前已不存在、越权及重复删除保持 404。普通 DELETE 不级联删除子片段；merge、冲突处理和 segment 的父子级联规则仍不在本契约范围内。
- 单组冲突处理必须同时提交旧、新片段的 `updated_at` 版本；版本缺失或与当前记录不一致时不会执行写入。
- `resolve-conflict` 在短数据库事务内完成保留片段更新、残余片段创建和舍弃片段删除，并要求提交双方 `updated_at`。`resolve-conflicts` 同样必须提供每一组冲突及双方版本，并在一个事务中采用全部新版；外部 LLM 调用不在这些事务中。
- `fragments/merge` 锁定同一用户、同一文章的两个片段，校验双方 `updated_at` 后在一个短事务内更新保留片段并删除另一片段；重复或过期请求不会部分写入。
- Embedding 固定使用 Gemini `gemini-embedding-001`，与文本生成 provider 分离。
- `/health/live/` 只验证 Django 进程能够响应；`/health/` 额外执行 `SELECT 1` 验证主数据库可用，失败时返回不含连接细节的 HTTP 503。健康检查不写入业务日志表；Railway 固定使用的 `healthcheck.railway.app` Host 已显式加入允许列表，其他部署域名仍由 `ALLOWED_HOSTS` 环境变量控制。

## 部署

Railway 当前使用 Railpack。仓库同时保留 `requirements.txt` 和 uv 文件时，Railpack 优先执行 `pip install -r requirements.txt`；`.python-version` 将生产 Python 固定为 3.12。`requirements.txt` 必须始终包含 Gunicorn，不能依赖 uv 的可选依赖组。

Railway 服务级 Custom Start Command 已移除，生产当前读取 [Procfile](./Procfile)。deployment `16878416-6ea5-4407-a4ce-e03fd46d7eb1` 的启动日志确认 Gunicorn 使用 `gthread`，并启动 2 个 worker；仓库参数为每个 worker 4 个线程、timeout 180 秒。平台配置仍可能覆盖仓库文件，因此后续部署验收必须继续以实际 deployment 命令和启动日志为准。

### 加密逻辑备份任务

独立的 [Dockerfile.backup](./Dockerfile.backup) 用于 Railway cron service，不替代 web service 的 Railpack / Procfile 部署。容器固定使用 PostgreSQL 18 client，将 `pg_dump --format=custom` 的 stdout 直接送入 age X25519 公钥加密；临时目录只出现密文，上传后清除。每天写入 `daily`，UTC 周日额外写入 `weekly`，UTC 每月 1 日额外写入 `monthly`。任何 dump、加密或上传步骤失败都会以非零状态退出，不把部分执行报告为成功。

数据库角色通过 [scripts/db_001_backup_reader.sql](./scripts/db_001_backup_reader.sql) 单独创建或轮换。脚本从执行进程的 `BACKUP_READER_PASSWORD` 读取密码，不包含固定凭据；角色固定为 `backup_reader`，继承 PostgreSQL `pg_read_all_data`，但保持 `NOSUPERUSER`、`NOCREATEDB`、`NOCREATEROLE`、`NOREPLICATION`、`NOBYPASSRLS`，并默认使用只读事务。production 执行必须通过 Railway 私网进入 Postgres，不能为备份重开 public TCP proxy。

cron service 只配置以下变量，值不得写入仓库、日志或 PR：

- `PGHOST`：必须以 `.railway.internal` 结尾
- `PGPORT`、`PGDATABASE`、`PGPASSWORD`，以及固定值 `PGUSER=backup_reader`
- `BACKUP_AGE_RECIPIENT`：由 Railway 外离线保存的 age identity 推导出的 X25519 公钥；cron service 永远不持有私钥
- `BACKUP_S3_BUCKET`、`BACKUP_S3_PREFIX`、`AWS_REGION`
- `AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY`；如使用短期凭据，可同时提供 `AWS_SESSION_TOKEN`
- 非 AWS 的 S3-compatible provider 可选配 HTTPS `BACKUP_S3_ENDPOINT_URL`

对象存储写入身份只允许目标 bucket 的指定 `daily` / `weekly` / `monthly` prefix 执行 `PutObject`，不授予 `GetObject`、`ListBucket` 或任何 delete 权限。AWS 最小权限示例见 [ops/backup/aws-writer-policy.example.json](./ops/backup/aws-writer-policy.example.json)。bucket owner 负责开启 versioning，并通过 [ops/backup/aws-lifecycle.example.json](./ops/backup/aws-lifecycle.example.json) 分别执行 14 天 daily、56 天 weekly、183 天 monthly 保留；日常 cron 凭据不能修改 lifecycle 或清理对象。若使用不同 prefix，应用模板前必须同步修改三条 resource/filter，不能直接保留示例占位符。

部署时应创建独立 Railway service，设置 `RAILWAY_DOCKERFILE_PATH=Dockerfile.backup`、每日 UTC cron schedule 和 `restartPolicyType=NEVER`。首次手动 trigger 只验证安全日志中的 object key、密文字节数、密文 SHA-256 与成功状态；不能下载或解密 production 备份。真实恢复只能在另行授权的隔离环境中使用 Railway 外的私钥执行。

任何部署相关改动都必须独立审核以下内容：

- 实际 builder、Python 版本和 package manager
- `requirements.txt`、lockfile 与 Gunicorn 安装路径
- 仓库 Procfile 与平台 Custom Start Command 是否一致
- migration 是否向后兼容
- 环境变量只核对名称与存在性，不把值写入仓库、日志或 PR

生产验收优先使用不写数据的路由、状态码和部署状态检查。涉及真实用户数据的 smoke test 必须另行获得明确授权。

## License / Author

Personal portfolio project by Yanxi Pan.
