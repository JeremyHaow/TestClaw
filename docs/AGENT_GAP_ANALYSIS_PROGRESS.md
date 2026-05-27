# TestClaw Agent Gap Analysis Progress Audit

日期：2026-05-27

本文严格对照 `docs/AGENT_GAP_ANALYSIS.md`，检查每个章节和关键判断在当前代码中的完成状态，并给出下一轮开发指导。结论基于当前代码证据，不等同于产品验收。

状态说明：

- `完成`：已落到代码、保持兼容，并有针对性测试覆盖。
- `基本完成`：主路径已实现，但仍有边界或深度不足。
- `部分完成`：已建立入口或 adapter，但原 gap 的核心架构问题仍未彻底解决。
- `未完成`：原 gap 仍成立，或本轮明确没有进入实现范围。
- `保持不做`：原文明确要求暂时不要做，当前也没有做。

## 1. Overall Status

1. Phase 0-7 的第一轮改造已经完成：诊断文档、统一协议字段、Playwright CLI Skill adapter、API 结构化 observation/evidence、Evaluator 协议化评估、Run Detail 接入、Memory/RAG first pass、Golden Tasks 都已落地。
2. 当前项目已经从“多个 runner 各自输出结果”推进到“API/UI/Memory/Evaluator 都能围绕 `Action / ToolCall / Observation / Evidence / Evaluation` 记录运转”。
3. 但它还不是完整 action-level Agent Runtime。`app/agent/graph.py::build_graph()` 仍是阶段级 LangGraph，`execution_evaluator.route_after_evaluation()` 仍在阶段边界做重试/重规划。
4. `app/agent/state.py::AgentState` 仍是大状态包；新增协议字段解决了可观察性，但还没有把运行领域模型从 state 中拆出去。
5. `api_runner.py`、`ui_runner.py`、`runs.py` 仍然很大。原 roadmap 要求先加 adapter 和测试、不直接拆大文件，目前遵守了这个约束。
6. Playwright CLI 已经有结构化 action 编译层，但 UI 主路径仍兼容并大量使用 command string；旧 prompt 中仍允许 `run-code`，只是结构化 action adapter 会阻断高风险 `run_code`。
7. Memory/RAG 已从“自然语言上下文”前进一步，能从 evaluation 生成 `memory_candidates`，并让 planner 使用高置信度、目标相关的 `rag_facts`；但还没有形成长期、可治理的决策记忆系统。
8. Run Detail 已能展示协议记录，但整体产品入口仍保留较多平台配置项，尚未完成“委派智能体工作台”的完整交互收敛。
9. Golden Tasks 已建立第一版能力回归，能防止一部分“只修一个 case”的问题；后续需要扩展成矩阵，而不是只覆盖当前 5 类典型场景。
10. 下一轮不应马上重做 UI 或数据库，而应把“协议记录”提升为“中心化 runtime 执行器和 failure taxonomy”，再逐步拆 runner。

## 2. Section-by-Section Completion Matrix

### 2.1 Executive Summary

| 原文判断 | 当前状态 | 代码依据 | 说明 |
| --- | --- | --- | --- |
| 已具备 FastAPI/Celery/LangGraph/OpenAPI/Playwright/RAG/Run Detail 等基础能力 | 完成 | `app/main.py`、`app/worker/tasks.py::run_agent_task`、`app/agent/graph.py::build_graph()`、`app/tools/doc_parser.py::parse_api_document_content()`、`app/tools/playwright_tool.py`、`frontend/src/pages/RunDetailPage.vue` | 基础能力仍保留。 |
| 最大问题是能力没有通过统一 Agent Runtime 协议闭环 | 部分完成 | `app/agent/action_runtime.py::AgentToolCall`、`AgentObservation`、`AgentEvidence`、`AgentEvaluation` | 协议字段和写入 helper 已有，但还不是中心化 runtime loop。 |
| LangGraph 有条件回路，但仍是阶段级重试/重规划 | 未完成 | `app/agent/graph.py::build_graph()`、`execution_evaluator.route_after_evaluation()` | 这个判断仍成立；下一轮核心任务是从阶段级推进到 action 级。 |
| `AgentState` 过大、边界过宽 | 未完成 | `app/agent/state.py::AgentState` | 新增协议字段后状态更可观察，但体积和职责仍未收敛。 |
| `action_runtime.py` 和 `tool_registry.py` 是起点，但统一协议只部分接入 | 基本完成 | `append_api_result_observations()`、`append_ui_result_observations()`、`append_evaluation_protocol()` | API/UI/Evaluator 已接入协议记录；仍缺统一 executor。 |
| API 能力成熟但 `api_runner.py` 过大 | 部分完成 | `app/agent/nodes/api_runner.py::run()`、`append_api_result_observations()` | 结构化输出已完成；拆分职责尚未开始。 |
| UI 能力有潜力，但核心接口是命令字符串 | 部分完成 | `app/tools/playwright_skill.py::compile_playwright_ui_action()`、`app/agent/nodes/ui_runner.py::_build_ui_case_batches()` | 已有 structured action adapter；旧 command string 路径仍是兼容主干。 |
| RAG/Memory 已参与规划，但不是稳定决策记忆 | 部分完成 | `knowledge_sink.py::_build_memory_candidate()`、`knowledge_retriever.py::_fact_from_candidate()`、`planner.py::_planner_memory_facts()` | 已能提炼高置信度事实；长期记忆治理和更多类型沉淀仍不足。 |
| 前端像 Agent Cockpit 的元素已有，但主路径仍偏平台配置 | 部分完成 | `RunDetailPage.vue`、`AgentTimeline.vue`、`RunPage.vue` | Run Detail 已增强；RunPage 和整体信息架构未重做。 |
| 下一步先统一执行协议，不先重做 UI/DB/RAG | 完成 | `app/agent/action_runtime.py`、`tests/test_agent_tooling.py` | 当前改造顺序遵守原建议。 |

