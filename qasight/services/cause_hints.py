"""A suggested cause for a failure nobody has marked yet, from well-known messages.

Marking a cause (:mod:`~qasight.services.triage`) is a person's call, so nothing
here is stored: the run page shows the suggestion next to "Mark the cause" and one
click accepts it. Only messages that point one way are matched. A refused
connection, a DNS failure, a full disk or an out-of-memory kill is the machine or
the network, not the product; an import error or a missing fixture is the test
suite itself. Anything else (an assertion, a plain exception) gets no suggestion.
"""

from __future__ import annotations

import re
from typing import Final

_RULES: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    (
        "environment",
        re.compile(
            r"connection ?refused|econnrefused|connection reset|econnreset|broken pipe"
            r"|name or service not known|nodename nor servname|getaddrinfo|temporary failure in name"
            r"|could not resolve host|no route to host|network is unreachable|enetunreach"
            r"|no space left on device|enospc|out of memory|memoryerror|oomkilled|cannot allocate memory"
            r"|ssl: certificate_verify_failed|certificate verify failed"
            r"|\b50[234] (bad gateway|service unavailable|gateway time-?out)"
            r"|too many open files|address already in use|eaddrinuse"
            r"|docker daemon|cannot connect to the docker",
            re.IGNORECASE,
        ),
    ),
    (
        "test_bug",
        re.compile(
            r"modulenotfounderror|importerror|syntaxerror|indentationerror"
            r"|fixture '[^']+' not found|cannot find module|referenceerror: \w+ is not defined"
            r"|no tests ran|collection error|error collecting",
            re.IGNORECASE,
        ),
    ),
)


def suggest_cause(message: str | None) -> str | None:
    """``"environment"`` or ``"test_bug"`` when the message clearly says so, else ``None``."""
    text = (message or "")[:4000]
    for cause, pattern in _RULES:
        if pattern.search(text):
            return cause
    return None
