# TestClaw 核心知识与面试指南

面向岗位：测试开发、测试平台、自动化测试、AI 测试 Agent。

## 1. 架构总览

TestClaw 是一个智能全链路测试平台，整体可以拆成五层：

- **前端工作台层**：Vue 3 实现 dashboard/start test/history/settings 简化导航。核心页面是 Testing Agent Workspace 和 Run Detail cockpit，负责任务委派、预检展示、运行观察、报告查看、API/UI 证据查看、截图、用例和可复现脚本展示。
- **API 服务层**：FastAPI 提供登录、运行、文档、环境、模型 Provider、用例库等接口。run API 支持 create、preflight、list/detail、screenshots、SSE stream、rerun、cancel/delete。
- **异步执行层**：Celery worker 执行耗时测试任务，Redis 负责队列和结果通信，避免 LLM、OpenAPI 解析和 Playwright 执行阻塞 HTTP 请求。
- **Agent 编排层**：LangGraph 将测试任务拆成输入识别、资源加载、计划、用例生成、API/UI 执行、报告和知识沉淀等节点。
- **数据与资产层**：PostgreSQL + SQLAlchemy async + Alembic 存储用户、任务、运行日志、API 文档、环境、测试用例、模型配置和执行资产；截图、报告、脚本等作为运行证据沉淀。

部署上使用 Docker/nginx，后端执行 pytest/ruff 做质量检查。

## 2. 请求与 Run 生命周期

一次测试运行可以这样讲：

1. 用户在 Start Test/Testing Agent Workspace 输入 source、objective、测试模式、API policy、安全边界和鉴权配置。
2. 前端调用 `/api/v1/runs/preflight`，后端识别输入类型，检查 URL/API 文档/鉴权/安全策略是否满足运行条件。
3. 用户确认后调用 `/api/v1/runs` 创建运行记录，后端落库并派发 Celery worker。
4. worker 加载任务上下文，进入 LangGraph Agent workflow。
5. 每个 Agent 节点执行后写入 workflow_steps、progress_events、current_step、artifacts、tool_calls 等进度数据。
6. 前端通过 `/api/v1/runs/{id}/stream` 的 SSE 实时接收快照，更新 cockpit、步骤、日志和报告区域。
7. API runner 或 UI runner 产生执行结果、截图、失败分类和可复现脚本。
8. reporter 汇总真实执行结果生成 final_report。
9. knowledge_sink 沉淀可复用知识或资产。
10. 用户可以在详情页查看报告、API/UI tabs、screenshots、cases、reproducible scripts，并执行 rerun/cancel。

面试表达重点：TestClaw 的核心不是“提交一个后台任务”，而是“可预检、可观察、可追踪、可复现、可重跑”的测试运行生命周期。

## 3. Agent Workflow

当前主 Agent graph：

```text
input_classifier
  -> source_loader
  -> planner
  -> tc_generator
  -> conditional api_runner / ui_login / ui_test_planner / ui_runner
  -> reporter
  -> knowledge_sink
```

节点职责：

- `input_classifier`：判断用户输入是 URL、OpenAPI 文档、API 文档文本或其他来源。
- `source_loader`：加载和解析 source，例如 API 文档、页面入口、环境配置等。
- `planner`：根据 objective、source、模式和策略生成测试计划。
- `tc_generator`：生成 API/UI 测试用例或执行计划。
- `api_runner`：按 OpenAPI、鉴权和安全策略执行接口测试。
- `ui_login`：准备 UI 登录上下文。
- `ui_test_planner`：为页面自动化生成可执行路径。
- `ui_runner`：通过 Playwright 执行 UI 命令并采集证据。
- `reporter`：基于真实 API/UI 执行结果生成结构化报告。
- `knowledge_sink`：沉淀运行知识、用例、脚本、失败信息或可复用资产。

补充说明：仓库中仍存在 legacy coder/executor/analyzer/healer 链路，面试时可以说这是早期执行链，当前产品化主线更强调 testing-agent workflow。

## 4. API 测试设计

API runner 的核心设计点：

- **安全策略**：支持 `safe_read_only`、`safe_with_auth`、`write_allowed`，控制只读接口、鉴权接口和写操作接口的执行边界。
- **鉴权处理**：支持手动 token/header，也支持 auth configuration 自动登录获取 token；运行中遇到 401/403 可按配置刷新并重试一次。
- **OpenAPI 驱动**：从 OpenAPI/Swagger 文档中读取路径、方法、参数、request body 和 schema，生成可执行请求。
- **mock 数据**：根据 schema 生成基础请求数据，降低手动构造测试数据成本。
- **断言体系**：支持 status、JSONPath、schema、body contains 等断言。
- **失败分类**：将失败归类为鉴权、网络、schema、断言失败、服务错误等类型，方便报告和排查。

