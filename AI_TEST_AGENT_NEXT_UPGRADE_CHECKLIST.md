# AI Test Agent 后续详细改造升级清单

> 目标：基于你当前已完成的内容，继续把项目升级成一个 **“输入网址或 Swagger/OpenAPI 文档 → Agent 自动完成测试计划、测试用例、API 自动化、UI 自动化、报告输出”** 的求职向 AI Agent 项目。
>
> 本清单只写 **后续要做的详细改造项**，不重复基础建设。

---

## 0. 目标定义（本轮改造完成后应达到的效果）

### 最终用户体验

用户只做一件事：

1. 输入一个 **网页 URL**
   或
2. 输入一个 **Swagger/OpenAPI URL**
   或
3. 粘贴 **Swagger/OpenAPI JSON/YAML**

然后点击：

**开始自动测试**

系统自动完成：

- 自动识别输入类型
- 自动解析 Swagger
- 自动生成测试计划
- 自动生成详细测试用例
- 自动执行 API 自动化测试
- 自动执行 UI 自动化测试
- 自动输出结构化报告

### 本轮 DoD（Definition of Done）

- [ ] 支持 Swagger URL / JSON / YAML 输入
- [ ] Agent 可直接调用 `playwright-cli`
- [ ] 不再依赖前端手动“生成脚本 → 再执行”
- [ ] API 测试不再依赖手动选择端点
- [ ] 前端主入口收敛成一个 Run 页面
- [ ] 详情页能完整展示：计划、用例、API 结果、UI 结果、截图、总结
- [ ] 历史页能查看每次运行记录

---

## 1. 当前已可复用基础（建议保留）

这部分不需要重写，直接在现有基础上升级：

- `app/tools/doc_parser.py`：已具备 OpenAPI / Swagger / Postman 基础解析能力
- `app/services/doc_service.py`：已能落库并保存 `parsed_endpoints`
- `app/api/v1/documents.py`：已支持 URL 导入 / 原文导入
- `app/tools/playwright_tool.py`：已具备 `playwright-cli` 单命令 / 脚本 / stream 调用能力
- `app/agent/graph.py`：已有 LangGraph 主流程骨架
- `app/agent/nodes/planner.py`、`tc_generator.py`：已有规划与用例生成雏形
- `app/api/v1/tasks.py` + `app/worker/tasks.py`：已有异步运行链路
- `frontend/src/pages/TasksPage.vue`、`TaskDetailPage.vue`、`ReportsPage.vue`：可分别改造成主入口 / 详情 / 历史页

---

## 2. 第一阶段：统一产品入口，去掉“手工操作感”

> 这一阶段的目标：从“多个工具页”收敛成“一个主入口 + 一个详情页 + 一个历史页”。

## 2.1 前端页面收敛

### 要做

- [ ] 把 `frontend/src/pages/TasksPage.vue` 改造成 **RunPage**
- [ ] 把 `frontend/src/pages/TaskDetailPage.vue` 改造成 **RunDetailPage**
- [ ] 把 `frontend/src/pages/ReportsPage.vue` 改造成 **HistoryPage**
- [ ] 路由只保留核心入口：
  - [ ] `/run`
  - [ ] `/runs/:id`
  - [ ] `/history`
- [ ] Sidebar 只保留 3 个主菜单：
  - [ ] 开始测试
  - [ ] 历史记录
  - [ ] （可选）系统设置

### 涉及文件

- `frontend/src/router/index.ts`
- `frontend/src/components/AppSidebar.vue`
- `frontend/src/components/AppHeader.vue`
- `frontend/src/pages/TasksPage.vue`
- `frontend/src/pages/TaskDetailPage.vue`
- `frontend/src/pages/ReportsPage.vue`

### 验收标准

- [ ] 用户进入系统后，第一眼看到的是“输入 URL / Swagger 的测试入口”
- [ ] 不需要再进“API 测试页”“UI 测试页”“用例生成页”分别操作

---

## 2.2 弱化或隐藏手工工具页

### 要做