### 2.2 Current Architecture Map

| 架构区域 | 当前状态 | 代码依据 | 审计结论 |
| --- | --- | --- | --- |
| Backend Entry and API Layer | 保持原状 | `app/main.py`、`app/api/router.py`、`app/api/v1/runs.py` | 没有做大规模 API 层重构，符合约束。 |
| Async Task and Persistence | 部分完成 | `app/worker/tasks.py::run_graph_with_progress()`、`app/agent/progress.py::EXECUTION_LOG_KEYS` | `execution_log` 已包含协议字段；尚未切到 DB event/evidence 表。 |
| Agent Graph | 未完成 | `app/agent/graph.py::build_graph()` | 图结构仍是固定阶段流水线加条件边。 |
| State and Runtime Contracts | 基本完成 | `AgentToolCall`、`AgentObservation`、`AgentEvidence`、`AgentEvaluation` | 协议字段已补齐；state 仍大。 |
| API Testing Stack | 基本完成 | `api_runner.py::run()`、`append_api_result_observations()` | 保留执行能力并追加协议记录。 |
| UI Testing Stack | 部分完成 | `playwright_skill.py`、`ui_runner.py::_build_ui_case_batches()` | Skill adapter 已有；命令字符串仍大量存在。 |
| RAG / Memory / Knowledge | 部分完成 | `knowledge_sink.py::_build_memory_candidate()`、`knowledge_retriever.py::run()` | 已接入结构化 memory facts；仍是 first pass。 |
| Frontend Product Surface | 部分完成 | `RunDetailPage.vue`、`AgentTimeline.vue`、`AgentEvidenceCard.vue` | Run Detail 能看协议过程；整体产品路径未彻底收敛。 |

### 2.3 What Already Works

原文列出的 10 项“已有价值能力”均保留，其中 6 项被增强：

| 已有能力 | 当前状态 | 代码依据 | 说明 |
| --- | --- | --- | --- |
| 完整运行链路 | 完成 | `runs.py::create_run()`、`worker/tasks.py::run_graph_with_progress()`、`run_stream_service.py::stream_run_events()` | 未破坏旧链路。 |
| LangGraph 覆盖主要测试阶段 | 完成 | `graph.py::build_graph()` | 节点仍完整。 |
| OpenAPI 解析 | 完成 | `doc_parser.py::parse_api_document_content()` | 未改动核心解析。 |
| API 安全边界 | 基本完成 | `api_scope.py::validate_generated_api_cases()`、`api_runner.py::_safe_write_skip_reason()` | Golden Tasks 覆盖 safe write blocked。 |
| API 真实执行能力 | 基本完成 | `api_runner.py::_request_with_retry()`、`_evaluate_assertions()`、`_classify_api_failure()` | 追加了 protocol observation/evidence。 |
| UI 真实浏览器动作 | 基本完成 | `playwright_tool.py::run_playwright_cli_command()`、`ui_runner.py::run()` | 追加结构化 action metadata。 |
| Playwright CLI skill 文档 | 完成 | `.codex/skills/playwright-cli/SKILL.md` | 仍作为能力目录。 |
| Agent 可观察性雏形 | 基本完成 | `agent_tool_calls`、`agent_observations`、`agent_evidence`、`agent_protocol_evaluations` | 已从雏形变成主路径协议记录。 |
| Evaluator 有有限重规划 | 基本完成 | `execution_evaluator.py::_guardrail_decision()`、`_apply_decision()`、`append_evaluation_protocol()` | 现在读取 protocol summary，但仍是阶段级。 |
| 测试基础 | 基本完成 | `tests/test_agent_tooling.py`、`tests/test_agent_golden_tasks.py` | 已新增 Golden Tasks。 |

## 3. Main Gaps Completion

### 3.1 Agent Runtime Gap

状态：`部分完成`

已经完成：

