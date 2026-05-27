# TestClaw Agent Gap Analysis

## 1. Executive Summary

1. TestClaw 已经不是一个空壳测试平台：它具备 FastAPI/Celery 异步运行、LangGraph 工作流、OpenAPI 解析、API 安全执行、Playwright CLI UI 执行、RAG 检索、Run Detail/SSE 进度展示等基础能力。
2. 当前最大问题不是缺少功能，而是这些功能没有通过统一的 Agent Runtime 协议闭环起来。API Runner、UI Runner、Evaluator、Memory 都在写自己的结果结构，Run Detail 只能被动展示这些结构。
3. `app/agent/graph.py::build_graph()` 已经有 `execution_evaluator` 条件回路，但它仍是阶段级重试/重规划，不是以 Action -> Observation -> Evaluation -> Replan 为核心的通用 Agent 循环。
4. `app/agent/state.py::AgentState` 承载了输入、计划、用例、执行结果、工具调用、RAG、登录、鉴权、前端展示等大量字段，状态边界过宽，导致后续 Codex 容易在单点字段上打补丁。
5. `app/agent/action_runtime.py` 和 `app/agent/tool_registry.py` 是很有价值的起点，但目前统一 Action/Observation 只部分接入，API/UI 执行结果仍没有统一 Evidence/Evaluation 数据模型。
6. API 能力相对最成熟：`app/tools/doc_parser.py`、`app/agent/api_scope.py`、`app/agent/nodes/api_runner.py` 已覆盖 OpenAPI 解析、安全只读策略、请求构造、断言和失败分类；但 `api_runner.py` 过大，混合了选择、执行、断言、分类、重试和结果汇总。
7. UI 能力有明显潜力：`app/tools/playwright_tool.py` 和 `.codex/skills/playwright-cli/SKILL.md` 覆盖了浏览器命令能力，`ui_runner.py` 能记录命令、截图、snapshot；但当前核心接口仍是命令字符串，不是受限的结构化 UI Action。
8. RAG/Memory 已经参与规划，但更像上下文补充和历史摘要，还不是 Agent 的决策记忆。`knowledge_sink.py` 只在 `last_error` 存在时沉淀知识，无法稳定学习成功路径、失败类型、可复用用例和修复策略。
9. 前端已经有 Agent Cockpit 元素，但主路径仍混合大量配置项和后台资产页面。用户看到的是“配置一个测试任务”，而不是“委派一个测试智能体并观察它决策”。
10. 下一步不应该先大重构 UI、数据库或 RAG，而应该先建立统一执行协议，让 API/UI/Playwright CLI/Evaluator/Run Detail 都围绕同一套 Action、ToolCall、Observation、Evaluation、Evidence 记录运转。

## 2. Current Architecture Map

### Backend Entry and API Layer

- `app/main.py` 创建 FastAPI 应用，配置 CORS，SQLite 启动时 `Base.metadata.create_all`，并挂载 `app/api/router.py`。
- `app/api/router.py` 统一挂载 `auth`、`agent_plans`、`dashboard`、`tasks`、`test_cases`、`documents`、`providers`、`environments`、`api_tests`、`ui_tests`、`runs`、`visuals`、`webhooks`、`knowledge` 等路由。
- `app/api/v1/runs.py` 是当前 Run 业务中枢，文件约 4124 行，包含：
  - Run 创建：`create_run()`
  - Run 详情：`get_run_detail()`
  - 预检和目标记忆：`RunTargetMemory` 相关模型与辅助函数
  - 流式事件：调用 `app/services/run_stream_service.py::stream_run_events()`
  - 人工介入：`create_run_intervention()`
  - 运行取消、重跑、导出、case asset 保存等大量逻辑

### Async Task and Persistence

- `app/worker/tasks.py::run_agent_task` 是 Celery 任务入口。
- `app/worker/tasks.py::run_graph_with_progress()` 运行 LangGraph，并在每个节点更新后持久化状态快照。
- `app/services/run_stream_service.py::stream_run_events()` 通过轮询 `Task.execution_log` 和 `RunEvent` 生成 SSE。
- `app/models/task.py::Task` 保存任务状态和 `execution_log`，`app/models/task.py::TestRun` 保存测试运行记录。
- `app/models/run_artifacts.py` 已有 `RunIntervention`、`RunToolCall`、`RunEvidence`、`RunFinding`、`TargetMemory`、`Artifact` 等实体，但当前运行过程仍主要依赖 `execution_log` 中的大 JSON 快照。

### Agent Graph

`app/agent/graph.py::build_graph()` 当前节点如下：

- `input_classifier`：判断输入类型。
- `source_loader`：加载 URL/Swagger 文档并解析目标。
- `mission_planner`：生成 mission、角色、子目标、环境需求和 memory query。
- `knowledge_retriever`：检索 RAG/历史知识。
- `planner`：生成 API/UI 计划、策略和工具计划。
- `agent_supervisor`：补充/观察工具动作，决定进入 API 或 UI 分支。
- `tc_generator`：生成 API 或 UI 测试用例。
- `api_runner`：执行 API 请求、断言、分类和汇总。
- `ui_login`：执行 UI 登录/准备步骤。
- `ui_test_planner`：基于 snapshot 和探索生成 UI 用例与脚本。
- `ui_runner`：执行 Playwright CLI 命令、收集 UI 证据。
- `execution_evaluator`：评估 API/UI 执行结果，决定 report/replan/continue。
- `reporter`：生成最终报告。
- `knowledge_sink`：写入失败知识。

图的主线是：

`input_classifier -> source_loader -> mission_planner -> knowledge_retriever -> planner -> agent_supervisor -> tc_generator/ui_login -> api_runner/ui_runner -> execution_evaluator -> reporter -> knowledge_sink`

`execution_evaluator` 能通过条件边返回 `tc_generator`、`ui_test_planner`、`ui_login` 或进入 `reporter`。这说明项目已经有“有限重规划”能力，但还不是统一 Agent Runtime loop。

### State and Runtime Contracts

- `app/agent/state.py::AgentState` 是一个大的 `TypedDict`，覆盖任务 ID、输入、解析内容、计划、用例、API/UI 执行结果、工具注册表、mission、actions、observations、evaluations、UI 登录、鉴权、RAG、progress 等字段。
- `app/agent/action_runtime.py` 定义了：
  - `AgentActionDiagnostic`
  - `AgentAction`
  - `ValidatedAgentAction`
  - `AgentActionObservation`
  - `validate_agent_action_plan()`
  - `validate_and_record_agent_action_plan()`
  - `record_agent_action_observation()`
