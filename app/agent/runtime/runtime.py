from __future__ import annotations

from typing import Any

from app.agent.action_runtime import (
    append_agent_observation,
    append_evaluation_protocol,
    record_agent_action_observation,
    validate_agent_action_plan,
)
from app.agent.runtime.event_store import AgentRuntimeEventStore
from app.agent.runtime.failure_taxonomy import next_action_hint
from app.agent.runtime.models import RuntimeDecision, ToolExecutionResult
from app.agent.runtime.policies import redact_runtime_payload
from app.agent.runtime.tool_executor import ToolExecutor


class AgentRuntime:
    """Action-level runtime facade for runner adapters.

    The graph still owns stage orchestration, but API/UI runners call this class
    for validated action records, tool dispatch, observation/evaluation records,
    and DB event persistence.
    """

    def __init__(
        self,
        state: dict[str, Any],
        *,
        executor: ToolExecutor | None = None,
    ) -> None:
        self.state = state
        self.executor = executor or ToolExecutor(state)

    def validate_plan(
        self,
        *,
        stage: str,
        strategy: dict[str, Any] | None,
        parsed_api_schema: list[dict[str, Any]] | None = None,
        execution_policy: str = "safe_read_only",
    ) -> list[dict[str, Any]]:
        actions = validate_agent_action_plan(
            (strategy or {}).get("tool_plan") or self.state.get("agent_tool_plan") or [],
            strategy=strategy,
            parsed_api_schema=parsed_api_schema,
            execution_policy=execution_policy,
        )
        self.state["agent_actions"] = actions
        diagnostics: list[dict[str, Any]] = []
        for action in actions:
            diagnostics.extend([item for item in action.get("diagnostics") or [] if isinstance(item, dict)])
            record_agent_action_observation(self.state, action, stage=stage)
        if diagnostics:
            self.state["agent_action_diagnostics"] = redact_runtime_payload(diagnostics)
        return actions

    async def run_plan(
        self,
        actions: list[dict[str, Any]] | None = None,
        *,
        stage: str = "agent_runtime",
        context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        observations = []
        for action in actions or self.state.get("agent_actions") or []:
            observations.append(await self.run_action(action, stage=stage, context=context))
        await self.flush_stage_events(stage=stage)
        return observations

    async def run_action(
        self,
        action: dict[str, Any],
        *,
        stage: str = "agent_runtime",
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not action.get("allowed", True):
            observation = append_agent_observation(
                self.state,
                stage=stage,
                layer=str(action.get("layer") or "unknown"),
                tool_name=str(action.get("tool_name") or "unknown"),
                status="blocked",
                outcome="blocked",
                summary=f"{action.get('tool_name') or 'tool'} blocked by runtime policy.",
                action_id=action.get("action_id"),
                failure_type="safe_write_blocked" if str(action.get("tool_name") or "").startswith("api.") else "ui_action_blocked",
                inputs=action.get("inputs") or {},
                outputs={"diagnostics": action.get("diagnostics") or []},
            )
            append_evaluation_protocol(self.state, self.evaluate_observation(observation).model_dump(mode="json"), stage=stage)
            return observation

        result = await self.executor.execute(
            str(action.get("tool_name") or ""),
            action.get("inputs") or {},
            context=context,
        )
        failure_type = result.outputs.get("failure_type")
        observation = append_agent_observation(
            self.state,
            stage=stage,
            layer=result.layer,
            tool_name=result.tool_name,
            status=result.status,
            summary=self._result_summary(result),
            action_id=action.get("action_id"),
            failure_type=str(failure_type) if failure_type else None,
            inputs=result.inputs,
            outputs=result.outputs,
            metadata={"source": "agent_runtime", "evidence": result.evidence},
        )
        append_evaluation_protocol(self.state, self.evaluate_observation(observation).model_dump(mode="json"), stage=stage)
        return observation

    def evaluate_observation(self, observation: dict[str, Any]) -> RuntimeDecision:
        failure_type = observation.get("failure_type")
        if failure_type:
            next_action = next_action_hint(failure_type, layer=observation.get("layer"))
            return RuntimeDecision(
                next_action=next_action,
                sufficient_evidence=next_action == "report",
                confidence="medium",
                reason=str(observation.get("summary") or failure_type),
                failure_type=str(failure_type),
                missing_evidence=[] if next_action == "report" else [str(observation.get("summary") or failure_type)],
            )
        return RuntimeDecision(
            next_action="report",
            sufficient_evidence=True,
            confidence="medium",
            reason=str(observation.get("summary") or "Runtime observation completed."),
        )

    async def flush_stage_events(self, *, stage: str | None = None) -> None:
        db = self.state.get("db_session")
        if not db:
            return
        await AgentRuntimeEventStore(db).flush_state(self.state, stage=stage)

    @staticmethod
    def _result_summary(result: ToolExecutionResult) -> str:
        if result.error:
            return f"{result.tool_name}: {result.status} ({result.error[:180]})"
        if result.outputs.get("status_code") is not None:
            return f"{result.tool_name}: {result.status} status={result.outputs.get('status_code')}"
        if result.outputs.get("selected_total") is not None:
            return f"{result.tool_name}: selected {result.outputs.get('selected_total')} candidate(s)"
        return f"{result.tool_name}: {result.status}"