- 定义了协议模型：`app/agent/action_runtime.py::AgentToolCall`、`AgentObservation`、`AgentEvidence`、`AgentEvaluation`。
- API/UI runner 能把结果追加到 `agent_tool_calls`、`agent_observations`、`agent_evidence`。
- Evaluator 能输出 `agent_protocol_evaluations`。

仍未完成：

- 没有中心化 `AgentRuntime` 或 `ToolExecutor`。
- `app/agent/graph.py::build_graph()` 仍按 `planner -> runner -> evaluator` 阶段推进。
- 每个 action 后即时 `Observe -> Evaluate -> Continue/Replan` 的循环还不存在。
- retry/replan/human escalation policy 已存在于 evaluator，但不是 runtime 全局策略。

下一步判断：

- 不能再只往各 runner 中追加 mapper；需要建立最小中心执行器，先负责接收结构化 action、分派到 API/UI adapter、返回 observation。

### 3.2 Tool Execution Gap

状态：`部分完成`

已经完成：

- `app/tools/playwright_skill.py::compile_playwright_ui_action()` 将 `open/goto/click_ref/fill_ref/snapshot/screenshot/assert_visible/wait_for` 编译为受限 CLI spec。
- `app/agent/action_runtime.py::append_api_result_observations()` 和 `append_ui_result_observations()` 统一输出 observation/evidence。
- `tests/test_agent_tooling.py` 覆盖 structured Playwright action、高风险 `run_code` 阻断、API/UI protocol mapping。

仍未完成：

- `tool_registry.py::ToolCapability` 仍主要是声明，不是实际调度入口。
- API request 和 UI command 还不是统一 `ToolCallRuntime` 调度。
- `ui_test_planner.py` 和 prompt 中仍保留旧 `run-code` 能力，结构化 adapter 只是新路径。

### 3.3 State / Data Model Gap

状态：`部分完成`

已经完成：

- `AgentState` 已增加 protocol 字段：`agent_tool_calls`、`agent_observations`、`agent_evidence`、`agent_protocol_evaluations`、`agent_protocol_summary`。
- `app/agent/progress.py::EXECUTION_LOG_KEYS`、`app/schemas/task.py::task_to_dict()`、`app/api/v1/runs.py::get_run_detail()` 已暴露协议字段。

仍未完成：

- `AgentState` 仍混合输入、计划、执行结果、RAG、鉴权、UI 登录、前端展示等字段。
- `app/models/run_artifacts.py::RunToolCall`、`RunEvidence`、`RunFinding` 仍不是主运行链路的数据源。
- Run Detail 仍读取 `Task.execution_log` 大 JSON 快照，而不是读取事件表。

### 3.4 Evaluation Gap

状态：`基本完成`

已经完成：

- `execution_evaluator.py::_protocol_observation_summary()` 从 `agent_observations` 和 `agent_evidence` 汇总 evidence。
- `_api_needs_retry()`、`_api_needs_human()`、`_api_needs_replan()`、`_ui_needs_retry()`、`_ui_needs_human()`、`_ui_needs_replan()` 使用协议 failure type 做判断。
- `append_evaluation_protocol()` 输出 `sufficient_evidence`、`outcome`、`next_action`、`confidence`、`failure_type`、`missing_evidence`、`replan_hint`。

仍未完成：

- 评估仍发生在 API/UI 阶段结束后，不是每个 action 后。
- 失败分类仍有一部分分散在 API/UI runner 和 action runtime 中。
- Human-in-the-loop 仍是 `ask_human` 决策和 assisted rerun 入口，不是同一运行中的可靠 pause/resume。

### 3.5 UI/UX Gap

状态：`部分完成`

已经完成：

- `frontend/src/pages/RunDetailPage.vue` 已接入 `agent_observations`、`agent_evidence`、`agent_protocol_evaluations`、`agent_protocol_summary`。
- `AgentTimeline.vue`、`AgentEvidenceCard.vue` 已在 Run Detail 主视图承载 protocol feed 和 evidence。
- `tests/test_run_detail_frontend_source.py::test_run_detail_wires_agent_protocol_records_into_cockpit()` 保护前端接线。

仍未完成：

- `frontend/src/pages/RunPage.vue` 仍有大量配置项，产品感仍偏测试平台。
- 用户主路径“输入目标 -> 计划 -> 执行 -> 观察 -> 评估 -> 报告 -> 记忆”还没有彻底收敛到单一工作台。
- 运行中介入仍不是同一 run 的 pause/resume。

### 3.6 Memory / RAG Gap

状态：`部分完成`

已经完成：

- `knowledge_sink.py::_build_memory_candidate()` 从 evaluation、observation、final report 生成结构化 memory candidate。
- `knowledge_retriever.py::_fact_from_candidate()` 只提取高置信度、目标相关 facts。
- `planner.py::_planner_memory_facts()` 将 facts 注入 plan，并写入 `memory_fact_count`、`known_blockers`、`successful_strategies`。
- `tests/test_agent_golden_tasks.py::test_golden_rag_memory_hit_is_carried_into_planner()` 覆盖 memory hit。

