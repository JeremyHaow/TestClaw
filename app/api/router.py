from fastapi import APIRouter

from app.api.v1 import (
    agent_plans,
    api_tests,
    auth,
    dashboard,
    discovery,
    documents,
    environments,
    knowledge,
    providers,
    runs,
    tasks,
    test_cases,
    ui_tests,
    visuals,
    webhooks,
)

router = APIRouter()
router.include_router(auth.router, prefix="/auth", tags=["auth"])
router.include_router(agent_plans.router, prefix="/agent-plans", tags=["agent-plans"])
router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
router.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
router.include_router(test_cases.router, prefix="/test-cases", tags=["test-cases"])
router.include_router(documents.router, prefix="/documents", tags=["documents"])
router.include_router(providers.router, prefix="/providers", tags=["providers"])
router.include_router(discovery.router, prefix="/providers", tags=["discovery"])
router.include_router(environments.router, prefix="/environments", tags=["environments"])
router.include_router(api_tests.router, prefix="/api-tests", tags=["api-tests"])
router.include_router(ui_tests.router, prefix="/ui-tests", tags=["ui-tests"])
router.include_router(runs.router, prefix="/runs", tags=["runs"])
router.include_router(visuals.router, prefix="/visual-baselines", tags=["visuals"])
router.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
router.include_router(knowledge.router, prefix="/knowledge", tags=["knowledge"])
