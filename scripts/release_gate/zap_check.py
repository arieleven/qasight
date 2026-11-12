"""Release gate: fail on any medium or high OWASP ZAP finding not accepted below.

Reads the traditional-json report the plan in zap-plan.yaml writes.

    python scripts/release_gate/zap_check.py .gate/zap/zap-report.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# ZAP risk codes: 0 informational, 1 low, 2 medium, 3 high.
FAIL_AT = 2
# pluginid -> why it is accepted. Empty: the 2026-11-12 scan had no medium or high finding.
ACCEPTED: dict[str, str] = {}


def main(report: Path) -> int:
    data = json.loads(report.read_text(encoding="utf-8"))
    if not data.get("site"):
        print("::error::the ZAP report lists no site: the scan did not reach the API")
        return 1
    failing = 0
    for site in data.get("site", []):
        for alert in site.get("alerts", []):
            risk = int(alert.get("riskcode", 0))
            where = (
                alert["instances"][0]["uri"] if alert.get("instances") else site.get("@name", "")
            )
            line = f"{alert.get('riskdesc', risk)}: {alert.get('name')} (plugin {alert.get('pluginid')}) at {where}"
            if risk < FAIL_AT:
                print(line)
            elif str(alert.get("pluginid")) in ACCEPTED:
                print(f"{line} - accepted: {ACCEPTED[str(alert['pluginid'])]}")
            else:
                print(f"::error::{line}")
                failing += 1
    print(f"ZAP: {failing} medium or high finding(s) not accepted")
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