仍未完成：

- Memory 仍存入通用 knowledge content，不是独立、可治理的目标级决策记忆表。
- 成功路径、locator 修复记录、endpoint dependency 经验还没有系统化 schema。
- 低质量 memory 污染治理仍依赖 confidence 和 target related 的初步过滤。

### 3.7 Testing / Regression Gap

状态：`基本完成`

已经完成：

- 新增 `tests/test_agent_golden_tasks.py`，覆盖：
  - API auth failure -> ask human
  - safe write blocked + dependency missing
  - UI locator missing -> replan UI
  - UI captcha/setup blocker -> ask human
  - RAG memory hit -> planner 使用 memory fact
- `tests/test_agent_tooling.py` 扩展了协议映射、Playwright skill、Evaluator 决策等测试。

仍未完成：

- Golden Tasks 还不是完整矩阵。每类失败只有少数代表样例。
- 缺少更接近真实 worker/run detail 的端到端 golden replay。
- 尚未把“新增 bug fix 必须补同类变体”制度化到开发文档或测试模板。

## 4. Root Cause Analysis Completion

| 原根因 | 当前状态 | 证据 | 说明 |
| --- | --- | --- | --- |
| 缺少统一协议，经验无法迁移 | 基本完成 | `action_runtime.py` protocol models + append helpers | 迁移基础已建立。 |
| 失败分类不在中心位置 | 部分完成 | `api_runner.py::_classify_api_failure()`、`action_runtime.py::_api_failure_type()`、`execution_evaluator.py` | Evaluator 已消费统一 failure type，但 taxonomy 仍分散。 |
| Runner 过度承担决策 | 未完成 | `api_runner.py` 3923 行、`ui_runner.py` 1417 行 | 当前只是追加协议输出，未拆职责。 |
| Graph 回路粒度太粗 | 未完成 | `graph.py::build_graph()` | 仍是阶段级。 |
| Memory 存结果，不是决策知识 | 部分完成 | `knowledge_sink.py::_build_memory_candidate()` | 已能生成决策事实，但治理能力有限。 |
| 前端展示未反向规范后端协议 | 部分完成 | `RunDetailPage.vue` protocol surface | 前端开始消费协议，但还没有反向驱动所有后端事件持久化。 |

结论：原文中“换一个问题还是不会”的主因已经被缓解，但没有完全消除。当前最强的改进是协议和 golden tests；最大未解问题是缺少中心 runtime 和 runner 职责过重。

## 5. Target Architecture Proposal Completion

### Planner

状态：`部分完成`

- 已完成：`planner.py` 能读取 `rag_facts` 并写入 `memory_fact_count`、`known_blockers`、`successful_strategies`。
- 未完成：Planner 还没有只输出结构化 `ActionPlan`；仍会输出 API/UI plan、strategy、tool_plan 多种结构。
- 下一步：让 Planner 的主输出变成 `ActionPlan`，case 生成作为 plan-to-case adapter。

### API Runner

状态：`基本完成`

- 已完成：`append_api_result_observations()` 为每个 API request 输出 method/path/url/status/duration/assertions/failure_type/safety_decision/evidence_refs。
- 未完成：`api_runner.py::run()` 仍混合选择、构造、执行、断言、分类、汇总。
- 下一步：先抽 `api_observation_mapper` 和 `api_failure_taxonomy`，再拆 request builder/executor。

### UI Runner

状态：`部分完成`

- 已完成：`compile_playwright_ui_actions()` 能将 structured UI action 编译为 CLI spec，`ui_runner.py` 能保留 `agent_action_type`、`transport`、`risk`。
- 未完成：旧 command string 和 `run-code` 兼容路径仍很重。
- 下一步：让 `ui_test_planner.py` 默认产出 `ui_actions`，command string 只作为 legacy fallback。

### Playwright CLI

状态：`部分完成`

- 已完成：`playwright_skill.py` 建立受限 adapter，阻断 structured `run_code/eval`。
- 未完成：Playwright CLI 还不是由中心 Tool Executor 调用的正式 Skill runtime。
- 下一步：把 `ui.playwright_cli` 接入中心 runtime，并统一 `ToolCall` 生命周期。

### pytest-playwright

状态：`保持不做`

- 已保留：`playwright_tool.py::execute_playwright_test()` 没有被强行改成探索主路径。
- 当前判断仍正确：pytest-playwright 应用于稳定回归脚本，不应成为实时探索主执行器。

### Evaluator

状态：`基本完成`

- 已完成：Evaluator 读取 protocol summary，输出结构化 protocol evaluation。
- 未完成：还没有 action-level 评估循环；failure taxonomy 仍未集中。
- 下一步：把 Evaluator 从“阶段评估器”拆出可复用的 `evaluate_observations()` 纯逻辑。

### Memory

状态：`部分完成`

- 已完成：evaluation -> memory candidate -> high-confidence fact -> planner。
- 未完成：没有独立 memory governance、TTL、去重、质量审查。
- 下一步：先扩展 memory candidate schema，再考虑 DB schema。

