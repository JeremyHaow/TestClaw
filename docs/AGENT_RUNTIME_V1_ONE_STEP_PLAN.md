# TestClaw Agent Runtime v1 One-Step Plan

日期：2026-05-27

本文是在 `docs/AGENT_GAP_ANALYSIS_PROGRESS.md` 之后的升级版开发指导。新的前提是：下一轮允许修改 UI 和数据库，不再限制为最小中心 executor。目标是一次完整开发任务内把 TestClaw 改到 `Agent Runtime v1`，而不是继续做局部 adapter。

这里的“一步到位”不是重写整个项目，也不是一个不可审查的巨型补丁。它的含义是：在一次明确目标内，直接完成 runtime、数据库事件模型、前端工作台、API/UI tool execution、Evaluator、Memory、Golden Tasks 的闭环。

## 1. Target Outcome

下一轮完成后，TestClaw 应该能被准确描述为：

> 一个以 Agent Runtime 为核心的 AI Testing Agent。用户提交目标后，系统生成结构化计划，按 Action 执行 API/UI/Memory/Human 工具调用，持续记录 ToolCall、Observation、Evidence、Evaluation，并能基于评估结果 retry、replan、ask human 或 report。Run Detail 是实时 Agent 工作台，数据库保存可回放的 runtime event stream，而不是只依赖 `Task.execution_log` 大 JSON。

必须达到的硬标准：

1. `AgentRuntime` 是 API/UI 执行的主入口，不再只是附加记录 helper。
2. `ToolExecutor` 是 tool call 的统一调度入口，不再让 API/UI runner 各自定义执行生命周期。
3. API 和 UI 都使用统一 `Action -> ToolCall -> Observation -> Evidence -> Evaluation` 协议。
4. 数据库保存 runtime events，并支持 Run Detail 直接读取或聚合展示。
5. Run Detail 主视图展示 Agent 正在做什么、为什么做、观察到了什么、证据是否充分、下一步是什么。
6. UI 默认走 structured `ui_actions`，legacy `playwright_commands` 只是 fallback。
7. failure taxonomy 集中定义，Evaluator 和 reporter 都引用同一套分类。
8. Golden Tasks 覆盖主要 failure matrix，防止只修单个 case。

## 2. Scope Change From Previous Audit

`docs/AGENT_GAP_ANALYSIS_PROGRESS.md` 中建议下一步不要马上改数据库和大 UI。这个约束现在取消，但保留三个工程边界：

1. 可以新增 Alembic migration，但必须是 additive migration，不能破坏现有 SQLite/PostgreSQL 兼容。
2. 可以改 Run Detail 和主路径 UI，但不要先做视觉重写；先让信息架构和数据流服务 runtime。
3. 可以拆 runner，但必须保留旧字段兼容：`api_execution_result`、`ui_execution_result`、`execution_result`、`Task.execution_log` 在迁移期仍要可用。

## 3. Target Backend Architecture

### 3.1 New Runtime Layer

建议新增目录：

```text
app/agent/runtime/
  __init__.py
  models.py
  runtime.py
  tool_executor.py
  event_store.py
  failure_taxonomy.py
  policies.py
```

职责：

- `models.py`
  - 定义 runtime v1 的 Pydantic models。
  - 从现有 `action_runtime.py` 迁移或复用 `AgentAction`、`AgentToolCall`、`AgentObservation`、`AgentEvidence`、`AgentEvaluation`。
  - 保持输出 key 与现有前端兼容。

- `runtime.py`
  - 实现 `AgentRuntime.run_plan()` 和 `AgentRuntime.run_action()`。
  - 对每个 action 做：validate -> execute tool -> persist event -> evaluate -> decide next。
  - 管理 budget、retry_count、replan_count、human escalation。

- `tool_executor.py`
  - 统一执行 `api.http_request`、`api.derive_schema_requests`、`ui.playwright_cli`、`memory.retrieve_rag_context`、`human.ask`。
  - 每次执行都返回 `ToolExecutionResult`，由 runtime 转换为 observation/evidence。