- [ ] 从主导航移除以下页面入口：
  - [ ] `CaseGenerationPage.vue`
  - [ ] `ApiTestingPage.vue`
  - [ ] `UiTestingPage.vue`
  - [ ] `DocumentsPage.vue`
  - [ ] `ProvidersPage.vue`
  - [ ] `AgentConfigPage.vue`
  - [ ] `KnowledgePage.vue`
  - [ ] `EnvironmentsPage.vue`
- [ ] 这些能力保留后端或后台入口即可，不作为主产品形态

### 验收标准

- [ ] 面试演示时，产品主流程只有“一键输入 → 自动运行 → 查看结果”

---

## 3. 第二阶段：Swagger / OpenAPI 解析能力升级

> 当前 `doc_parser.py` 只抽取了 path / method / summary / operationId，够展示，不够自动测试。
> 接下来要升级成“可供 Agent 直接消费的结构化接口描述”。

## 3.1 升级 Swagger 解析输出结构

### 要做

- [ ] 为每个 endpoint 增加以下字段：
  - [ ] `path`
  - [ ] `method`
  - [ ] `summary`
  - [ ] `operationId`
  - [ ] `tags`
  - [ ] `path_params`
  - [ ] `query_params`
  - [ ] `header_params`
  - [ ] `request_body_schema`
  - [ ] `response_schema`
  - [ ] `required_fields`
  - [ ] `auth_required`
  - [ ] `example_request`
  - [ ] `example_response`
- [ ] 兼容：
  - [ ] Swagger 2.0
  - [ ] OpenAPI 3.x
  - [ ] JSON
  - [ ] YAML

### 涉及文件

- `app/tools/doc_parser.py`

### 验收标准

- [ ] 输入 Swagger 后，系统拿到的不再只是端点列表，而是可直接生成测试请求的结构化 schema

---

## 3.2 增加 Swagger 输入识别与加载

### 要做

- [ ] 新增输入识别逻辑：
  - [ ] 识别普通网页 URL
  - [ ] 识别 Swagger/OpenAPI URL
  - [ ] 识别 JSON/YAML 原文
- [ ] 新增统一加载器：
  - [ ] 如果是 Swagger URL，自动抓取文档内容
  - [ ] 如果是原文，直接解析
  - [ ] 如果是网页 URL，跳过 Swagger 解析

### 建议新增文件

- `app/agent/nodes/input_classifier.py`
- `app/agent/nodes/source_loader.py`

### 需要同步修改

- `app/agent/state.py`

### 新增状态字段建议

- [ ] `input_type`
- [ ] `source_input`
- [ ] `document_content`
- [ ] `parsed_api_schema`
- [ ] `ui_seed_url`

### 验收标准

- [ ] 用户无需手动指定“这是 Swagger 还是网页”
- [ ] Agent 可自动识别并走不同分支

---

## 3.3 文档接口改造成辅助能力

### 要做

- [ ] 保留 `documents/import` 和 `documents/upload`
- [ ] 增加一个轻量解析接口供前端预检（可选）
- [ ] `Run` 创建接口支持直接传 `source`

### 涉及文件

- `app/api/v1/documents.py`
- 建议新增：`app/api/v1/runs.py`

### 验收标准

- [ ] 不需要先“导入文档”再“创建任务”
- [ ] 可以直接“贴 Swagger → 开始跑”

---

## 4. 第三阶段：重构 Agent 工作流

> 当前流程：`planner -> tc_generator -> coder/api_executor -> executor -> analyzer`
>
> 目标流程：  
> `input_classifier -> source_loader -> planner -> case_generator -> api_runner -> ui_runner -> analyzer -> reporter`

## 4.1 重构状态定义

### 要做

- [ ] 扩展 `AgentState`
- [ ] 明确区分 API 与 UI 上下文
- [ ] 明确区分计划、用例、执行结果、报告结果

### 建议新增字段

