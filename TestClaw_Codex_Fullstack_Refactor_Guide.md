# TestClaw 全栈智能体化改造开发指南（给 Codex 使用）

> 目标：把 TestClaw 从“测试管理后台 + 若干 AI 能力”改造成一个更清晰、更智能、更适合演示的 **AI Testing Agent Workspace**。
>
> 本文档用于指导 Codex 分阶段开发，覆盖前端、后端、数据库、Agent 工作流、API、UI/UX、测试、迁移与验收标准。

---

## 0. 项目改造总目标

### 0.1 产品定位

TestClaw 的主产品形态应从：

> 多模块测试平台 / 后台管理系统

收敛为：

> 用户输入测试目标，AI 主动追问、生成计划、执行 API/UI 测试、采集证据、输出报告并沉淀记忆的 AI Testing Agent。

### 0.2 核心体验闭环

```text
用户输入目标
  ↓
AI 追问缺失信息
  ↓
生成计划草案
  ↓
运行前预检
  ↓
Agent 执行 API/UI 测试
  ↓
实时展示动作、日志、证据
  ↓
生成结构化报告
  ↓
沉淀质量记忆和可复用用例
```

### 0.3 产品原则

1. **用户不是来配置系统的，而是来委派任务的。**
2. **所有 AI 输出都必须结构化，可保存、可复用、可追溯。**
3. **所有测试执行都必须有证据，不只给结论。**
4. **默认安全，只读优先。任何写操作都需要显式确认。**
5. **Run Detail 要像 Agent Cockpit，而不是静态报告页。**

### 0.4 核心概念

| 概念 | 说明 |
|---|---|
| Mission | 用户委派的测试意图 |
| Plan | AI 生成的测试计划草案 |
| Run | 一次实际测试运行 |
| Evidence | API 响应、截图、trace、日志、断言等证据 |
| Finding | 测试发现 / 风险 / 缺陷 |
| Memory | 目标历史质量记忆 |
| Asset | 文档、用例、环境、知识等可复用资产 |

---

## 1. 当前项目基础与保留策略

### 1.1 保留技术栈

后端继续使用：

- FastAPI
- SQLAlchemy Async ORM
- PostgreSQL / SQLite 兼容
- Redis
- Celery
- LangGraph
- httpx
- Playwright
- Pydantic v2
- Alembic
- structlog / logging
- PyYAML / JSON OpenAPI parser

前端继续使用：

- Vue 3
- Vite
- TypeScript
- Pinia
- Vue Router
- Tailwind CSS
- lucide-vue-next
- Axios
- ECharts（仅用于历史趋势和质量统计）

### 1.2 保留但重定位的模块

| 当前能力 | 新定位 |
|---|---|
| 接口文档 | Agent 可理解的 API 资产 |
| 测试环境 | Agent 运行环境配置 |
| 用例资产 | Agent 可复用测试资产 |
| RAG 知识库 | Agent 背景知识和规则来源 |
| 模型 Provider | Agent 能力配置 |
| API 测试 | Agent 内部执行工具 |
| UI 测试 | Agent 内部执行工具 |
| Reports | Run Detail / History 的一部分 |
| Tasks | 逐步迁移为 Runs 的语义 |

### 1.3 旧模块兼容原则

- 不要一次性删除旧 API。
- 新 UI 可以先消费旧 API，但命名和交互要以 Run / Mission / Plan 为核心。
- 后端先新增 service 层，再逐步把 `runs.py` / `tasks.py` 中的复杂逻辑迁出。
- 数据库优先新增表和字段，不做破坏性改表。
- 如果现有 `tasks` 表已经承担 run 角色，短期可保留表名，但前端文案统一使用“运行 / Run”。

---

## 2. 信息架构重构

### 2.1 主导航

左侧导航分三组。

```text
Workspace
- 智能计划        /agent-plan
- 任务委派        /run
- 运行历史        /history
- 质量记忆        /quality-memory

Assets
- 接口文档        /documents
- 测试环境        /environments
- 用例资产        /test-cases

Settings
- 模型与 Agent    /providers
- RAG 知识库      /knowledge
```

### 2.2 页面主次关系

一级主路径：

- `/agent-plan`：AI 计划模式，默认入口。
- `/run`：手动任务委派，高级入口。
- `/runs/:id`：Agent Cockpit，运行详情。
- `/history`：历史运行与报告。

二级资产路径：

- `/documents`
- `/environments`
- `/test-cases`
- `/quality-memory`

高级设置路径：

- `/providers`
- `/knowledge`

### 2.3 默认跳转

登录成功后进入：

```text
/agent-plan
```

根路径 `/` 和 `/dashboard` 都重定向到 `/agent-plan`。

---

## 3. 视觉设计系统

### 3.1 整体风格

风格关键词：

- Light SaaS
- Agent Workspace
- Rounded cards
- Calm spacing
- Soft shadows
- Blue accent
- Structured AI planning

### 3.2 颜色规范

```text
Page background:     #F5F7FB / #F7F9FC
Surface white:       #FFFFFF
Border light:        #E5EAF3
Border softer:       #EEF2F7
Primary blue:        #2563EB
Primary hover:       #1D4ED8
Blue light bg:       #EFF6FF
Success green:       #10B981
Success bg:          #ECFDF5
Warning orange:      #F59E0B
Warning bg:          #FFFBEB
Danger red:          #EF4444
Danger bg:           #FEF2F2
Text primary:        #0F172A
Text secondary:      #475569
Text muted:          #94A3B8
Text faint:          #CBD5E1
```

### 3.3 圆角与阴影

```text
Small radius:        8px
Default radius:      12px
Large card radius:   16px
Hero radius:         20px

Card shadow:
0 8px 24px rgba(15, 23, 42, 0.04)

Floating shadow:
0 16px 40px rgba(15, 23, 42, 0.08)
```

### 3.4 字体层级

```text
Page title:          text-2xl / font-semibold
Section title:       text-base / font-semibold
Card title:          text-sm or text-base / font-semibold
Body:                text-sm
Meta:                text-xs
Micro label:         text-[11px] uppercase tracking-wide
```

### 3.5 通用状态

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

---

## 4. 前端目录规划

### 4.1 新增 UI 基础组件

```text
frontend/src/components/ui/
  TcButton.vue
  TcCard.vue
  TcBadge.vue
  TcInput.vue
  TcTextarea.vue
  TcSelect.vue
  TcStepBar.vue
  TcOptionCard.vue
  TcProgress.vue
  TcTabs.vue
  TcEmptyState.vue
  TcSkeleton.vue
  TcDrawer.vue
  TcModal.vue
  TcTooltip.vue
```

### 4.2 新增 Agent 业务组件