- `event_store.py`
  - 持久化 runtime events。
  - 同步写 DB runtime tables 和 `Task.execution_log` 兼容快照。

- `failure_taxonomy.py`
  - 集中定义 failure type。
  - 暴露 `classify_api_failure()`、`classify_ui_failure()`、`next_action_hint()`。

- `policies.py`
  - 统一安全策略、写接口 gate、高风险 UI action gate、redaction、artifact size 限制。

### 3.2 Graph Role After Runtime

`app/agent/graph.py` 不需要消失，但职责要变：

- 保留：输入识别、source loading、mission planning、knowledge retrieval、initial planning、reporting、knowledge sink。
- 改造：API/UI 执行节点调用 `AgentRuntime`，而不是自己管理完整执行生命周期。
- 收敛：`execution_evaluator` 成为 runtime evaluator 的可复用模块；graph 的条件边只处理大阶段切换。

目标形态：

```text
input_classifier
-> source_loader
-> mission_planner
-> knowledge_retriever
-> planner
-> agent_runtime
-> reporter
-> knowledge_sink
```

如果一次性替换 `graph.py` 风险过高，可以保留现有节点名，但 `api_runner`、`ui_runner` 内部必须调用 runtime。

### 3.3 API Runner Target

`api_runner.py` 应降级为 API adapter，不再负责所有决策。

目标拆分：

```text
app/agent/api/
  request_builder.py
  executor.py
  assertions.py
  observation_mapper.py
```

保留旧入口：

- `app/agent/nodes/api_runner.py::run()` 仍存在。
- 它负责把旧 state/cases/schema 转成 runtime actions，然后交给 `AgentRuntime`。
- 它继续写 `api_execution_result`，但主证据来自 runtime events。

### 3.4 UI Runner Target

`ui_runner.py` 应降级为 UI adapter。

目标拆分：

```text
app/agent/ui/
  action_schema.py
  playwright_adapter.py
  observation_mapper.py
  login_context.py
```

核心规则：

- UI planner 默认输出 `ui_actions`。
- `playwright_commands` 只作为 legacy fallback 和导出脚本来源。
- structured `run_code/eval` 默认 blocked，除非 action 明确 `risk=high` 且策略允许。
- Playwright CLI 返回的 snapshot、screenshot、console、network、trace 都转成 evidence。

## 4. Target Database Architecture

当前已有：

- `run_events`
- `run_tool_calls`
- `run_evidence`
- `run_findings`
- `run_interventions`
- `target_memories`
- `artifacts`

这些表已经在 `alembic/versions/0006_run_operational_tables.py` 和 `app/models/run_artifacts.py` 中存在。下一轮可以直接把它们变成主路径，并新增缺失的 action/evaluation 表。

### 4.1 Migration 0007

建议新增：

```text
alembic/versions/0007_agent_runtime_v1.py
```

新增表：

```text
run_agent_actions
  id
  run_id
  sequence
  action_id
  action_type
  tool_name
  stage
  status
  risk
  reason
  inputs_json
  expected_observation
  created_at
  updated_at

run_agent_observations
  id
  run_id
  sequence
  observation_id
  action_id
  tool_call_id
  stage
  layer
  tool_name
  status
  outcome
  failure_type
  summary
  inputs_json
  outputs_json
  evidence_ids_json
  created_at

run_agent_evaluations
  id
  run_id
  sequence
  evaluation_id
  stage
  sufficient_evidence
  outcome
  next_action
  confidence
  failure_type
  reason
  missing_evidence_json
  replan_hint
  observation_ids_json
  created_at
```

可以复用现有表：

- `run_events` 作为 ordered runtime event stream。
- `run_tool_calls` 保存底层 tool call。
- `run_evidence` 保存 screenshot、snapshot、API response、assertion、trace 等证据。
- `run_findings` 保存 reporter 输出的 bug/finding。
- `artifacts` 保存文件型证据。