- [ ] `input_type`
- [ ] `source_input`
- [ ] `document_content`
- [ ] `parsed_api_schema`
- [ ] `ui_discovery`
- [ ] `api_plan`
- [ ] `ui_plan`
- [ ] `api_cases`
- [ ] `ui_cases`
- [ ] `api_execution_result`
- [ ] `ui_execution_result`
- [ ] `final_report`
- [ ] `artifacts`

### 涉及文件

- `app/agent/state.py`

---

## 4.2 重构 Planner

### 当前问题

- 现在 `planner.py` 基本只吃 `objective/target_url/test_type`
- 没有真正利用 Swagger 结构
- 没有区分 API 计划和 UI 计划

### 要做

- [ ] 基于 `parsed_api_schema` 生成 API 测试计划
- [ ] 基于 URL 生成 UI 测试计划
- [ ] 若模式为 `auto`，同时生成两部分计划
- [ ] 输出结构化计划：
  - [ ] `title`
  - [ ] `scope`
  - [ ] `case_count`
  - [ ] `priority`
  - [ ] `strategy`

### 涉及文件

- `app/agent/nodes/planner.py`
- `app/agent/prompts.py`

### 验收标准

- [ ] Swagger 输入时，计划里明确包含：冒烟、参数校验、异常分支、鉴权类测试
- [ ] 网页输入时，计划里明确包含：页面可访问、关键交互、表单、跳转、错误提示类测试

---

## 4.3 重构 Case Generator

### 当前问题

- 当前 `tc_generator.py` 输出的是混合 test cases
- 没有区分 API / UI
- 对 Swagger 的利用深度不够

### 要做

- [ ] 改为分别生成：
  - [ ] `api_cases`
  - [ ] `ui_cases`
- [ ] 用例结构统一为：
  - [ ] `title`
  - [ ] `preconditions`
  - [ ] `steps`
  - [ ] `expected`
  - [ ] `priority`
  - [ ] `category`
  - [ ] `case_type`
  - [ ] `request_template`（API）
  - [ ] `assertions`（API/UI）

### 涉及文件

- `app/agent/nodes/tc_generator.py`
- 或新建：`app/agent/nodes/case_generator.py`
- `app/agent/prompts.py`

### 验收标准

- [ ] 前端详情页中，API 与 UI 用例可分别展示
- [ ] API 用例不只是自然语言，还包含可执行请求模板

---

## 4.4 新增 Reporter 节点

### 要做

- [ ] 在 Agent 末尾增加 `reporter` 节点
- [ ] 汇总：
  - [ ] 计划
  - [ ] 用例
  - [ ] API 结果
  - [ ] UI 结果
  - [ ] 截图 / trace
  - [ ] 失败摘要
  - [ ] LLM 生成的最终总结

### 建议新增文件

- `app/agent/nodes/reporter.py`

### 验收标准

- [ ] 前端拿到的是一份完整、可直接渲染的报告 JSON

---

## 4.5 重构 Graph 编排

### 要做

- [ ] 重写 `app/agent/graph.py`
- [ ] 增加条件路由：
  - [ ] Swagger 输入：走 `api_runner`
  - [ ] URL 输入：走 `ui_runner`
  - [ ] `auto` 模式：两者都走
- [ ] `analyzer` 改成汇总型节点，而不是只看 Python 执行结果

### 涉及文件

- `app/agent/graph.py`
- `app/agent/nodes/analyzer.py`

### 验收标准

- [ ] 整个流程从一次运行里直接完成 API + UI 分支

---

## 5. 第四阶段：API 自动化测试能力升级

> 目标：真正实现“输入 Swagger → Agent 自动跑接口测试”。

## 5.1 重构 API Runner

### 当前问题

- 现在 `api_executor.py` 只会遍历 plan 中 step
- 没有充分利用 Swagger schema
- 没有自动构造样例参数 / 请求体 / 断言

### 要做

- [ ] 新建或重构 `api_runner.py`
- [ ] 根据 `parsed_api_schema` 自动选择端点
- [ ] 自动生成：
  - [ ] 正常请求
  - [ ] 缺失必填项请求
  - [ ] 错误类型请求
  - [ ] 边界值请求
  - [ ] 未授权请求（如适用）
