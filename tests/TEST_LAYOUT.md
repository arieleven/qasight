## Test layout (enterprise scaling)

This repo historically used a flat `tests/` layout. We now split tests by intent:

- `tests/unit/`: pure function + branch-matrix tests (heavy mocking)
- `tests/integration/`: component interaction / state persistence (FastAPI TestClient)
- `tests/e2e/`: sequential user journeys (Allure-stepped)
- `tests/contracts/`: schema/contract enforcement (Pydantic)
- `tests/contract/`: contract enforcement for CLI/API/CI wrappers (legacy singular path still supported)
- `tests/e2e/flows/`: reusable flow scenarios (`provision -> execute -> poll -> verify -> cleanup`)
- `tests/e2e/provisioners/`: local/mock/external resource provisioners
- `tests/e2e/verifiers/`: DB/API/artifact/cleanup verifiers
- `tests/fixtures/e2e/`: repeatable fixture packs and env templates for each tier

## Tier markers

- `tier_fast`: deterministic PR-required baseline (no external provider resource creation)
- `tier_heavy`: PR-optional deep suite (full stack + plugin path with local/mocked providers)
- `tier_external`: nightly/release suite (real provider lifecycle)
- `provider_github` / `provider_gitlab`: provider-specific scenario coverage
- `plugin_path`: plugin-enabled scenario coverage
- `cleanup_required`: ephemeral resource lifecycle with mandatory cleanup audit
- `quarantined`: temporarily excluded flaky test bucket

### Migration map (old → new)

#### Engine core (unit)
- `tests/test_paths.py` → `tests/unit/qasight/test_paths.py`
- `tests/test_paths_behave_native.py` → `tests/unit/qasight/test_paths_behave_native.py`
- `tests/test_command_builders_extended.py` → `tests/unit/qasight/test_command_builders_extended.py`
- `tests/test_integrations_unit.py` → `tests/unit/qasight/test_integrations_unit.py`
- `tests/test_integrations_more.py` → `tests/unit/qasight/test_integrations_more.py`
- `tests/test_report_generator.py` → `tests/unit/qasight/test_report_generator.py`
- `tests/test_report_generator_helpers.py` → `tests/unit/qasight/test_report_generator_helpers.py`
- `tests/test_report_generator_http.py` → `tests/unit/qasight/test_report_generator_http.py`
- `tests/test_report_generator_locust.py` → `tests/unit/qasight/test_report_generator_locust.py`
- `tests/test_report_generator_static_sync.py` → `tests/unit/qasight/test_report_generator_static_sync.py`
- `tests/test_report_service_more.py` → `tests/unit/qasight/test_report_service_more.py`
- `tests/test_run_history_unit.py` → `tests/unit/qasight/test_run_history_unit.py`
- `tests/test_run_history_snapshot.py` → `tests/unit/qasight/test_run_history_snapshot.py`
- `tests/test_result_management.py` → `tests/unit/qasight/test_result_management.py`
- `tests/test_event_drain.py` → `tests/unit/qasight/test_event_drain.py`
- `tests/test_services.py` → `tests/unit/qasight/test_services.py`
- `tests/test_runners_unit.py` → `tests/unit/qasight/test_runners_unit.py`
- `tests/test_runners_audit_mock.py` → `tests/unit/qasight/test_runners_audit_mock.py`
- `tests/test_runners.py` → `tests/unit/qasight/test_runners.py`
- `tests/test_metrics.py` → `tests/unit/qasight/test_metrics.py`
- `tests/test_metrics_advanced.py` → `tests/unit/qasight/test_metrics_advanced.py`
- `tests/test_metrics_extractor_unit.py` → `tests/unit/qasight/test_metrics_extractor_unit.py`
- `tests/test_metrics_extractor_more.py` → `tests/unit/qasight/test_metrics_extractor_more.py`
- `tests/test_defaults_and_placeholders.py` → `tests/unit/test_defaults_and_placeholders.py`
- `tests/test_isolation_integrity.py` → `tests/unit/test_isolation_integrity.py`

#### New test families (added)
- Contract tests for `sample_target_repo/mock_api.py` → `tests/contracts/`
- Stateful flow tests for sandbox API → `tests/integration/sandbox_api/`
- Sequential user journeys → `tests/e2e/sandbox_api/`

