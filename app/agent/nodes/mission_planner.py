from __future__ import annotations

import re
from typing import Any

from app.agent.progress import persist_progress
from app.agent.state import AgentState
from app.agent.tool_registry import install_tool_context, record_tool_call


_ROLE_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "role": "supervisor_planner",
        "label": "Supervisor / Planner",
        "responsibility": "Own the mission plan, scope boundaries, role delegation, and replan decisions.",
        "tools": ["agent.create_mission_plan", "planner.generate_execution_plan"],
    },
    {
        "role": "memory_researcher",
        "label": "Memory Researcher",
        "responsibility": "Retrieve relevant historical knowledge and feed bounded RAG context to planning.",
        "tools": ["memory.retrieve_rag_context"],
    },
    {
        "role": "api_executor",
        "label": "API Executor",
        "responsibility": "Build and execute safe API requests, assertions, auth refresh, and dependency injection.",
        "tools": [
            "api.safe_write_gate",
            "api.http_request",
            "api.status_assert",
            "api.json_path_assert",
            "api.schema_assert",
        ],
    },
    {
        "role": "ui_explorer",
        "label": "UI Explorer",
        "responsibility": "Prepare browser context, inspect snapshots, run UI actions, and capture screenshots.",
        "tools": ["ui.playwright_cli", "ui.smart_wait", "ui.snapshot_assert"],
    },
    {
        "role": "evidence_evaluator",
        "label": "Evidence Evaluator",
        "responsibility": "Judge whether evidence is sufficient and trigger bounded replanning when needed.",
        "tools": ["planner.evaluate_execution_evidence"],
    },
    {
        "role": "reporter",
        "label": "Reporter",
        "responsibility": "Produce findings, reproduction guidance, recommendations, and reusable asset summaries.",
        "tools": ["reporter.failure_analysis"],
    },
)