索引要求：

- `(run_id, sequence)` 用于 timeline。
- `(run_id, action_id)` 用于 action detail。
- `(run_id, failure_type)` 用于 triage。
- `(run_id, created_at)` 用于 SSE 增量拉取。

兼容要求：

- `Task.execution_log` 继续写摘要和最后状态。
- 旧 `get_run_detail()` 可以从新表聚合出旧 payload key。
- 如果 migration 没跑，SQLite dev 模式仍不能崩。

## 5. Target Frontend Architecture

### 5.0 Visual Design Reference

下一轮 Codex 做 Runtime Workbench 和前端 UI 时，必须先阅读仓库根目录的：

```text
TestClaw_Codex_Fullstack_Refactor_Guide.md
```

参考范围：

- `3. 视觉设计系统`
- `4. 前端目录规划`
- `6. 页面 UI 与交互设计`
- `Phase 1：设计系统与布局壳`
- `Phase 4：Agent Cockpit`

这份 guide 作为视觉方向和产品交互参考，不作为组件实现方式的最终约束。

必须采用的视觉方向：

- Light SaaS
- Agent Workspace
- Calm spacing
- Soft shadows
- Blue accent
- Structured AI planning
- Run Detail 像 Agent Cockpit，而不是静态报告页

必须参考的视觉 token：

```text
Page background:     #F5F7FB / #F7F9FC
Surface white:       #FFFFFF
Border light:        #E5EAF3
Border softer:       #EEF2F7
Primary blue:        #2563EB
Primary hover:       #1D4ED8
Blue light bg:       #EFF6FF
Success green:       #10B981
Warning orange:      #F59E0B
Danger red:          #EF4444
Text primary:        #0F172A
Text secondary:      #475569
Text muted:          #94A3B8
```

必须参考的状态语义：

```text
Draft       gray
Pending     gray / blue outline
Ready       blue
Running     blue + subtle animation
Blocked     red
Warning     orange
Passed      green
Failed      red
Skipped     gray
Bug Found   red/purple
Cancelled   gray
```

关键约束：

- 不要照搬 guide 里旧的 `TcButton.vue`、`TcCard.vue`、`TcBadge.vue` 自建 UI 组件路线。
- 新 UI 基础组件必须来自 shadcn-vue 生成的 shadcn/ui 风格 Vue 组件。
- 如果需要业务封装，只能在 `frontend/src/components/runtime/` 或 `frontend/src/components/agent/` 中组合 shadcn-vue 组件，不要在 `frontend/src/components/ui` 写业务逻辑。
- `frontend/src/components/ui` 只保存 shadcn-vue 生成或同风格的基础设计系统组件。
- guide 中的 `Tc*` 组件命名只能作为旧文档背景，不是下一轮实现目标。
- 如果 guide 的“不要引入新的 UI 框架”和当前要求冲突，以当前文档为准：允许并要求引入 `shadcn-vue`。

### 5.1 Run Detail 改成 Agent Runtime Workbench

`frontend/src/pages/RunDetailPage.vue` 可以继续保留，但主区域应重组为：

```text
Run Header
  目标、状态、当前 next_action、人类介入状态

Runtime Timeline
  Action
  ToolCall
  Observation
  Evidence
  Evaluation

Current Step Panel
  当前 action
  为什么做
  预期观察
  实际观察
  Evaluator 判断
  下一步

Evidence Drawer
  API response
  UI snapshot
  screenshot
  console/network/trace
  assertion

Human Handoff Panel
  ask_human 问题
  需要用户补什么
  提交后 resume/replan

Raw Details
  legacy api_execution_result
  legacy ui_execution_result
  logs
```

### 5.2 New Components

建议新增或重构：

