---
type: spec
status: current
created: 2026-11-15
updated: 2026-11-15
related: "Release Gate - 2026-11-12"
---

# Contributors Cleanup - 2026-11-15

[Index](../Index.md) · Taltal asked on 2026-11-12, and again on 2026-11-15, to remove Claude, Cursor and Dependabot from the repository's Contributors list on GitHub.

## Context

GitHub builds the Contributors list from the authors and `Co-authored-by` trailers of the commits on the default branch. There is no setting that hides an account. On 2026-11-12, `main` had:

- 16 commits authored by Cursor (`Cursor Agent`, `cursor[bot]`) and about 30 `Co-authored-by: Cursor` trailers, from early work done in Cursor.
- 5 commits authored by `Claude` and about 250 `Co-Authored-By: Claude …` trailers, added by Claude Code sessions. Cloud sessions committed as `Claude <noreply@anthropic.com>`, so every squash merge added a Claude trailer.
- 15 squash-merged Dependabot PRs authored by `dependabot[bot]`.

## Decision

1. **Rewrite history.** `git filter-repo` with a mailmap moved those commits to the maintainer and dropped the trailers. The trees are identical, only the SHAs changed. Claude and Cursor went on 2026-11-12 (`58b39ae` → `f9207d5`). Dependabot went on 2026-11-15 (`2047108` → `911a09b`). The release tags were moved to the rewritten commits each time.
2. **Refresh GitHub's cache.** The Insights graph updated straight away, but the sidebar list kept the old accounts. Switching the default branch away and back makes GitHub recount.
3. **Keep them off.** `commitlint.yml` has a `no-bot-authors` job that fails a PR when any commit is authored or committed by Claude, Cursor or Dependabot, or has a `Co-authored-by` trailer for them. The session-start hook sets the git identity to the maintainer in Claude Code cloud sessions. `.claude/settings.json` turns off Claude Code's co-author trailer. [CLAUDE.md](../../CLAUDE.md) states the rule.
4. **Dependabot PRs are not merged as they are.** They fail `no-bot-authors` on purpose. Land the update as your own commit, for example by applying the PR's diff on a new branch, and close the Dependabot PR.

## Consequences

- Every commit SHA before 2026-11-15 changed twice. Links to old SHAs, existing clones and forks, and branches made before the rewrite point at the old history. Clone again, or run `git fetch && git reset --hard origin/main`. Open PRs need `git rebase --onto origin/main <old main tip> <branch>`.
- Release tags `v1` and `v1.1.1` to `v1.7.0` point at the rewritten commits. Their trees, the PyPI and Docker artifacts, and the GitHub Releases are unchanged.
- Dependabot updates take one extra step.