def _safe_text(value: Any, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _has_api_target(state: AgentState) -> bool:
    test_type = str(state.get("test_type") or "auto").lower()
    if test_type == "ui":
        return False
    return test_type in {"api", "full", "suite"} or bool(
        state.get("parsed_api_schema") or state.get("api_cases") or state.get("base_url_override")
    )


def _has_ui_target(state: AgentState) -> bool:
    test_type = str(state.get("test_type") or "auto").lower()
    if test_type == "api":
        return False
    input_type = str(state.get("input_type") or "").lower()
    return test_type in {"ui", "full", "suite"} or input_type == "url" or bool(
        state.get("ui_seed_url") or state.get("ui_cases")
    )


def _objective_clauses(objective: str) -> list[str]:
    cleaned = _safe_text(objective, 1000)
    if not cleaned:
        return []
    pieces = re.split(r"(?:,|;|\n| then | and |，|；|。|然后|并且)", cleaned, flags=re.I)
    clauses = []
    seen: set[str] = set()
    for piece in pieces:
        clause = _safe_text(piece, 120)
        if len(clause) < 4 or clause.lower() in seen:
            continue
        seen.add(clause.lower())
        clauses.append(clause)
    return clauses[:6]


def _memory_queries(state: AgentState, clauses: list[str]) -> list[dict[str, str]]:
    objective = _safe_text(state.get("objective"), 220)
    target = _safe_text(state.get("target_url") or state.get("ui_seed_url"), 180)
    queries = []
    if objective or target:
        queries.append(
            {
                "query": _safe_text(f"{objective} {target}", 260),
                "purpose": "Find prior failures, known blockers, selectors, auth notes, and reusable strategy for this target.",
            }
        )
    for clause in clauses[:3]:
        queries.append(
            {
                "query": _safe_text(f"{clause} {target}", 260),
                "purpose": "Ground a mission subgoal in prior run memory when similar behavior was tested before.",
            }
        )
    endpoints = [
        f"{endpoint.get('method', 'GET')} {endpoint.get('path')}"
        for endpoint in (state.get("parsed_api_schema") or [])[:5]
        if isinstance(endpoint, dict) and endpoint.get("path")
    ]
    if endpoints:
        queries.append(
            {
                "query": _safe_text(" ".join(endpoints), 260),
                "purpose": "Find prior API contract issues for documented endpoints.",
            }
        )
    return queries[:5]


def _environment_needs(state: AgentState) -> list[dict[str, str]]:
    needs = [
        {
            "need": "classify_input",
            "status": "done" if state.get("input_type") else "pending",
            "evidence": f"input_type={state.get('input_type', 'unknown')}",
        }
    ]
    if _has_api_target(state):
        needs.append(
            {
                "need": "api_schema_and_base_url",
                "status": "ready" if state.get("parsed_api_schema") or state.get("base_url_override") else "needs_discovery",
                "evidence": f"endpoints={len(state.get('parsed_api_schema') or [])}, base_url_override={bool(state.get('base_url_override'))}",
            }
        )
    if _has_ui_target(state):
        needs.append(
            {
                "need": "browser_surface",
                "status": "ready" if state.get("ui_seed_url") or state.get("target_url") else "needs_discovery",
                "evidence": _safe_text(state.get("ui_seed_url") or state.get("target_url"), 180),
            }
        )
    if state.get("auth_preflight"):
        auth = state.get("auth_preflight") or {}
        needs.append(
            {
                "need": "auth_readiness",
                "status": str(auth.get("status") or "unknown"),
                "evidence": str(auth.get("strategy") or auth.get("next_action") or ""),
            }
        )
    return needs


def _roster_for_state(state: AgentState) -> list[dict[str, Any]]:
    active_roles = {"supervisor_planner", "memory_researcher", "evidence_evaluator", "reporter"}
    if _has_api_target(state):
        active_roles.add("api_executor")
    if _has_ui_target(state):
        active_roles.add("ui_explorer")
    return [
        {**role, "active": role["role"] in active_roles}
        for role in _ROLE_DEFINITIONS
        if role["role"] in active_roles
    ]


def _subgoals(state: AgentState, clauses: list[str]) -> list[dict[str, Any]]:
    subgoals: list[dict[str, Any]] = [
        {
            "id": "M1",
            "title": "Understand target, scope, safety policy, and available tools",
            "owner": "supervisor_planner",
            "inputs": ["objective", "input_type", "test_type", "api_execution_policy"],
            "acceptance": "The run has explicit modality, target, safety, skill, and tool boundaries.",
            "status": "planned",
        },
        {
            "id": "M2",
            "title": "Retrieve relevant historical memory before test design",
            "owner": "memory_researcher",
            "inputs": ["objective", "target_url", "source_input", "parsed_api_schema"],
            "acceptance": "Planner receives bounded RAG context or an explicit no-memory observation.",
            "status": "planned",
        },
    ]
    for index, clause in enumerate(clauses[:4], start=3):
        subgoals.append(
            {
                "id": f"M{index}",
                "title": f"Cover objective slice: {clause}",
                "owner": "supervisor_planner",
                "inputs": ["mission_objective_slice", "retrieved_memory", "environment_observations"],
                "acceptance": "A concrete API or UI case, assertion, or exploration step maps back to this slice.",
                "status": "planned",
            }
        )

    next_id = len(subgoals) + 1
    if _has_api_target(state):
        subgoals.append(
            {
                "id": f"M{next_id}",
                "title": "Execute safe API evidence collection",
                "owner": "api_executor",
                "inputs": ["api_cases", "auth_headers", "api_execution_policy"],
                "acceptance": "Executed or explicitly skipped API requests produce request, response, assertion, and policy evidence.",
                "status": "planned",
            }
        )
        next_id += 1
    if _has_ui_target(state):
        subgoals.append(
            {
                "id": f"M{next_id}",
                "title": "Explore UI surface and capture browser evidence",
                "owner": "ui_explorer",
                "inputs": ["ui_seed_url", "setup_instructions", "ui_cases", "page_snapshots"],
                "acceptance": "UI actions produce snapshots, screenshots, command outcomes, and reproducible context.",
                "status": "planned",
            }
        )
        next_id += 1
    subgoals.extend(
        [
            {
                "id": f"M{next_id}",
                "title": "Evaluate evidence and replan if coverage is too shallow",
                "owner": "evidence_evaluator",
                "inputs": ["api_execution_result", "ui_execution_result", "tool_calls"],
                "acceptance": "The next decision is report, continue, or bounded replan with clear missing evidence.",
                "status": "planned",
            },
            {
                "id": f"M{next_id + 1}",
                "title": "Report findings, risks, evidence, and reusable assets",
                "owner": "reporter",
                "inputs": ["plans", "cases", "execution_results", "artifacts", "agent_trace"],
                "acceptance": "The final report explains what was tested, evidence quality, failures, and next actions.",
                "status": "planned",
            },
        ]
    )
    return subgoals


def _delegation_trace(roster: list[dict[str, Any]], subgoals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    active_roles = {role["role"] for role in roster if role.get("active")}
    trace = []
    for subgoal in subgoals:
        owner = str(subgoal.get("owner") or "supervisor_planner")
        trace.append(
            {
                "from": "supervisor_planner",
                "to": owner,
                "subgoal_id": subgoal["id"],
                "task": subgoal["title"],
                "inputs": subgoal.get("inputs", []),
                "status": "delegated" if owner in active_roles else "inactive",
            }
        )
    return trace


async def run(state: AgentState) -> AgentState:
    install_tool_context(state)
    objective = _safe_text(state.get("objective"), 500) or "Run a bounded testing mission"
    clauses = _objective_clauses(objective)
    roster = _roster_for_state(state)
    subgoals = _subgoals(state, clauses)
    memory_queries = _memory_queries(state, clauses)
    environment_needs = _environment_needs(state)
    selected_skills = [
        {
            "name": skill.get("name"),
            "layer": skill.get("layer"),
            "tools": skill.get("tools", []),
        }
        for skill in state.get("skill_plan", [])
        if isinstance(skill, dict)
    ]
    mission_plan = {
        "version": "2026-05-25",
        "control_pattern": "plan_execute_react",
        "objective": objective,
        "target": _safe_text(state.get("target_url") or state.get("ui_seed_url"), 240),
        "test_type": state.get("test_type") or "auto",
        "input_type": state.get("input_type") or "unknown",
        "summary": (
            "Mission decomposed into memory research, planning, execution, evidence "
            "evaluation, bounded replanning, and reporting."
        ),
        "subgoals": subgoals,
        "memory_needs": memory_queries,
        "environment_needs": environment_needs,
        "selected_skills": selected_skills,
        "execution_order": [item["id"] for item in subgoals],
        "success_criteria": [
            "Every executed action has a visible tool/evidence observation.",
            "API requests stay within documented schema and configured safety policy.",
            "UI evidence includes snapshots or screenshots when browser execution runs.",
            "Report states coverage, blockers, failures, confidence, and next actions.",
        ],
    }

    state["agent_mission_plan"] = mission_plan
    state["agent_roster"] = roster
    state["agent_delegation_trace"] = _delegation_trace(roster, subgoals)

    record_tool_call(
        state,
        tool_name="agent.create_mission_plan",
        layer="supervisor",
        status="success",
        input_summary={
            "objective": objective,
            "test_type": mission_plan["test_type"],
            "input_type": mission_plan["input_type"],
        },
        output_summary={
            "subgoal_count": len(subgoals),
            "active_roles": [role["role"] for role in roster],
            "memory_queries": len(memory_queries),
            "selected_skills": [skill["name"] for skill in selected_skills],
        },
        metadata={
            "reason": "Create a visible mission-level plan before downstream test design and execution.",
            "next_decision": "delegate_memory_research_and_test_planning",
        },
    )

    detail = f"Mission plan ready: {len(subgoals)} subgoal(s), {len(roster)} active role(s)"
    state.setdefault("workflow_steps", []).append(
        {"node": "mission_planner", "status": "done", "detail": detail}
    )
    await persist_progress(state, "mission_planner", "done", detail)
    return state
