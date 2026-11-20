from __future__ import annotations

import logging
import os

from qasight.services.ai.cli_settings import settings_store
from qasight.services.ai.integration_settings import InMemoryAiSettingsStore
from qasight.services.ask_tests import AskTestsService
from qasight.services.failure_analysis_service import FailureAnalysisService
from qasight.services.project_review import ProjectReviewService
from qasight_api.cycle_execution_manager import CycleExecutionManager

_CYCLE_MANAGER = CycleExecutionManager()


# The Settings > AI file, or TESTO_AI_PROVIDER and friends when set (a server's .env:
# a model inside a closed network), the same rule the CLI follows.
# Changes on the Settings page then last until the server restarts.
def _ai_settings_store() -> InMemoryAiSettingsStore:
    try:
        return settings_store()
    except ValueError as exc:  # an unknown TESTO_AI_PROVIDER: say so, keep serving
        logging.getLogger(__name__).warning("AI settings from the environment ignored: %s", exc)
        os.environ.pop("TESTO_AI_PROVIDER", None)
        return settings_store()


_AI_SETTINGS_STORE: InMemoryAiSettingsStore = _ai_settings_store()
_FAILURE_ANALYSIS_SERVICE = FailureAnalysisService(settings_store=_AI_SETTINGS_STORE)


def get_cycle_execution_manager() -> CycleExecutionManager:
    return _CYCLE_MANAGER


def get_ai_settings_store() -> InMemoryAiSettingsStore:
    return _AI_SETTINGS_STORE


def get_failure_analysis_service() -> FailureAnalysisService:
    return _FAILURE_ANALYSIS_SERVICE


_PROJECT_REVIEW_SERVICE = ProjectReviewService(settings_store=_AI_SETTINGS_STORE)


def get_project_review_service() -> ProjectReviewService:
    return _PROJECT_REVIEW_SERVICE


_ASK_TESTS_SERVICE = AskTestsService(settings_store=_AI_SETTINGS_STORE)


def get_ask_tests_service() -> AskTestsService:
    return _ASK_TESTS_SERVICE
