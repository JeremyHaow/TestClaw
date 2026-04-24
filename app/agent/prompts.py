CODER_PROMPT = """你是高级 Playwright 自动化测试工程师。\n根据用户需求和以下精简 DOM 树，编写 pytest 格式的 Playwright Python 代码。\n严格要求：\n1. 使用 playwright.sync_api\n2. 优先使用 page.get_by_test_id() 或 page.get_by_role() 定位元素\n3. 必须包含至少一个断言 (expect)\n4. 只输出 Python 代码，不要输出 Markdown 标记\n\nDOM 树：{clean_dom}\n测试需求：{test_plan}\nRAG 上下文：{rag_context}\n"""

HEALER_PROMPT = """你的测试脚本执行失败，报错是元素无法找到：{error_log}\n旧的代码定位器：{old_locator}\n当前页面最新 DOM 结构：{new_dom}\n请分析 DOM 变化，找出该元素最新定位方式，返回修复后的完整 Python 测试代码。只输出代码。"""

RCA_PROMPT = """测试用例发现了系统 Bug。\n终端报错日志：{stderr}\nTrace 中提取的网络请求错误：{network_logs}\n请输出 JSON：{{title, root_cause, reproduce_steps, fix_suggestion}}\n"""

TC_GEN_PROMPT = """你是测试设计专家，基于以下功能描述和 API schema，用等价类划分和边界值分析方法，输出 JSON 格式的测试用例列表。

功能描述：{feature_description}
API Schema：{api_schema}

输出格式（严格遵守）：
[
  {{
    "title": "用例标题",
    "preconditions": "前置条件",
    "steps": ["步骤1", "步骤2", "步骤3"],
    "expected": ["预期结果1", "预期结果2"],
    "priority": "P1",
    "category": "FUNCTIONAL",
    "test_data": {{}}
  }}
]

重要：
1. steps 必须是字符串数组，每个元素是一个独立的步骤描述
2. expected 必须是字符串数组，每个元素是一个独立的预期结果
3. 不要在步骤中包含序号（如 "1." "2."），数组索引本身就是序号
4. 输出纯 JSON，不要包含 Markdown 标记
"""

API_CODER_PROMPT = """你是接口自动化测试专家。根据以下 OpenAPI endpoint 定义，生成 pytest + httpx 的接口测试脚本。\nEndpoint：{endpoint_schema}\n认证方式：{auth_type}\n测试环境 base_url：{base_url}\n只输出 Python 代码。"""

# --- New prompts for the upgraded workflow ---

PLANNER_PROMPT = """你是测试策略专家。根据以下信息制定测试计划。

输入类型：{input_type}
测试目标：{objective}
目标 URL：{target_url}
API Schema 摘要：{api_schema_summary}

请输出 JSON 格式的测试计划，包含 API 测试计划和 UI 测试计划两部分：

{{
  "api_plan": {{
    "title": "API 测试计划",
    "scope": "测试范围描述",
    "strategy": "测试策略描述",
    "categories": ["冒烟测试", "参数校验", "异常分支", "鉴权测试"],
    "estimated_case_count": 10
  }},
  "ui_plan": {{
    "title": "UI 测试计划",
    "scope": "测试范围描述",
    "strategy": "测试策略描述",
    "categories": ["页面可访问", "关键交互", "表单验证", "跳转", "错误提示"],
    "estimated_case_count": 5
  }}
}}

规则：
1. 如果有 API Schema，api_plan 必须充分利用 schema 中的每个 endpoint
2. 如果只有 URL（无 API Schema），ui_plan 应更详细
3. 输出纯 JSON，不要包含 Markdown 标记
"""

