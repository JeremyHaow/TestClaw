# TestClaw 项目经历简历稿

面向岗位：测试开发、自动化测试、测试平台、AI 测试平台。

## 一句话总结

TestClaw 是一个智能全链路测试平台，基于 FastAPI、Vue 3、Celery/Redis、PostgreSQL、LangGraph 和 Playwright，将 API 文档解析、运行前预检、Agent 测试规划、API/UI 自动化执行、实时进度、证据采集和报告沉淀串成闭环。

## 技术栈

- 后端：FastAPI、Python、SQLAlchemy async、Alembic、PostgreSQL、Redis、Celery、Pydantic。
- 前端：Vue 3、TypeScript、Vite、Pinia、Vue Router、SSE/EventSource。
- Agent 与自动化：LangGraph、Playwright、OpenAPI 驱动请求生成、LLM 测试规划。
- 工程与部署：Docker、nginx、pytest、ruff。
- 数据与安全：异步 ORM、运行日志持久化、截图/脚本/报告资产、敏感数据脱敏、鉴权配置。

## 项目背景

传统测试平台常把 API 文档、环境配置、用例库、执行记录、截图证据和测试报告拆成多个工具页，测试人员需要手动串联流程。TestClaw 的目标是做成面向测试工作的 Agent Workspace：用户输入测试目标、来源、鉴权方式和安全边界后，系统自动完成输入识别、预检、测试计划生成、API/UI 自动化执行、证据采集、结构化报告和可复用资产沉淀。

当前产品能力包括本地登录、仪表盘、任务/运行创建与详情、测试用例库、API 文档导入与解析、模型 Provider 管理、环境管理、基础 Agent 执行链和任务状态记录。前端主导航已简化为 dashboard/start test/history/settings；运行页是 Testing Agent Workspace，支持任务委派、source/objective、API/UI/auto 模式、安全边界、API policy、鉴权配置和 preflight；运行详情页通过 SSE 展示实时进度、执行 cockpit、workflow steps、logs、report、API/UI tabs、screenshots、cases、reproducible scripts，并支持 rerun/cancel。

## 我的职责

- 参与设计 Testing Agent Workspace 的核心产品链路，将“创建任务 -> 预检 -> Agent 执行 -> 实时观察 -> 报告复盘 -> 资产沉淀”梳理为用户可理解的测试工作流。
- 设计后端 run API 能力边界，覆盖 create、preflight、list/detail、screenshots、SSE stream、rerun、cancel/delete 等运行生命周期接口。
- 基于 LangGraph 拆分测试 Agent 工作流，串联 input_classifier、source_loader、planner、tc_generator、api_runner/ui_login/ui_test_planner/ui_runner、reporter、knowledge_sink 等节点。
- 设计 API 测试执行策略，支持 safe_read_only、safe_with_auth、write_allowed 三类安全策略，处理鉴权、刷新、OpenAPI 驱动请求生成、mock 数据和断言。
- 设计 UI 自动化执行链路，支持登录上下文、LLM 规划、Playwright 命令执行、智能等待、截图证据、命令标准化和可复现脚本。
- 维护运行进度与日志持久化结构，记录 workflow_steps、progress_events、current_step、final_report、artifacts、tool_calls，并在持久化和展示链路中脱敏敏感数据。
- 通过 pytest/ruff 保障关键契约，包括运行创建、预检、状态流转、API/UI 执行结果、取消/重跑、报告生成和敏感信息脱敏。

## 简历 Bullet 版本

- 参与建设 TestClaw 智能全链路测试平台，基于 FastAPI + Vue 3 + Celery/Redis + PostgreSQL + LangGraph + Playwright 打通 API/UI 测试从输入、预检、执行到报告的闭环。
- 设计 Testing Agent Workspace，支持用户以 source/objective 方式委派测试任务，并选择 API、UI、auto 模式，配置安全边界、API policy、鉴权信息和运行前 preflight。
- 实现运行生命周期接口能力，覆盖 create、preflight、list/detail、screenshots、SSE 实时进度、rerun、cancel/delete，支撑前端运行详情 cockpit 的实时可观测性。
- 基于 LangGraph 编排测试 Agent 状态机，将输入识别、source 加载、测试规划、用例生成、API 执行、UI 登录、UI 规划、UI 执行、报告生成和知识沉淀拆为可追踪节点。
- 设计 OpenAPI 驱动的 API 测试执行链路，支持 safe_read_only/safe_with_auth/write_allowed 安全策略、鉴权处理与刷新、mock 请求数据、状态码/JSONPath/schema/body contains 等断言和失败分类。
- 设计 Playwright UI 自动化执行链路，支持登录上下文、LLM 生成测试计划、命令标准化、智能等待、截图证据采集和可复现脚本输出，降低生成脚本不可执行的风险。
- 建设 SSE 进度与日志持久化方案，将 workflow_steps、progress_events、current_step、final_report、artifacts、tool_calls 写入运行记录，前端实时展示 Agent 当前步骤、日志、截图、用例和报告。
- 梳理敏感数据治理策略，对鉴权配置、token、密码、请求头等信息在日志、tool calls、报告和持久化链路中进行脱敏，避免测试证据泄露凭据。
- 支持测试资产管理能力，包括测试用例库、API 文档导入/解析、模型 Provider 管理、环境管理和可复用脚本/截图/报告沉淀。
- 使用 pytest/ruff 维护质量门禁，重点覆盖运行 API、预检、Agent 路由、API/UI 执行契约、取消/重跑、报告统计和安全脱敏等核心路径。

