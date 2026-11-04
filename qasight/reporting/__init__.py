"""Post-execution reporting: collect → generate → serve / export.

Only the CLI's ``qasight report`` command depends on this module.  The runtime
engine writes raw ``allure-results/`` only; HTML and any UI is produced on
demand here.
"""

from qasight.reporting.collector import CollectedResults, collect_results
from qasight.reporting.entry import dispatch_report

__all__ = ["CollectedResults", "collect_results", "dispatch_report"]