- `app/agent/tool_registry.py` 定义 `ToolCapability`、`AutomationSkill`、`TOOL_CAPABILITIES`、`AUTOMATION_SKILLS`、`select_skills_for_state()`，包括 API、UI、auth、memory、human、evidence evaluator 等工具声明。

### API Testing Stack

- `app/tools/doc_parser.py::parse_api_document_content()` 解析 OpenAPI v3/v2 和 Postman Collection。
- `app/tools/doc_parser.py::_normalize_openapi_document_v3()` 和 `_normalize_openapi_document_v2()` 提取 method、path、summary、parameters、request_body、responses、auth_required、example_request、required_fields。
- `app/agent/nodes/source_loader.py::run()` 调用文档解析，提取 `target_url`、`base_url_override`、`api_path_prefix_rewrite`。
- `app/agent/api_scope.py` 提供 schema 范围和安全策略，包含 `SAFE_API_METHODS`、`WRITE_API_METHODS`、`validate_generated_api_cases()`、`sanitize_api_case_assertions()`、`documented_api_scope_text()`。
- `app/agent/nodes/api_runner.py` 是核心 API 执行器，约 3921 行，包含安全写保护、请求生成、依赖参数提取、mock body、重试、httpx 请求、断言、失败分类和执行汇总。
- `app/agent/nodes/api_executor.py::run()` 和 `app/tools/api_tool.py::execute_api_test()` 是更早的简化 API 执行路径，应视为兼容/遗留能力。

### UI Testing Stack

- `.codex/skills/playwright-cli/SKILL.md` 描述了浏览器自动化命令能力，包括 `open`、`goto`、`click`、`fill`、`snapshot`、`screenshot`、`console`、`requests`、`tracing-start/stop`、`state-save/load`、`generate-locator` 等。
- `app/tools/playwright_tool.py` 提供两条执行路径：
  - `execute_playwright_test()`：写入 pytest 脚本并运行 `pytest`，用于 pytest-playwright 风格脚本。
  - `run_playwright_cli_command()`、`run_playwright_cli_stream()`、`run_playwright_cli_script()`：执行 playwright-cli 命令或命令序列。
- `app/tools/playwright_commands.py::normalize_playwright_commands()` 把原始命令字符串规范化，识别 unsupported、screenshot、assertion、viewport、run-code 等命令。
- `app/agent/nodes/ui_login.py::run()` 执行登录/准备命令、处理验证码和登录验证。
- `app/agent/nodes/ui_test_planner.py::run()` 使用 Playwright CLI snapshot 和页面探索生成 UI 用例与可复现脚本。
- `app/agent/nodes/ui_runner.py::run()` 执行 UI case batches，记录命令日志、snapshot、截图和 `ui_execution_result`。

### RAG / Memory / Knowledge

- `app/agent/nodes/knowledge_retriever.py::run()` 通过 `KnowledgeVectorStore` 检索知识，写入 `rag_context` 和 `rag_retrieval`。
- `planner.py`、`tc_generator.py` 会消费 `rag_context`，说明 RAG 已经参与规划和用例生成。
- `app/agent/nodes/knowledge_sink.py::run()` 在 `last_error` 存在时生成 bug report 并调用 `knowledge_service.create()` 存储知识。
- `app/models/knowledge.py::KnowledgeEntry`、`app/services/knowledge_service.py`、`app/services/vector_store.py` 支撑知识库和向量检索。
- `app/api/v1/runs.py` 中的 `RunTargetMemory` 系列模型和目标历史摘要用于预检和前端提示。

### Frontend Product Surface

- `frontend/src/router/index.ts` 将 `/` 和 `/dashboard` 重定向到 `/agent-plan`，说明当前主入口已倾向 Agent。
- `frontend/src/pages/AgentPlanPage.vue` 提供任务收集、计划草稿和问题卡片。
- `frontend/src/pages/RunPage.vue` 提供运行提交、预检、鉴权、验证码、Base URL、API 执行策略等配置，文件约 1556 行。
- `frontend/src/pages/RunDetailPage.vue` 是运行详情/Cockpit，文件约 2344 行，接入 SSE，展示 `AgentCurrentActionCard`、`AgentTimeline`、`AgentEvidenceCard`、API/UI tabs、截图、工具、用例、脚本、日志、RAG 和 intervention。
- `frontend/src/components/agent/AgentInterventionDrawer.vue` 支持补充上下文并触发 assisted rerun，但不是同一次运行中的暂停/继续控制。

## 3. What Already Works

1. **完整运行链路已存在。** `app/api/v1/runs.py::create_run()` 可以创建任务并派发 Celery；`app/worker/tasks.py::run_graph_with_progress()` 可以运行 LangGraph；`run_stream_service.py::stream_run_events()` 可以把状态推给前端。
2. **LangGraph 节点结构已经覆盖测试 Agent 的主要阶段。** `graph.py::build_graph()` 包含输入识别、source loading、mission planning、RAG、planning、supervision、case generation、API/UI execution、evaluation、reporting、knowledge sink。
3. **OpenAPI 解析是可保留资产。** `doc_parser.py` 能处理 OpenAPI v3/v2 和 Postman，并提取 endpoint、method、parameters、request body、responses、auth_required。
4. **API 安全边界已有基础。** `api_scope.py` 和 `api_runner.py` 都定义了 safe/read-only 与 write method 策略，默认策略是 `safe_read_only`，写接口会被 gate 保护。
5. **API Runner 具备真实执行能力。** `api_runner.py::_request_with_retry()` 使用 httpx 执行请求，`_evaluate_assertions()` 做断言，`_classify_api_failure()` 做失败分类，`run()` 汇总 `api_execution_result`。
6. **UI Runner 已能执行真实浏览器动作。** `playwright_tool.py::run_playwright_cli_command()` 调用 `playwright-cli`，`ui_runner.py::run()` 能执行命令并收集 screenshot/snapshot/command 结果。
7. **Playwright CLI skill 文档很完整。** `.codex/skills/playwright-cli/SKILL.md` 已明确命令空间、raw/json 输出、storage、network、console、requests、tracing、video、locator 生成等能力，适合作为 Skill 化输入。
8. **Agent 可观察性已有雏形。** `AgentState` 有 `tool_calls`、`agent_react_trace`、`agent_action_observations`、`agent_evaluations`；前端 Run Detail 已展示 timeline、current action、evidence、tools。
9. **Evaluator 已经能做有限重规划。** `execution_evaluator.py::_api_needs_replan()`、`_ui_needs_replan()`、`_guardrail_decision()`、`_model_decision()`、`_merge_decisions()`、`_apply_decision()` 可以决定 `replan_api`、`replan_ui`、`continue_to_ui` 或 `report`。
10. **测试基础不弱。** `tests/test_agent_tooling.py` 覆盖 tool registry、action runtime、supervisor、evaluator；`tests/test_runs_preflight.py` 覆盖预检和鉴权；`tests/test_doc_parser.py` 覆盖文档解析；还有多份前端 source regression 测试。