## 测试开发亮点

- **测试闭环**：不是只生成用例，而是覆盖输入识别、计划、执行、证据、报告、重跑和资产沉淀。
- **API 自动化能力**：从 OpenAPI 解析接口定义，生成请求数据，执行断言，并按状态码、JSONPath、schema、响应体包含关系判断结果。
- **UI 自动化能力**：将 LLM 计划转换为 Playwright 可执行命令，并通过智能等待、截图和脚本输出保证可复现。
- **安全执行策略**：用 safe_read_only、safe_with_auth、write_allowed 区分只读、鉴权和写操作，减少测试 Agent 误操作风险。
- **运行可观测性**：SSE 实时推送步骤和日志，前端 cockpit 展示当前动作、workflow steps、API/UI 证据、截图、报告和脚本。
- **工程化质量**：异步后端、Celery worker、Redis 队列、PostgreSQL 持久化、Docker/nginx 部署和 pytest/ruff 质量检查形成完整工程链路。

## STAR 故事

**S（背景）**：测试人员面对 API 文档、页面入口、鉴权配置和用例库时，需要在多个工具之间切换，手动完成环境准备、接口调试、UI 自动化脚本编写、执行观察和报告整理，流程割裂且难复现。

**T（任务）**：需要把 TestClaw 从普通测试工具后台升级为 Testing Agent Workspace，让用户能像委派任务一样描述测试目标，并在运行中看到 Agent 的计划、执行、证据和结论。

**A（行动）**：围绕 run 生命周期设计 create/preflight/SSE/detail/rerun/cancel 接口；用 LangGraph 拆分 input_classifier、source_loader、planner、tc_generator、api_runner、ui_login、ui_test_planner、ui_runner、reporter、knowledge_sink；API 侧引入安全策略、鉴权处理、OpenAPI 请求生成和断言；UI 侧基于 Playwright 执行命令、智能等待和截图；进度侧持久化 workflow_steps、progress_events、current_step、final_report、artifacts、tool_calls，并做敏感数据脱敏。

**R（结果）**：形成了可展示、可追踪、可重跑的测试 Agent 执行闭环。可量化指标需根据真实项目数据补充，例如：[补充：覆盖接口数]、[补充：生成用例数]、[补充：平均执行耗时]、[补充：发现缺陷数]、[补充：回归测试通过率]。

## 不同岗位版本

### 测试开发岗位

项目描述可以突出“平台工程 + 测试能力”：

> 负责 TestClaw 智能测试平台的运行链路和自动化执行能力建设，基于 FastAPI、Celery、PostgreSQL、LangGraph 和 Playwright 实现 API/UI 测试任务创建、预检、异步执行、实时进度、证据采集、报告生成和重跑取消。重点建设 OpenAPI 驱动的接口测试、安全执行策略、鉴权处理、断言体系、Playwright UI 执行和日志脱敏能力。

适合强调的关键词：测试平台、接口自动化、UI 自动化、异步任务、测试报告、证据采集、质量门禁。

### 自动化测试岗位

项目描述可以突出“可执行脚本 + 断言 + 证据”：

> 基于 OpenAPI 和 Playwright 建设 API/UI 自动化执行链路，支持接口请求生成、mock 数据、状态码/JSONPath/schema/body contains 断言、鉴权刷新、UI 登录上下文、命令标准化、智能等待、截图证据和可复现脚本输出，使自动化测试结果可追踪、可复盘、可重跑。

适合强调的关键词：Playwright、OpenAPI、断言设计、截图证据、脚本复现、失败分类。

### AI 测试平台岗位

项目描述可以突出“Agent 编排 + 可控性”：

