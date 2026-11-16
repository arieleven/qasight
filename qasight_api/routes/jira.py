"""File a failing test in Jira and link it: see :mod:`qasight.services.jira_issues`."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from qasight.services.jira_issues import JiraClient, JiraError, config_from_env, summary_for
from qasight.services.triage import TriageError, TriageStore
from qasight_api.models import ApiModel
from qasight_api.security import TOKEN_PRINCIPAL, current_principal, require_cycle_role
from qasight_api.ui import ui_dir

router = APIRouter(prefix="/api/v1", tags=["jira"])


class JiraStatusResponse(ApiModel):
    enabled: bool
    project: str | None = None
    url: str | None = None


class JiraIssueRequest(ApiModel):
    cycle: str = Field(min_length=1, max_length=512)
    test: str = Field(min_length=1, max_length=1000)
    message: str = Field(default="", max_length=20000)
    run_id: str = Field(default="", max_length=64)
    test_key: str = Field(default="", max_length=200)
    summary: str | None = Field(default=None, max_length=250)


class JiraIssueResponse(ApiModel):
    key: str
    url: str
    # True when the failure's cause was marked with the issue (it had none yet).
    cause_marked: bool


@router.get("/jira", response_model=JiraStatusResponse)
def jira_status() -> JiraStatusResponse:
    """Whether the server can file issues in Jira (``TESTO_JIRA_*`` set), and where."""
    cfg = config_from_env()
    if not cfg.enabled:
        return JiraStatusResponse(enabled=False)
    return JiraStatusResponse(enabled=True, project=cfg.project, url=cfg.url)


@router.post("/jira/issues", response_model=JiraIssueResponse, status_code=201)
def file_jira_issue(body: JiraIssueRequest, request: Request) -> JiraIssueResponse:
    """Create a Jira issue for a failing test and mark its cause with the issue."""
    require_cycle_role(request, body.cycle)
    cfg = config_from_env()
    if not cfg.enabled:
        raise HTTPException(
            status_code=409,
            detail="Jira isn't set up: set TESTO_JIRA_URL, TESTO_JIRA_TOKEN and TESTO_JIRA_PROJECT.",
        )
    base = str(request.base_url).rstrip("/")
    run_url = f"{base}/runs/{body.run_id}" if body.run_id and ui_dir() else None
    try:
        issue = JiraClient(cfg).create_issue(
            summary=body.summary or summary_for(body.test, body.message),
            cycle=body.cycle,
            test=body.test,
            message=body.message,
            run_url=run_url,
        )
    except JiraError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    marked = False
    if body.message.strip():
        store = TriageStore()
        if not store.for_messages(body.cycle, [body.message]):
            principal = current_principal(request)
            try:
                store.mark(
                    cycle=body.cycle,
                    message=body.message,
                    cause="product_bug",
                    note=f"{issue.key} {issue.url}",
                    run_id=body.run_id,
                    test_key=body.test_key,
                    marked_by=principal.username
                    if principal and principal != TOKEN_PRINCIPAL
                    else None,
                )
                marked = True
            except TriageError:
                marked = False
    return JiraIssueResponse(key=issue.key, url=issue.url, cause_marked=marked)