## 4. Main Gaps to a Real AI Testing Agent

### Agent Runtime Gap

当前 Graph 是“有条件回路的测试流水线”，不是完整 Agent Runtime。

- 证据：`app/agent/graph.py::build_graph()` 以固定节点顺序组织执行，只有 `agent_supervisor`、`tc_generator`、`ui_login`、`execution_evaluator` 后有条件边。
- 证据：`app/agent/nodes/agent_supervisor.py::_execute_action()` 对很多动作返回 `planned`、`blocked` 或 observation，而不是把所有 action 交给统一 executor 真执行。
- 证据：`execution_evaluator.py::route_after_evaluation()` 只在阶段边界选择下一个图节点，不是每个 tool/action 执行后都能重新观察、评估、规划。

缺口：

- 没有统一的 Agent loop：Plan -> Select Action -> Execute Tool -> Observe -> Evaluate -> Replan/Continue/Ask Human。
- 没有统一的 action budget、retry policy、failure taxonomy、human escalation policy。
- Planner、Supervisor、Runner、Evaluator 的职责存在重叠，后续修改容易在多个节点重复补逻辑。

### Tool Execution Gap

工具能力有声明和局部封装，但没有统一 Tool Call Runtime。

- 证据：`tool_registry.py::ToolCapability` 声明了 schema、timeout、retry、permission、redaction，但运行时并未统一通过一个 tool executor 调度。
- 证据：`action_runtime.py::ValidatedAgentAction` 只完成动作校验和记录，API/UI runner 仍直接读各自状态执行。
- 证据：`playwright_tool.py::run_playwright_cli_command()` 接受字符串命令；`ui_runner.py` 也围绕 command string 和 normalization 运转。

缺口：

- API request、UI click/fill/snapshot、RAG retrieve、human.ask、evaluator 都应该输出统一 `ToolCall` 和 `Observation`。
- Playwright CLI 当前更像工具函数，不像可审计、可限制、可重试、可回放的 Agent Skill。
- pytest-playwright 与 playwright-cli 的边界不够清晰：前者适合稳定回归脚本，后者适合交互探索和短动作执行。

### State / Data Model Gap

`AgentState` 过大且字段横跨多个层次。

- 证据：`app/agent/state.py::AgentState` 同时包含 `api_plan`、`ui_plan`、`test_plan`、`api_cases`、`ui_cases`、`api_execution_result`、`ui_execution_result`、`tool_calls`、`agent_actions`、`agent_action_observations`、`evidence_evaluation`、`rag_context`、`auth_config` 等。
- 证据：`api_execution_result`、`ui_execution_result`、`execution_result` 三套结果并存，兼容性好，但也说明协议没有收敛。
- 证据：`app/models/run_artifacts.py::RunToolCall`、`RunEvidence`、`RunFinding` 已存在，但运行主链仍更多写入 `Task.execution_log`。

缺口：

- 缺少稳定的 `Action`、`ToolCall`、`Observation`、`Evaluation`、`Evidence`、`Artifact` 领域对象边界。
- 缺少跨 API/UI 统一的 request/response/assertion/error 分类字段。
- Run Detail 读取大 JSON 快照，不是读取一条条可回放的 Agent event。

### Evaluation Gap

Evaluator 已存在，但还不是“证据充分性和下一步动作”的事实来源。

- 证据：`execution_evaluator.py::_guardrail_decision()` 和 `_model_decision()` 合并规则可以决定是否 replan。
- 证据：`_api_needs_replan()`、`_ui_needs_replan()` 主要读取 `api_execution_result` 和 `ui_execution_result` 的摘要，而不是读取统一 Evidence graph。
- 证据：`_apply_decision()` 写入 `agent_next_node`、`agent_replan_counts`、`agent_replan_feedback`，说明重规划是阶段级控制。

缺口：

- 没有对“证据是否足够支撑结论”形成强结构化输出，例如 evidence coverage、blocking reason、confidence、next action、required observation。
- 失败类型分散在 API runner、UI runner、reporter 和 evaluator 中。
- Human-in-the-loop 主要是 assisted rerun，不是运行中 pause/ask/resume。

### UI/UX Gap

前端已有 Agent Cockpit 元素，但用户路径仍偏平台配置。

- 证据：`frontend/src/router/index.ts` 已将默认入口指向 `/agent-plan`。
- 证据：`frontend/src/pages/RunPage.vue` 暴露 auth mode、captcha mode、base_url、API policy、advanced auth inputs、preflight 等大量配置项。
- 证据：`frontend/src/pages/RunDetailPage.vue` 接入 SSE、timeline、current action、evidence、API/UI/screenshot/tool/log tabs，但展示面仍依赖 runner 输出的原始结果结构。
- 证据：`AgentInterventionDrawer.vue` 对应 `runs.py::create_run_intervention()`，当前更像“补充信息并重跑”，不是同一运行中的实时介入。

缺口：

- 主路径需要更明确地表达：输入目标 -> Agent 生成计划 -> 用户确认/补充 -> 执行 -> 观察 -> 评估 -> 报告 -> 记忆。
- Run Detail 应显示 Agent 为什么这么做、观察到了什么、证据是否足够、下一步选择，而不只是日志和结果 tab。
- 配置项需要分层：常用委派入口保留少量输入，高级鉴权/安全策略移动到可展开设置或资产配置。

### Memory / RAG Gap

RAG 已参与规划，但学习闭环不稳定。

- 证据：`knowledge_retriever.py::run()` 将检索结果写入 `rag_context`，`planner.py` 和 `tc_generator.py` 消费该上下文。
- 证据：`knowledge_sink.py::run()` 只有在 `state.get("last_error")` 存在时才写入失败知识。
- 证据：`app/api/v1/runs.py::RunTargetMemory` 系列模型提供历史摘要，但主要服务预检和前端提示。

缺口：

