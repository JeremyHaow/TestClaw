import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from app.agent.prompts import CODER_PROMPT
from app.core.dependencies import CurrentUser, DbSession
from app.core.llm_gateway import llm_gateway
from app.tools.playwright_tool import (
    execute_playwright_test,
    run_playwright_cli_command,
    run_playwright_cli_script,
    run_playwright_cli_stream,
)

logger = logging.getLogger(__name__)
router = APIRouter()

PLAYWRIGHT_CLI_PROMPT = """你是 Playwright CLI 测试专家。根据用户的测试需求，生成一系列 playwright-cli 命令来自动化测试。

可用命令格式（每行一个命令）：
- playwright-cli open <url>                          # 打开浏览器并导航
- playwright-cli click <selector>                    # 点击元素
- playwright-cli type <selector> <text>              # 输入文字
- playwright-cli fill <selector> <value>             # 填充表单
- playwright-cli press <key>                         # 按键
- playwright-cli screenshot <filename>               # 截图
- playwright-cli snapshot                            # 获取页面快照
- playwright-cli goto <url>                          # 导航到 URL
- playwright-cli select <selector> <value>           # 下拉选择
- playwright-cli check <selector>                    # 勾选
- playwright-cli uncheck <selector>                  # 取消勾选
- playwright-cli hover <selector>                    # 悬停
- playwright-cli eval <expression>                   # 执行 JS

严格要求：
1. 每行一个命令，不要输出 Markdown 标记
2. 第一行必须是 playwright-cli open <url>
3. 用 snapshot 查看页面结构后再操作
4. 用 screenshot 记录关键步骤

测试需求：{test_plan}
"""


class GenerateScriptRequest(BaseModel):
    url: str
    objective: str = "Test the page functionality"
    test_type: str = "functional"


@router.post("/generate-script")
async def generate_script(payload: GenerateScriptRequest, db: DbSession, _: CurrentUser):
    try:
        llm = await llm_gateway.get_coder(db)
    except RuntimeError:
        raise HTTPException(status_code=400, detail="No default coder provider configured")

    prompt = PLAYWRIGHT_CLI_PROMPT.format(
        test_plan=f"Objective: {payload.objective}\nTarget URL: {payload.url}\nTest type: {payload.test_type}",
    )
    try:
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        content = resp.content if hasattr(resp, "content") else str(resp)
        text = content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return {"script": text, "url": payload.url, "language": "playwright-cli"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Script generation failed: {e}")


class ExecuteScriptRequest(BaseModel):
    script: str


@router.post("/execute")
async def execute_script(payload: ExecuteScriptRequest, _: CurrentUser):
    if not payload.script.strip():
        raise HTTPException(status_code=400, detail="Script cannot be empty")
    result = execute_playwright_test.invoke({"code_content": payload.script})
    return result


class RunCliRequest(BaseModel):
    script: str  # Line-by-line playwright-cli commands


@router.post("/run-cli")
async def run_cli(payload: RunCliRequest, _: CurrentUser):
    if not payload.script.strip():
        raise HTTPException(status_code=400, detail="Script cannot be empty")
    result = await run_playwright_cli_script(payload.script)
    return result


@router.post("/run-cli/stream")
async def run_cli_stream(payload: RunCliRequest, _: CurrentUser):
    if not payload.script.strip():
        raise HTTPException(status_code=400, detail="Script cannot be empty")
    lines = [l.strip() for l in payload.script.strip().split("\n") if l.strip() and not l.strip().startswith("#")]

    async def event_generator():
        async for chunk in run_playwright_cli_stream(lines):
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


class SingleCommandRequest(BaseModel):
    command: str


@router.post("/command")
async def run_single_command(payload: SingleCommandRequest, _: CurrentUser):
    """Execute a single playwright-cli command."""
    if not payload.command.strip():
        raise HTTPException(status_code=400, detail="Command cannot be empty")
    cmd = payload.command.strip()
    if not cmd.startswith("playwright-cli"):
        cmd = f"playwright-cli {cmd}"
    result = await run_playwright_cli_command(cmd)
    return result
