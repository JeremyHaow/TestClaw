# AI Test Agent 项目技术栈与改造方案

> 目标：把当前 TestClaw 从“多页面测试平台”收缩成一个更适合求职展示的 **AI Test Agent Demo**。  
> 用户只需输入 **网页 URL** 或 **OpenAPI/Swagger 文档**，系统即可自动完成：
>
> - 自动生成测试计划
> - 自动生成详细测试用例
> - 自动执行 API 自动化测试
> - 自动执行 UI 自动化测试
> - 自动输出结构化测试报告

---

## 一、项目定位

### 1.1 项目一句话描述

**AI Test Agent**：一个基于 FastAPI、LangGraph、Playwright 和 OpenAPI 解析能力构建的智能测试系统，输入网址或接口文档后，自动完成测试规划、用例生成、接口/UI 自动化执行与报告输出。

### 1.2 为什么这样更适合求职

相比“大而全测试平台”，这个方向更容易体现：

- AI Agent 工作流设计能力
- 常见后端技术栈能力
- 自动化测试能力
- 前后端联调能力
- 产品闭环能力

面试官也更容易快速理解项目价值。

---

## 二、推荐技术栈

## 2.1 后端技术栈

| 模块 | 技术 | 说明 |
|---|---|---|
| Web 框架 | FastAPI | 轻量、现代、适合 AI/异步任务系统 |
| ORM | SQLAlchemy | 当前项目已在用，保留即可 |
| 数据库 | PostgreSQL / SQLite | 求职 Demo 可先用 SQLite，本地部署简单 |
| 异步任务 | Celery + Redis | 当前项目已具备，适合长任务执行 |
| 配置管理 | Pydantic Settings | 当前已具备，保留 |
| HTTP 请求 | httpx | 适合 API 测试执行 |
| OpenAPI 解析 | PyYAML + JSON 解析 | 当前已有基础能力，继续扩展 |

## 2.2 AI / Agent 技术栈

| 模块 | 技术 | 说明 |
|---|---|---|
| Agent 编排 | LangGraph | 这是项目亮点，建议保留 |
| LLM 接入 | OpenAI / Anthropic | 当前已有 Provider 设计，可简化保留 |
| 结构化输出 | Pydantic + JSON Schema | 让测试计划和测试用例可稳定落地 |
| 提示工程 | Prompt Templates | 用于规划、用例生成、脚本生成、总结 |

## 2.3 自动化测试技术栈

| 模块 | 技术 | 说明 |
|---|---|---|
| UI 自动化 | Playwright | 最适合展示现代前端自动化测试能力 |
| API 自动化 | httpx | 简洁直接，便于根据 OpenAPI 自动组装请求 |
| 结果产出 | pytest 风格日志 / JSON 报告 | 方便前端展示 |
| 截图 / Trace | Playwright screenshot / trace | 提升项目展示效果 |

## 2.4 前端技术栈

| 模块 | 技术 | 说明 |
|---|---|---|
| 框架 | Vue 3 | 当前项目已使用，继续保留即可 |
| 构建工具 | Vite | 当前已具备 |
| 状态管理 | Pinia | 当前已具备 |
| 样式 | Tailwind CSS | 当前已具备，适合快速收敛页面 |
| 请求库 | Axios | 当前已具备 |

## 2.5 部署与工程化

| 模块 | 技术 | 说明 |
|---|---|---|
| 容器化 | Docker + Docker Compose | 当前项目已有，保留 |
| 测试运行环境 | Sandbox / 临时脚本目录 | 当前已有 sandbox，可继续使用 |
| 日志 | structlog / 标准 logging | 当前已有 logging 基础，足够 |

---

## 三、推荐的整体架构

```text
Frontend (Vue3)
    ├── Run Page（输入 URL / 接口文档，一键开始）
    ├── Run Detail Page（计划、用例、执行结果、报告）
    └── History Page（历史记录）

Backend (FastAPI)
    ├── /runs/create
    ├── /runs/{id}
    ├── /runs/{id}/stream
    ├── /documents/parse
    └── /health

Agent Workflow (LangGraph)
    ├── input_classifier
    ├── source_loader
    ├── planner
    ├── case_generator
    ├── api_runner
    ├── ui_runner
    ├── analyzer
    └── reporter

Execution Layer
    ├── OpenAPI Parser
    ├── API Runner (httpx)
    └── UI Runner (Playwright)

Storage
    ├── runs
    ├── test_cases
    ├── documents
    └── artifacts (screenshots / traces / logs)
```

