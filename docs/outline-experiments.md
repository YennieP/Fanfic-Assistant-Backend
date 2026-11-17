# 隔离大纲实验 runner：合成 dry-run

本批工具只验证实验流程，不调用模型、不读取 production Key、不连接数据库，也不生成可用于文学质量评价的结果。跨系统设计与开放任务以私有前端仓库中的 evaluation、IMPLEMENTATION 和 ToDo 为准。

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
- `review/index.json` 和中性 ID 的 Markdown：打乱后的作者审阅版，只显示最终方案；不显示组别、顺序 seed、map 或用量。
- `review/author-review-form.md`：作者评价表。

新 run 目录使用 0700，新文件使用 0600（依操作系统权限语义）；只存入 `.runtime`，不提交 Git。该路径约束是本地工具边界，不是对同权限用户的操作系统 sandbox。

## 拆分、失败与预算

A1 标记必须为独立行 `【CONTEXT_MAP】`、`【FINAL_OUTLINE】`，允许 Markdown heading。只接受一次、按顺序、非空区块；被捕获正文保持原字符和换行，仅移除标记行。

缺失、重复、顺序错误或空区块时，完整原始输出继续留存并展示，状态标为 `parse_error` / `blinding=degraded`，不人工重写，不伪称盲化成功。D0 保留完整响应，不禁止模型自行组织。

每组只执行一次假请求，无自动重试或 fallback。`--max-requests` 必须为 2；不足或扩大预算都在请求前拒绝。`--max-output-tokens` 默认 4096，仅对合成 usage 校验，同样覆盖 A1 的 map 与最终方案合计；这不是已批准的真实模型预算。

拒答、timeout、transport/rate-limit 模拟、空响应、截断和解析失败分别保留。某条件失败不删除它，也不阻止预算内的另一条件被记录。输入或假响应在运行中改变时，不恢复源文件；使用已校验快照完成两组并记录 `source_unchanged=false`，该对不能标为可比较成功。

响应即使标注 `status=ok`，若 `finish_reason=ERROR`，仍记为 `finish_error` / `error_code=error_finish_reason`，优先于空响应、超预算或区块解析分类。原始文本和 ERROR 原因保留，两条件仍各记录一次，整对返回退出码 1、`completed_with_failures` 和 `comparable_pair=false`。A1 此时展示未拆分原文，标记 `blinding=degraded`；明确的拒答或传输失败状态继续保留自己的分类。

退出码：0 表示合成流程完整成功；1 表示已记录一对但有失败、截断、解析或输入变更；2 表示前置拒绝或 I/O 错误。所有结果显式标记 `real_model_calls=0`、`model_judging_calls=0`，`simulated_requests` 不能写成真实模型调用次数。

## 后续边界

真实 provider、SDK 请求计数/重试、模型参数、费用、凭据渠道和私人材料外传需要后续独立实现与授权。当前 runner 没有这些功能，dry-run 不证明 API 可用、token 计量准确、完整盲评或补纲有效。