- Memory 还不是 Agent 决策上下文。历史 run、失败原因、成功登录方式、稳定用例、常见环境 blocker 没有统一沉淀成 planner 可选择的结构化事实。
- Knowledge sink 太依赖 `last_error`，无法沉淀“本次成功使用了什么策略”“哪些 endpoint 被安全跳过”“哪些 UI locator 失效”等学习材料。
- 现阶段不宜先做复杂 RAG，应先让执行协议和证据链稳定，否则 RAG 只会检索到不稳定日志。

### Testing / Regression Gap

现有测试覆盖功能点较多，但缺少 Agent 能力回归。

- 证据：`tests/test_agent_tooling.py` 覆盖 action runtime、tool registry、evaluator 决策等局部能力。
- 证据：`tests/test_runs_preflight.py` 对鉴权预检覆盖很细。
- 证据：存在 `tests/test_*_frontend_source.py` 这类 source-level 前端回归。

缺口：

- 缺少 Golden Tasks：固定输入、固定 mock OpenAPI/UI 页面、固定期望 Action/Observation/Evaluation 序列。
- 缺少跨阶段回归：计划是否引用 schema、执行是否记录证据、失败是否分类、Evaluator 是否选择合理 next action。
- 缺少“不能只修一个 case”的测试约束，例如同一失败类型的多种 OpenAPI 形态、多种 UI locator 形态、多种 auth failure 形态。

## 5. Root Cause Analysis

现在“修好一个问题，换一个问题还是不会”的根因不是模型单独不够强，而是系统给模型的闭环反馈不够稳定。

1. **缺少统一协议，导致经验无法迁移。** 一个 API case 的修复通常落在 `api_runner.py` 某个分支；一个 UI case 的修复通常落在 `ui_runner.py` 或 `ui_test_planner.py` 的命令处理。它们没有共同的 Action/Observation/Evaluation contract，所以模式不能迁移。
2. **失败分类不在中心位置。** API failure、UI failure、auth blocker、environment blocker、assertion failure、evidence insufficient 分散在不同文件中，Evaluator 无法基于统一 taxonomy 学习下一步。
3. **Runner 过度承担决策。** `api_runner.py` 同时做请求选择、数据依赖、mock body、执行、断言、重试和分类；`ui_runner.py` 同时做命令规范化、语义定位、执行、截图和汇总。Runner 做太多会让 Planner/Evaluator 无法清楚知道“哪里失败、为什么失败、下一步缺什么”。
4. **Graph 的回路粒度太粗。** 当前回路发生在 API/UI 阶段结束后，不是在每个 action 后。模型不能及时用 observation 纠正下一步，只能等一个阶段失败后再重规划。
5. **Memory 存的是结果，不是决策知识。** `knowledge_sink.py` 主要在 `last_error` 存在时存储失败知识，缺少结构化的“可复用策略”和“失败 -> 诊断 -> 修复动作”映射。
6. **前端展示了很多数据，但没有反过来规范后端协议。** Run Detail 已有 timeline/evidence/tools，但没有强制后端所有能力都以统一 event/evidence 输出。

## 6. Target Architecture Proposal

目标不是引入更多名词，而是把现有模块收敛到清晰职责。

### Planner

Planner 应负责：

- 读取用户目标、source schema、preflight、target memory、rag_context。
- 生成高层测试目标和可执行计划，不直接生成任意脚本。
- 输出结构化 `ActionPlan`，每一步包含 `action_type`、`tool_name`、`target`、`inputs`、`expected_observation`、`success_criteria`、`risk`、`budget`。
- 明确哪些信息缺失，需要 `human.ask`，而不是让 runner 在执行时临时猜。

Planner 不应负责：

- httpx 请求细节执行。
- Playwright CLI 字符串拼接细节。
- 最终失败分类。
- 数据库持久化。

建议落点：

- 保留 `app/agent/nodes/planner.py::run()`，但逐步让它只输出统一计划协议。
- 保留 `mission_planner.py::run()` 作为 mission artifact 生成器。
- 保留 `action_runtime.py::validate_and_record_agent_action_plan()`，扩展为计划协议校验入口。

### API Runner

API Runner 应负责：

- 接收结构化 API Action，例如 `api.request`、`api.assert_schema`、`api.extract_dependency`。
- 只执行 OpenAPI schema 范围内、符合安全策略的请求。
- 记录标准 `ToolCall`、`Observation`、`Evidence`，包括 method、url、request headers/body redaction、response status/body summary、duration、assertions、error_type。
- 对鉴权失败、网络错误、超时、断言失败、schema mismatch、safe write blocked、dependency missing 做标准分类。

API Runner 不应负责：

- 高层测试目标规划。
- UI 后续动作选择。
- Memory 写入。
- 前端展示格式。

建议落点：

- 保留 `api_runner.py` 的核心能力，但拆出 adapter 层，先让 `run()` 同时输出旧 `api_execution_result` 和新 protocol observations。
- 暂时不要删除 `api_executor.py`，先标记为 legacy path，避免破坏旧测试。

### UI Runner

UI Runner 应负责：

- 接收结构化 UI Action，例如 `ui.open`、`ui.click_ref`、`ui.fill_ref`、`ui.snapshot`、`ui.screenshot`、`ui.assert_visible`。
- 将结构化 action 编译为 Playwright CLI 命令。
- 执行命令并记录 snapshot、screenshot、console、network、trace、locator resolution、command stdout/stderr。
- 对 locator missing、timeout、navigation blocked、auth required、captcha required、assertion missing、environment unavailable 做标准分类。

UI Runner 不应负责：

- 让 LLM 任意生成 `run-code`。
- 把所有 UI 探索、登录、用例生成、执行都混在一个函数里。
- 直接决定最终报告结论。

建议落点：

- 保留 `ui_runner.py` 的执行和证据能力。
- 把命令字符串作为底层 transport，而不是 Agent action 的主协议。

### Playwright CLI

Playwright CLI 应负责：

- 浏览器会话控制、页面交互、snapshot/screenshot/console/network/trace 等原子能力。
- 作为受限 Skill 被 Agent Runtime 调用。
- 返回机器可解析 observation，至少包含 command、status_code、stdout/stderr summary、artifact paths、snapshot text、error_type。

Playwright CLI 不应负责：

- 高层测试策略。
- 自由执行任意脚本作为默认路径。
- 隐式吞掉失败原因。

建议落点：

- 以 `.codex/skills/playwright-cli/SKILL.md` 为能力目录，建立 `ui.playwright_cli` skill adapter。
- `app/tools/playwright_commands.py::normalize_playwright_commands()` 可继续作为命令规范化层。