- [ ] 自动断言：
  - [ ] 状态码
  - [ ] 响应字段是否存在
  - [ ] 响应结构是否符合 schema
  - [ ] 响应耗时记录

### 建议涉及文件

- 新建：`app/agent/nodes/api_runner.py`
- 可参考：`app/api/v1/api_tests.py`
- 可补充：`app/tools/api_tool.py`

### 验收标准

- [ ] 不需要用户手工选端点
- [ ] Swagger 输入后至少能自动跑一组冒烟 + 参数校验测试

---

## 5.2 加入环境与鉴权注入

### 要做

- [ ] 支持从运行参数里传：
  - [ ] `base_url`
  - [ ] `headers`
  - [ ] `token`
- [ ] 若接口文档里有鉴权描述，自动注入 Header 模板
- [ ] 支持未来扩展环境变量映射

### 涉及文件

- `app/agent/state.py`
- `app/api/v1/runs.py`
- `app/agent/nodes/api_runner.py`

### 验收标准

- [ ] 面试演示时可以对需要 Token 的 API 做自动化测试

---

## 6. 第五阶段：用 playwright-cli 完成 UI 自动化，并让 Agent 直接调用

> 这是本轮最关键点之一。

## 6.1 放弃“前端手动生成脚本 → 手动执行”的模式

### 要做

- [ ] 不再把 `ui_tests/generate-script` 作为主流程入口
- [ ] 不再要求用户在前端手工改 playwright-cli 命令
- [ ] 改为 Agent 直接生成 playwright-cli 指令并执行

### 涉及文件

- `app/api/v1/ui_tests.py`
- `frontend/src/pages/UiTestingPage.vue`
- `app/agent/nodes/coder.py`
- `app/agent/nodes/executor.py`

### 验收标准

- [ ] 用户只输入 URL，不需要再看脚本编辑器

---

## 6.2 新建 UI Runner（playwright-cli 版）

### 要做

- [ ] 新建 `app/agent/nodes/ui_runner.py`
- [ ] 输入：
  - [ ] URL
  - [ ] UI 测试计划
  - [ ] UI 测试用例
- [ ] 输出：
  - [ ] 生成的 playwright-cli 命令脚本
  - [ ] 执行日志
  - [ ] 截图产物
  - [ ] 执行结果

### 推荐调用方式

优先直接调用现有能力：

- `run_playwright_cli_script`
- `run_playwright_cli_stream`
- `run_playwright_cli_command`

### 涉及文件

- `app/tools/playwright_tool.py`
- 新建：`app/agent/nodes/ui_runner.py`

### 验收标准

- [ ] Agent 能直接组织并执行 `playwright-cli open/snapshot/click/type/screenshot` 命令

---

## 6.3 升级 playwright-cli 产物保存

### 要做

- [ ] 明确截图目录
- [ ] 保存每次运行的脚本内容
- [ ] 保存命令执行日志
- [ ] 保存失败步骤对应截图
- [ ] 若可行，补充 trace 或页面快照

### 建议输出结构

- [ ] `commands`
- [ ] `stdout`
- [ ] `stderr`
- [ ] `screenshots`
- [ ] `snapshot_text`
- [ ] `status_code`

### 涉及文件

- `app/tools/playwright_tool.py`
- `app/agent/nodes/ui_runner.py`

### 验收标准

- [ ] Run Detail 页能看到至少一张 UI 执行截图

---

## 6.4 Prompt 改成生成 playwright-cli 命令，不再生成 pytest 脚本

### 当前问题

- `CODER_PROMPT` 当前是生成 Python Playwright 脚本
- 但你的目标是让 Agent **直接调用 playwright-cli**

### 要做

- [ ] 新增 `PLAYWRIGHT_CLI_AGENT_PROMPT`
- [ ] 输出格式改成“每行一个 playwright-cli 命令”
- [ ] 要求命令包含：
  - [ ] `open`
  - [ ] `snapshot`
  - [ ] `click/type/fill`
  - [ ] `screenshot`
  - [ ] 基础断言替代策略（比如 snapshot 后文本检查）