```text
frontend/src/components/agent/
  AgentQuestionCard.vue
  AgentPlanDraft.vue
  AgentMissingInfoList.vue
  AgentChatInput.vue
  AgentExecutionFlow.vue
  AgentMemoryPanel.vue
  AgentStatusRail.vue
  AgentTimeline.vue
  AgentCurrentActionCard.vue
  AgentEvidenceCard.vue
  AgentInterventionDrawer.vue
  AgentRunSummary.vue
```

### 4.3 新增 Run 组件

```text
frontend/src/components/run/
  RunMissionCard.vue
  RunModeSelector.vue
  RunPolicySelector.vue
  RunTargetContextCard.vue
  RunSafetyBoundaryCard.vue
  RunAuthPreflightCard.vue
  RunPreflightStatusCard.vue
  RunHandoffPreview.vue
  RunBottomActionBar.vue
```

### 4.4 新增 Asset 组件

```text
frontend/src/components/assets/
  DocumentAssetCard.vue
  EnvironmentCard.vue
  TestCaseAssetCard.vue
  KnowledgeAssetCard.vue
```

### 4.5 API 客户端拆分

当前可保留 `frontend/src/lib/api.ts`，但建议逐步拆为：

```text
frontend/src/api/http.ts
frontend/src/api/runs.ts
frontend/src/api/agentPlans.ts
frontend/src/api/documents.ts
frontend/src/api/environments.ts
frontend/src/api/testCases.ts
frontend/src/api/providers.ts
frontend/src/api/knowledge.ts
frontend/src/api/memory.ts
```

`http.ts` 统一处理：

- baseURL
- Authorization header
- 401 跳转登录
- error normalization
- request timeout

---

## 5. 前端状态管理规划

### 5.1 Pinia store

```text
frontend/src/stores/auth.ts          保留
frontend/src/stores/agentPlan.ts     新增/重构
frontend/src/stores/runs.ts          新增/重构
frontend/src/stores/assets.ts        可选
frontend/src/stores/ui.ts            全局 drawer/modal/toast/sidebar
```

### 5.2 agentPlan store

```ts
interface AgentPlanState {
  sessionId: string | null
  currentStep: 'target' | 'scope' | 'auth' | 'safety' | 'success'
  collected: {
    target?: PlanTarget
    scope?: PlanScope
    auth?: PlanAuth
    safety?: PlanSafety
    success?: PlanSuccess
  }
  draft: PlanDraft | null
  messages: AgentPlanMessage[]
  missingInfo: MissingInfo[]
  isGenerating: boolean
}
```

### 5.3 runs store

```ts
interface RunsState {
  currentRun: RunDetail | null
  streamConnected: boolean
  timeline: RunTimelineEvent[]
  currentAction: AgentAction | null
  evidence: EvidenceItem[]
  findings: Finding[]
  report: RunReport | null
}
```

---

## 6. 页面 UI 与交互设计

## 6.1 智能计划页：`AgentPlanPage.vue`

### 6.1.1 页面目标

这是默认入口，用于引导用户像和 AI 对话一样生成测试计划。

### 6.1.2 布局

```text
┌─────────────────────────────────────────────────────┐
│ Header: TESTCLAW / Workspace + 生成测试计划按钮       │
├──────────────┬───────────────────────┬──────────────┤
│ 会话历史      │ AI 计划问答区           │ 计划草案      │
└──────────────┴───────────────────────┴──────────────┘
```

### 6.1.3 左侧会话历史

内容：

- `+ 新建计划`
- 会话历史列表
- 当前会话高亮
- 状态：进行中 / 已完成 / 已归档
- 底部回收站

### 6.1.4 中间问答区

顶部：

```text
让我们先明确测试目标
我会逐步向你确认关键信息，你可以直接选择，也可以补充说明。
```

步骤条：

```text
1 测试目标
2 覆盖范围
3 登录方式
4 安全边界
5 成功标准
```

当前问题卡：

```text
你要先测试哪类目标？

[接口文档 / OpenAPI]
[网页页面]
[登录流程]
[回归巡检]
[自定义目标]

补充说明（可选）
[textarea]

[跳过] [稍后补充] [继续]
```

### 6.1.5 右侧计划草案

```text
计划草案
已收集 2 / 5

测试目标：未确认 / 待补充
覆盖范围：关键路径、基础可用性
凭证：需要登录
安全边界：待读取
成功标准：待确认

[预览完整计划]
```

### 6.1.6 底部自由输入

```text
直接告诉我更多背景，或粘贴 URL / OpenAPI / Swagger 来源…
[粘贴接口文档] [上传截图] [添加约束]
```

### 6.1.7 交互规则

1. 用户选择 option card 后，右侧草案实时更新。
2. 用户点击继续后进入下一步骤。
3. 用户在自由输入中补充自然语言后，前端调用 `/api/v1/agent-plans/sessions/{id}/intake`。
4. 后端返回结构化抽取结果和下一步问题。
5. 如果 5 步都完成，按钮从 `继续` 变为 `生成测试计划`。
6. 生成计划后进入预览 Modal，用户可选择：
   - 回到计划继续编辑
   - 创建 Run
   - 保存为草案

### 6.1.8 空状态

无会话时显示：

```text
还没有测试计划
告诉 TestClaw 你想测试的网站或接口文档，它会帮你一步步补齐计划。
[创建第一个计划]
```

---

## 6.2 任务委派页：`RunPage.vue`

### 6.2.1 页面目标

高级模式，用户可以直接配置一次测试任务，但 UI 仍然要像 Agent Mission Control。

### 6.2.2 顶部 Hero

```text
MISSION CONTROL
Testing Agent Workspace
像分配测试任务一样描述目标、上下文和安全边界。TestClaw 会先预检，再自动规划 API/UI 路径、执行并沉淀证据。

[Blocked] [查看历史]

1 识别目标
2 制定测试计划
3 生成用例
4 执行并采集证据
5 输出报告
```

### 6.2.3 主区域

#### 任务委派卡

字段：

- 测试任务 textarea
- API 文档 select
- 接口文档跳转按钮

#### 测试模式卡

选项：

- 接口测试
- UI 测试

#### API 执行策略卡

选项：

- 安全只读
- 带鉴权只读
- 允许写入

#### 目标上下文卡

字段：

- 推断目标
- 高级/可选目标设置 accordion

高级字段：

- 目标模块
- 优先级
- 期望输出
- 是否保存用例资产

#### 安全边界卡

字段：

- 安全约束 textarea
- 推荐约束 chips

#### 鉴权预检卡

字段：

- 鉴权方式：自动获取 Token / 手动 Token / 无需登录
- 用户名
- 密码
- Header name
- Token prefix
- 验证码策略：无验证码 / 固定验证码 / 动态验证码

### 6.2.4 右侧栏

卡片：

- 预检状态
- 目标记忆 / Agent Memory
- 智能体执行流
- 任务交接预览

### 6.2.5 底部操作栏

```text
启动后，智能体会进入 Agent Cockpit，持续展示计划、当前动作、日志和证据。

[运行前预检] [启动测试智能体]
```

