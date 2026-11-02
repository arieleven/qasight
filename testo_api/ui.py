"""Serve the built React UI from the API process (``testo ui`` and the team server image).

The UI is a ``npm run build`` output built with ``VITE_API_BASE_URL=""`` so it
calls the API on its own origin. Released wheels carry one in
``testo_api/ui_dist/`` (the publish workflow builds it, see
``scripts/build_ui_bundle.sh``); ``TESTO_UI_DIR`` points at another build and
wins over the bundled one (the lookup is :mod:`testo_core.ui_bundle`, shared with
``testo export``). The UI is mounted at ``/``, after every API route,
and unknown paths fall back to ``index.html`` so client-side routes like
``/runs/<id>`` load on refresh.
"""

from __future__ import annotations

from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from testo_core.ui_bundle import UI_DIR_ENV, ui_dir

__all__ = ["UI_DIR_ENV", "SpaStaticFiles", "ui_dir"]


class SpaStaticFiles(StaticFiles):
    """Static files with a single-page-app fallback to ``index.html``."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            # API and report paths keep their real 404; only UI routes fall back.
            if exc.status_code != 404 or path.startswith(("api/", "history/")):
                raise
            return await super().get_response("index.html", scope)