```text
frontend/src/components/runtime/
  RuntimeTimeline.vue
  RuntimeCurrentStep.vue
  RuntimeEvidenceDrawer.vue
  RuntimeEvaluationPanel.vue
  RuntimeHumanHandoff.vue
  RuntimeFailureBadge.vue
```

旧组件处理方式：

- `AgentTimeline.vue` 可以作为 `RuntimeTimeline.vue` 的基础迁移。
- `AgentEvidenceCard.vue` 可以保留，但数据源改为 runtime evidence。
- `AgentInterventionDrawer.vue` 改为运行中 handoff/resume 的入口，而不只是 assisted rerun。

### 5.3 UI Product Principle

新的 Run Detail 不能只是多展示一些 JSON。它必须回答五个问题：

1. Agent 现在在做什么 action？
2. 为什么执行这个 action？
3. 它看到了什么 observation？
4. 证据是否足够，失败类型是什么？
5. 下一步为什么是 retry、replan、ask human 或 report？

### 5.4 shadcn-vue Design System Setup

下一轮 Codex 必须把前端 UI 系统标准化到 `shadcn-vue`，不要继续手写大量一次性 Tailwind class 作为新 UI 的主方式。

官方参考：

- shadcn-vue Vite installation: `https://www.shadcn-vue.com/docs/installation/vite`
- shadcn-vue components: `https://www.shadcn-vue.com/docs/components`

重要判断：

- 当前 `frontend` 是 Vue 3 + Vite + Tailwind v4，不是 React 项目。
- 不要安装 React 版 `shadcn/ui`。
- 必须使用 Vue 生态的 `shadcn-vue`。
- 当前仓库已有 `frontend/package-lock.json`，项目前端包管理优先使用 `npm` / `npx`，不要无故切换成 pnpm。

Codex 在开始前端 Runtime Workbench 实现前必须执行：

1. 检查 `frontend/components.json` 是否存在。
2. 如果不存在，先配置 shadcn-vue：

```bash
cd /opt/testclaw/frontend
npx shadcn-vue@latest init
```

3. 初始化过程中按当前项目约定选择：

```text
Framework: Vite
Language: TypeScript
Tailwind CSS file: src/styles/main.css
Components alias: @/components
UI components path: src/components/ui
Utils alias: @/lib/utils
Utils path: src/lib/utils
Base color: neutral 或 zinc
CSS variables: yes
```

4. 如果 `@` alias 尚未配置，必须补齐：

- `frontend/vite.config.ts` 增加 `resolve.alias`，将 `@` 指向 `frontend/src`。
- `frontend/tsconfig.json` 增加 `baseUrl` 和 `paths`。

5. 安装 Runtime Workbench 需要的基础组件：

```bash
cd /opt/testclaw/frontend
npx shadcn-vue@latest add button card badge input textarea select tabs dialog sheet tooltip table scroll-area separator skeleton dropdown-menu alert
```

6. 安装后检查：

- `frontend/components.json`
- `frontend/src/components/ui/`
- `frontend/src/lib/utils.ts`
- `frontend/src/styles/main.css`
- `frontend/package.json`
- `frontend/package-lock.json`

7. 跑前端构建：

```bash
cd /opt/testclaw/frontend
npm run build
```

### 5.5 shadcn-vue Skill and MCP Setup

Codex 下一轮应尽量启用 shadcn-vue Skill 和 MCP，但不能假设它们在当前会话里已经可用。

官方参考：

- shadcn-vue Skill: `https://www.shadcn-vue.com/docs/skills`
- shadcn-vue MCP: `https://www.shadcn-vue.com/docs/mcp`

Skill 安装指令：

```bash
pnpm dlx skills add unovue/shadcn-vue
```

执行规则：

- 如果 `pnpm` 或 `skills` CLI 不可用，不要阻塞主任务；在最终回复中说明 skill 未安装。
- 如果安装成功，继续使用 skill 读取 `components.json`、已安装组件和 shadcn-vue 约定。
- Skill 是 Codex 环境增强，不应写入项目业务代码。