### Frontend Run Detail

状态：`基本完成`

- 已完成：Run Detail 显示 protocol timeline、recent observations、evidence count、evaluation。
- 未完成：不是完整实时 pause/resume 工作台；raw API/UI tabs 仍较重。
- 下一步：后端先输出更稳定的 action-level events，前端再轻量适配。

## 6. Recommended Refactor Roadmap Completion

| Phase | 原目标 | 当前状态 | 证据 | 下一步 |
| --- | --- | --- | --- | --- |
| Phase 0 | 诊断和文档 | 完成 | `docs/AGENT_GAP_ANALYSIS.md` | 当前文档作为阶段复盘。 |
| Phase 1 | 统一 Action/Observation/Evaluation 协议 | 基本完成 | `action_runtime.py` protocol classes、`agent_protocol_summary` | 建中心 runtime。 |
| Phase 2 | Playwright CLI Skill 化 | 部分完成 | `app/tools/playwright_skill.py`、`tests/test_agent_tooling.py` | 让 UI planner 默认产出 `ui_actions`。 |
| Phase 3 | API 执行结构化 | 基本完成 | `append_api_result_observations()`、API golden tests | 抽 taxonomy/mapper。 |
| Phase 4 | Evaluator 评估证据和失败类型 | 基本完成 | `execution_evaluator.py::_protocol_observation_summary()`、`append_evaluation_protocol()` | 拆出 action-level evaluator。 |
| Phase 5 | Run Detail 接入执行过程 | 基本完成 | `RunDetailPage.vue` protocol computed fields/components | 等 action-level events 后再继续前端。 |
| Phase 6 | Memory/RAG 接入计划生成 | 部分完成 | `knowledge_sink.py`、`knowledge_retriever.py`、`planner.py` | 加 memory schema/governance。 |
| Phase 7 | Golden Tasks 回归测试 | 基本完成 | `tests/test_agent_golden_tasks.py` | 扩成失败类型矩阵。 |

## 7. What Not To Do Yet Audit

| 原约束 | 当前是否遵守 | 说明 |
| --- | --- | --- |
| 不要先重做 UI | 遵守 | 只增强 Run Detail 数据接入，没有重做页面体系。 |
| 不要先加复杂 RAG | 基本遵守 | Memory/RAG 做了 first pass，但没有引入复杂新系统或依赖。 |
| 不要让 LLM 直接生成任意脚本 | 部分遵守 | structured action 阻断 `run_code`，但 legacy prompt/command path 仍允许 `run-code`。 |
| 不要先大规模改数据库 | 遵守 | 没有新增迁移，协议仍写 `execution_log`。 |
| 不要针对单个 case 写特殊判断 | 基本遵守 | Golden Tasks 推动按 failure type 处理，但 taxonomy 分散仍有风险。 |
| 不要立即拆 `api_runner.py` 和 `runs.py` | 遵守 | 大文件仍保留，先加 adapter/tests。 |
| 不要删除 legacy 字段 | 遵守 | `api_execution_result`、`ui_execution_result`、`execution_result` 等仍兼容。 |
| 不要一开始做复杂 HITL pause/resume | 遵守 | 只做到 `ask_human` 和现有 intervention/rerun。 |
| 不要混淆 Playwright CLI 和 pytest-playwright | 遵守 | CLI 是探索 transport，pytest-playwright 仍是脚本执行/回归路径。 |

## 8. First 3 Codex Tasks Completion

### Task 1: 建立统一执行协议

状态：`基本完成`

代码依据：

- `app/agent/action_runtime.py::AgentToolCall`
- `app/agent/action_runtime.py::AgentObservation`
- `app/agent/action_runtime.py::AgentEvidence`
- `app/agent/action_runtime.py::AgentEvaluation`
- `app/agent/state.py::AgentState`
- `app/agent/progress.py::EXECUTION_LOG_KEYS`
- `app/schemas/task.py::task_to_dict()`
- `tests/test_agent_tooling.py`

遗留问题：

- 协议记录已存在，但不是中心 runtime 的唯一执行入口。

### Task 2: Playwright CLI Skill 化

状态：`部分完成`

代码依据：

- `app/tools/playwright_skill.py::compile_playwright_ui_action()`
- `app/tools/playwright_skill.py::compile_playwright_ui_actions()`
- `app/agent/nodes/ui_runner.py::_build_ui_case_batches()`
- `tests/test_agent_tooling.py::test_playwright_skill_compiles_structured_actions_and_blocks_run_code()`

遗留问题：

- UI planner 仍会生成 legacy command string。
- prompt 中仍有 `run-code` 使用说明。
- Skill adapter 还没接入中心 Tool Executor。

### Task 3: API Runner 结构化执行

状态：`基本完成`

代码依据：

- `app/agent/action_runtime.py::append_api_result_observations()`
- `app/agent/nodes/api_runner.py::run()`
- `tests/test_agent_tooling.py`
- `tests/test_agent_golden_tasks.py`

