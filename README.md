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
- Anthropic, Gemini, Groq, Cerebras, OpenRouter
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
| LLM config | `/api/auth/llm-config/` | 多 provider Key 与当前 provider；不返回明文 Key |
| Characters | `/api/characters/` | BaseCard CRUD |
| AU Mods | `/api/characters/{character_id}/mods/` | 角色下的 AU Mod CRUD |
| Translation | `/api/characters/{canonical_id}/translate/`, `/versions/` | 多语言版本创建与查询 |
| Relationships | `/api/relationships/` | 关系实体与 nested memberships |
| Taxonomy | `/api/taxonomy/`, `/api/label-history/` | 双语标签体系与历史 |
| Generation | `/api/generate/stream/` | SSE 流式生成 |
| Evaluation | `/api/evaluation/score/`, `/score/{id}/rate/` | LLM judge 与人工评分 |
| Articles | `/api/examples/articles/` | 文章 CRUD、segment，以及只处理显式片段 ID 的 confirm-selected；segment 会先完成并验证全部缺口结果，再用单个短事务替换旧的未确认草稿 |
| Fragments | `/api/examples/fragments/` | 片段列表/创建、详情、infer-tags、confirm |
| Fragment merge | `/api/examples/fragments/merge/` | 带版本校验地原子更新保留片段并删除另一片段 |
| Conflict resolution | `/api/examples/fragments/resolve-conflict/`, `/resolve-conflicts/` | 单组与批量处理均要求双方版本；批量接口在一个事务中采用全部新版，任一冲突无效则整体不写入 |

路由细节以各 app 的 `urls.py` 与 serializer 为准。新增或修改 endpoint 时，应同步本表和跨系统实现稿。

## 关键运行边界

- `POST /api/generate/stream/` 返回 `text/event-stream`，事件类型包括 `chunk`、`done` 和 `error`；错误事件使用机器可读 `code`。
- 对 SSE，`RestApiLog.status_code=200` 只表示流式响应已建立，`latency_ms` 只记录响应准备耗时；完整 provider 流耗时与最终成功/失败记录在同一 request ID 的 `LlmCallLog`。客户端提前断开按 error 记录，错误信息为 `stream interrupted before completion`。
- 生成请求支持 `characterId`、`auModId`、`activeRelationshipIds`、结构化 `sceneInput`、`outputLanguage` 和可选 `forcedFragmentId`。
- 文章自动切割要求 provider 复制输入中标注的绝对行号，`start` / `end` 均为 inclusive；每个非空行必须恰好覆盖一次，空白行可以并入相邻片段或省略，但纯空白片段、重叠和越出当前 gap 的范围均会被拒绝。当前不猜测性修正 EOF 多一行等越界结果。
- `confirm-selected` 只处理请求中显式列出的、属于当前文章且包含可向量化有效标签的片段 ID；若任一片段没有有效标签，会在调用 embedding 前拒绝整批请求。旧 `confirm-all` 已在前端切换完成后退役，避免绕过显式范围约束。
- 单组冲突处理必须同时提交旧、新片段的 `updated_at` 版本；版本缺失或与当前记录不一致时不会执行写入。
- `resolve-conflict` 在短数据库事务内完成保留片段更新、残余片段创建和舍弃片段删除，并要求提交双方 `updated_at`。`resolve-conflicts` 同样必须提供每一组冲突及双方版本，并在一个事务中采用全部新版；外部 LLM 调用不在这些事务中。
- `fragments/merge` 锁定同一用户、同一文章的两个片段，校验双方 `updated_at` 后在一个短事务内更新保留片段并删除另一片段；重复或过期请求不会部分写入。
- Embedding 固定使用 Gemini `gemini-embedding-001`，与文本生成 provider 分离。
- `/health/live/` 只验证 Django 进程能够响应；`/health/` 额外执行 `SELECT 1` 验证主数据库可用，失败时返回不含连接细节的 HTTP 503。健康检查不写入业务日志表；Railway 固定使用的 `healthcheck.railway.app` Host 已显式加入允许列表，其他部署域名仍由 `ALLOWED_HOSTS` 环境变量控制。

## 部署

Railway 当前使用 Railpack。仓库同时保留 `requirements.txt` 和 uv 文件时，Railpack 优先执行 `pip install -r requirements.txt`；`.python-version` 将生产 Python 固定为 3.12。`requirements.txt` 必须始终包含 Gunicorn，不能依赖 uv 的可选依赖组。

Railway 服务级 Custom Start Command 已移除，生产当前读取 [Procfile](./Procfile)。deployment `16878416-6ea5-4407-a4ce-e03fd46d7eb1` 的启动日志确认 Gunicorn 使用 `gthread`，并启动 2 个 worker；仓库参数为每个 worker 4 个线程、timeout 180 秒。平台配置仍可能覆盖仓库文件，因此后续部署验收必须继续以实际 deployment 命令和启动日志为准。

任何部署相关改动都必须独立审核以下内容：

- 实际 builder、Python 版本和 package manager
- `requirements.txt`、lockfile 与 Gunicorn 安装路径
- 仓库 Procfile 与平台 Custom Start Command 是否一致
- migration 是否向后兼容
- 环境变量只核对名称与存在性，不把值写入仓库、日志或 PR

生产验收优先使用不写数据的路由、状态码和部署状态检查。涉及真实用户数据的 smoke test 必须另行获得明确授权。

## License / Author

Personal portfolio project by Yanxi Pan.