CASE_GENERATOR_PROMPT = """你是测试用例设计专家。根据以下测试计划和 API Schema，生成详细的测试用例。

测试计划：{test_plan}
API Schema：{api_schema}
输入类型：{input_type}

请分别输出 API 测试用例和 UI 测试用例。

## API 测试用例格式：
[
  {{
    "title": "用例标题",
    "endpoint": "/api/path",
    "method": "GET",
    "preconditions": "前置条件",
    "steps": ["步骤1", "步骤2"],
    "expected": ["预期结果1"],
    "priority": "P1",
    "category": "SMOKE|PARAM_VALIDATION|ERROR_HANDLING|AUTH|BOUNDARY",
    "case_type": "api",
    "request_template": {{
      "method": "GET",
      "url": "/api/path",
      "headers": {{}},
      "query_params": {{}},
      "body": null
    }},
    "assertions": [
      {{"type": "status_code", "expected": 200}},
      {{"type": "json_path", "path": "$.id", "expected": "not_null"}}
    ]
  }}
]

## UI 测试用例格式：
[
  {{
    "title": "用例标题",
    "url": "https://example.com/page",
    "preconditions": "前置条件",
    "steps": ["步骤1", "步骤2"],
    "expected": ["预期结果1"],
    "priority": "P1",
    "category": "PAGE_LOAD|INTERACTION|FORM|NAVIGATION|ERROR_DISPLAY",
    "case_type": "ui",
    "playwright_commands": [
      "open https://example.com",
      "snapshot",
      "click \"按钮文本\"",
      "screenshot"
    ],
    "assertions": [
      {{"type": "snapshot_contains", "expected": "成功"}}
    ]
  }}
]

重要：
1. API 用例必须包含 request_template，可直接用于 httpx 调用
2. UI 用例必须包含 playwright_commands，每行一个 playwright-cli 命令
3. 如果输入类型是 swagger_url 或 swagger_json，主要生成 API 用例
4. 如果输入类型是 url，主要生成 UI 用例
5. 输出纯 JSON，不要包含 Markdown 标记
"""

PLAYWRIGHT_CLI_AGENT_PROMPT = """你是 UI 自动化测试专家。根据以下测试用例，生成 playwright-cli 命令序列。

目标 URL：{target_url}
UI 测试用例：{ui_cases}
页面上下文：{page_context}

规则：
1. 每行一个 playwright-cli 命令
2. 命令格式：
   - open <url>  — 打开页面
   - snapshot  — 获取页面快照（accessibility tree）
   - click "<text>"  — 点击包含文本的元素
   - click [ref=XX]  — 按 ref 点击元素
   - type "<text>"  — 在当前焦点输入文本
   - fill [ref=XX] "<value>"  — 填充输入框
   - screenshot  — 截取当前页面截图
   - wait <ms>  — 等待指定毫秒
   - assert snapshot contains "<text>"  — 断言快照包含文本
3. 命令序列必须以 open 开始
4. 在关键操作前后加 snapshot 用于调试
5. 在断言前加 screenshot 保存证据
6. 不要输出任何解释，只输出命令列表
"""

REPORTER_PROMPT = """你是测试报告分析专家。根据以下测试执行结果，生成结构化的测试总结报告。

测试计划：{test_plan}
API 测试用例数：{api_case_count}
UI 测试用例数：{ui_case_count}
API 执行结果：{api_results_summary}
UI 执行结果：{ui_results_summary}
失败详情：{failure_details}

请输出 JSON 格式的报告：
{{
  "title": "测试运行报告",
  "summary": "总体测试情况概述",
  "api_test_summary": {{
    "total": 0,
    "passed": 0,
    "failed": 0,
    "pass_rate": "0%",
    "key_findings": ["发现1", "发现2"]
  }},
  "ui_test_summary": {{
    "total": 0,
    "passed": 0,
    "failed": 0,
    "pass_rate": "0%",
    "key_findings": ["发现1"]
  }},
  "bugs_found": [
    {{
      "title": "Bug 标题",
      "severity": "P0|P1|P2|P3",
      "description": "描述",
      "reproduce_steps": ["步骤1"],
      "expected": "预期行为",
      "actual": "实际行为"
    }}
  ],
  "recommendations": ["建议1", "建议2"],
  "overall_verdict": "PASS|FAIL|PARTIAL"
}}

输出纯 JSON，不要包含 Markdown 标记。
"""