### 涉及文件

- `app/agent/prompts.py`
- `app/agent/nodes/coder.py`（建议重命名或废弃）
- `app/agent/nodes/ui_runner.py`

### 验收标准

- [ ] Agent 最终产物是 playwright-cli 命令列表，而不是 Python 文件

---

## 7. 第六阶段：Run 模型与接口语义统一

> 现在虽然用 `Task` 也能跑，但对求职展示来说，`Run` 更自然。

## 7.1 将任务语义切换为 Run

### 要做

- [ ] 新建 `runs` 语义接口
- [ ] 前端文案全部从“任务”改为“测试运行”
- [ ] 不急着改数据库表名，但 API 层先统一

### 涉及文件

- 新建：`app/api/v1/runs.py`
- 参考：`app/api/v1/tasks.py`
- 复用：`app/services/task_service.py`（可后续改名）

### 验收标准

- [ ] 前端全流程都以“运行”而不是“任务”表达

---

## 7.2 扩展运行详情结构

### 要做

- [ ] `get_run_detail` 返回：
  - [ ] 基础信息
  - [ ] 测试计划
  - [ ] API 用例
  - [ ] UI 用例
  - [ ] API 执行结果
  - [ ] UI 执行结果
  - [ ] 截图列表
  - [ ] 最终总结

### 涉及文件

- `app/schemas/task.py`（或新建 run schema）
- `app/api/v1/tasks.py` / `runs.py`

### 验收标准

- [ ] 前端详情页不需要再自己拼很多结构

---

## 8. 第七阶段：前端详情页升级为“完整演示页”

## 8.1 Run Detail 页需要新增的模块

### 要做

- [ ] 顶部运行摘要卡片
- [ ] 自动生成测试计划面板
- [ ] 自动生成测试用例面板
- [ ] API 执行结果表格
- [ ] UI 执行结果面板
- [ ] 截图预览区域
- [ ] 最终分析总结卡片

### 涉及文件

- `frontend/src/pages/TaskDetailPage.vue`

### 验收标准

- [ ] 打开详情页时，能一页看完整个 Agent 闭环

---

## 8.2 主入口页改成真正的一键输入页

### 要做

- [ ] 只保留一个输入卡片：
  - [ ] URL / Swagger URL / JSON/YAML 文本
- [ ] 增加模式：
  - [ ] `auto`
  - [ ] `api`
  - [ ] `ui`
- [ ] 点击后直接创建运行

### 涉及文件

- `frontend/src/pages/TasksPage.vue`

### 验收标准

- [ ] 演示时 10 秒内可发起一次完整运行

---

## 8.3 历史页改成真正的运行历史

### 要做

- [ ] 历史页显示：
  - [ ] 输入类型
  - [ ] 来源
  - [ ] 模式
  - [ ] 状态
  - [ ] 时间
  - [ ] 详情入口

### 涉及文件

- `frontend/src/pages/ReportsPage.vue`

### 验收标准

- [ ] 至少能清楚查看最近多次测试运行

---

## 9. 第八阶段：测试与稳定性补齐

## 9.1 后端单测 / 集成测试

### 要做

- [ ] 为 Swagger 解析补测试
- [ ] 为 Run 创建接口补测试
- [ ] 为 Agent 分支路由补测试
- [ ] 为 API Runner 补测试
- [ ] 为 playwright-cli 执行包装补测试（可 mock）

### 建议新增测试文件

- `tests/test_run_api.py`
- `tests/test_swagger_parser.py`
- `tests/test_agent_workflow.py`
- `tests/test_ui_runner.py`

---

## 9.2 前端基本联调检查

### 要做

- [ ] Run 页面提交流程检查
- [ ] 详情页 SSE/轮询更新检查
- [ ] 历史页列表展示检查
- [ ] 错误态 / 空态检查

---

## 10. 文件级改造清单（按当前代码库拆分）

## 10.1 后端：必须改