---

## 四、前端设计

## 4.1 页面数量控制

为了突出“Agent 自动化能力”，前端建议只保留 **3 个核心页面**。

### 页面 1：Run Page（一键运行页）

用途：用户唯一主入口。

#### 页面输入

- 输入一个网页 URL  
或
- 输入一个 OpenAPI / Swagger URL  
或
- 粘贴 OpenAPI JSON / YAML 文本

#### 页面控件

- 输入框 / 文本域
- 自动识别类型提示
- 测试类型选择（Auto / API / UI）
- 开始测试按钮

#### 页面输出

- 当前任务状态
- 简单进度条
- 跳转到运行详情页

---

### 页面 2：Run Detail Page（运行详情页）

这是项目核心展示页。

#### 展示内容

1. 基础信息
   - 输入类型
   - 目标地址
   - 启动时间
   - 运行状态

2. 自动生成的测试计划
   - 测试范围
   - 测试阶段
   - 优先级

3. 自动生成的测试用例
   - 标题
   - 步骤
   - 预期结果
   - 分类

4. API 自动化执行结果
   - 接口列表
   - 状态码
   - 耗时
   - 断言结果

5. UI 自动化执行结果
   - 执行步骤
   - 截图
   - 失败步骤
   - Trace 路径

6. 最终报告
   - 总用例数
   - 通过数
   - 失败数
   - 风险总结
   - 失败原因摘要

---

### 页面 3：History Page（历史记录页）

用途：体现项目完整性。

#### 展示内容

- 每次运行记录
- 输入类型（URL / OpenAPI）
- 状态（running / success / failed）
- 创建时间
- 详情入口

---

## 4.2 前端组件设计

建议保留以下通用组件：

- `StatusBadge`
- `LoadingSpinner`
- `EmptyState`
- `Toast`
- `SearchInput`（可选）

建议新增：

- `RunInputCard`
- `RunProgressTimeline`
- `GeneratedCasesPanel`
- `ApiResultsTable`
- `UiArtifactsPanel`
- `FinalSummaryCard`

---

## 4.3 前端路由建议

建议最终路由精简为：

```ts
/login                // 可选，保留基础登录
/run                  // 主入口
/runs/:id             // 运行详情
/history              // 历史记录
```

不再推荐作为主路径暴露的页面：

- `/agent-config`
- `/case-generation`
- `/api-testing`
- `/ui-testing`
- `/reports`
- `/providers`
- `/documents`
- `/knowledge`
- `/environments`

这些能力可以保留后端或后台设置，但不要让它们成为主产品形态。

---

## 五、后端设计

## 5.1 后端核心模块

建议将后端重新收敛成以下模块：

### 1）Run Orchestrator

负责：

- 接收输入
- 创建运行任务
- 调用 Celery / Agent 工作流
- 汇总最终结果

### 2）Input Parser

负责识别输入类型：

- 普通 URL
- OpenAPI URL
- OpenAPI JSON / YAML

### 3）Planner

根据输入生成测试计划：

- API 模式：生成接口测试计划
- UI 模式：生成页面功能测试计划
- Auto 模式：同时规划 API + UI

### 4）Case Generator

生成结构化测试用例：

- title
- steps
- expected
- priority
- category

### 5）API Runner

根据 OpenAPI 端点自动执行：

- 冒烟请求
- 状态码断言
- 关键字段断言
- 基础异常场景

### 6）UI Runner

根据页面 URL 自动执行：

- 页面访问
- 页面元素识别
- 简单交互
- 截图 / 日志 / 失败输出

### 7）Reporter

统一输出报告结构，供前端展示。

---

## 5.2 推荐的数据模型

建议保留并简化为以下几个核心模型：

### `Run`

- id
- input_type (`url` / `openapi`)
- source_input
- target_url
- status
- created_at
- finished_at
- summary_json
- execution_log