遗留问题：

- API failure taxonomy 未集中。
- `api_runner.py` 仍是大文件。

## 9. Evidence From Code Delta

以下是当前最关键代码证据，说明原 gap 已经被部分或基本解决：

- 统一协议：`app/agent/action_runtime.py::AgentToolCall`、`AgentObservation`、`AgentEvidence`、`AgentEvaluation`。
- 协议写入：`append_agent_observation()`、`append_api_result_observations()`、`append_ui_result_observations()`、`append_evaluation_protocol()`。
- 状态暴露：`app/agent/state.py::AgentState`、`app/agent/progress.py::EXECUTION_LOG_KEYS`、`app/schemas/task.py::task_to_dict()`、`app/api/v1/runs.py::get_run_detail()`。
- API 执行结构化：`app/agent/nodes/api_runner.py::run()` 调用 `append_api_result_observations()`。
- UI Skill adapter：`app/tools/playwright_skill.py::compile_playwright_ui_action()`，`app/agent/nodes/ui_runner.py::_build_ui_case_batches()`。
- Evaluator 协议评估：`execution_evaluator.py::_protocol_observation_summary()`、`_guardrail_decision()`、`_apply_decision()`、`run()`。
- Memory first pass：`knowledge_sink.py::_build_memory_candidate()`、`knowledge_retriever.py::_fact_from_candidate()`、`planner.py::_planner_memory_facts()`。
- Run Detail 接入：`frontend/src/pages/RunDetailPage.vue` 中 `agentProtocolObservations`、`agentProtocolEvidence`、`agentProtocolEvaluations`、`protocolTimelineItems`。
- Golden Tasks：`tests/test_agent_golden_tasks.py`。

以下证据说明核心架构 gap 仍存在：

- 阶段级 graph：`app/agent/graph.py::build_graph()` 仍使用固定节点和条件边。
- 大状态：`app/agent/state.py::AgentState` 仍包含超过 100 行跨层字段。
- 大 runner：`app/agent/nodes/api_runner.py` 3923 行，`app/agent/nodes/ui_runner.py` 1417 行。
- API 层过重：`app/api/v1/runs.py` 4134 行。
- DB event models 未成为主路径：`app/models/run_artifacts.py::RunToolCall`、`RunEvidence`、`RunFinding` 已存在但运行过程仍主要依赖 `Task.execution_log`。

## 10. Risk List Status

| 原风险 | 当前状态 | 已有缓解 | 下一步规避方式 |
| --- | --- | --- | --- |
| 兼容性风险 | 降低 | 保留旧字段并追加新协议 | 继续禁止删除 legacy result，直到前端和 tests 完全迁移。 |
| 大文件拆分风险 | 仍高 | 未直接拆大文件 | 下一轮先抽纯 mapper/taxonomy，再拆执行器。 |
| LLM 不稳定风险 | 中等 | structured action、guardrail、golden tests | UI planner 默认 structured actions，禁用默认 `run-code`。 |
| 安全风险 | 中等 | safe_read_only、redaction、high-risk block | 中心 runtime 统一 permission/risk gate。 |
| 证据膨胀风险 | 仍高 | helper 中有截断和 summary | 后续需要 artifact/event persistence，不要长期堆 `execution_log`。 |
| Evaluator 误判风险 | 中等 | guardrail 先行，LLM merge 受限 | 集中 failure taxonomy，增加 evaluator golden matrix。 |
| Memory 污染风险 | 中等 | high-confidence + target-related filtering | 增加 memory candidate 去重、过期、审查字段。 |
| 前端复杂度风险 | 仍高 | 只接入组件，不重做页面 | 先稳定 action-level events，再简化信息架构。 |
| 测试成本风险 | 降低 | `tests/test_agent_golden_tasks.py` 已有 mock golden | 扩矩阵时继续用 mock，不依赖外部真实服务。 |
| HITL 范围风险 | 仍高 | 只做 ask_human 决策 | pause/resume 单独立项，不和 runtime executor 同时做。 |

## 11. What Is Now Reliable

当前可以认为比较可靠的能力：

1. API/UI 执行结果能进入统一 protocol record，并暴露到 run detail。
2. API failure 可以在典型 auth/safe write/dependency/assertion/network 场景中形成结构化分类。
3. UI failure 可以在 locator missing、setup blocker、高风险 structured action 场景中形成结构化分类。
4. Evaluator 可以基于 protocol observation 选择 `ask_human`、`retry_same_action`、`replan_api`、`replan_ui`、`report`。
5. Run Detail 能从主视图看到 observation/evidence/evaluation，而不仅是 raw logs。
6. Memory 可以从 evaluation 生成 candidate，并在下一次 planner 中作为高置信度 fact 使用。
7. Golden Tasks 已能约束典型 Agent 能力，不再只验证零散函数。

## 12. What Is Still Not a Real Agent Runtime