> 基于 LangGraph 设计测试 Agent 工作流，将输入分类、资源加载、计划生成、用例生成、API 执行、UI 登录、UI 测试规划、UI 执行、报告和知识沉淀拆成可观测节点；通过 SSE 和持久化日志把 Agent 决策、工具调用、当前步骤、证据和最终报告实时展示给前端，提升 AI 测试执行的透明度与可控性。

适合强调的关键词：LangGraph、Agent workflow、人工监督、运行控制、SSE、工具调用、可观测性、安全边界。

## 指标占位符

不要直接编造生产指标。简历中可以保留如下占位符，面试前用真实统计替换：

- 覆盖接口数量：[补充：覆盖接口数]
- 支持导入文档数量：[补充：OpenAPI/Swagger/Postman 文档数]
- 生成测试用例数量：[补充：生成用例数]
- UI 自动化场景数量：[补充：UI 场景数]
- 平均运行耗时：[补充：平均执行耗时]
- 失败分类准确率或有效率：[补充：失败分类有效率]
- 回归测试覆盖范围：[补充：pytest 用例数/模块数]
- 缺陷发现或定位效率：[补充：缺陷数/效率提升]

## 面试讲解要点

- **为什么需要 preflight**：运行前确认输入类型、API 文档解析结果、URL 可达性、鉴权是否就绪、安全策略和预计执行范围，避免任务开始后才发现缺少 token、文档不可解析或目标不可达。
- **为什么用 SSE**：测试运行是长任务，前端需要实时看到 Agent 当前步骤、日志和证据；SSE 比轮询更轻，比 WebSocket 更适合单向状态推送。
- **为什么用 Celery/Redis**：测试执行和 LLM/Playwright 调用耗时长，不能阻塞 API 请求；Celery 负责异步调度，Redis 作为 broker/result backend。
- **为什么用 LangGraph**：测试 Agent 不是一次性 prompt，需要可恢复、可追踪、可分支的状态机，便于 API/UI 条件路由和报告汇总。
- **API 测试怎么保证安全**：用 safe_read_only、safe_with_auth、write_allowed 分级控制请求范围；鉴权信息只用于执行，不进入日志和报告明文。
- **UI 自动化怎么降低不稳定性**：通过命令标准化、智能等待、登录上下文、截图证据和可复现脚本，让 LLM 生成的计划落到可执行的 Playwright 命令。
- **报告可信度来自哪里**：reporter 使用真实 api_execution_result 和 ui_execution_result 汇总，而不是直接相信 LLM 生成的描述。

## 常见追问准备

- **问：你负责的是前端还是后端？**  
  答：可以按真实经历说明。如果偏后端，重点说 run API、Agent graph、API/UI runner、SSE 和持久化；如果偏全栈，补充 Testing Agent Workspace、Run Detail cockpit、API/UI tabs、截图和报告展示。

- **问：Agent 出错怎么处理？**  
  答：节点内部尽量捕获异常并降级为结构化失败结果，记录 last_error、workflow_steps 和 progress_events，最终报告从真实执行结果汇总，避免因为单个工具失败导致前端无状态。

- **问：怎么处理取消和重跑？**  
  答：取消会更新任务状态并在 execution_log 中保留 cancelled/cancelled_at，同时避免后续进度覆盖 cancelled；重跑基于原任务目标和配置重新创建或触发执行，保留历史记录便于对比。

- **问：API 鉴权怎么做？**  
  答：支持手动 token/header，也支持 auth_config 自动登录获取 token；preflight 和 create 都会服务端校验鉴权准备情况，运行中遇到 401/403 可按配置刷新一次，日志中只记录方法、URL、状态等元信息。

- **问：UI 生成命令不合法怎么办？**  
  答：先做命令标准化，把 wait、assert snapshot contains、screenshot 等伪命令转换为 runner 支持的动作；无法支持的动作记录 normalization_warnings，不把生成语法错误误报成产品缺陷。

## 避坑提醒

- 不要说已经有大规模生产落地、线上用户量、稳定收益或明确性能提升，除非有真实数据。
- 不要把 TestClaw 描述成只会“调用大模型生成测试用例”，核心价值是 Agent 执行闭环和证据沉淀。
- 不要说 Agent 可以任意执行写操作，必须强调安全策略和鉴权边界。
- 不要把报告结果说成完全由 LLM 判断，应该强调 reporter 基于真实 API/UI 执行结果统计。
- 不要暴露真实 token、密码、业务系统地址或客户数据，面试中用脱敏示例说明。
- 不要只讲技术名词，要把技术和测试场景连接起来：预检解决启动失败，SSE 解决可观测性，截图/脚本解决复现，安全策略解决误操作风险。