### `GeneratedTestCase`

- id
- run_id
- title
- steps
- expected
- priority
- category
- case_type (`api` / `ui`)

### `Document`（可选保留）

- id
- name
- raw_content
- parsed_endpoints

### `Artifact`

- id
- run_id
- type (`screenshot` / `trace` / `log`)
- file_path

---

## 5.3 核心 API 设计

建议简化为下面这组核心接口：

### 创建运行

`POST /api/v1/runs`

请求：

```json
{
  "source": "https://example.com 或 swagger json/url",
  "mode": "auto"
}
```

### 获取运行详情

`GET /api/v1/runs/{id}`

### 实时状态流

`GET /api/v1/runs/{id}/stream`

### 获取历史记录

`GET /api/v1/runs`

### 可选：单独解析文档

`POST /api/v1/documents/parse`

---

## 六、建议的 Agent 工作流

## 6.1 推荐流程

```text
input_classifier
    ↓
source_loader
    ↓
planner
    ↓
case_generator
    ↓
api_runner (if openapi/auto)
    ↓
ui_runner (if url/auto)
    ↓
analyzer
    ↓
reporter
```

## 6.2 各节点职责

### `input_classifier`

识别输入是：

- 网页 URL
- OpenAPI URL
- OpenAPI 内容

### `source_loader`

负责：

- 拉取 URL 内容
- 解析 OpenAPI 文档
- 准备给规划节点的结构化上下文

### `planner`

输出测试计划，例如：

- 页面访问测试
- 表单测试
- 导航测试
- 接口冒烟测试
- 参数边界测试

### `case_generator`

输出详细测试用例，要求是结构化 JSON。

### `api_runner`

直接运行接口测试，不再需要用户手工选端点。

### `ui_runner`

自动生成 Playwright 脚本并执行，不再需要前端单独“生成脚本 -> 点执行”。

### `analyzer`

负责汇总：

- 哪些用例失败
- 哪些断言失败
- 错误摘要

### `reporter`

统一生成前端展示用的最终 JSON。

---

## 七、当前项目的改造思路

## 7.1 可以保留的部分

以下内容建议保留并重用：

### 后端

- `app/agent/`：整体工作流框架保留
- `app/tools/doc_parser.py`：OpenAPI / Postman 解析能力保留
- `app/tools/playwright_tool.py`：Playwright 执行能力保留
- `app/core/llm_gateway.py`：LLM 接入能力保留
- `app/worker/`：Celery 异步任务保留
- `app/api/v1/tasks.py`：可改造成 runs 主入口
- `app/models/test_case.py`：可重命名或继续复用

### 前端

- `frontend/src/lib/api.ts`
- `frontend/src/stores/auth.ts`
- `frontend/src/components/StatusBadge.vue`
- `frontend/src/components/LoadingSpinner.vue`
- `frontend/src/components/EmptyState.vue`
- `frontend/src/components/Toast.vue`
- `frontend/src/layouts/AdminLayout.vue`

---

## 7.2 建议弱化或移除的部分

以下模块不适合作为求职项目主形态，建议隐藏、下线或并入后台配置：

### 前端页面

- `AgentConfigPage.vue`
- `CaseGenerationPage.vue`
- `ApiTestingPage.vue`
- `UiTestingPage.vue`
- `ReportsPage.vue`
- `DocumentsPage.vue`
- `ProvidersPage.vue`
- `EnvironmentsPage.vue`
- `KnowledgePage.vue`

### 原因

这些页面让产品看起来像“平台后台”，而不是“一键自动测试 Agent”。

---

## 7.3 推荐的页面级改造

### 现有页面到新页面的映射

| 当前页面 | 建议处理 |
|---|---|
| `TasksPage.vue` | 改造成 `RunPage.vue`，作为主入口 |
| `TaskDetailPage.vue` | 改造成 `RunDetailPage.vue` |
| `ReportsPage.vue` | 改造成 `HistoryPage.vue` |
| `LoginPage.vue` | 保留 |

---

## 7.4 推荐的后端改造

### 1）把 `tasks` 概念重命名为 `runs`