这些问题仍不能在简历或文档里夸大为“已经完成”：

1. 还没有 action-level runtime loop。
2. 还没有统一 Tool Executor。
3. 还没有把 `RunToolCall`、`RunEvidence`、`RunFinding` 作为主事件存储。
4. Planner 还没有只输出受限 `ActionPlan`。
5. UI planner 还没有默认使用 structured UI action。
6. Human-in-the-loop 还不是同一 run 中的 pause/resume。
7. 大文件和大状态还没有拆分。
8. Memory 还不是完整的、可治理的目标级决策记忆系统。

## 13. Next Development Guide

下一次开发应进入 `Phase 8: Action-level Runtime Hardening`。目标不是继续堆功能，而是把已经出现的协议记录提升为真正执行中心。

### 13.1 推荐优先级

1. 建立最小中心 runtime executor：接收 `AgentAction`，分派 API/UI adapter，返回 `AgentObservation`，同时保持旧 runner 兼容。
2. 集中 failure taxonomy：把 API/UI/Runtime/Evaluator 共用的失败类型、可重试性、人类介入建议统一成一个模块。
3. 让 UI planner 默认产出 `ui_actions`：旧 `playwright_commands` 只保留为 fallback，并把默认 `run-code` 降级为需要显式 high-risk reason。
4. 抽 API observation mapper 和 request builder：不要一次拆完整 `api_runner.py`，先拆纯函数。
5. 扩 Golden Tasks 矩阵：每新增一个 failure type 或 runtime 行为，都加同类变体。

### 13.2 暂时仍不要做

1. 不要马上做数据库迁移，把协议直接迁到 event tables。
2. 不要重写 Run Detail 或 RunPage。
3. 不要把 Memory/RAG 做成主智能来源。
4. 不要删除旧 `api_execution_result`、`ui_execution_result`、`playwright_commands`。
5. 不要大规模拆 `runs.py`，除非先有 run detail/regression 覆盖。

## 14. Next 5 Codex Tasks

### Task 1: 建立最小中心 Agent Runtime Executor

```text
你现在在 JeremyHaow/TestClaw 项目中开发。

目标：把现有协议记录推进为最小中心 Agent Runtime Executor，但不要重构整个 graph，也不要删除旧 runner 输出。

请先阅读：
- docs/AGENT_GAP_ANALYSIS.md
- docs/AGENT_GAP_ANALYSIS_PROGRESS.md
- app/agent/action_runtime.py
- app/agent/tool_registry.py
- app/agent/nodes/api_runner.py
- app/agent/nodes/ui_runner.py
- app/tools/playwright_skill.py
- tests/test_agent_tooling.py
- tests/test_agent_golden_tasks.py

要求：
1. 不改数据库迁移，不改前端页面，不引入新依赖。
2. 新增一个最小 runtime 层，例如 app/agent/runtime/，用于接收 validated AgentAction 并分派到 API/UI adapter。
3. 第一版 runtime 可以只支持 api.http_request、api.derive_schema_requests、ui.playwright_cli 这几个已有动作。
4. 旧 api_runner/ui_runner 仍可作为调用方或兼容路径，但 action -> tool call -> observation 的生命周期必须由 runtime helper 统一封装。
5. 所有输出仍写入 agent_tool_calls、agent_observations、agent_evidence、agent_protocol_summary。
6. 添加测试覆盖：API action 成功/阻断、UI structured action 成功/高风险阻断、未知 tool action。
7. 不拆 api_runner.py 和 ui_runner.py 的大结构，只抽最小 adapter。

完成后提交一次 commit，并在最终回复中说明 runtime 支持的 action、兼容策略、测试结果和 commit hash。
```

### Task 2: 集中 Failure Taxonomy

```text
你现在在 JeremyHaow/TestClaw 项目中开发。

目标：把 API/UI/Runtime/Evaluator 共享的失败类型集中管理，减少散落在 runner 和 evaluator 中的字符串判断。

请先阅读：
- docs/AGENT_GAP_ANALYSIS_PROGRESS.md
- app/agent/action_runtime.py
- app/agent/nodes/api_runner.py
- app/agent/nodes/ui_runner.py
- app/agent/nodes/execution_evaluator.py
- tests/test_agent_tooling.py
- tests/test_agent_golden_tasks.py

要求：
1. 不改数据库迁移，不改前端页面，不引入新依赖。
2. 新增或整理 failure taxonomy 模块，例如 app/agent/failure_taxonomy.py。
3. 统一定义 failure_type、layer、retryable、human_action_required、default_next_action、report_category。
4. 先迁移最常用类型：auth_failure、network_error、timeout、assertion_failure、schema_contract、backend_error、safe_write_blocked、dependency_missing、environment_blocked、ui_locator_missing、ui_assertion_failure、ui_setup_failed、ui_high_risk_action_blocked。
5. 保持旧字段兼容，不要改变对外 payload key。
6. Evaluator 使用 taxonomy 判断 retry/replan/ask_human 的默认倾向。
7. 添加测试覆盖 taxonomy 映射和现有 Golden Tasks 行为不变。

完成后提交一次 commit，并在最终回复中说明迁移范围、剩余散落判断、测试结果和 commit hash。
```

