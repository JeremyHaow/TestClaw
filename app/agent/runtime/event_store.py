from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.runtime.policies import redact_runtime_payload
from app.models.run_artifacts import RunEvidence, RunFinding, RunToolCall
from app.models.run_event import RunEvent
from app.models.run_runtime import RunAgentAction, RunAgentEvaluation, RunAgentObservation

logger = logging.getLogger(__name__)

_EVIDENCE_PROTOCOL_ID_KEY = "_protocol_evidence_id"
_TOOL_CALL_PROTOCOL_ID_KEY = "_protocol_tool_call_id"
_INTERNAL_PROTOCOL_KEYS = {_EVIDENCE_PROTOCOL_ID_KEY, _TOOL_CALL_PROTOCOL_ID_KEY}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _created_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            return datetime.utcnow()
    return datetime.utcnow()


def _summary_count(observations: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    by_layer: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_failure_type: dict[str, int] = {}
    by_evidence_kind: dict[str, int] = {}
    for observation in observations:
        layer = str(observation.get("layer") or "unknown")
        status = str(observation.get("status") or "unknown")
        by_layer[layer] = by_layer.get(layer, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
        failure_type = observation.get("failure_type")
        if failure_type:
            key = str(failure_type)
            by_failure_type[key] = by_failure_type.get(key, 0) + 1
    for item in evidence:
        kind = str(item.get("kind") or item.get("evidence_type") or "unknown")
        by_evidence_kind[kind] = by_evidence_kind.get(kind, 0) + 1
    return {
        "observation_total": len(observations),
        "evidence_total": len(evidence),
        "by_layer": by_layer,
        "by_status": by_status,
        "by_failure_type": by_failure_type,
        "by_evidence_kind": by_evidence_kind,
    }


def _storage_id(run_id: str, protocol_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"testclaw.runtime:{run_id}:{protocol_id}"))


def _dict_payload(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _public_payload(value: Any) -> dict[str, Any]:
    payload = dict(_dict_payload(value))
    for key in _INTERNAL_PROTOCOL_KEYS:
        payload.pop(key, None)
    return payload


class AgentRuntimeEventStore:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def flush_state(self, state: dict[str, Any], *, stage: str | None = None) -> None:
        run_id = str(state.get("task_id") or "")
        if not run_id:
            return
        try:
            sequence = await self._next_sequence(run_id)
            sequence = await self._flush_actions(run_id, state, sequence, stage=stage)
            sequence = await self._flush_tool_calls(run_id, state, sequence)
            sequence = await self._flush_evidence(run_id, state, sequence)
            sequence = await self._flush_observations(run_id, state, sequence)
            await self._flush_evaluations(run_id, state, sequence)
            await self.db.commit()
        except Exception as exc:
            await self.db.rollback()
            logger.warning("Failed to flush runtime event store for run %s: %s", run_id, exc)

    async def _next_sequence(self, run_id: str) -> int:
        tables = (RunAgentAction, RunAgentObservation, RunAgentEvaluation, RunEvent)
        max_value = 0
        for model in tables:
            result = await self.db.execute(select(func.max(model.sequence)).where(model.run_id == run_id))
            max_value = max(max_value, int(result.scalar_one_or_none() or 0))
        return max_value + 1

    async def _existing_values(self, model: Any, run_id: str, column: Any) -> set[str]:
        result = await self.db.execute(select(column).where(model.run_id == run_id))
        return {str(item) for item in result.scalars() if item}

    async def _existing_tool_call_keys(self, run_id: str) -> set[str]:
        result = await self.db.execute(
            select(RunToolCall.id, RunToolCall.input_json, RunToolCall.output_json).where(RunToolCall.run_id == run_id)
        )
        keys: set[str] = set()
        for row_id, input_json, output_json in result.all():
            if row_id:
                keys.add(str(row_id))
            for payload in (_dict_payload(input_json), _dict_payload(output_json)):
                protocol_id = payload.get(_TOOL_CALL_PROTOCOL_ID_KEY)
                if protocol_id:
                    keys.add(str(protocol_id))
        return keys

    async def _existing_evidence_keys(self, run_id: str) -> set[str]:
        result = await self.db.execute(
            select(RunEvidence.id, RunEvidence.metadata_json).where(RunEvidence.run_id == run_id)
        )
        keys: set[str] = set()
        for row_id, metadata_json in result.all():
            if row_id:
                keys.add(str(row_id))
            metadata = _dict_payload(metadata_json)
            protocol_id = metadata.get(_EVIDENCE_PROTOCOL_ID_KEY) or metadata.get("evidence_id")
            if protocol_id:
                keys.add(str(protocol_id))
        return keys

    async def _existing_event_keys(self, run_id: str) -> set[str]:
        result = await self.db.execute(select(RunEvent.event_type, RunEvent.payload_json).where(RunEvent.run_id == run_id))
        keys = set()
        for event_type, payload in result.all():
            if isinstance(payload, dict):
                identifier = (
                    payload.get("action_id")
                    or payload.get("observation_id")
                    or payload.get("evaluation_id")
                    or payload.get("tool_call_id")
                    or payload.get("evidence_id")
                )
                if identifier:
                    keys.add(f"{event_type}:{identifier}")
        return keys

    def _add_event(
        self,
        run_id: str,
        sequence: int,
        event_type: str,
        title: str,
        summary: str,
        payload: dict[str, Any],
    ) -> None:
        self.db.add(
            RunEvent(
                run_id=run_id,
                sequence=sequence,
                event_type=event_type,
                title=title[:255],
                summary=summary,
                payload_json=redact_runtime_payload(payload) if isinstance(payload, dict) else {},
            )
        )

    async def _flush_actions(self, run_id: str, state: dict[str, Any], sequence: int, *, stage: str | None) -> int:
        existing = await self._existing_values(RunAgentAction, run_id, RunAgentAction.action_id)
        event_keys = await self._existing_event_keys(run_id)
        for action in _safe_list(state.get("agent_actions")):
            if not isinstance(action, dict):
                continue
            action_id = str(action.get("action_id") or "")
            if not action_id or action_id in existing:
                continue
            status = "validated" if action.get("allowed", True) else "blocked"
            safe_action = redact_runtime_payload(action)
            self.db.add(
                RunAgentAction(
                    run_id=run_id,
                    sequence=sequence,
                    action_id=action_id,
                    action_type=str((action.get("inputs") or {}).get("type") or action.get("action_type") or ""),
                    tool_name=str(action.get("tool_name") or "unknown"),
                    stage=str(stage or action.get("stage") or "agent_runtime"),
                    status=status,
                    risk=action.get("risk"),
                    reason=action.get("reason"),
                    inputs_json=safe_action.get("inputs") or {},
                    expected_observation=action.get("expected_observation"),
                    created_at=_created_at(action.get("timestamp")),
                    updated_at=datetime.utcnow(),
                )
            )
            key = f"agent.action:{action_id}"
            if key not in event_keys:
                self._add_event(run_id, sequence, "agent.action", f"Action {action.get('tool_name')}", action.get("reason") or status, safe_action)
                event_keys.add(key)
            existing.add(action_id)
            sequence += 1
        return sequence

    async def _flush_tool_calls(self, run_id: str, state: dict[str, Any], sequence: int) -> int:
        existing = await self._existing_tool_call_keys(run_id)
        event_keys = await self._existing_event_keys(run_id)
        for call in _safe_list(state.get("agent_tool_calls")):
            if not isinstance(call, dict):
                continue
            tool_call_id = str(call.get("tool_call_id") or "")
            storage_id = _storage_id(run_id, tool_call_id) if tool_call_id else ""
            if not tool_call_id or tool_call_id in existing or storage_id in existing:
                continue
            input_json = redact_runtime_payload(call.get("inputs") or {})
            output_json = redact_runtime_payload(call.get("outputs") or {})
            input_json[_TOOL_CALL_PROTOCOL_ID_KEY] = tool_call_id
            self.db.add(
                RunToolCall(
                    id=storage_id,
                    run_id=run_id,
                    node_name=str(call.get("layer") or "agent_runtime"),
                    tool_name=str(call.get("tool_name") or "unknown"),
                    input_json=input_json,
                    output_json=output_json,
                    status=str(call.get("status") or "unknown"),
                    duration_ms=int(call.get("elapsed_ms") or 0) if call.get("elapsed_ms") is not None else None,
                    created_at=_created_at(call.get("timestamp")),
                )
            )
            key = f"agent.tool_call:{tool_call_id}"
            if key not in event_keys:
                self._add_event(run_id, sequence, "agent.tool_call", f"Tool {call.get('tool_name')}", str(call.get("status") or ""), call)
                event_keys.add(key)
            existing.add(tool_call_id)
            existing.add(storage_id)
            sequence += 1
        return sequence

    async def _flush_evidence(self, run_id: str, state: dict[str, Any], sequence: int) -> int:
        existing = await self._existing_evidence_keys(run_id)
        event_keys = await self._existing_event_keys(run_id)
        for item in _safe_list(state.get("agent_evidence")):
            if not isinstance(item, dict):
                continue
            evidence_id = str(item.get("evidence_id") or "")
            storage_id = _storage_id(run_id, evidence_id) if evidence_id else ""
            if not evidence_id or evidence_id in existing or storage_id in existing:
                continue
            metadata = redact_runtime_payload(item.get("data") or {})
            if not isinstance(metadata, dict):
                metadata = {"value": metadata}
            metadata.setdefault("stage", item.get("stage"))
            metadata.setdefault("layer", item.get("layer"))
            metadata[_EVIDENCE_PROTOCOL_ID_KEY] = evidence_id
            self.db.add(
                RunEvidence(
                    id=storage_id,
                    run_id=run_id,
                    evidence_type=str(item.get("kind") or "runtime"),
                    title=str(item.get("title") or item.get("kind") or "Runtime evidence")[:255],
                    summary=item.get("summary"),
                    status=item.get("status"),
                    file_path=item.get("uri"),
                    url=item.get("url"),
                    metadata_json=metadata,
                    created_at=_created_at(item.get("timestamp")),
                )
            )
            key = f"agent.evidence:{evidence_id}"
            if key not in event_keys:
                self._add_event(run_id, sequence, "agent.evidence", str(item.get("title") or "Evidence"), str(item.get("summary") or ""), item)
                event_keys.add(key)
            existing.add(evidence_id)
            existing.add(storage_id)
            sequence += 1
        return sequence

    async def _flush_observations(self, run_id: str, state: dict[str, Any], sequence: int) -> int:
        existing = await self._existing_values(RunAgentObservation, run_id, RunAgentObservation.observation_id)
        event_keys = await self._existing_event_keys(run_id)
        for observation in _safe_list(state.get("agent_observations")):
            if not isinstance(observation, dict):
                continue
            observation_id = str(observation.get("observation_id") or "")
            if not observation_id or observation_id in existing:
                continue
            tool_call_ids = _safe_list(observation.get("tool_call_ids"))
            safe_observation = redact_runtime_payload(observation)
            self.db.add(
                RunAgentObservation(
                    run_id=run_id,
                    sequence=sequence,
                    observation_id=observation_id,
                    action_id=observation.get("action_id"),
                    tool_call_id=str(tool_call_ids[0]) if tool_call_ids else None,
                    stage=str(observation.get("stage") or "agent_runtime"),
                    layer=str(observation.get("layer") or "unknown"),
                    tool_name=str(observation.get("tool_name") or "unknown"),
                    status=str(observation.get("status") or "unknown"),
                    outcome=str(observation.get("outcome") or "unknown"),
                    failure_type=observation.get("failure_type"),
                    summary=observation.get("summary"),
                    inputs_json=safe_observation.get("inputs") or {},
                    outputs_json=safe_observation.get("outputs") or {},
                    evidence_ids_json=safe_observation.get("evidence_ids") or [],
                    created_at=_created_at(observation.get("timestamp")),
                )
            )
            key = f"agent.observation:{observation_id}"
            if key not in event_keys:
                self._add_event(run_id, sequence, "agent.observation", f"Observation {observation.get('tool_name')}", observation.get("summary") or "", safe_observation)
                event_keys.add(key)
            existing.add(observation_id)
            sequence += 1
        return sequence

    async def _flush_evaluations(self, run_id: str, state: dict[str, Any], sequence: int) -> int:
        existing = await self._existing_values(RunAgentEvaluation, run_id, RunAgentEvaluation.evaluation_id)
        event_keys = await self._existing_event_keys(run_id)
        for evaluation in _safe_list(state.get("agent_protocol_evaluations")):
            if not isinstance(evaluation, dict):
                continue
            evaluation_id = str(evaluation.get("evaluation_id") or "")
            if not evaluation_id or evaluation_id in existing:
                continue
            safe_evaluation = redact_runtime_payload(evaluation)
            self.db.add(
                RunAgentEvaluation(
                    run_id=run_id,
                    sequence=sequence,
                    evaluation_id=evaluation_id,
                    stage=str(evaluation.get("stage") or "agent_runtime"),
                    sufficient_evidence=bool(evaluation.get("sufficient_evidence")),
                    outcome=str(evaluation.get("outcome") or "unknown"),
                    next_action=str(evaluation.get("next_action") or "report"),
                    confidence=str(evaluation.get("confidence") or "unknown"),
                    failure_type=evaluation.get("failure_type"),
                    reason=evaluation.get("reason"),
                    missing_evidence_json=safe_evaluation.get("missing_evidence") or [],
                    replan_hint=evaluation.get("replan_hint"),
                    observation_ids_json=safe_evaluation.get("observation_ids") or [],
                    created_at=_created_at(evaluation.get("timestamp")),
                )
            )
            key = f"agent.evaluation:{evaluation_id}"
            if key not in event_keys:
                self._add_event(run_id, sequence, "agent.evaluation", f"Evaluation {evaluation.get('next_action')}", evaluation.get("reason") or "", safe_evaluation)
                event_keys.add(key)
            existing.add(evaluation_id)
            sequence += 1
        return sequence


async def load_runtime_detail(db: AsyncSession, run_id: str) -> dict[str, Any]:
    try:
        actions_result = await db.execute(select(RunAgentAction).where(RunAgentAction.run_id == run_id).order_by(RunAgentAction.sequence))
        observations_result = await db.execute(select(RunAgentObservation).where(RunAgentObservation.run_id == run_id).order_by(RunAgentObservation.sequence))
        evaluations_result = await db.execute(select(RunAgentEvaluation).where(RunAgentEvaluation.run_id == run_id).order_by(RunAgentEvaluation.sequence))
        tool_result = await db.execute(select(RunToolCall).where(RunToolCall.run_id == run_id).order_by(RunToolCall.created_at))
        evidence_result = await db.execute(select(RunEvidence).where(RunEvidence.run_id == run_id).order_by(RunEvidence.created_at))
        findings_result = await db.execute(select(RunFinding).where(RunFinding.run_id == run_id).order_by(RunFinding.created_at))
        events_result = await db.execute(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.sequence))
    except Exception as exc:
        logger.debug("Runtime detail unavailable for run %s: %s", run_id, exc)
        return {}

    actions = [
        {
            "action_id": row.action_id,
            "tool_name": row.tool_name,
            "stage": row.stage,
            "status": row.status,
            "risk": row.risk,
            "reason": row.reason,
            "inputs": row.inputs_json,
            "expected_observation": row.expected_observation,
            "timestamp": row.created_at.isoformat() if row.created_at else None,
        }
        for row in actions_result.scalars()
    ]
    observations = [
        {
            "observation_id": row.observation_id,
            "stage": row.stage,
            "layer": row.layer,
            "tool_name": row.tool_name,
            "status": row.status,
            "outcome": row.outcome,
            "summary": row.summary,
            "action_id": row.action_id,
            "failure_type": row.failure_type,
            "inputs": row.inputs_json,
            "outputs": row.outputs_json,
            "evidence_ids": row.evidence_ids_json,
            "tool_call_ids": [row.tool_call_id] if row.tool_call_id else [],
            "timestamp": row.created_at.isoformat() if row.created_at else None,
        }
        for row in observations_result.scalars()
    ]
    evaluations = [
        {
            "evaluation_id": row.evaluation_id,
            "stage": row.stage,
            "sufficient_evidence": row.sufficient_evidence,
            "outcome": row.outcome,
            "next_action": row.next_action,
            "confidence": row.confidence,
            "failure_type": row.failure_type,
            "reason": row.reason,
            "missing_evidence": row.missing_evidence_json,
            "replan_hint": row.replan_hint,
            "observation_ids": row.observation_ids_json,
            "timestamp": row.created_at.isoformat() if row.created_at else None,
        }
        for row in evaluations_result.scalars()
    ]
    tool_calls = [
        {
            "tool_call_id": _dict_payload(row.input_json).get(_TOOL_CALL_PROTOCOL_ID_KEY)
            or _dict_payload(row.output_json).get(_TOOL_CALL_PROTOCOL_ID_KEY)
            or row.id,
            "tool_name": row.tool_name,
            "layer": row.node_name,
            "status": row.status,
            "inputs": _public_payload(row.input_json),
            "outputs": _public_payload(row.output_json),
            "elapsed_ms": row.duration_ms,
            "timestamp": row.created_at.isoformat() if row.created_at else None,
        }
        for row in tool_result.scalars()
    ]
    evidence = [
        {
            "evidence_id": _dict_payload(row.metadata_json).get(_EVIDENCE_PROTOCOL_ID_KEY)
            or _dict_payload(row.metadata_json).get("evidence_id")
            or row.id,
            "kind": row.evidence_type,
            "stage": (row.metadata_json or {}).get("stage") or "agent_runtime",
            "layer": (row.metadata_json or {}).get("layer") or "unknown",
            "title": row.title,
            "status": row.status,
            "summary": row.summary,
            "uri": row.file_path or row.url,
            "data": _public_payload(row.metadata_json),
            "timestamp": row.created_at.isoformat() if row.created_at else None,
        }
        for row in evidence_result.scalars()
    ]
    findings = [
        {
            "finding_id": row.id,
            "title": row.title,
            "severity": row.severity,
            "confidence": row.confidence,
            "category": row.category,
            "surface": row.surface,
            "description": row.description,
            "evidence_ids": row.evidence_ids_json,
            "reproduction_steps": row.reproduction_steps_json,
            "next_action": row.next_action,
            "status": row.status,
            "timestamp": row.created_at.isoformat() if row.created_at else None,
        }
        for row in findings_result.scalars()
    ]
    events = [
        {
            "id": row.id,
            "sequence": row.sequence,
            "event_type": row.event_type,
            "title": row.title,
            "summary": row.summary,
            "payload": row.payload_json,
            "timestamp": row.created_at.isoformat() if row.created_at else None,
        }
        for row in events_result.scalars()
        if str(row.event_type or "").startswith("agent.")
    ]
    if not any([actions, tool_calls, evidence, observations, evaluations, findings, events]):
        return {}
    return redact_runtime_payload(
        {
            "agent_actions": actions,
            "agent_tool_calls": tool_calls,
            "agent_evidence": evidence,
            "agent_observations": observations,
            "agent_protocol_evaluations": evaluations,
            "run_findings": findings,
            "agent_protocol_summary": _summary_count(observations, evidence),
            "runtime_events": events,
        }
    )