当前项目主要围绕 `tasks` 设计，但对求职展示来说：

- `run` 更贴近“一次自动测试运行”
- `run detail` 更贴近“报告页”

建议：

- 保留数据库表可不立刻改名
- 但接口语义和前端文案改为 `run`

### 2）把手动 API/UI 接口改为内部能力

当前：

- `api_tests.py` 偏手工请求执行
- `ui_tests.py` 偏手工脚本执行

建议：

- 前端不再直接暴露这些“人工工具型接口”
- 由 Agent 工作流内部自动调用

### 3）文档导入能力改成辅助输入能力

当前 `documents.py` 适合做后台管理；

建议新模式：

- 用户直接输入 OpenAPI URL / 文本
- 系统自动解析
- 可选再落库

### 4）重新设计 `planner/tc_generator/api_executor/coder/executor`

当前问题：

- `planner` 太依赖 prompt 返回，且上下文不够丰富
- `tc_generator` 能生成用例，但没有很好区分 API/UI
- `api_executor` 目前只会跑很浅层的 step
- `coder/executor` 更像脚本生成器，不像“自动 UI 测试 Agent”

建议改造为：

- `planner`：显式输出 `api_plan` / `ui_plan`
- `case_generator`：显式输出 `api_cases` / `ui_cases`
- `api_runner`：根据 endpoint 自动生成请求并断言
- `ui_runner`：根据 URL 直接生成并执行 Playwright 脚本

---

## 八、建议的代码级改造方向

## 8.1 前端目录调整建议

建议新增：

```text
frontend/src/pages/RunPage.vue
frontend/src/pages/RunDetailPage.vue
frontend/src/pages/HistoryPage.vue

frontend/src/components/run/RunInputCard.vue
frontend/src/components/run/RunSummaryCard.vue
frontend/src/components/run/GeneratedCasesPanel.vue
frontend/src/components/run/ApiResultsTable.vue
frontend/src/components/run/UiResultsPanel.vue
```

建议精简：

```text
frontend/src/router/index.ts
frontend/src/components/AppSidebar.vue
frontend/src/components/AppHeader.vue
```

---

## 8.2 后端目录调整建议

建议新增或重构：

```text
app/api/v1/runs.py
app/services/run_service.py

app/agent/nodes/input_classifier.py
app/agent/nodes/source_loader.py
app/agent/nodes/case_generator.py
app/agent/nodes/api_runner.py
app/agent/nodes/ui_runner.py
app/agent/nodes/reporter.py
```

建议逐步弱化：

```text
app/api/v1/api_tests.py
app/api/v1/ui_tests.py
```

不再作为主流程入口，而是作为内部执行能力保留或删减。

---

## 九、建议的 MVP 实施顺序

## 第 1 步：统一输入入口

目标：

- 前端只保留一个主入口
- 支持 URL / OpenAPI 输入

产出：

- `RunPage`
- `POST /runs`

## 第 2 步：改造 Agent 工作流

目标：

- 输入后自动走完规划、用例生成、执行、汇总

产出：

- 新的 LangGraph 流程

## 第 3 步：自动 API 测试

目标：

- 从 OpenAPI 自动生成端点测试，不再人工选择

产出：

- `api_runner`
- 结构化 API 执行结果

## 第 4 步：自动 UI 测试

目标：

- 输入 URL 后自动生成并执行 Playwright 测试

产出：

- `ui_runner`
- 截图 / 日志 / 失败输出

## 第 5 步：详情页和报告页

目标：

- 展示完整闭环结果

产出：

- `RunDetailPage`
- `HistoryPage`

---

## 十、最终建议

对于当前阶段，这个项目不应该继续往“复杂平台”方向扩展，而应该：

### 收缩目标

从：

> 多模块测试管理平台

收缩为：

> 一键输入网址或接口文档即可自动完成测试的 AI Test Agent

### 项目展示重点

重点展示：

1. Agent 自动规划能力
2. OpenAPI 自动解析与 API 自动测试能力
3. Playwright UI 自动化能力
4. 异步任务与实时状态展示能力
5. 结构化测试报告能力

这套组合已经足够作为一个很完整的求职项目。

