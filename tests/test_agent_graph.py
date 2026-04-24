import pytest

from app.agent.graph import agent_graph


@pytest.mark.asyncio
async def test_agent_graph_runs():
    result = await agent_graph.ainvoke(
        {
            "task_id": "task-1",
            "objective": "open page",
            "target_url": "http://example.com",
            "test_type": "ui",
            "retry_count": 3,
            "messages": [],
        }
    )
    assert result["test_plan"]
    assert result["generated_code"]