可以这样回答“API 自动化难点”：

> 难点不只是发请求，而是如何根据 API 文档生成安全、可执行、可断言的请求。TestClaw 通过 API policy 限制风险，通过 preflight 提前发现鉴权缺失，通过 OpenAPI schema 生成参数和 mock body，通过断言和失败分类把执行结果转成测试报告。

## 5. UI 自动化设计

UI runner 的核心设计点：

- **setup/login context**：在执行业务路径前处理登录态、测试账号和必要上下文。
- **LLM planning**：根据 objective 和页面入口生成 UI 测试步骤。
- **Playwright commands**：把计划转换为 Playwright 可执行动作。
- **command normalization**：对 `wait`、`assert snapshot contains`、`screenshot` 等伪命令进行标准化，避免把 LLM 语法问题误报成产品缺陷。
- **smart waits**：通过智能等待降低页面加载、异步渲染、网络延迟导致的不稳定。
- **screenshots/evidence**：按 run/case/step 保存截图，作为报告证据。
- **reproducible scripts**：输出可复现脚本，方便开发或测试人员本地复跑。

可以这样回答“UI 自动化如何稳定”：

> 我们不能完全相信 LLM 直接生成的命令，所以 TestClaw 在执行前做命令标准化和智能等待，执行中采集截图和页面快照，执行后输出可复现脚本。这样即使命令失败，也能知道是页面问题、选择器问题、登录问题还是生成计划问题。

## 6. 异步任务与 SSE 设计

为什么需要异步：

- LLM 规划、API 执行、UI 浏览器自动化都可能耗时较长。
- HTTP 请求不适合长时间阻塞。
- Celery worker 可以独立执行任务，API 服务只负责创建任务、查询状态和推送进度。

为什么用 SSE：

- 运行详情页需要实时展示 Agent 当前步骤、日志和证据。
- SSE 是服务端到浏览器的单向推送，适合进度流。
- 相比轮询，SSE 减少请求开销；相比 WebSocket，SSE 对“服务端单向推送运行状态”更简单。

持久化进度字段：

- `workflow_steps`：粗粒度节点步骤。
- `progress_events`：细粒度事件流。
- `current_step`：当前最新步骤。
- `final_report`：最终报告。
- `artifacts`：截图、脚本、用例等资产。
- `tool_calls`：工具调用摘要。

面试重点：SSE 只是传输方式，真正重要的是进度有数据库持久化。即使前端刷新，也能从 detail 接口恢复当前运行状态。

## 7. 数据模型与资产沉淀

TestClaw 的数据资产可以分成：

- **运行类**：任务/运行记录、状态、模式、目标、source、execution_log。
- **进度类**：workflow_steps、progress_events、current_step。
- **执行结果类**：api_execution_result、ui_execution_result、final_report。
- **证据类**：screenshots、logs、artifacts、tool_calls。
- **测试资产类**：测试用例库、API 文档、环境配置、模型 Provider。
- **可复现资产**：reproducible scripts、失败用例、截图路径、报告结论。

回答“为什么要沉淀资产”：

> Agent 一次执行的价值不能停留在临时日志里。测试平台要把生成的用例、截图、失败分类、脚本和报告变成可复用资产，支持后续回归和问题复盘；如果谈到团队协作，要说明这是后续扩展方向，不要当成已完成能力。

## 8. 安全、脱敏与鉴权

安全设计包括三部分：

- **执行安全**：通过 safe_read_only、safe_with_auth、write_allowed 限制 API 执行范围，避免测试 Agent 默认执行高风险写操作。
- **鉴权安全**：支持 token/header 和 auth_config，但 token、密码、captcha、tenant 等敏感配置不应明文进入 execution_log、tool_calls、截图说明或最终报告。
- **日志脱敏**：对 Authorization、Cookie、password、token、secret 等字段做脱敏，只保留必要的 method、URL、状态码、错误类型等排障信息。

可以这样回答“为什么 create 也要校验鉴权”：

> preflight 是用户体验层的提前检查，但不能只信浏览器缓存的 preflight 结果。真正创建运行时，后端还要再次解析和校验鉴权配置，确保任务入队前就是可执行和安全的。

## 9. 前端 Workspace 设计

当前产品前端重点：

- 简化导航：dashboard、start test、history、settings。
- Run page 是 Testing Agent Workspace，不只是普通表单。
- 支持任务委派：source、objective、API/UI/auto 模式。
- 支持安全边界：API policy、auth configuration、preflight。
- Run detail 是 execution cockpit：SSE 实时进度、workflow steps、logs、report、API/UI tabs、screenshots、cases、reproducible scripts。
- 支持运行控制：rerun、cancel。