MCP 配置指令：

```toml
[mcp_servers.shadcn]
command = "npx"
args = ["shadcn-vue@latest", "mcp"]
```

执行规则：

- 将以上配置写入 `~/.codex/config.toml` 需要明确当前环境允许修改 Codex 配置。
- MCP 配置后通常需要重启 Codex 才能暴露工具；当前运行中的 Codex 不应假设 MCP 立刻可用。
- 如果本次会话没有 shadcn MCP 工具，继续使用本地 `npx shadcn-vue@latest add ...` 和官方文档完成组件安装。
- 不要把 MCP 配置写入项目仓库。

使用规则：

- 新增 Runtime Workbench 组件优先使用 `src/components/ui` 下的 shadcn-vue 组件。
- `Button`、`Badge`、`Card`、`Tabs`、`Sheet`、`Dialog`、`Tooltip`、`Table`、`ScrollArea` 等基础交互必须来自 shadcn-vue。
- Runtime Workbench 视觉风格参考 `TestClaw_Codex_Fullstack_Refactor_Guide.md` 第 3 章，但实现必须使用 shadcn-vue shadcn/ui 组件。
- 业务组件放 `frontend/src/components/runtime/`，不要把业务逻辑塞进 `components/ui`。
- `components/ui` 只保存可复用设计系统组件。
- 不要混用另一个大型 UI 框架。
- 不要把 React shadcn 组件复制到 Vue 项目。

## 6. Human-in-the-loop v1

既然 UI 和数据库允许改，可以把 HITL 做到 v1，而不是只保留 assisted rerun。

建议能力：

- Evaluator 输出 `next_action=ask_human` 时，runtime 将 run 状态标记为 `waiting_for_human`。
- 写入 `run_interventions` 或新增 `run_human_requests`。
- 前端 Run Detail 显示问题和需要补充的信息。
- 用户提交后，worker resume 或触发同 run replan。

如果同 run resume 一次性风险太高，可以分两步：

1. DB 和 UI 显示 `waiting_for_human`。
2. 用户提交后创建 continuation run，但在 UI 上串联为同一 mission。

不要只保留“失败后重跑”，否则 Agent 感仍然不足。

## 7. One-Step Implementation Sequence

虽然目标是一步到位，实际提交应按以下内部顺序推进：

### Step A: Runtime Models and DB

产出：

- `app/agent/runtime/models.py`
- `app/agent/runtime/failure_taxonomy.py`
- `app/models/run_runtime.py` 或扩展 `run_artifacts.py`
- `alembic/versions/0007_agent_runtime_v1.py`
- migration tests

验收：

- 所有新表进入 `Base.metadata`。
- Alembic migration test 覆盖新表和索引。
- runtime models 能与现有 protocol payload 互转。

### Step B: Event Store and Compatibility

产出：

- `app/agent/runtime/event_store.py`
- `runs.py::get_run_detail()` 从 DB runtime tables 聚合 protocol records。
- `Task.execution_log` 保留兼容摘要。

验收：

- 老 Run Detail payload key 不丢。
- 新 runtime events 能按 sequence 返回。
- SSE 能读取新 event 或兼容旧 execution_log。

### Step C: ToolExecutor and Runtime Loop

产出：

- `app/agent/runtime/tool_executor.py`
- `app/agent/runtime/runtime.py`
- API/UI tool adapter 接入。

验收：

- API action 能执行并写 action/tool_call/observation/evidence/evaluation。
- UI action 能执行并写 action/tool_call/observation/evidence/evaluation。
- unknown/high-risk action 有标准 blocked observation。

### Step D: API/UI Runner Cutover

产出：

- `api_runner.py::run()` 调 runtime。
- `ui_runner.py::run()` 调 runtime。
- `ui_test_planner.py` 默认输出 `ui_actions`。
- legacy fields 继续写。

