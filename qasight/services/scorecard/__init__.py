"""Release scorecard: run the repo review tools on a version, score it 0-100 and compare it with the version before.

* :mod:`.tools`: the tools QASight knows (what each checks, how a scan runs it).
* :mod:`.parsers`: each tool's own report file to a score and findings.
* :mod:`.store`: results and on/off switches in the run-history database.
* :mod:`.report`: a version's scorecard, the release gate and the Markdown summary.
* :mod:`.scan`: runs the tools on a folder or a git URL.

Design: ``docs/Specs & ADRs/Release Scorecard - 2026-11-12.md``.
"""
