"""Service layer: orchestration and use-cases (framework-agnostic where possible)."""

from .ai import (
    AiGenerationRequest,
    AiGenerationResult,
    AiIntegrationSettings,
    AiProvider,
    AiProviderConfig,
    AiProviderError,
    InMemoryAiSettingsStore,
    ProviderMisconfiguredError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    UnsupportedProviderModelError,
    build_ai_provider,
)
from .ci_provenance import CIProvenance, detect_ci_environment, detect_ci_provenance
from .dashboard_service import (
    DashboardDataFreshness,
    DashboardHeadlineKpis,
    DashboardOverview,
    DashboardRecentRun,
    DashboardReportLink,
    DashboardReportLinks,
    DashboardRollup,
    DashboardRollupSummary,
    DashboardService,
    DashboardTrendIndicator,
)
from .delta_models import DeltaComparisonResult, DeltaStatusSummary, MetricDelta
from .delta_service import (
    DeltaComparisonError,
    DeltaComparisonService,
    IncompatibleRunDataError,
    InvalidRunIdError,
    RunNotFoundComparisonError,
)
from .failure_analysis_service import FailureAnalysisService, FailureAnalysisSummary
from .failure_context_builder import FailureContext, FailureContextBudget, build_failure_context
from .metrics_service import MetricsService
from .report_service import ReportService

__all__ = [
    "AiGenerationRequest",
    "AiGenerationResult",
    "AiIntegrationSettings",
    "AiProvider",
    "AiProviderConfig",
    "AiProviderError",
    "CIProvenance",
    "DashboardDataFreshness",
    "DashboardHeadlineKpis",
    "DashboardOverview",
    "DashboardRecentRun",
    "DashboardReportLink",
    "DashboardReportLinks",
    "DashboardRollup",
    "DashboardRollupSummary",
    "DashboardService",
    "DashboardTrendIndicator",
    "DeltaComparisonError",
    "DeltaComparisonResult",
    "DeltaComparisonService",
    "DeltaStatusSummary",
    "detect_ci_environment",
    "detect_ci_provenance",
    "FailureAnalysisService",
    "FailureAnalysisSummary",
    "FailureContext",
    "FailureContextBudget",
    "IncompatibleRunDataError",
    "InvalidRunIdError",
    "MetricDelta",
    "MetricsService",
    "InMemoryAiSettingsStore",
    "ReportService",
    "ProviderMisconfiguredError",
    "ProviderRateLimitError",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
    "UnsupportedProviderModelError",
    "build_failure_context",
    "build_ai_provider",
    "RunNotFoundComparisonError",
]
