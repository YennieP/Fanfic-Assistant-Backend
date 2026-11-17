# 隔离大纲实验：合成 runner 与 Gemini transport

合成 runner 只验证实验流程，不调用模型、不读取 production Key、不连接数据库，也不生成文学质量评价结果。独立 Gemini transport 只有在完整、明确的执行许可下才调用真实模型；开发与自动验收仍使用合成数据和假网络。跨系统设计、默认分支状态与开放任务以前端仓库中的 evaluation、IMPLEMENTATION 和 ToDo 为准。

## 运行

先按 [后端 README](../README.md) 安装仓库隔离 Python 3.12 环境。macOS：

```bash
./scripts/python-project.sh scripts/experiments/outline_runner.py --dry-run --task-dir scripts/experiments/fixtures/outline-dry-run --seed 17
```

Windows 可用相同参数调用仓库 wrapper；命令示例不代表已完成本批 Windows 实机验收：

```powershell
.\scripts\python-project.ps1 scripts\experiments\outline_runner.py --dry-run --task-dir scripts\experiments\fixtures\outline-dry-run --seed 17
```

CLI 强制 `--dry-run`，没有 live/provider/Key 选项。任务包必须标注 `synthetic` 或 `synthetic_fixture`，输入和协议已认可且 hash 匹配，真实执行/外传标记保持 false。私人类型的任务包会在读取共同输入之前被拒绝；没有“顺便试一次”的真实请求。

## 输入和条件

任务目录包含 `manifest.json` 与以下五份校验文件：共同输入、评价参考、pilot 协议、生成条件、作者评价表。路径和文件名固定为合成夹具中的命名；manifest 中的其他路径或源材料引用不会被自动读取。

`generation-conditions.md` 按认可的布局提供三个三反引号 `text` fence：共同 system instruction、D0 追加文本、A1 追加文本。runner 只按代码块外的所属二级标题识别角色（shared 标题含 `system instruction`），不按出现顺序猜组别；模板正文中的标题原样保留，其他语言、较长反引号或波浪号代码块中的示例不提供模板或角色。多余、缺失、重复、空模板或未闭合代码块拒绝执行。完整保留共同输入，再拼接对应追加文本；评价参考和作者评价表只校验/留存，不进入生成 prompt。

假 provider 从任务目录的 `dry-run-responses.json` 读取预写结果。替代文件只允许简单 JSON 文件名，不允许路径跳转。该文件的 hash 与快照同样保留。预写文本和 token 是合成数据，不是模型生成或实际模型用量。

## 输出与留存

默认输出位于本仓库 `.runtime/outline-experiments/<随机 run ID>/`。可用 `--output-root` 选择本仓库 `.runtime` 内的其他目录；越界、symlink 和同名已有 run 会拒绝，已有产物不会覆盖。

- `internal/inputs/`：本次使用的校验文件及假响应快照。
- `internal/prompts/`：D0/A1 的完整 system/user prompt，不含认证头或环境变量。
- `internal/raw/`：每个条件的完整原始输出。
- `internal/context-map.txt`：A1 成功拆分的过程区块。
- `internal/receipt.json`：runner hash、Python 版本、UTC 时点、输入/响应 hash、运行顺序、匿名映射、每次假请求状态/合成用量、本地假调用延迟与产物 hash。
- `review/index.json` 和中性 ID 的 Markdown：打乱后的作者审阅版，索引不显示组别、顺序 seed 或用量；成功拆分的 A1 只展示最终方案，未拆分的 A1 失败响应保留原文并明确标记 `blinding=degraded`，原文可能包含 map。
- `review/author-review-form.md`：作者评价表。

新 run 目录使用 0700，新文件使用 0600（依操作系统权限语义）；只存入 `.runtime`，不提交 Git。该路径约束是本地工具边界，不是对同权限用户的操作系统 sandbox。

## 拆分、失败与预算

A1 标记必须为独立行 `【CONTEXT_MAP】`、`【FINAL_OUTLINE】`，允许 Markdown heading。只接受一次、按顺序、非空区块；被捕获正文保持原字符和换行，仅移除标记行。

缺失、重复、顺序错误或空区块时，完整原始输出继续留存并展示，状态标为 `parse_error` / `blinding=degraded`，不人工重写，不伪称盲化成功。D0 保留完整响应，不禁止模型自行组织。

每组只执行一次假请求，无自动重试或 fallback。`--max-requests` 必须为 2；不足或扩大预算都在请求前拒绝。`--max-output-tokens` 默认 4096，仅对合成 usage 校验，同样覆盖 A1 的 map 与最终方案合计；这不是已批准的真实模型预算。