验收：

- 旧测试通过。
- Golden Tasks 通过。
- `api_execution_result` 和 `ui_execution_result` 仍存在，但 Run Detail 主证据来自 runtime tables。

### Step E: Evaluator, Reporter, Memory

产出：

- Evaluator 读取 DB/runtime observations。
- Reporter 从 `run_findings`/runtime evidence 派生报告。
- Memory 从 `run_agent_evaluations` 和 `run_findings` 生成 candidate。

验收：

- `ask_human/retry/replan/report` 都来自统一 evaluation。
- Memory candidate 引用 observation/evidence ids。

### Step F: Frontend Runtime Workbench

产出：

- 初始化 shadcn-vue，如果 `components.json` 不存在。
- 安装 Runtime Workbench 所需 shadcn-vue 基础组件。
- Runtime components。
- Run Detail 主视图改为 runtime timeline/current step/evidence/evaluation/handoff。
- Raw tabs 降级。

验收：

- `npm run build` 通过。
- 新增 UI 组件优先复用 `frontend/src/components/ui`。
- 用户不用打开 raw logs 也能理解 Agent 当前状态。
- waiting_for_human 有清晰入口。
- API/UI raw results 仍可展开排查。

### Step G: Golden Failure Matrix

产出：

- 扩展 `tests/test_agent_golden_tasks.py`。
- 增加 DB persistence tests。
- 增加 Run Detail source tests。

验收：

- 覆盖 API 401/403、network、timeout、5xx、schema assertion、safe write、dependency missing。
- 覆盖 UI locator missing、assertion missing、navigation timeout、setup/captcha、high-risk blocked。
- 覆盖 HITL waiting/resume 或 continuation。
- 覆盖 memory known blocker hit。

## 8. Acceptance Criteria

开发完成必须同时满足：

1. `uv run pytest` 全量通过。
2. Alembic migration tests 通过。
3. Golden Tasks 全部通过。
4. Run Detail source tests 覆盖 runtime workbench。
5. 新 runtime tables 在 SQLite 和 PostgreSQL 类型上都合理。
6. `Task.execution_log` 仍能支持旧接口兼容。
7. 新代码中不能让 LLM 默认生成任意 `run-code`。
8. 每个 runtime event 都要 redaction。
9. 每个 failure_type 都能映射到 retryable、human_required、report_category。
10. UI 主视图必须能展示 Action、Observation、Evaluation 三件事。
11. shadcn-vue 已初始化，`frontend/components.json`、`frontend/src/components/ui`、`frontend/src/lib/utils.ts` 存在。
12. `cd frontend && npm run build` 通过。

## 9. What Can Be Removed Later, Not Now

即使一步到位，本次仍不建议立刻删除：

- `api_execution_result`
- `ui_execution_result`
- `execution_result`
- `Task.execution_log`
- legacy `playwright_commands`
- `app/agent/nodes/api_executor.py`
- 旧 Run Detail raw tabs

删除这些需要单独迁移期和前端/接口兼容公告。

## 10. Single Codex Prompt For The Next Development