可以这样回答“前端如何体现 Agent 产品感”：

> 我会把用户操作从“填表创建任务”改成“委派测试任务”：先明确目标、范围、鉴权和安全边界，再做 preflight，让用户知道 Agent 将测试什么、不能测试什么、缺少什么。执行中用 cockpit 展示当前动作、证据和阻塞点，而不是只给一堆日志。

## 10. 部署与工程化

部署链路：

- FastAPI 作为后端 API 服务。
- Celery worker 执行测试任务。
- Redis 作为 broker/result backend。
- PostgreSQL 存储业务数据。
- Vue 3 前端构建后由 nginx 托管。
- nginx 代理 `/api/` 和健康检查。
- Docker 统一编排后端、worker、前端、Redis、PostgreSQL、nginx。

工程质量：

- SQLAlchemy async + Alembic 管理数据库模型和迁移。
- pytest 覆盖关键后端行为。
- ruff 做 Python 代码检查。
- 前端通过 Vite/TypeScript 构建检查。

面试表达重点：这是一个“能跑起来的测试平台工程”，不是单文件 demo。

## 11. 质量与测试策略

建议从四类测试讲：

- **接口契约测试**：run create、preflight、detail、SSE、rerun、cancel/delete 的参数、状态码和错误处理。
- **Agent 路由测试**：auto/API/UI 模式下是否进入正确 runner，是否根据 API schema、URL、鉴权条件选择路径。
- **执行结果测试**：API 断言统计、UI 截图路径、命令标准化、报告汇总是否基于真实执行结果。
- **安全测试**：鉴权缺失阻断、token 脱敏、auth refresh 不泄露敏感信息、取消状态不被后续进度覆盖。

可以强调：

> 测试 Agent 平台最容易出现“看起来跑了，但实际没覆盖”的问题，所以测试策略要验证真实执行结果和报告一致，不能让 LLM 计划或草稿报告冒充执行结果。

## 12. 面试 Q&A

### Q1：TestClaw 是什么？

答：TestClaw 是智能全链路测试平台，面向 API/UI 自动化和测试 Agent 场景。用户输入 URL、API 文档或测试目标后，系统通过 preflight 确认可执行性，再由 LangGraph Agent 规划和执行测试，最后通过 SSE 展示进度，并输出截图、用例、脚本和结构化报告。

可能追问：

- 和普通自动化平台有什么区别？
- Agent 在里面具体做了什么？
- 怎么证明它真的执行了测试？

### Q2：为什么选择 FastAPI + Celery + Redis？

答：FastAPI 适合提供异步 API 和 Pydantic schema；测试执行、LLM 调用和 Playwright 都是耗时任务，所以用 Celery worker 异步执行；Redis 负责 broker/result backend。这样创建运行的 HTTP 请求可以快速返回，真正的测试过程由 worker 执行，前端通过 SSE 观察进度。

可能追问：

- Celery 任务失败怎么办？
- 如果 worker 掉线，前端状态怎么展示？
- 为什么不用直接在 FastAPI 里 await 执行？

### Q3：为什么用 LangGraph？

答：测试 Agent 是多步骤、有状态、可能分支的流程，不适合一个 prompt 一次性完成。LangGraph 可以把 input_classifier、source_loader、planner、tc_generator、api_runner、ui_runner、reporter 等节点拆开，每个节点可追踪、可持久化、可路由，便于调试和产品展示。

可能追问：

- auto 模式怎么路由到 API 或 UI？
- 节点失败后怎么恢复？
- LangGraph 和普通 chain 的区别是什么？

### Q4：preflight 做什么？

答：preflight 是运行前检查，主要判断 source 类型、API 文档是否可解析、URL 是否可达、鉴权是否准备好、安全策略是否允许、预计会执行 API 还是 UI。它能提前发现缺 token、接口文档无效、目标不可达等问题，避免创建任务后才失败。

可能追问：

- preflight 通过后 create 还要校验吗？
- 鉴权失败是阻断还是 warning？
- 如何判断 API 是否需要鉴权？

### Q5：API runner 怎么设计？

答：API runner 基于 OpenAPI 生成请求，结合 API policy 判断是否允许执行，再注入鉴权 header 或自动登录获取 token，执行后用 status、JSONPath、schema、body contains 等断言判断结果，并做失败分类。它强调安全执行和结果可信，而不是盲目扫接口。

可能追问：

- 写接口怎么处理？
- token 过期怎么办？
- OpenAPI schema 不完整怎么办？

### Q6：UI runner 怎么设计？