### pytest-playwright

pytest-playwright 应负责：

- 对已经稳定的场景生成可维护回归脚本。
- 运行离线/CI regression，不作为探索阶段的主执行器。
- 产出 trace、video、screenshot 等可用于报告的 artifact。

pytest-playwright 不应负责：

- 实时 Agent 探索和短循环观察。
- 接收未约束的 LLM 任意脚本作为默认执行路径。

建议落点：

- 保留 `playwright_tool.py::execute_playwright_test()`，但将其定位为“回归脚本执行/导出验证”，不是 primary agent executor。

### Evaluator

Evaluator 应负责：

- 读取统一 Observations/Evidence。
- 判断证据是否充分、失败类型、责任归因、可重试性、下一步动作。
- 输出结构化 `Evaluation`：
  - `sufficient_evidence`
  - `outcome`
  - `failure_type`
  - `confidence`
  - `next_action`
  - `missing_evidence`
  - `replan_hint`
  - `human_question`
  - `memory_candidate`
- 维护 retry/replan budget，并避免无限循环。

Evaluator 不应负责：

- 直接构造 API 请求。
- 直接生成 Playwright 命令。
- 只基于 summary 字段做结论。

建议落点：

- 保留 `execution_evaluator.py` 的 guardrail + LLM 双层结构。
- 将 `_api_needs_replan()`、`_ui_needs_replan()` 从读取 runner summary，逐步改为读取统一 observations。

### Memory

Memory 应负责：

- 存储结构化的目标级知识：成功策略、失败类型、环境 blocker、auth 方法、稳定 endpoint/use case、UI locator 修复记录。
- 给 Planner 提供可引用、可验证、带置信度的上下文。
- 给 Evaluator 提供历史失败模式和建议下一步。

Memory 不应负责：

- 在执行协议稳定前承担主要智能来源。
- 只存自然语言 bug report。
- 存储未脱敏 secret。

建议落点：

- 保留 `knowledge_retriever.py` 和 `knowledge_sink.py`。
- 扩展 `knowledge_sink.py` 的输入，从 `last_error` 改为 evaluation/memory_candidate。

### Frontend Run Detail

Run Detail 应展示：

- 当前目标和计划：Agent 要验证什么。
- 当前 action：为什么执行这个 action，调用哪个 tool，预期观察是什么。
- Observation：实际看到了什么，包含 API response、UI snapshot、截图、console/network、错误。
- Evaluation：证据是否充分，失败类型是什么，下一步为什么是 continue/retry/replan/ask human/report。
- Human-in-the-loop：当 Agent 缺少凭据、验证码、环境确认或安全授权时，可以暂停并等待用户补充。
- Final report：报告应该从 evidence/evaluation 派生，而不是从 API/UI summary 拼接。

建议落点：

- 保留 `RunDetailPage.vue`、`AgentCurrentActionCard.vue`、`AgentTimeline.vue`、`AgentEvidenceCard.vue`。
- 后端先输出统一事件，再让现有组件读取新结构，不要先重做页面。

## 7. Recommended Refactor Roadmap

### Phase 0: 先做诊断和文档

目标：

- 完成本文件。
- 明确现有能力、差距、模块保留/重构边界。
- 后续所有 Codex 任务必须围绕统一协议迭代，避免继续修单点 case。

产出：

- `docs/AGENT_GAP_ANALYSIS.md`
- 后续 task prompts

### Phase 1: 统一 Action / Observation / Evaluation 执行协议

目标：

- 新增或整理统一协议，不大改 runner。
- 定义 `AgentAction`、`ToolCall`、`Observation`、`Evaluation`、`Evidence` 的稳定字段。
- API/UI runner 继续输出旧结构，同时追加 protocol-compatible records。

建议范围：

- 优先复用 `app/agent/action_runtime.py::AgentAction` 和 `AgentActionObservation`。
- 不先改数据库 schema；先写入 `AgentState` 和 `execution_log`。
- 给协议 mapping 加测试，覆盖 API success/failure、UI success/failure、human ask、RAG retrieve。

验收：

- 一个 API run 和一个 UI run 都能在 `agent_action_observations` 或新字段中看到统一 observation。
- `execution_evaluator` 可以读取统一 observation 的最小字段。

### Phase 2: Playwright CLI Skill 化

目标：

- 把 Playwright CLI 从“字符串工具函数”升级为受限 Skill adapter。
- 定义结构化 UI action schema：`open`、`goto`、`click_ref`、`fill_ref`、`snapshot`、`screenshot`、`assert_visible`、`wait_for`。
- 明确哪些命令默认禁用或需要高风险标记，例如任意 `run-code`。

建议范围：

- 保留 `app/tools/playwright_tool.py::run_playwright_cli_command()` 作为底层 transport。
- 保留 `app/tools/playwright_commands.py::normalize_playwright_commands()` 作为兼容层。
- 在 `ui_runner.py` 外包一层 adapter，把 structured action 编译为命令字符串。

验收：

- UI Runner 能记录每个结构化 action 对应的 command、snapshot、screenshot、error_type。
- Run Detail 能区分“Agent action”和“底层 CLI command”。

### Phase 3: API 执行结构化

目标：

- 将 `api_runner.py` 的执行结果映射到统一 Observation/Evidence。
- 标准化 API failure taxonomy。
- 明确 `safe_read_only`、`safe_with_auth`、`write_allowed` 的行为和跳过原因。

建议范围：

- 不直接拆 `api_runner.py` 大文件。
- 先新增内部 mapper 函数，例如把 `results[]` 转成 `Observation[]`。
- 保持 `api_execution_result` 向后兼容。

验收：

- 每个 API request 都有 method、path、url、status、duration、assertion results、failure_type、evidence refs。
- 鉴权失败、网络错误、超时、断言失败、safe write blocked 都有标准分类。

### Phase 4: execution_evaluator 真正评估证据和失败类型

目标：

- Evaluator 从读取 summary 改为读取统一 observations。
- 输出结构化 Evaluation。
- 支持明确 next action：continue、retry_same_action、replan_api、replan_ui、ask_human、report。

建议范围：

- 保留当前 guardrail + LLM merge 设计。
- 先把 `_api_needs_replan()`、`_ui_needs_replan()` 改造成基于 Observation 的判断。
- 加 replan budget 和 missing evidence 字段。

验收：