- [ ] `app/agent/state.py`
- [ ] `app/agent/graph.py`
- [ ] `app/agent/prompts.py`
- [ ] `app/agent/nodes/planner.py`
- [ ] `app/agent/nodes/tc_generator.py` 或拆为 `case_generator.py`
- [ ] `app/tools/doc_parser.py`
- [ ] `app/tools/playwright_tool.py`
- [ ] `app/api/v1/tasks.py` 或新建 `runs.py`
- [ ] `app/worker/tasks.py`

## 10.2 后端：建议新增

- [ ] `app/agent/nodes/input_classifier.py`
- [ ] `app/agent/nodes/source_loader.py`
- [ ] `app/agent/nodes/api_runner.py`
- [ ] `app/agent/nodes/ui_runner.py`
- [ ] `app/agent/nodes/reporter.py`
- [ ] `app/api/v1/runs.py`

## 10.3 前端：必须改

- [ ] `frontend/src/router/index.ts`
- [ ] `frontend/src/components/AppSidebar.vue`
- [ ] `frontend/src/components/AppHeader.vue`
- [ ] `frontend/src/pages/TasksPage.vue`
- [ ] `frontend/src/pages/TaskDetailPage.vue`
- [ ] `frontend/src/pages/ReportsPage.vue`

## 10.4 前端：建议删除主入口依赖

- [ ] `frontend/src/pages/CaseGenerationPage.vue`
- [ ] `frontend/src/pages/ApiTestingPage.vue`
- [ ] `frontend/src/pages/UiTestingPage.vue`

---

## 11. 推荐实施顺序（按求职项目优先级）

### P0（先做）

- [ ] 统一 Run 主入口
- [ ] Swagger 输入识别
- [ ] Swagger 结构化解析升级
- [ ] Agent 自动分支
- [ ] API 自动运行
- [ ] playwright-cli 由 Agent 直接调用
- [ ] 详情页展示完整闭环

### P1（其次做）

- [ ] 历史记录页
- [ ] 截图产物保存
- [ ] 最终总结报告
- [ ] SSE/轮询实时进度

### P2（最后做）

- [ ] 保留后台设置页
- [ ] 多环境注入
- [ ] 更复杂的鉴权策略
- [ ] 更细致的断言生成

---

## 12. 暂缓项（这轮先不要做）

为了更贴合求职项目，以下内容这轮建议先不扩展：

- [ ] 多角色权限系统
- [ ] 团队协作
- [ ] 评论 / 审批
- [ ] 通知中心
- [ ] 复杂知识库
- [ ] 多项目 / 多租户
- [ ] 大量系统管理页面
- [ ] 复杂 dashboard 指标

---

## 13. 最终交付验收清单

### 业务能力验收

- [ ] 输入 Swagger URL 后，系统可自动解析并跑接口测试
- [ ] 输入网页 URL 后，系统可自动调用 playwright-cli 跑 UI 测试
- [ ] 输入后无需再手工选端点、手工改脚本、手工点多步按钮
- [ ] 可自动生成详细测试用例
- [ ] 可自动输出结构化报告

### 展示效果验收

- [ ] 首页只有一个主输入入口
- [ ] 详情页能展示完整 Agent 闭环
- [ ] 至少有一次 Swagger 测试演示
- [ ] 至少有一次网页 UI 测试演示
- [ ] 至少能看到截图 / 日志 / 失败摘要

### 简历价值验收

- [ ] 能明确讲出 LangGraph 工作流设计
- [ ] 能明确讲出 Swagger 自动解析与 API 测试生成
- [ ] 能明确讲出 playwright-cli 被 Agent 直接调用
- [ ] 能明确讲出 FastAPI + Celery + Redis 的异步执行链路

---

## 14. 一句话建议

这轮不要再做“更多页面”，而要重点完成：

> **Swagger 自动解析 + Agent 自动分支 + playwright-cli 直接执行 + 一键式运行体验**

这四件事做完，你这个项目就已经很像一个完成度很高的求职向 AI Agent 项目了。