答：UI runner 先准备登录上下文，再让 LLM 规划 UI 测试步骤，然后转换为 Playwright 命令执行。执行前会做命令标准化，执行中做智能等待和截图，执行后保存证据和可复现脚本，方便排查失败原因。

可能追问：

- LLM 生成的选择器不可用怎么办？
- UI 自动化如何减少 flaky？
- 截图如何和用例步骤对应？

### Q7：报告怎么保证可信？

答：报告不能只相信 LLM 的自然语言总结。TestClaw 的 reporter 应该从真实的 api_execution_result 和 ui_execution_result 汇总数量、失败、截图和断言结果，再生成 final_report。这样报告能回答哪些接口/页面测了、哪些失败、证据是什么、如何复现。

可能追问：

- 如果 API 没执行但计划里有 API 用例，报告怎么展示？
- UI-only 模式是否展示 API 结果？
- 怎样避免虚假覆盖率？

### Q8：SSE 进度如何恢复？

答：SSE 负责实时推送，但进度源头是数据库里的 execution_log。每个节点写入 workflow_steps、progress_events、current_step 等字段。前端刷新后，可以通过 detail 接口读取快照，再继续订阅 SSE，所以不会因为连接断开丢失全部状态。

可能追问：

- SSE 断线怎么办？
- 为什么不用 WebSocket？
- 进度事件太多怎么处理？

### Q9：敏感数据如何处理？

答：鉴权信息只用于执行，不应明文进入 execution_log、tool_calls、截图说明或 final_report。日志里对 Authorization、Cookie、password、token、secret 等字段做脱敏，只保留排障所需的 method、URL、状态码和错误类型。

可能追问：

- token refresh 怎么记录？
- 截图里出现敏感信息怎么办？
- 多用户环境下如何隔离数据？

### Q10：你会怎么介绍这个项目的难点？

答：难点主要有三类。第一是执行闭环，要把 API/UI/Agent/报告/证据串起来；第二是可信度，报告必须来自真实执行结果，不能只来自 LLM 草稿；第三是安全可控，Agent 执行 API 和 UI 自动化时要有 preflight、API policy、鉴权边界和脱敏机制。

可能追问：

- 哪个模块你最熟？
- 如果让你继续优化，你会做什么？
- 如何量化项目效果？

## 13. 高频追问速答

- **如何量化效果？**  
  用真实数据回答：[补充：覆盖接口数]、[补充：生成用例数]、[补充：平均执行耗时]、[补充：发现缺陷数]、[补充：回归测试通过率]。没有数据时不要编。

- **TestClaw 和 Postman/Newman 的区别？**  
  Postman/Newman 更偏接口集合和执行；TestClaw 目标是把 API 文档、UI 自动化、Agent 规划、实时观察、证据和报告串成测试工作台。

- **TestClaw 和传统 UI 自动化框架的区别？**  
  传统框架更偏手写脚本；TestClaw 加入 Agent 规划、运行前预检、截图证据、报告和资产沉淀，但底层仍依赖 Playwright 这类成熟执行工具。

- **Agent 会不会乱测？**  
  通过 objective、mode、safety boundary、API policy、auth configuration 和 preflight 限制范围。对写操作不能默认放开，需要 write_allowed 策略或用户明确授权。

- **如何处理测试环境差异？**  
  通过 environment management 管理 base URL、header、账号、变量等配置，运行时结合 source 和 auth config 注入执行上下文。

- **失败后怎么定位？**  
  看 workflow_steps 判断失败节点，看 progress_events 和 logs 判断动作，看 API/UI tabs 看断言和截图，看 reproducible scripts 本地复现。

- **为什么需要 rerun/cancel？**  
  测试运行是长任务，用户需要中断错误任务，也需要基于相同目标复跑验证修复或排查 flaky。

- **未来还能怎么优化？**  
  可以补充项目级记忆、趋势分析、用例 accept/edit/reject、CI 集成、协作评审、失败自动归因和更细的权限控制。面试时要说明这些是后续方向，不要当成已完成能力。

## 14. 面试避坑

- 不要编造生产规模、用户量、覆盖率、提效百分比。
- 不要说 LLM 自动化可以完全替代测试人员，应强调辅助规划、执行和证据整理。
- 不要把 API 写操作默认说成可执行，必须强调安全策略。
- 不要说报告完全由模型判断，应强调真实执行结果汇总。
- 不要暴露真实账号、token、业务域名或客户数据。
- 不要只背技术栈，要把每个技术点讲到测试问题：Celery 解决长任务，SSE 解决实时观察，Playwright 解决 UI 执行，LangGraph 解决 Agent 状态机，脱敏解决安全合规。