- 失败不是只写 `last_error`，而是有 failure_type、confidence、missing_evidence、replan_hint。
- 同一失败类型在 API/UI 不同 case 中行为一致。

### Phase 5: Run Detail 页面接入执行过程

目标：

- 让 Run Detail 按统一 event/evidence 展示 Agent 正在做什么、为什么做、看到了什么、下一步是什么。
- 降低 raw logs 和 runner-specific tabs 的主路径权重。

建议范围：

- 保留 `RunDetailPage.vue` 现有 tab。
- 优先改数据适配层和 agent 组件，不重做整体 UI。
- 将 `AgentTimeline` 和 `AgentEvidenceCard` 的数据源切换到统一 protocol records。

验收：

- 用户能从页面主区域看懂 Action -> Observation -> Evaluation。
- API/UI raw result 仍可作为详情展开。

### Phase 6: Memory / RAG 再接入计划生成

目标：

- 在协议稳定后，把历史执行经验沉淀成结构化 memory。
- Planner 读取 memory 时能引用具体历史 evidence/evaluation，而不是只吃自然语言段落。

建议范围：

- 扩展 `knowledge_sink.py`，从 evaluation 中生成 memory candidate。
- `knowledge_retriever.py` 返回结构化 facts + snippets。
- `planner.py` 只使用高置信度、目标相关的 memory。

验收：

- 重复目标运行时，Planner 能显式引用历史成功策略或已知 blocker。
- Memory 不泄漏 secret。

### Phase 7: Golden Tasks 回归测试

目标：

- 防止后续 Codex 只修一个 case。
- 为 Agent 能力建立固定回归集。

建议范围：

- 增加 mock OpenAPI 文档、mock API 服务、mock UI 页面。
- 每个 Golden Task 验证计划、action、observation、evaluation、report 的关键字段。
- 覆盖多形态 auth failure、safe write skip、path param dependency、UI locator missing、captcha blocker、RAG memory hit。

验收：

- 新增或修改 runner/evaluator 时必须通过 Golden Tasks。
- 每个 bug fix 都补一个同类变体，而不是只补当前输入。

## 8. What Not To Do Yet

1. 不要先重做 UI。Run Detail 和 AgentPlan 已有基础，当前瓶颈在后端协议和事件模型。
2. 不要先加复杂 RAG。没有稳定 observation/evaluation，RAG 只会检索不稳定日志。
3. 不要让 LLM 直接生成任意 Playwright/Python 脚本作为默认执行方式。应优先使用结构化 action 和受限 skill。
4. 不要先大规模改数据库。先在 `execution_log` 中输出协议结构，确认稳定后再考虑事件表/证据表迁移。
5. 不要继续针对单个 case 写特殊判断。每个修复都应回到 failure taxonomy、action schema 或 evaluator 规则。
6. 不要立即拆分 `api_runner.py` 和 `runs.py`。先建立 adapter 和测试，再按职责拆分，降低回归风险。
7. 不要删除 legacy 字段，例如 `test_plan`、`execution_result`、`api_executor.py`。先兼容输出，后续再收敛。
8. 不要把所有 Human-in-the-loop 都做成运行中复杂暂停。第一步先定义 `ask_human` action/evaluation，再让前端逐步接入。
9. 不要把 Playwright CLI 和 pytest-playwright 混成一个概念。CLI 用于探索和短动作，pytest-playwright 用于稳定回归。

## 9. First 3 Codex Tasks

### Task 1: 建立统一执行协议

```text
你现在在 JeremyHaow/TestClaw 项目中开发。

目标：建立统一 Agent 执行协议，但不要重构业务 runner。

请先阅读：
- docs/AGENT_GAP_ANALYSIS.md
- app/agent/state.py
- app/agent/action_runtime.py
- app/agent/tool_registry.py
- app/agent/nodes/api_runner.py
- app/agent/nodes/ui_runner.py
- app/agent/nodes/execution_evaluator.py
- tests/test_agent_tooling.py

要求：
1. 复用并扩展现有 app/agent/action_runtime.py，不要引入新依赖。
2. 定义统一协议字段：AgentAction、ToolCall、Observation、Evaluation、Evidence。
3. 不要改数据库迁移，不要改前端页面。
4. API/UI runner 旧输出必须保持兼容。
5. 先添加最小 adapter/mapping，让 API/UI 执行后都能追加 protocol-compatible observation。
6. 添加 focused tests，覆盖 API success/failure 和 UI command success/failure 的 observation mapping。
7. 不要大规模拆分 api_runner.py 或 ui_runner.py。

完成后提交一次 commit，并在最终回复中说明协议字段、修改文件、测试结果和 commit hash。
```

### Task 2: Playwright CLI Skill 化

```text
你现在在 JeremyHaow/TestClaw 项目中开发。

目标：把 Playwright CLI 从命令字符串工具函数升级为 Agent Skill adapter。

请先阅读：
- docs/AGENT_GAP_ANALYSIS.md
- .codex/skills/playwright-cli/SKILL.md
- app/tools/playwright_tool.py
- app/tools/playwright_commands.py
- app/agent/nodes/ui_runner.py
- app/agent/nodes/ui_test_planner.py
- app/agent/action_runtime.py
- tests/test_agent_tooling.py

要求：
1. 不要重做 UI 页面，不要改数据库迁移，不要引入新依赖。
2. 定义结构化 UI action schema：open/goto/click_ref/fill_ref/snapshot/screenshot/assert_visible/wait_for。
3. 保留 run_playwright_cli_command 作为底层 transport。
4. 新增 adapter 将结构化 UI action 编译为受限 playwright-cli command。
5. 默认限制任意 run-code；如必须保留，标记为 high_risk 并记录 reason。
6. UI Runner 继续兼容旧 command string，但新 observation 里必须区分 agent action 和 cli command。
7. 添加测试覆盖 command 编译、unsupported command、安全限制、observation 输出。

完成后提交一次 commit，并在最终回复中说明 Skill adapter 边界、测试结果和 commit hash。
```

### Task 3: API Runner 结构化执行