### 6.2.6 交互规则

1. 只要必填信息缺失，右侧状态为 `Blocked`。
2. 点击 `运行前预检` 调用 `/api/v1/runs/preflight`。
3. 预检成功后状态变为 `Ready`。
4. 点击 `启动测试智能体` 调用 `POST /api/v1/runs`。
5. 创建成功后跳转 `/runs/:id`。

---

## 6.3 运行详情页：`RunDetailPage.vue`

### 6.3.1 页面目标

把运行详情做成 Agent Cockpit，实时展示智能体行为、证据和结论。

### 6.3.2 顶部概览

```text
TESTCLAW / Run Detail
电商网站接口测试

状态：Running
目标：OpenAPI 文档
模式：API
策略：安全只读
开始时间：10:32
耗时：02:14

[暂停] [人工介入] [导出报告]
```

### 6.3.3 三栏布局

左侧：Agent Timeline

```text
✓ 识别输入
✓ 解析接口
● 生成测试计划
○ 生成用例
○ 执行 API 测试
○ 汇总报告
```

中间：主内容 tabs

```text
[当前动作] [测试计划] [测试用例] [执行日志] [证据] [报告]
```

右侧：运行摘要

```text
进度：3/5
已生成用例：18
已执行：6
通过：5
失败：1
跳过：4
当前阻塞：/users 返回 401，正在尝试自动鉴权
```

### 6.3.4 当前动作卡

```text
Planner 正在生成测试计划

思考摘要：
我会优先覆盖登录、用户信息、权限校验和只读查询接口。
写接口将根据安全策略跳过。

工具：
OpenAPI Parser
LLM Planner

[查看原始输出]
```

### 6.3.5 人工介入 Drawer

```text
给智能体补充说明

[textarea]
例如：这个接口需要 header X-Tenant-ID，值为 demo

应用范围：
[当前步骤] [后续所有步骤] [取消当前执行并重规划]

[取消] [提交给智能体]
```

调用接口：

```http
POST /api/v1/runs/{run_id}/interventions
```

### 6.3.6 实时流

前端通过 SSE 连接：

```http
GET /api/v1/runs/{run_id}/stream
```

事件类型：

```ts
type RunStreamEvent =
  | { type: 'run.status'; status: string }
  | { type: 'agent.step.started'; step: string; title: string }
  | { type: 'agent.step.finished'; step: string; summary: string }
  | { type: 'agent.action'; action: AgentAction }
  | { type: 'tool.call'; tool: string; inputSummary: string }
  | { type: 'tool.result'; tool: string; outputSummary: string; status: string }
  | { type: 'evidence.created'; evidence: EvidenceItem }
  | { type: 'finding.created'; finding: Finding }
  | { type: 'run.report.updated'; report: RunReport }
  | { type: 'run.finished'; status: string }
```

---

## 6.4 运行历史页：`HistoryPage.vue`

### 6.4.1 页面目标

展示历史运行、质量趋势、可复用资产。

### 6.4.2 顶部统计

```text
总运行次数
成功率
发现问题数
平均耗时
证据完整率
```

### 6.4.3 过滤器

```text
状态：全部 / 运行中 / 成功 / 失败 / 发现问题
模式：全部 / API / UI / Full
时间：今天 / 7 天 / 30 天 / 自定义
搜索：目标、文档、问题关键词
```

### 6.4.4 运行卡片

```text
电商网站回归测试
API + UI · 安全只读 · 运行完成

目标：https://example.com/swagger.json
结果：通过 21 / 失败 2 / 跳过 8
主要风险：用户搜索接口空参数返回 500

[查看详情] [重新运行] [导出报告]
```

---

## 6.5 质量记忆页：`QualityMemoryPage.vue`

### 6.5.1 页面目标

把历史运行沉淀为 Agent 的长期记忆。

### 6.5.2 顶部统计

```text
已记忆目标
复用用例
高频阻塞
平均复用率
```

### 6.5.3 目标记忆卡

```text
电商后台管理系统
历史运行：18 次
最近问题：登录 Token 过期、用户列表 500
可复用用例：23
建议策略：先运行鉴权预检，再执行只读接口测试

[查看记忆] [用于新计划]
```

### 6.5.4 详情 Drawer

```text
高频失败主题
已沉淀资产
已知阻塞点
推荐下次策略
相关历史运行
```

---

## 6.6 接口文档页：`DocumentsPage.vue`

### 6.6.1 页面目标

管理 Agent 可理解的 API 资产。

### 6.6.2 顶部

```text
接口文档
导入 OpenAPI / Swagger / Postman 文档，让智能体理解接口结构并生成测试计划。

[导入文档] [粘贴 OpenAPI] [从 URL 解析]
```

### 6.6.3 文档卡片

```text
电商后台 Swagger
OpenAPI 3.0
Endpoints：56
需要鉴权：31
最近使用：今天 10:32
解析状态：Ready
基础地址：https://api.example.com

[查看接口] [运行测试] [更新文档]
```

### 6.6.4 文档详情 tabs

```text
[概览] [Endpoints] [鉴权链路] [Schema] [历史运行]
```

---

## 6.7 测试环境页：`EnvironmentsPage.vue`

### 6.7.1 页面目标

管理 base_url、headers、proxy、安全策略。

### 6.7.2 环境卡片

```text
开发环境
base_url: https://dev-api.example.com
默认策略：安全只读
状态：可用

[设为默认] [编辑] [运行健康检查]
```

### 6.7.3 健康检查状态

```text
✓ Base URL 可访问
✓ Redis Worker 在线
✓ 浏览器执行器可用
✗ 登录账号缺失
```

---

## 6.8 用例资产页：`TestCasesPage.vue`

### 6.8.1 页面目标

展示 Agent 生成和人工整理的可复用用例。

### 6.8.2 用例卡片

```text
登录成功获取 Token
类型：API
优先级：P0
来源：Agent 生成
最近结果：通过
复用次数：8

[查看] [加入计划] [编辑]
```

---

## 6.9 模型与 Agent 页：`ProvidersPage.vue`

### 6.9.1 页面目标

配置 Planner / Executor / Vision 模型和 Agent 策略。

### 6.9.2 模型卡片

```text
Planner Model
用于生成测试计划和用例
当前：xxx
状态：Active

[设为默认] [测试连接] [编辑]
```

### 6.9.3 Agent 策略

```text
保守模式
- 默认只读
- 不执行删除/修改
- 失败后优先询问用户

平衡模式
- 允许临时测试数据
- 自动重试
- 自动补充边界用例

探索模式
- 更主动发现路径
- 更高工具调用次数
- 适合测试环境
```

---

## 6.10 RAG 知识库页：`KnowledgePage.vue`

### 6.10.1 页面目标

为 Agent 提供业务规则、测试规范、历史缺陷、接口说明。

### 6.10.2 知识卡片