拒答、timeout、transport/rate-limit 模拟、空响应、截断和解析失败分别保留。某条件失败不删除它，也不阻止预算内的另一条件被记录。输入或假响应在运行中改变时，不恢复源文件；使用已校验快照完成两组并记录 `source_unchanged=false`，该对不能标为可比较成功。

响应即使标注 `status=ok`，若 `finish_reason=ERROR`，仍记为 `finish_error` / `error_code=error_finish_reason`，优先于空响应、超预算或区块解析分类。原始文本和 ERROR 原因保留，两条件仍各记录一次，整对返回退出码 1、`completed_with_failures` 和 `comparable_pair=false`。A1 此时展示未拆分原文，标记 `blinding=degraded`；明确的拒答或传输失败状态继续保留自己的分类。

A1 默认采用保守的 `blinding=degraded`，只有响应成功且 map/final 可靠拆分后才标为 `intact`。截断、超预算、拒答、timeout、transport/rate-limit、空响应、ERROR 和解析失败均保留原文及自己的失败分类，receipt 与审阅索引同步标记降级。该标记描述单份输出的过程隔离，与整对可比较性独立：源文件后来改变时，整对仍不可比较，已经成功拆分的审阅输出可以继续标为 `intact`。

退出码：0 表示合成流程完整成功；1 表示已记录一对但有失败、截断、解析或输入变更；2 表示前置拒绝或 I/O 错误。所有结果显式标记 `real_model_calls=0`、`model_judging_calls=0`，`simulated_requests` 不能写成真实模型调用次数。

## 后续边界

合成 runner 不提供真实 provider、凭据或私人材料外传能力；下述独立 Gemini transport 的实际参数、计费和私人执行仍须分别确认授权。dry-run 不证明 API 可用、token 计量准确、完整盲评或补纲有效。

## 独立 Gemini transport

`outline_live_runner.py` 为独立的 Gemini Developer API 执行入口，首个 adapter 仅支持 `gemini-2.5-flash`；不会初始化 Django、读取平台数据库/production Key 或使用生产 adapter 的默认重试。以下命令只创建未批准的计划，不读取共同输入正文、不取 Key、不联网：

```bash
./scripts/python-project.sh scripts/experiments/outline_live_runner.py --prepare-plan --task-dir scripts/experiments/fixtures/outline-dry-run --execution-plan .runtime/outline-plan.json
```

计划中的参数是草案建议：temperature `0.7`、top-p `0.95`、输出预算 `4096`、thinking budget `0`、provider seed `null`。order seed `17` 只影响执行/展示顺序；执行与展示依次推进同一个 RNG，固定 seed 可重放顺序，展示位置不固定对应 D0/A1。provider seed 可单独指定，均不保证输出或后端权重确定性。价格值 `0` 没有通过审核，不能据此视为免费或执行授权。生成计划不会批准执行；示例也不代表已完成真实 API 可用性验收。

### 执行许可与冻结

实际执行前，必须由操作者根据产品所有者的明确授权确认完整计划：输入/协议五文件 hash、冻结 manifest hash、来源类型、精确请求模型与预期 `modelVersion`、采样/输出参数、价格上界、预算、凭据来源及到期时间，并明确设置 `execution_authorized`、`external_transmission_authorized` 和 `pricing.reviewed` 为 true。到期时间必须带时区且在未来 24 小时内；执行计划只能位于本 checkout 的 `.runtime`，绑定其路径摘要、四个运行模块的内容摘要和 Python/httpx 版本。

TASK-002 的冻结输入、评价、协议及原 manifest 不回写。原 manifest 的 false 权限/零调用计数属于预实验快照；本次许可来自另一个绑定该快照的执行计划，实际尝试以 execution claim、request journal 和 receipt 为准，不能用旧 manifest 的 0 推断尚未调用。private 来源仅允许 `author_private_outline_minimal_extract`，且完整输入 hash 和 manifest 绑定都满足后才读取共同输入正文。未批准、来源/版本/参数/运行环境不符时，不读取私人正文/凭据、不构造网络客户端。

每个自动生成的 32 位 hex authorization ID 在固定 runtime 中仅启动一次执行，在计划元数据校验完成后、正文/凭据读取前，通过独占创建 claim 固定到 run；已使用的许可不再次读取正文。输入验证、缺 Key、网络失败、中断或崩溃均不自动释放。每次 HTTP 尝试前重新核对计划、代码/运行环境摘要和到期时间，已在途请求不能撤回；观察到变更后不发下一次请求。复制到其他 checkout 的计划不能复用，新的 ID/计划须有新的相应执行授权。此机制是受控本地操作声明与记录，不是对同权限操作者的 OS sandbox 或远端签名认证。