```text
你现在在 JeremyHaow/TestClaw 项目中开发。

目标：让 API Runner 输出统一结构化 Observation/Evidence，同时保持现有 api_execution_result 兼容。

请先阅读：
- docs/AGENT_GAP_ANALYSIS.md
- app/tools/doc_parser.py
- app/agent/api_scope.py
- app/agent/nodes/api_runner.py
- app/agent/action_runtime.py
- app/agent/nodes/execution_evaluator.py
- tests/test_agent_tooling.py
- tests/test_doc_parser.py
- tests/test_runs_preflight.py

要求：
1. 不要大规模拆分 api_runner.py。
2. 不要改数据库迁移，不要改前端页面，不要引入新依赖。
3. 为每个 API request 生成统一 observation：method/path/url/status/duration/assertions/error_type/failure_type/safety_decision/evidence_refs。
4. 标准化 failure taxonomy：auth_failure/network_error/timeout/assertion_failure/schema_contract/backend_error/safe_write_blocked/dependency_missing/environment_blocked。
5. safe_read_only/safe_with_auth/write_allowed 行为必须保持兼容。
6. execution_evaluator 可以读取新 observation 的最小字段，但旧 summary 路径仍保留。
7. 添加测试覆盖只读成功、写接口跳过、鉴权失败、断言失败、网络异常。

完成后提交一次 commit，并在最终回复中说明兼容策略、测试结果和 commit hash。
```

## 10. Evidence From Code

### Agent Graph and Runtime

- `app/agent/graph.py::build_graph()` 定义了完整 LangGraph 节点和条件边，是当前 agent workflow 的入口。
- `app/agent/graph.py::_after_execution_evaluator()` 调用 `execution_evaluator.route_after_evaluation()`，说明重规划发生在 evaluator 阶段边界。
- `app/agent/state.py::AgentState` 同时包含 input、plan、case、execution result、tool、mission、action、evaluation、auth、RAG、progress 字段，说明状态承载过多层次。
- `app/agent/action_runtime.py::AgentAction`、`ValidatedAgentAction`、`AgentActionObservation` 是统一协议的起点。
- `app/agent/action_runtime.py::validate_agent_action_plan()` 和 `validate_and_record_agent_action_plan()` 已能校验并记录模型生成动作。
- `app/agent/action_runtime.py::_validate_api_action()` 校验 API action 是否符合 schema 和策略。
- `app/agent/action_runtime.py::_validate_ui_action()` 当前主要校验 `ui.playwright_cli` 命令字符串，说明 UI action 仍未结构化。
- `app/agent/tool_registry.py::ToolCapability` 和 `AutomationSkill` 已声明工具能力、schema、权限、重试、脱敏策略。
- `app/agent/tool_registry.py::select_skills_for_state()` 已根据 state 选择技能计划，是 Skill 化的基础。
- `app/agent/nodes/agent_supervisor.py::_execute_action()` 当前更偏观察/规划状态，不是统一 executor。

### Planning and Case Generation

- `app/agent/nodes/mission_planner.py::run()` 生成 mission plan、roster、delegation trace、memory queries 和 success criteria，适合保留。
- `app/agent/nodes/planner.py::run()` 读取 schema、auth preflight、RAG、strategy 并生成计划，但职责偏宽。
- `app/agent/nodes/tc_generator.py::run()` 生成 API/UI cases，并有 fallback cases，适合作为 case generation 层保留。

### API Capability

- `app/tools/doc_parser.py::parse_api_document_content()` 是 OpenAPI/Postman 解析入口。
- `app/tools/doc_parser.py::_extract_parameters()` 提取 query/path/header/cookie 参数。
- `app/tools/doc_parser.py::_extract_request_body_v3()` 和 `_extract_request_body_v2()` 提取 request body。
- `app/tools/doc_parser.py::_has_auth()` 根据 operation/global security 判断鉴权要求。
- `app/tools/doc_parser.py::_normalize_openapi_document_v3()` 和 `_normalize_openapi_document_v2()` 统一 endpoint 结构。
- `app/agent/nodes/source_loader.py::_extract_document_base_url()` 从 OpenAPI servers、Swagger host/basePath 提取 base URL。
- `app/agent/nodes/source_loader.py::run()` 调用 `parse_api_document_content()`，设置 `parsed_api_schema`、`target_url`、`api_path_prefix_rewrite`。
- `app/agent/api_scope.py::SAFE_API_METHODS`、`WRITE_API_METHODS`、`validate_generated_api_cases()`、`sanitize_api_case_assertions()` 是安全执行和断言约束基础。
- `app/agent/nodes/api_runner.py::SAFE_API_METHODS`、`WRITE_API_METHODS`、`API_EXECUTION_POLICIES` 定义执行策略。
- `app/agent/nodes/api_runner.py::_safe_write_skip_reason()` 决定写接口是否跳过。
- `app/agent/nodes/api_runner.py::_build_test_requests()` 从 schema/cases 构造请求候选。
- `app/agent/nodes/api_runner.py::_request_with_retry()` 使用 httpx 执行请求并重试。
- `app/agent/nodes/api_runner.py::_evaluate_assertions()` 执行断言。
- `app/agent/nodes/api_runner.py::_classify_api_failure()` 分类 API 失败。
- `app/agent/nodes/api_runner.py::_update_api_execution_state()` 持续更新 API 执行状态。
- `app/agent/nodes/api_runner.py::run()` 是 API 执行主入口，但文件约 3921 行，职责过大。
- `app/agent/nodes/api_executor.py::run()` 与 `app/tools/api_tool.py::execute_api_test()` 是简化/遗留执行路径。

### UI Capability

- `.codex/skills/playwright-cli/SKILL.md` 定义 Playwright CLI 命令空间和 raw/json 输出能力。
- `app/tools/playwright_tool.py::execute_playwright_test()` 运行 pytest 脚本，适合稳定回归。
- `app/tools/playwright_tool.py::run_playwright_cli_command()` 执行单个 CLI 命令。
- `app/tools/playwright_tool.py::run_playwright_cli_stream()` 和 `run_playwright_cli_script()` 执行命令序列。
- `app/tools/playwright_commands.py::normalize_playwright_command()` 和 `normalize_playwright_commands()` 对命令字符串做规范化。
- `app/agent/nodes/ui_login.py::run()` 执行 setup/login 并验证登录状态。
- `app/agent/nodes/ui_test_planner.py::_explore_after_login()` 使用 CLI 探索页面。
- `app/agent/nodes/ui_test_planner.py::run()` 生成 UI cases 和可复现脚本。
- `app/agent/nodes/ui_runner.py::run()` 执行 UI case batches，文件约 1373 行，承担命令执行、截图、snapshot、结果汇总等职责。
- `app/agent/nodes/ui_runner.py` 多处直接调用 `run_playwright_cli_command()`，说明 CLI 是底层执行 transport。

### Evaluation and Reporting