```text
支付模块测试规范
类型：测试规范
片段：32
最近更新：昨天
使用次数：11

[查看] [重新索引] [禁用]
```

---

## 7. 后端重构规划

## 7.1 后端目标架构

```text
app/
  api/
    router.py
    v1/
      runs.py
      agent_plans.py
      documents.py
      environments.py
      test_cases.py
      providers.py
      knowledge.py
      quality_memory.py
  schemas/
    run.py
    agent_plan.py
    document.py
    environment.py
    test_case.py
    memory.py
  services/
    run_service.py
    agent_plan_service.py
    preflight_service.py
    auth_preflight_service.py
    target_memory_service.py
    evidence_service.py
    report_service.py
    artifact_service.py
    document_service.py
  agent/
    orchestrator.py
    state.py
    graph.py
    nodes/
      input_classifier.py
      source_loader.py
      mission_planner.py
      knowledge_retriever.py
      planner.py
      case_generator.py
      api_runner.py
      ui_login.py
      ui_test_planner.py
      ui_runner.py
      execution_evaluator.py
      reporter.py
      knowledge_sink.py
  models/
  migrations/
```

### 7.2 控制器瘦身原则

`app/api/v1/*.py` 只负责：

- 接收请求
- 校验权限
- 调用 service
- 返回 response

禁止在 API 文件中写大量：

- OpenAPI 解析逻辑
- 预检业务逻辑
- 鉴权探测逻辑
- Memory 聚合逻辑
- Report 生成逻辑

这些都应放到 `services/` 或 `agent/nodes/`。

---

## 8. 后端核心 API 设计

## 8.1 Agent Plan API

### 创建计划会话

```http
POST /api/v1/agent-plans/sessions
```

Request:

```json
{
  "title": "测试智能体计划"
}
```

Response:

```json
{
  "id": "uuid",
  "title": "测试智能体计划",
  "status": "draft",
  "current_step": "target",
  "created_at": "..."
}
```

### 用户补充信息 / AI intake

```http
POST /api/v1/agent-plans/sessions/{session_id}/intake
```

Request:

```json
{
  "message": "主要测登录、用户管理、权限，不要执行删除操作",
  "selected_option": null,
  "current_step": "target"
}
```

Response:

```json
{
  "extracted": {
    "target": "接口文档 / OpenAPI",
    "scope": ["登录", "用户管理", "权限"],
    "safety": ["禁止删除操作"]
  },
  "draft": {
    "target": { "value": "接口文档 / OpenAPI", "status": "confirmed" },
    "scope": { "value": "登录、用户管理、权限", "status": "confirmed" },
    "auth": { "value": null, "status": "missing" },
    "safety": { "value": "禁止删除操作", "status": "confirmed" },
    "success": { "value": null, "status": "pending" }
  },
  "next_question": {
    "step": "auth",
    "title": "目标是否需要登录？",
    "options": ["需要登录", "不需要登录", "稍后补充"]
  },
  "missing_info": [
    { "key": "auth", "label": "登录方式", "required": true }
  ]
}
```

### 生成计划草案

```http
POST /api/v1/agent-plans/sessions/{session_id}/generate
```

Response:

```json
{
  "plan_id": "uuid",
  "status": "ready",
  "summary": "本次将覆盖登录、用户管理、权限接口，默认使用安全只读策略。",
  "api_plan": {},
  "ui_plan": {},
  "safety_boundary": {},
  "recommended_run_payload": {}
}
```

### 从计划创建 Run

```http
POST /api/v1/agent-plans/{plan_id}/create-run
```

Response:

```json
{
  "run_id": "uuid",
  "detail_url": "/runs/uuid"
}
```

---

## 8.2 Run API

### 运行前预检

```http
POST /api/v1/runs/preflight
```

Request:

```json
{
  "source": "https://example.com/swagger.json",
  "test_type": "api",
  "objective": "验证登录、用户管理和权限接口",
  "document_id": "uuid",
  "environment_id": "uuid",
  "api_execution_policy": "safe_read_only",
  "auth_mode": "auto",
  "auth_credentials": {
    "username": "admin",
    "password": "***"
  },
  "safety_boundary": "不要删除真实数据"
}
```

Response:

```json
{
  "readiness": "ready",
  "checks": [
    { "key": "model", "label": "默认规划模型", "status": "passed", "detail": "已配置" },
    { "key": "worker", "label": "Worker", "status": "passed", "detail": "在线" },
    { "key": "document", "label": "接口文档", "status": "passed", "detail": "发现 56 个 endpoints" },
    { "key": "auth", "label": "鉴权", "status": "warning", "detail": "需要运行登录预检" }
  ],
  "mission_preview": {
    "input_type": "openapi",
    "test_mode": "api",
    "execution_policy": "safe_read_only",
    "endpoint_count": 56,
    "estimated_executable_count": 40,
    "estimated_skipped_count": 16,
    "auth_required_count": 31
  },
  "can_start": true
}
```

### 创建 Run

```http
POST /api/v1/runs
```

Request:

```json
{
  "source": "https://example.com/swagger.json",
  "test_type": "api",
  "objective": "验证登录、用户管理和权限接口",
  "document_id": "uuid",
  "environment_id": "uuid",
  "api_execution_policy": "safe_read_only",
  "auth_mode": "auto",
  "auth_credentials": {
    "username": "admin",
    "password": "***"
  },
  "safety_boundary": "不要删除真实数据",
  "plan_id": "uuid"
}
```

Response:

```json
{
  "id": "uuid",
  "status": "queued",
  "detail_url": "/runs/uuid"
}
```

### 获取 Run 详情

```http
GET /api/v1/runs/{run_id}
```

Response 需要包含：

```json
{
  "id": "uuid",
  "status": "running",
  "objective": "...",
  "target": "...",
  "test_type": "api",
  "execution_policy": "safe_read_only",
  "timeline": [],
  "current_action": {},
  "api_plan": {},
  "ui_plan": {},
  "api_cases": [],
  "ui_cases": [],
  "api_results": [],
  "ui_results": [],
  "evidence": [],
  "findings": [],
  "report": null,
  "created_at": "...",
  "started_at": "...",
  "finished_at": null
}
```

### Run 流式事件

```http
GET /api/v1/runs/{run_id}/stream
```

使用 `text/event-stream`。

事件格式：

```text
event: agent.step.started
data: {"step":"planner","title":"生成测试计划"}
```

### 人工介入

```http
POST /api/v1/runs/{run_id}/interventions
```

Request:

```json
{
  "supplemental_instructions": "所有请求都需要 X-Tenant-ID=demo",
  "scope": "future_steps",
  "cancel_current": false,
  "replan": true
}
```

### 取消 Run

```http
POST /api/v1/runs/{run_id}/cancel
```

### 导出报告

```http
GET /api/v1/runs/{run_id}/exports/markdown
GET /api/v1/runs/{run_id}/exports/json
```

