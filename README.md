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

使用仓库专属的 Python 3.12 虚拟环境，不要修改系统 Python、全局 PATH 或其他项目环境。默认分支当前仍以 `requirements.txt` / `requirements-dev.txt` 为安装契约；隔离 uv 环境方案在单独分支审核完成前不作为生产构建依据。

macOS / Linux 示例：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

Windows PowerShell 示例：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python.exe manage.py migrate
.venv\Scripts\python.exe manage.py runserver
```

本地 `.env` 至少需要独立的开发 `SECRET_KEY` 和 `ENCRYPTION_KEY`。未提供 `DATABASE_URL` 时使用仓库内 SQLite。禁止复制 Railway 的数据库 URL、密钥或生产数据到本地。

## 统一验证

```bash
.venv/bin/python scripts/verify.py
```

Windows：

```powershell
.venv\Scripts\python.exe scripts\verify.py
```

该命令与 GitHub Actions 一致，依次执行：

1. Markdown 本地链接、文件名大小写和 whitespace 检查
2. `manage.py check`
3. 全部 pytest 测试与 coverage 报告

脚本只在子进程中为缺失的 `SECRET_KEY` / `ENCRYPTION_KEY` 提供固定的非生产验证值，使干净 CI 无需保存 secrets；已有环境变量不会被覆盖。

统一门禁同时运行 `manage.py check`、`makemigrations --check --dry-run` 和完整 pytest；模型与 migration 状态不一致会直接失败。任何真实 schema 变更仍须单独审查 migration，并遵守跨系统 `ToDo.md` 中的生产数据库核验边界。

## API 总览

除注册和 JWT 获取/刷新外，API 默认要求 Bearer token。JSON 通过 camel-case parser/renderer 与前端交互。

| 领域 | 路径 | 说明 |
|---|---|---|
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
| Articles | `/api/examples/articles/` | 文章 CRUD、segment、confirm-all |
| Fragments | `/api/examples/fragments/` | 片段列表/创建、详情、infer-tags、confirm |
| Fragment merge | `/api/examples/fragments/merge/` | 带版本校验地原子更新保留片段并删除另一片段 |
| Conflict resolution | `/api/examples/fragments/resolve-conflict/` | 在一个事务中处理重叠片段 |

路由细节以各 app 的 `urls.py` 与 serializer 为准。新增或修改 endpoint 时，应同步本表和跨系统实现稿。

## 关键运行边界

- `POST /api/generate/stream/` 返回 `text/event-stream`，事件类型包括 `chunk`、`done` 和 `error`；错误事件使用机器可读 `code`。
- 对 SSE，`RestApiLog.status_code=200` 只表示流式响应已建立，`latency_ms` 只记录响应准备耗时；完整 provider 流耗时与最终成功/失败记录在同一 request ID 的 `LlmCallLog`。客户端提前断开按 error 记录，错误信息为 `stream interrupted before completion`。
- 生成请求支持 `characterId`、`auModId`、`activeRelationshipIds`、结构化 `sceneInput`、`outputLanguage` 和可选 `forcedFragmentId`。
- `resolve-conflict` 在短数据库事务内完成保留片段更新、残余片段创建和舍弃片段删除；外部 LLM 调用不在该事务中。
- `fragments/merge` 锁定同一用户、同一文章的两个片段，校验双方 `updated_at` 后在一个短事务内更新保留片段并删除另一片段；重复或过期请求不会部分写入。
- Embedding 固定使用 Gemini `gemini-embedding-001`，与文本生成 provider 分离。
- Railway 启动命令来自 [Procfile](./Procfile)：先执行 migration 和 collectstatic，再启动 Gunicorn gthread worker。

## 部署

默认分支由 Railway 自动部署。任何部署相关改动都必须独立审核以下内容：

- 实际 builder 和 Python package manager
- `requirements.txt`、未来的 lockfile 与 Gunicorn 安装路径
- migration 是否向后兼容
- 环境变量只核对名称与存在性，不把值写入仓库、日志或 PR

生产验收优先使用不写数据的路由、状态码和部署状态检查。涉及真实用户数据的 smoke test 必须另行获得明确授权。

## License / Author

Personal portfolio project by Yanxi Pan.