```text
你现在在 JeremyHaow/TestClaw 项目中开发。

目标：直接实现 TestClaw Agent Runtime v1，一步到位完成 runtime、数据库事件模型、Run Detail 工作台、API/UI 执行接入、Evaluator、Memory 和 Golden Tasks 闭环。

重要背景：
- 请先阅读 docs/AGENT_GAP_ANALYSIS.md
- 请先阅读 docs/AGENT_GAP_ANALYSIS_PROGRESS.md
- 请先阅读 docs/AGENT_RUNTIME_V1_ONE_STEP_PLAN.md
- 请先阅读 TestClaw_Codex_Fullstack_Refactor_Guide.md，重点看第 3 章视觉设计系统、第 4 章前端目录规划、第 6 章页面交互设计、Phase 4 Agent Cockpit。
- 这次允许修改 UI。
- 这次允许新增数据库模型和 Alembic migration。
- 但必须保持旧字段兼容，不能删除 legacy API/UI result。
- 前端必须标准化到 shadcn-vue。当前项目是 Vue3，不要使用 React 版 shadcn/ui。
- 视觉方向参考 TestClaw_Codex_Fullstack_Refactor_Guide.md，但基础组件必须使用 shadcn-vue 生成的 shadcn/ui 风格 Vue 组件。
- 当前 frontend 有 package-lock.json，项目依赖命令优先使用 npm/npx，不要无故切换包管理器。
- shadcn-vue Skill 和 MCP 是 Codex 环境增强；能安装就安装，不能安装要继续完成项目代码并在最终回复说明。

必须阅读：
- TestClaw_Codex_Fullstack_Refactor_Guide.md
- app/agent/action_runtime.py
- app/agent/state.py
- app/agent/graph.py
- app/agent/tool_registry.py
- app/agent/nodes/api_runner.py
- app/agent/nodes/ui_runner.py
- app/agent/nodes/ui_test_planner.py
- app/agent/nodes/execution_evaluator.py
- app/agent/nodes/reporter.py
- app/agent/nodes/knowledge_sink.py
- app/tools/playwright_skill.py
- app/tools/playwright_tool.py
- app/api/v1/runs.py
- app/models/run_artifacts.py
- alembic/versions/0006_run_operational_tables.py
- frontend/package.json
- frontend/vite.config.ts
- frontend/tsconfig.json
- frontend/src/styles/main.css
- frontend/src/pages/RunDetailPage.vue
- frontend/src/components/agent/
- tests/test_agent_tooling.py
- tests/test_agent_golden_tasks.py
- tests/test_database_migrations.py
- tests/test_run_detail_frontend_source.py

实现要求：
1. 先检查并安装 shadcn-vue：如果 frontend/components.json 不存在，执行 `cd frontend && npx shadcn-vue@latest init`。
2. shadcn-vue 初始化参数必须匹配本项目：Vite、TypeScript、Tailwind CSS file=`src/styles/main.css`、UI path=`src/components/ui`、utils path=`src/lib/utils`、base color=`neutral` 或 `zinc`、CSS variables=yes。
3. 如果 `@` alias 缺失，补齐 `frontend/vite.config.ts` 的 `resolve.alias` 和 `frontend/tsconfig.json` 的 `baseUrl/paths`。
4. 安装基础 UI 组件：`cd frontend && npx shadcn-vue@latest add button card badge input textarea select tabs dialog sheet tooltip table scroll-area separator skeleton dropdown-menu alert`。
5. 尝试安装 shadcn-vue Skill：`pnpm dlx skills add unovue/shadcn-vue`。如果环境不支持，不要阻塞主任务，最终回复说明。
6. 如环境允许，配置 Codex MCP 到 `~/.codex/config.toml`，追加 `[mcp_servers.shadcn]`、`command = "npx"`、`args = ["shadcn-vue@latest", "mcp"]`。配置后说明需要重启 Codex；不要假设当前会话立刻有 MCP 工具。
7. 新增 app/agent/runtime/，包含 models、runtime、tool_executor、event_store、failure_taxonomy、policies。
8. 新增 Alembic migration 0007_agent_runtime_v1.py，增加 run_agent_actions、run_agent_observations、run_agent_evaluations，或给出更好的等价 schema，但必须支持 action/observation/evaluation 查询和 timeline。
9. 运行时以 AgentRuntime 为主入口，执行 Action -> ToolCall -> Observation -> Evidence -> Evaluation。
10. API runner 和 UI runner 必须接入 AgentRuntime，旧 api_execution_result/ui_execution_result 保持兼容。
11. UI planner 默认输出 structured ui_actions，playwright_commands 只做 legacy fallback。
12. Playwright CLI 作为受限 skill/tool 执行，默认禁止任意 run-code/eval。
13. failure taxonomy 集中定义，并被 runtime、API/UI adapter、Evaluator、Reporter 共用。
14. Evaluator 基于 runtime observations/evidence 输出 retry/replan/ask_human/report。
15. Run Detail 改为 Runtime Workbench，主视图展示 current action、timeline、observation、evidence、evaluation、human handoff。raw API/UI 结果降级为详情。
16. Runtime Workbench 新增 UI 必须优先使用 `frontend/src/components/ui` 的 shadcn-vue 组件；业务组件放 `frontend/src/components/runtime/`。
17. Runtime Workbench 视觉风格必须参考 `TestClaw_Codex_Fullstack_Refactor_Guide.md` 的 Light SaaS / Agent Workspace / Blue accent / Calm spacing / Agent Cockpit 方向，但不要实现 guide 中旧的 `TcButton/TcCard/TcBadge` 自建基础组件。
18. guide 中的 `Tc*` 基础组件需求要映射到 shadcn-vue 组件：button -> `Button`，card -> `Card`，badge -> `Badge`，tabs -> `Tabs`，drawer -> `Sheet`，modal -> `Dialog`，tooltip -> `Tooltip`，table -> `Table`，skeleton -> `Skeleton`。
19. Memory candidate 必须引用 evaluation/observation/evidence ids，并只把高置信度、目标相关事实喂给 planner。
20. SSE 或 run detail 查询必须能读取 runtime events；Task.execution_log 继续保留兼容快照。
21. 添加或更新 Golden Tasks failure matrix，覆盖 API、UI、HITL、Memory 的主要失败类型。
22. 添加数据库 migration tests 和前端 source tests。
23. 全程 redaction，不能把 token/cookie/header secret 写入 observation/evidence/memory。

不要做：
- 不要删除 legacy 字段。
- 不要让 LLM 默认生成任意 Playwright/Python 脚本。
- 不要把 RAG 当成主要智能来源。
- 不要只修一个 golden case。
- 不要提交未通过测试的中间状态。
- 不要把 React 版 shadcn/ui 组件复制进 Vue 项目。
- 不要按 `TestClaw_Codex_Fullstack_Refactor_Guide.md` 旧方案新增 `TcButton/TcCard/TcBadge` 作为新的基础 UI 系统。
- 不要把业务组件写进 `frontend/src/components/ui`。
- 不要引入另一个大型 UI 框架来和 shadcn-vue 混用。

验收：
- uv run pytest 全量通过。
- cd frontend && npm run build 通过。
- 新 migration tests 通过。
- Golden Tasks 能证明 runtime v1 的 Action -> ToolCall -> Observation -> Evaluation 闭环。
- Run Detail 页面源码测试证明 UI 使用 runtime events。
- Run Detail 页面源码测试证明 Runtime Workbench 使用 shadcn-vue `components/ui` 基础组件。
- Run Detail 页面源码测试证明没有把旧 guide 的 `TcButton/TcCard/TcBadge` 作为新基础组件路线。
- frontend/components.json 存在，并且基础组件已生成到 frontend/src/components/ui。
- 最终提交一次或多次 commit，并在最终回复中列出 commit hash、迁移文件、主要修改文件、测试结果和当前 git status。
```

## 11. Practical Warning

这是一项大改，预计会同时触碰后端 runtime、数据库、worker、API detail、前端 Run Detail、测试矩阵。为了避免失控，下一次开发必须坚持：

1. 先迁移模型和事件写入，再改 runner。
2. 先保持旧接口可用，再切前端主视图。
3. 先扩 Golden Tasks，再做大拆分。
4. 每完成一个内部 step 就跑 focused tests。
5. 最终必须跑全量 `uv run pytest`。

如果开发中发现一次完成会破坏太多旧行为，优先保住 runtime 数据模型、event store 和 API/UI runner 接入，不要牺牲兼容性去追求表面上的“全替换”。