凭据渠道只有交互式 TTY 的 getpass（默认）或显式选定的 `FANFIC_EXPERIMENT_GEMINI_API_KEY`。仅接收 Gemini Developer API 的 `AIza` API Key 格式，Key 不进入计划、prompt、日志或回执；不自动读取 `.env`、其他 provider 变量、平台 Key 或密码管理器。如果输入/许可材料中出现本次 Key，拒绝快照和传输。实际使用哪个 Key、账户计费和第三方数据使用条件仍须执行前确认。

### 请求和费用预算

同一对最多 4 次客户端 HTTP 尝试：两个完整 `generateContentRequest` 的 `countTokens` 查询、D0/A1 各一次 `generateContent`。计划必须显式批准 `max_http_requests=4`、`max_generation_requests=2`、`max_retries=0`；全部四次都记入 journal，无 SDK/项目重试、fallback 或 redirect。请求固定到 Google 的 Gemini API host；不继承代理环境或跨请求 cookies，不支持任意 endpoint、工具调用、对话历史或 continuation。

先按已批准的单次 count-query 费用上界检查查询预算，再取得两组 token 数；在任何生成请求之前，以完整输入 token、每组最大输出 token（含内部过程）和已审核输入/输出价格计算整对保守预留。预留超过 `max_cost_usd` 时，已有查询仍记录，生成次数为 0。该费用约束依赖 Google 报告的计数、请求输出上限及操作者确认的价格上界；它不是 provider invoice、账户级硬账单限制或免费额度保证。不能确认这些前提时不能声称费用有硬保证，应在执行前结合账户侧配额/计费控制决策。

响应记录 `modelVersion`、候选/思考/未分类 token、完整 usage、真实请求耗时和估算依据。与预期版本不符、usage 缺失或超过计数/输出界限、工具/非文本结果会使该输出不可比较并停止剩余生成；只记录 provider 返回的版本字符串，不证明后端权重不可变。未知费用不填 0，`estimated_cost_usd=null` / `cost_estimate_complete=false` 与可核对的部分估算分开。HTTP 失败、timeout 或许可变化也停止剩余请求；已开始但未取得可用响应的生成标为 `request_failed` / attempt 1，尚未开始的条件标为 `not_attempted` / attempt 0。

### 输出与实际执行

计划确认、Key 就绪后才保存私人输入快照和构造客户端。每次尝试在 dispatch 前独占保存 `internal/requests/N-started.json`，之后保存响应状态、字节数、原始摘要及可安全保留的原始 UTF-8 body/JSON；明确的 HTTP 错误或无效 JSON 可能回显 Key，仅留状态/大小/hash。同一 JSON 对象中的重复字段（包括嵌套对象和转义后同名的字段）视为歧义响应，在响应正文或解析对象落盘前以 `provider_response_invalid` 拒绝，仅留状态/大小/hash，并停止剩余请求；不同对象可以各自使用相同字段名。有效 JSON 回显本次 Key 时保存脱敏 trace 并使该次失败，不保存原秘密。网络响应最多 1 MiB，拒绝压缩响应，记录 HTTPX 各 I/O 阶段 timeout 和流读取中的耗时截止检查；这不承诺全链路硬墙钟 deadline。

生成成功/拒答/截断/解析失败的原文与 review/index/receipt 共用合成 runner 的区块拆分和盲化分类：A1 未可靠拆分均保守标 degraded；成功拆分才只展示 final、标 intact。源文件后来变化会使整对不可比较，不改写源文件或快照。request journal 的“attempted”不证明 provider 已接受或收费；若末尾产物写入失败，CLI 以请求数 unknown 返回，须检查原 claim/journal，不能按 0 重跑。

下列命令会联网、可能产生费用并传输已批准输入，只能在代码审核和完整执行授权之后运行：

```bash
./scripts/python-project.sh scripts/experiments/outline_live_runner.py --execute --task-dir /absolute/path/to/frozen/task --execution-plan .runtime/outline-plan.json
```

退出码 0 表示完整可比较结果；1 表示已有 HTTP 尝试但失败、不完整或完整性变化；2 表示前置拒绝/无 HTTP 的失败，或产物无法确认的 I/O 错误（此时请求数可为 unknown）。本批自动测试仅使用合成内容、格式正确的 dummy Key、HTTPX MockTransport 与本地 HTTP server，不发送真实 Gemini 推断请求。