- `app/agent/nodes/execution_evaluator.py::_api_needs_replan()` 和 `_ui_needs_replan()` 根据执行摘要判断是否重规划。
- `app/agent/nodes/execution_evaluator.py::_guardrail_decision()` 提供 deterministic gate。
- `app/agent/nodes/execution_evaluator.py::_model_decision()` 调用模型评估证据。
- `app/agent/nodes/execution_evaluator.py::_merge_decisions()` 合并 guardrail 和模型结果。
- `app/agent/nodes/execution_evaluator.py::_apply_decision()` 写入 next node、replan count 和 feedback。
- `app/agent/nodes/execution_evaluator.py::run()` 是 evaluation 主入口。
- `app/agent/nodes/reporter.py::run()` 生成 final report，`_collect_failure_details()`、`_build_bug_findings()`、`_build_recommendations()` 从 API/UI 结果拼接报告。

### Memory / RAG

- `app/agent/nodes/knowledge_retriever.py::run()` 调用 vector store，写入 `rag_context` 和 `rag_retrieval`。
- `knowledge_retriever.py` 在向量不可用时有 lexical fallback，说明 RAG 是 best-effort。
- `app/agent/nodes/knowledge_sink.py::run()` 只有 `last_error` 存在才生成 bug report 和 knowledge，学习入口偏窄。
- `app/services/vector_store.py`、`app/services/knowledge_service.py`、`app/models/knowledge.py::KnowledgeEntry` 是知识库基础。
- `app/api/v1/runs.py::RunTargetMemory` 系列模型用于目标历史摘要和预检提示。

### Frontend and Product

- `frontend/src/router/index.ts` 将 `/` 和 `/dashboard` 重定向到 `/agent-plan`。
- `frontend/src/pages/AgentPlanPage.vue` 提供 Agent 计划入口、问题卡、计划草稿和 chat input。
- `frontend/src/pages/RunPage.vue` 暴露 preflight、auth mode、captcha、base_url、policy、高级鉴权输入等运行配置，文件约 1556 行。
- `frontend/src/pages/RunDetailPage.vue` 接入 EventSource，展示 `api_execution_result`、`ui_execution_result`、`tool_calls`、`rag_context`、intervention、tabs，文件约 2344 行。
- `frontend/src/components/agent/AgentCurrentActionCard.vue`、`AgentTimeline.vue`、`AgentEvidenceCard.vue` 是 Agent Cockpit 的基础组件。
- `frontend/src/components/agent/AgentInterventionDrawer.vue` 支持补充信息，但后端对应 `runs.py::create_run_intervention()`，当前主要是 assisted rerun。

### Engineering Quality

- `app/api/v1/runs.py` 约 4124 行，API 层承担大量业务逻辑。
- `app/agent/nodes/api_runner.py` 约 3921 行，Runner 承担过多职责。
- `app/agent/nodes/ui_runner.py` 约 1373 行，UI 执行和证据逻辑集中。
- `app/agent/nodes/ui_test_planner.py` 约 952 行，混合页面探索、case 生成、脚本生成。
- `tests/test_agent_tooling.py` 已覆盖 tool registry、action runtime、evaluator 等 Agent 基础，但缺少端到端 Golden Tasks。
- `tests/test_runs_preflight.py` 说明预检/鉴权能力已被较细地测试。
- `tests/test_run_detail_frontend_source.py`、`tests/test_run_page_frontend_source.py` 等 source regression 测试说明前端关键文案/结构已有一定保护。

## 11. Risk List

1. **兼容性风险：** 旧前端和 tests 依赖 `api_execution_result`、`ui_execution_result`、`execution_log`。规避方式：Phase 1-5 只追加新协议输出，不删除旧字段。
2. **大文件拆分风险：** `api_runner.py` 和 `runs.py` 太大，直接拆容易破坏隐含行为。规避方式：先写 adapter 和 tests，再逐步移动纯函数。
3. **LLM 不稳定风险：** 如果继续让 LLM 直接生成脚本/命令，会产生不可控执行。规避方式：结构化 action schema + tool registry 校验 + high_risk 标记。
4. **安全风险：** 写接口、凭据、验证码、cookie、token、storage state 都可能泄漏或造成副作用。规避方式：保留 safe_read_only 默认策略，所有 observation 做 redaction。
5. **证据膨胀风险：** screenshot、trace、stdout、response body 可能让 `execution_log` 过大。规避方式：artifact 存路径/摘要，body 做截断，重要证据引用 artifact id。
6. **Evaluator 误判风险：** LLM evaluator 可能把环境问题误判成产品 bug。规避方式：guardrail 先行，failure taxonomy 明确 environment_blocked/auth_failure/network_error。
7. **Memory 污染风险：** 如果把低质量失败日志写入知识库，后续规划会变差。规避方式：只有 structured evaluation 且 confidence 达标才生成 memory candidate。
8. **前端复杂度风险：** Run Detail 已经很大，直接重做会引入视觉和状态回归。规避方式：先改数据协议和小组件适配。
9. **测试成本风险：** Golden Tasks 如果过早覆盖真实外部服务会不稳定。规避方式：使用 mock OpenAPI、mock API server、mock UI page。
10. **Human-in-the-loop 范围风险：** 运行中 pause/resume 涉及 worker、SSE、DB、UI 状态机。规避方式：先定义 `ask_human` action 和 evaluation，再实现暂停机制。

## 12. Resume Positioning

如果按上述方向改好，简历上应突出“真实工程设计”和“可观测 Agent Runtime”，而不是堆技术名词。

推荐描述：

> 设计并实现 AI Testing Agent Runtime，将 API/UI 自动化能力统一到 Action -> ToolCall -> Observation -> Evaluation 的执行协议中；基于 FastAPI、LangGraph、Celery、Playwright CLI、httpx 和 PostgreSQL 构建可重规划、可观测、可回放的测试智能体。系统支持 OpenAPI 安全只读执行、UI 浏览器探索、证据链采集、失败类型分类、Human-in-the-loop 和历史记忆检索，并通过 Golden Tasks 回归集约束 Agent 行为，避免只针对单个 case 打补丁。

更具体的项目亮点：

- 不是“接了 LangGraph”，而是实现阶段级到 action 级的执行闭环。
- 不是“会调用 Playwright”，而是把 Playwright CLI 抽象为受限、可审计、可回放的 Agent Skill。
- 不是“做了 RAG”，而是让历史失败、成功策略和证据评估进入下一次规划。
- 不是“测试平台 CRUD”，而是从用户委派目标到计划、执行、观察、评估、报告、记忆的完整 Agent 工作台。