### Task 3: UI Planner 默认输出 Structured UI Actions

```text
你现在在 JeremyHaow/TestClaw 项目中开发。

目标：让 UI planner 默认产出 structured ui_actions，而不是把 command string 作为 Agent 主协议。旧 playwright_commands 继续兼容。

请先阅读：
- docs/AGENT_GAP_ANALYSIS_PROGRESS.md
- app/agent/nodes/ui_test_planner.py
- app/agent/nodes/ui_runner.py
- app/tools/playwright_skill.py
- app/agent/prompts.py
- tests/test_agent_tooling.py
- tests/test_agent_golden_tasks.py

要求：
1. 不重做前端，不改数据库，不引入新依赖。
2. UI case 优先包含 ui_actions，动作类型限制为 open/goto/click_ref/fill_ref/snapshot/screenshot/assert_visible/wait_for。
3. playwright_commands 只作为 legacy fallback 或导出展示。
4. 默认不要生成 run-code；如必须保留，必须标记 high_risk reason，并让 adapter 记录 blocked 或 requires_approval。
5. ui_runner 继续兼容旧 command string。
6. 添加测试覆盖：planner 输出 ui_actions、runner 执行 ui_actions、legacy command fallback、run-code 不作为默认动作。

完成后提交一次 commit，并在最终回复中说明 UI action schema、兼容策略、测试结果和 commit hash。
```

### Task 4: 抽 API Observation Mapper 和 Request Builder

```text
你现在在 JeremyHaow/TestClaw 项目中开发。

目标：降低 api_runner.py 职责，但不要一次性大拆。先把纯映射和请求构造逻辑抽出来。

请先阅读：
- docs/AGENT_GAP_ANALYSIS_PROGRESS.md
- app/agent/nodes/api_runner.py
- app/agent/action_runtime.py
- app/agent/api_scope.py
- tests/test_agent_tooling.py
- tests/test_agent_golden_tasks.py

要求：
1. 不改变 API runner 对外行为。
2. 不改数据库、不改前端、不引入新依赖。
3. 优先抽纯函数模块：API result -> protocol observation/evidence 的 mapper；schema/case/action -> request candidates 的 builder。
4. 保持 append_api_result_observations 的行为和 payload key 不变。
5. 添加或移动测试，证明抽取前后 Golden Tasks 行为不变。
6. 不同时重构 httpx 执行循环和 evaluator。

完成后提交一次 commit，并在最终回复中说明抽出的模块、保持兼容的字段、测试结果和 commit hash。
```

### Task 5: 扩展 Golden Tasks 为 Failure Matrix

```text
你现在在 JeremyHaow/TestClaw 项目中开发。

目标：把现有 Golden Tasks 从 5 个代表用例扩展成 failure matrix，防止后续只修单个 case。

请先阅读：
- docs/AGENT_GAP_ANALYSIS_PROGRESS.md
- tests/test_agent_golden_tasks.py
- tests/test_agent_tooling.py
- app/agent/nodes/execution_evaluator.py
- app/agent/action_runtime.py

要求：
1. 不改业务逻辑，除非发现测试暴露真实 bug；如果需要改代码，先最小修复。
2. 增加同类变体，而不是只复制现有 case。
3. 至少覆盖：API 401/403 auth、network exception、timeout、5xx backend、schema assertion、safe write skip、path dependency missing、UI locator missing、UI assertion missing、navigation timeout、setup/captcha blocker、high-risk UI action blocked、memory known blocker hit。
4. 每个 golden case 都要断言 plan/action、observation、failure_type、evaluation next_action、report 的关键字段。
5. 测试必须使用 mock，不依赖真实外部服务。

完成后提交一次 commit，并在最终回复中说明新增矩阵项、测试结果和 commit hash。
```

## 15. Resume Positioning Update

当前项目可以在简历中这样描述，但不要夸大为完全 action-level runtime：

> 设计并落地 AI Testing Agent 的统一执行协议，将 API/UI 执行结果规范化为 ToolCall、Observation、Evidence 和 Evaluation，并接入 LangGraph 阶段级重规划、Run Detail 可观察界面、结构化 Memory/RAG 与 Golden Tasks 回归集。该改造把原本分散的 API Runner、Playwright CLI UI Runner、Evaluator 和 Memory 链路收敛到可审计、可评估、可逐步重规划的 Agent 运行模型，为后续 action-level runtime 和中心 Tool Executor 奠定基础。

后续如果完成中心 runtime executor、统一 failure taxonomy、UI structured action 主路径和 event persistence，再可以升级为：

> 构建 action-level AI Testing Agent Runtime，实现 Plan -> Action -> ToolCall -> Observation -> Evaluation -> Replan/Ask Human/Report 的可观察执行闭环。