---

## 9. 数据库设计

## 9.1 设计原则

- 保留现有表，优先新增表和字段，减少破坏性迁移。
- `tasks` 可以逐步兼容为 `runs`，但新接口和前端文案使用 Run。
- 所有 AI 生成内容必须结构化保存，便于前端展示和历史复用。
- 所有工具调用、证据、发现、人工介入都要可追溯。

---

## 9.2 表：agent_plan_sessions

用于智能计划页的会话。

```sql
CREATE TABLE agent_plan_sessions (
  id UUID PRIMARY KEY,
  user_id UUID NULL,
  title VARCHAR(255) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'draft',
  current_step VARCHAR(64) NOT NULL DEFAULT 'target',
  collected_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  draft_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  missing_info_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
  archived_at TIMESTAMP NULL
);
```

状态：

```text
draft
ready
converted_to_run
archived
```

---

## 9.3 表：agent_plan_messages

保存计划页对话与选择记录。

```sql
CREATE TABLE agent_plan_messages (
  id UUID PRIMARY KEY,
  session_id UUID NOT NULL REFERENCES agent_plan_sessions(id) ON DELETE CASCADE,
  role VARCHAR(32) NOT NULL,
  content TEXT NOT NULL,
  step VARCHAR(64) NULL,
  selected_option_json JSONB NULL,
  extracted_json JSONB NULL,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

role：

```text
user
assistant
system
```

---

## 9.4 表：agent_plans

保存生成后的测试计划草案。

```sql
CREATE TABLE agent_plans (
  id UUID PRIMARY KEY,
  session_id UUID NULL REFERENCES agent_plan_sessions(id),
  title VARCHAR(255) NOT NULL,
  objective TEXT NOT NULL,
  target_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  scope_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  auth_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  safety_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  success_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  api_plan_json JSONB NULL,
  ui_plan_json JSONB NULL,
  recommended_run_payload_json JSONB NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'ready',
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

## 9.5 表：runs

如果项目已有 `tasks` 表，可以先新增 `runs` 视图语义或直接新增表。推荐新增 `runs`，再逐步迁移。

```sql
CREATE TABLE runs (
  id UUID PRIMARY KEY,
  plan_id UUID NULL REFERENCES agent_plans(id),
  user_id UUID NULL,
  title VARCHAR(255) NOT NULL,
  objective TEXT NOT NULL,
  source_input TEXT NOT NULL,
  input_type VARCHAR(64) NOT NULL,
  target_url TEXT NULL,
  document_id UUID NULL,
  environment_id UUID NULL,
  test_type VARCHAR(32) NOT NULL,
  execution_policy VARCHAR(64) NOT NULL DEFAULT 'safe_read_only',
  auth_mode VARCHAR(64) NOT NULL DEFAULT 'auto',
  captcha_mode VARCHAR(64) NOT NULL DEFAULT 'none',
  safety_boundary TEXT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  progress_percent INTEGER NOT NULL DEFAULT 0,
  current_step VARCHAR(128) NULL,
  current_action_json JSONB NULL,
  preflight_json JSONB NULL,
  api_plan_json JSONB NULL,
  ui_plan_json JSONB NULL,
  final_report_json JSONB NULL,
  summary_json JSONB NULL,
  error_message TEXT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  queued_at TIMESTAMP NULL,
  started_at TIMESTAMP NULL,
  finished_at TIMESTAMP NULL,
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

status：

```text
pending
queued
running
waiting_for_user
blocked
succeeded
failed
bug_found
cancelled
```

---

## 9.6 表：run_events

保存 timeline 和 stream 事件。

```sql
CREATE TABLE run_events (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  sequence INTEGER NOT NULL,
  event_type VARCHAR(128) NOT NULL,
  title VARCHAR(255) NULL,
  summary TEXT NULL,
  payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_run_events_run_seq ON run_events(run_id, sequence);
```

---

## 9.7 表：run_interventions

保存用户人工介入。

```sql
CREATE TABLE run_interventions (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  user_id UUID NULL,
  supplemental_instructions TEXT NOT NULL,
  scope VARCHAR(64) NOT NULL DEFAULT 'future_steps',
  cancel_current BOOLEAN NOT NULL DEFAULT FALSE,
  replan BOOLEAN NOT NULL DEFAULT TRUE,
  status VARCHAR(32) NOT NULL DEFAULT 'submitted',
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  applied_at TIMESTAMP NULL
);
```

---

## 9.8 表：run_tool_calls

保存工具调用。

```sql
CREATE TABLE run_tool_calls (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  node_name VARCHAR(128) NOT NULL,
  tool_name VARCHAR(128) NOT NULL,
  input_summary TEXT NULL,
  input_json JSONB NULL,
  output_summary TEXT NULL,
  output_json JSONB NULL,
  status VARCHAR(32) NOT NULL,
  duration_ms INTEGER NULL,
  error_message TEXT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

## 9.9 表：run_evidence

保存 API 响应、截图、trace、日志等证据。

```sql
CREATE TABLE run_evidence (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  case_id UUID NULL,
  evidence_type VARCHAR(64) NOT NULL,
  title VARCHAR(255) NOT NULL,
  summary TEXT NULL,
  status VARCHAR(32) NULL,
  file_path TEXT NULL,
  url TEXT NULL,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

evidence_type：

```text
api_response
screenshot
playwright_trace
log
assertion
html_snapshot
network
```

---

## 9.10 表：run_findings

保存测试发现。

```sql
CREATE TABLE run_findings (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  title VARCHAR(255) NOT NULL,
  severity VARCHAR(32) NOT NULL DEFAULT 'medium',
  confidence VARCHAR(32) NOT NULL DEFAULT 'medium',
  category VARCHAR(64) NOT NULL,
  surface VARCHAR(255) NULL,
  description TEXT NOT NULL,
  evidence_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  reproduction_steps_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  next_action TEXT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'open',
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

## 9.11 表：target_memories

保存目标级质量记忆。

```sql
CREATE TABLE target_memories (
  id UUID PRIMARY KEY,
  target_key VARCHAR(512) NOT NULL UNIQUE,
  target_label VARCHAR(255) NOT NULL,
  target_type VARCHAR(64) NOT NULL,
  run_count INTEGER NOT NULL DEFAULT 0,
  last_run_id UUID NULL,
  recurring_themes_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  known_blockers_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  reusable_assets_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  suggested_strategy TEXT NULL,
  confidence VARCHAR(32) NOT NULL DEFAULT 'medium',
  updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

## 9.12 表：artifacts

如果已有 artifact 逻辑，可复用；否则新增：

```sql
CREATE TABLE artifacts (
  id UUID PRIMARY KEY,
  run_id UUID NULL REFERENCES runs(id) ON DELETE CASCADE,
  artifact_type VARCHAR(64) NOT NULL,
  storage_backend VARCHAR(64) NOT NULL DEFAULT 'local',
  file_path TEXT NOT NULL,
  public_url TEXT NULL,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

## 10. Agent 工作流重构

## 10.1 新 AgentState 分层

将现在的大状态拆为逻辑分区。

```py
class MissionContext(TypedDict, total=False):
    objective: str
    source_input: str
    input_type: str
    target_url: str | None
    document_id: str | None
    environment_id: str | None
    safety_boundary: str | None
    auth_context: dict
    constraints: list[dict]

class PlanningState(TypedDict, total=False):
    api_plan: dict | None
    ui_plan: dict | None
    risk_plan: dict | None
    missing_info: list[dict]

class ExecutionState(TypedDict, total=False):
    api_cases: list[dict]
    ui_cases: list[dict]
    api_results: list[dict]
    ui_results: list[dict]
    evidence: list[dict]
    tool_calls: list[dict]

class EvaluationState(TypedDict, total=False):
    evidence_score: float
    blockers: list[dict]
    findings: list[dict]
    replan_required: bool
    replan_reason: str | None
    final_confidence: str | None

class MemoryState(TypedDict, total=False):
    previous_runs: list[dict]
    reusable_cases: list[dict]
    recurring_failures: list[dict]
```

最终 AgentState：

```py
class AgentState(TypedDict, total=False):
    run_id: str
    mission: MissionContext
    planning: PlanningState
    execution: ExecutionState
    evaluation: EvaluationState
    memory: MemoryState
    progress_events: list[dict]
    current_step: str | None
    last_error: str | None
```

兼容旧字段时，可在 `orchestrator.py` 中做 legacy adapter。

---

## 10.2 推荐 LangGraph 流程

```text
input_classifier
  ↓
source_loader
  ↓
mission_planner
  ↓
knowledge_retriever
  ↓
planner
  ↓
case_generator
  ↓
api_runner or ui_login
  ↓
ui_test_planner / ui_runner
  ↓
execution_evaluator
  ↓
[replan?] → planner / case_generator / ui_test_planner
  ↓
reporter
  ↓
knowledge_sink
  ↓
END
```

### 10.3 节点职责

#### input_classifier

输入：source_input

输出：

```json
{
  "input_type": "swagger_url | swagger_json | swagger_yaml | url | unknown",
  "confidence": "high",
  "reason": "..."
}
```

#### source_loader

职责：

- 拉取 URL
- 解析 OpenAPI
- 获取 endpoints
- 推断 base_url
- 推断 auth endpoints

输出：

```json
{
  "parsed_api_schema": [],
  "ui_seed_url": "...",
  "document_profile": {
    "endpoint_count": 56,
    "auth_required_count": 31
  }
}
```

#### mission_planner

职责：

- 将用户 objective 转换成 mission plan
- 决定 API / UI / Full
- 生成安全边界
- 标记缺失信息

#### knowledge_retriever

职责：

- 查找 target_memory
- 查找历史 blockers
- 查找可复用用例

#### planner

职责：

- 显式输出 api_plan / ui_plan
- 标注优先级、风险和跳过原因

#### case_generator

职责：

- 生成 api_cases / ui_cases
- 每条 case 必须结构化

API case 示例：

```json
{
  "title": "获取用户列表",
  "priority": "P0",
  "method": "GET",
  "path": "/users",
  "headers": {},
  "query": {},
  "body": null,
  "assertions": [
    { "type": "status_code", "expected": 200 },
    { "type": "json_type", "path": "$", "expected": "array" }
  ],
  "safety": "read_only"
}
```

UI case 示例：

```json
{
  "title": "登录后进入用户列表页",
  "priority": "P0",
  "steps": [
    { "action": "goto", "target": "/login" },
    { "action": "fill", "selector_hint": "用户名", "value_ref": "username" },
    { "action": "fill", "selector_hint": "密码", "value_ref": "password" },
    { "action": "click", "selector_hint": "登录" },
    { "action": "expect_url_contains", "value": "/dashboard" }
  ],
  "evidence_required": ["screenshot", "trace"]
}
```

#### api_runner

职责：

- 根据 policy 跳过写接口
- 注入 auth headers
- 执行 httpx 请求
- 生成 assertion evidence
- 记录 tool_calls

#### ui_runner

职责：

- 使用 Playwright 执行 UI cases
- 采集截图、trace、console、network
- 失败时输出复现步骤

#### execution_evaluator

职责：

- 评估证据充分度
- 决定是否 replan
- 判断 bug_found / failed / succeeded

输出：

```json
{
  "evidence_sufficient": false,
  "evidence_score": 0.72,
  "missing_evidence": ["登录后首页截图"],
  "replan_required": true,
  "next_node": "ui_test_planner",
  "reason": "API 已完成，但缺少 UI 主路径证据"
}
```

#### reporter

职责：

- 生成最终结构化报告
- 聚合 findings
- 输出 risk summary

#### knowledge_sink

职责：

- 更新 target_memories
- 保存 recurring_themes
- 保存 reusable_assets

---

## 11. 后端 Service 设计

## 11.1 run_service.py

职责：

- create_run
- get_run_detail
- list_runs
- cancel_run
- append_event
- update_status
- persist_current_action

核心方法：

```py
class RunService:
    async def create_run(self, db, payload, user) -> Run: ...
    async def get_run_detail(self, db, run_id) -> RunDetail: ...
    async def list_runs(self, db, filters) -> Page[RunListItem]: ...
    async def append_event(self, db, run_id, event_type, payload) -> RunEvent: ...
    async def update_status(self, db, run_id, status, **kwargs) -> None: ...
```

## 11.2 preflight_service.py

职责：

- 检查模型配置
- 检查 worker
- 检查 browser executor
- 检查 document
- 检查 environment
- 检查 auth readiness
- 生成 mission_preview

## 11.3 auth_preflight_service.py

职责：

- 自动推断登录接口
- 使用用户名密码换取 token
- 验证受保护接口
- 缓存 auth_preflight_id
- 输出 next_action

## 11.4 target_memory_service.py

职责：

- 根据 target_key 查询历史记忆
- 从 run findings 聚合 recurring themes
- 保存 reusable cases
- 输出 suggested_strategy

## 11.5 evidence_service.py

职责：

- 保存 evidence
- 保存 artifact
- 生成 evidence summary
- 关联 findings

## 11.6 report_service.py

职责：

- 生成 JSON report
- 生成 Markdown report
- 生成 triage export

---

## 12. 前后端数据类型对齐

前端 `types` 必须和后端 schema 对齐。

```text
frontend/src/types/run.ts
frontend/src/types/agentPlan.ts
frontend/src/types/evidence.ts
frontend/src/types/memory.ts
frontend/src/types/assets.ts
```

### 12.1 Run 类型

```ts
export type RunStatus =
  | 'pending'
  | 'queued'
  | 'running'
  | 'waiting_for_user'
  | 'blocked'
  | 'succeeded'
  | 'failed'
  | 'bug_found'
  | 'cancelled'

export interface RunDetail {
  id: string
  title: string
  objective: string
  status: RunStatus
  input_type: string
  target_url?: string
  test_type: 'api' | 'ui' | 'full' | 'auto'
  execution_policy: string
  progress_percent: number
  current_step?: string
  current_action?: AgentAction
  timeline: RunEvent[]
  api_plan?: unknown
  ui_plan?: unknown
  api_cases: TestCase[]
  ui_cases: TestCase[]
  evidence: EvidenceItem[]
  findings: Finding[]
  report?: RunReport
  created_at: string
  started_at?: string
  finished_at?: string
}
```

---

## 13. 开发任务拆分

## Phase 1：设计系统与布局壳

### 任务 1.1：新增 UI 基础组件

文件：

```text
frontend/src/components/ui/TcButton.vue
frontend/src/components/ui/TcCard.vue
frontend/src/components/ui/TcBadge.vue
frontend/src/components/ui/TcOptionCard.vue
frontend/src/components/ui/TcStepBar.vue
```

验收：

- 所有组件支持 primary / secondary / ghost / danger 状态。
- 所有组件视觉风格统一。
- 不引入新的 UI 框架。

### 任务 1.2：重构 AppSidebar

文件：

```text
frontend/src/components/AppSidebar.vue
```

要求：

- 改导航分组为 Workspace / Assets / Settings。
- active 样式使用深色或蓝色高亮。
- 保留折叠能力。
- 文案改为：智能计划、任务委派、运行历史、质量记忆。

### 任务 1.3：重构 AppHeader

要求：

- 右侧显示：历史、已认证、admin、退出。
- 移动端显示菜单按钮。
- 风格与新设计统一。

---

## Phase 2：智能计划页

### 任务 2.1：实现 AgentPlanPage 新 UI

文件：

```text
frontend/src/pages/AgentPlanPage.vue
frontend/src/components/agent/AgentQuestionCard.vue
frontend/src/components/agent/AgentPlanDraft.vue
frontend/src/components/agent/AgentChatInput.vue
```

验收：

- 页面布局与原型一致。
- 支持 5 步 stepper。
- 支持 option card 选择。
- 支持右侧草案实时更新。
- 支持底部自由输入。

### 任务 2.2：后端新增 agent_plans API

文件：

```text
app/api/v1/agent_plans.py
app/schemas/agent_plan.py
app/services/agent_plan_service.py
```

接口：

- POST `/agent-plans/sessions`
- GET `/agent-plans/sessions`
- GET `/agent-plans/sessions/{id}`
- POST `/agent-plans/sessions/{id}/intake`
- POST `/agent-plans/sessions/{id}/generate`
- POST `/agent-plans/{id}/create-run`

验收：

- 能创建会话。
- 能保存用户消息。
- 能更新 draft_json。
- 能返回 next_question。
- 能生成 recommended_run_payload。

---

## Phase 3：任务委派页

### 任务 3.1：重构 RunPage UI

文件：

```text
frontend/src/pages/RunPage.vue
frontend/src/components/run/RunMissionCard.vue
frontend/src/components/run/RunModeSelector.vue
frontend/src/components/run/RunPolicySelector.vue
frontend/src/components/run/RunAuthPreflightCard.vue
frontend/src/components/run/RunPreflightStatusCard.vue
frontend/src/components/run/RunHandoffPreview.vue
```

验收：

- 功能不减少。
- 表单布局变成 Mission Control。
- 右侧显示预检状态、Memory、执行流、交接预览。
- 点击运行前预检调用后端。
- 预检成功后可启动 run。

### 任务 3.2：后端拆分 preflight_service

文件：

```text
app/services/preflight_service.py
app/services/auth_preflight_service.py
```

验收：

- `runs.py` 中预检逻辑迁出。
- API 返回结构不破坏前端。
- 预检结果包含 checks、mission_preview、target_memory、auth_preflight。

---

## Phase 4：Agent Cockpit

### 任务 4.1：重构 RunDetailPage

文件：

```text
frontend/src/pages/RunDetailPage.vue
frontend/src/components/agent/AgentTimeline.vue
frontend/src/components/agent/AgentCurrentActionCard.vue
frontend/src/components/agent/AgentEvidenceCard.vue
frontend/src/components/agent/AgentInterventionDrawer.vue
frontend/src/components/agent/AgentRunSummary.vue
```

验收：

- 顶部显示 run summary。
- 左侧 timeline 实时更新。
- 中间 tabs 展示计划、用例、日志、证据、报告。
- 右侧显示运行摘要和 findings。
- 支持人工介入 drawer。

### 任务 4.2：实现 SSE stream

文件：

```text
app/api/v1/runs.py
app/services/run_stream_service.py
```

验收：

- GET `/runs/{id}/stream` 返回 event-stream。
- 前端断线可重连。
- stream 事件写入 run_events。
- 运行完成后发送 `run.finished`。

---

## Phase 5：数据库迁移

### 任务 5.1：新增 Alembic migration

新增表：

- agent_plan_sessions
- agent_plan_messages
- agent_plans
- runs 或兼容 tasks 的 run 扩展表
- run_events
- run_interventions
- run_tool_calls
- run_evidence
- run_findings
- target_memories
- artifacts

验收：

- PostgreSQL 迁移通过。
- SQLite 本地开发可运行。
- 旧数据不丢失。
- create_all 不再作为生产迁移依赖。

---

## Phase 6：历史、记忆、资产页

### 任务 6.1：HistoryPage 新 UI

验收：

- 顶部统计卡。
- 过滤器。
- run 卡片列表。
- 支持查看详情、重新运行、导出报告。

### 任务 6.2：QualityMemoryPage 新 UI

验收：

- 目标记忆列表。
- 高频主题。
- 可复用资产。
- 一键用于新计划。

### 任务 6.3：DocumentsPage / EnvironmentsPage / TestCasesPage 新 UI

验收：

- 统一卡片风格。
- 资产能被 RunPage/AgentPlanPage 复用。
- 空状态和加载状态完整。

---

## 14. Codex 开发约束

### 14.1 不要一次性大爆炸修改

每个 PR / commit 只做一个主题：

- UI 组件
- 页面重构
- API 新增
- Service 拆分
- 数据库迁移
- Agent 节点重构

### 14.2 兼容现有功能

除非明确说明，不要删除旧接口。先新增新接口，再逐步迁移前端。

### 14.3 类型优先

所有新增 API 必须有：

- Pydantic request schema
- Pydantic response schema
- 前端 TypeScript interface
- 错误响应结构

### 14.4 结构化 AI 输出

所有 LLM 输出必须要求 JSON schema，不允许只返回自然语言。

### 14.5 可观测性

所有 Agent 节点必须发出 progress event：

```py
await progress.emit(
    run_id=run_id,
    event_type="agent.step.started",
    payload={"step": "planner", "title": "生成测试计划"},
)
```

### 14.6 安全边界

默认策略必须是：

```text
safe_read_only
```

写接口只有在用户明确选择 `write_allowed` 且环境标记为 test/staging 时才能执行。

### 14.7 敏感信息

以下内容不得明文出现在日志、报告、stream 事件中：

- password
- token
- Authorization
- Cookie
- secret
- api_key

统一使用 redaction service。

---

## 15. 测试计划

### 15.1 后端单元测试

新增测试：

```text
tests/services/test_agent_plan_service.py
tests/services/test_preflight_service.py
tests/services/test_target_memory_service.py
tests/services/test_evidence_service.py
tests/api/test_agent_plans_api.py
tests/api/test_runs_stream.py
tests/agent/test_execution_evaluator.py
```

### 15.2 前端测试建议

当前项目未配置 Vitest，可先不强制。但新增复杂逻辑应尽量放 composable，便于后续测试。

建议新增：

```text
frontend/src/composables/useRunStream.ts
frontend/src/composables/useAgentPlanSteps.ts
frontend/src/composables/usePreflight.ts
```

### 15.3 手工验收场景

#### 场景 1：从智能计划创建 Run

1. 进入 `/agent-plan`。
2. 选择接口文档 / OpenAPI。
3. 选择关键路径、基础可用性。
4. 选择需要登录。
5. 输入安全边界。
6. 生成计划。
7. 创建 Run。
8. 跳转 `/runs/:id`。

验收：

- 计划草案正确。
- Run payload 正确。
- RunDetail 能展示 timeline。

#### 场景 2：任务委派预检

1. 进入 `/run`。
2. 选择 API 文档。
3. 选择接口测试。
4. 选择安全只读。
5. 输入账号密码。
6. 点击运行前预检。

验收：

- 右侧状态从 Blocked 到 Ready。
- mission_preview 显示 endpoint_count。
- 可启动智能体。

#### 场景 3：运行中人工介入

1. 启动 Run。
2. 进入 RunDetail。
3. 点击人工介入。
4. 补充 Header。
5. 选择后续步骤生效。

验收：

- intervention 被保存。
- timeline 出现用户补充事件。
- Agent 后续请求使用补充信息。

---

## 16. 推荐落地顺序

```text
1. UI Design System
2. Sidebar/Header/Layout 统一
3. AgentPlanPage 重构
4. agent_plans 后端 API
5. RunPage 重构
6. preflight_service 拆分
7. RunDetailPage / Agent Cockpit
8. SSE stream 完善
9. 数据库新增 run_events/evidence/findings
10. target_memory_service
11. HistoryPage
12. QualityMemoryPage
13. Documents/Environments/TestCases 新 UI
14. Providers/Knowledge 新 UI
15. 清理旧页面入口和文案
```

---

## 17. Codex 每次开发前检查清单

开发前先确认：

```text
[ ] 本次修改属于哪个 Phase？
[ ] 是否会破坏现有路由？
[ ] 是否需要新增数据库迁移？
[ ] 是否需要更新前端 types？
[ ] 是否需要新增 service 层？
[ ] 是否有 loading / empty / error 状态？
[ ] 是否有敏感信息脱敏？
[ ] 是否有验收方式？
```

---

## 18. 最终验收标准

### 18.1 产品体验

- 用户打开产品后，第一眼知道这是 AI 测试智能体。
- 用户可以通过智能计划页一步步生成测试计划。
- 用户可以通过任务委派页直接启动高级测试。
- 用户可以在 RunDetail 中看到 Agent 的实时动作和证据。
- 用户可以从历史和质量记忆中复用资产。

### 18.2 技术体验

- API 层变薄，业务逻辑进入 services。
- AgentState 分层，节点职责清晰。
- Run 事件可追溯。
- Evidence / Finding / Memory 可结构化查询。
- 前端组件复用度提升。

### 18.3 展示效果

项目应该能清楚展示：

1. AI Agent 工作流设计能力。
2. OpenAPI 自动解析与 API 测试能力。
3. Playwright UI 自动化能力。
4. 异步任务和实时状态展示能力。
5. 结构化报告与质量记忆能力。
6. 产品级 UI/UX 设计能力。

---

## 19. 给 Codex 的第一批具体任务建议

建议按下面顺序开工：

### Task A：实现 UI 基础组件

```text
请在 frontend/src/components/ui 下新增 TcCard、TcButton、TcBadge、TcOptionCard、TcStepBar、TcTextarea 组件，并统一使用 TestClaw 新视觉风格。不要改业务逻辑。
```

### Task B：重构 Sidebar

```text
请重构 frontend/src/components/AppSidebar.vue，把导航改为 Workspace / Assets / Settings 三组，文案使用 智能计划、任务委派、运行历史、质量记忆、接口文档、测试环境、用例资产、模型与 Agent、RAG 知识库。保留折叠与移动端能力。
```

### Task C：重构 AgentPlanPage

```text
请按 docs/CODEX_FULLSTACK_AGENT_REFACTOR_GUIDE.md 中 6.1 的原型重构 AgentPlanPage.vue，先使用本地 mock 数据，不接后端。实现左侧会话历史、中间 AI 计划问答、右侧计划草案、底部自由输入。
```

### Task D：新增 agent_plans 后端基础 API

```text
请新增 agent_plan_sessions、agent_plan_messages、agent_plans 数据模型、schema、service 和 API，支持创建会话、保存消息、更新 draft、生成 mock plan。先不接真实 LLM。
```

### Task E：接通 AgentPlanPage 与 API

```text
请将 AgentPlanPage 从 mock 数据改为调用 /api/v1/agent-plans/sessions 和 /intake API。保留 loading、error、empty 状态。
```

---

## 20. 附录：建议的首轮 Codex Prompt

```text
你现在在 TestClaw 项目中开发。请先阅读 docs/CODEX_FULLSTACK_AGENT_REFACTOR_GUIDE.md。
本次只实现 Phase 1 的 UI 基础组件，不要修改业务 API。
要求：
1. 新增 frontend/src/components/ui/TcCard.vue、TcButton.vue、TcBadge.vue、TcOptionCard.vue、TcStepBar.vue、TcTextarea.vue。
2. 组件使用 Vue 3 + TypeScript + Tailwind。
3. 风格遵循文档第 3 节。
4. 不引入新的 UI 框架。
5. 每个组件要支持常用 props 和 slot。
6. 完成后运行 npm run build，修复类型和构建错误。
```

---

## 21. 备注

本文档是全栈改造的主开发指南。后续如果实现过程中发现旧表或旧接口已经覆盖部分能力，优先复用旧能力，但必须保持新的产品心智：

> 用户委派任务，AI 生成计划，Agent 执行测试，系统沉淀证据和记忆。
