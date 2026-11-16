---
type: guide
status: current
created: 2026-11-16
updated: 2026-11-16
---

# Beta case study kit

[Index](../Index.md) · [Moving from ReportPortal](Moving%20from%20ReportPortal.md) · [Benchmark](Benchmark.md)

How to run a month with a design-partner team and come out with a case study that has real numbers in it. The numbers come from the team's own history with [`qasight case-study`](../CLI%20Commands/Command%20Reference.md#qasight-case-study); the story comes from two short interviews.

## Before day one

1. **Agree what to measure.** Pick the team's cycles (the suites they care about) and write down the switch day. Ask which of these hurts most today: time to fix a red build, flaky tests, sorting failures, or not knowing what changed.
2. **Get a "before" period.** Import what they already have, so there is something to compare with:
   - from ReportPortal: [`qasight import reportportal`](Moving%20from%20ReportPortal.md#3-import-what-you-already-have);
   - from GitHub Actions artifacts: `qasight ingest --from-github`;
   - otherwise, start a week early and only upload results, with no other change.
3. **First interview** (20 minutes, questions below). Record the answers word for word.

## During the month

- Upload every CI run (the [GitHub Action](CI-CD%20Pipeline%20Setup.md#github-action) or `qasight ingest --server`), and point ReportPortal agents at the server if they have them.
- Ask the team to mark the cause of failures they look at (or accept a suggested one), and to file product bugs with the Jira button. Marked causes are what the "time to a cause" and "suggestions that were right" numbers count.
- Check in weekly: `qasight case-study --cycle <cycle> --since <switch day>` and `qasight triage accuracy --cycle <cycle>`. A number going the wrong way is worth a question.

## At the end

```bash
qasight case-study --cycle shop-api --cycle shop-web --since 2026-11-01 --days 30 > numbers.md
```

Then the second interview, then fill in the template. Ask the team to read the draft and approve every number and quote before it goes anywhere, and whether they want to be named.

## Interview questions

First interview (before):

1. What do you use today to see test results across runs? What do you like about it?
2. When the build goes red, what happens next, and how long until someone knows why?
3. How many tests do you consider flaky? How do you find them, and what do you do with them?
4. Who decides whether a failure is a product bug, a test bug or the environment? Where is that written down?
5. What would have to be true at the end of the month for you to keep using QASight?

Second interview (after):

1. What do you open first when a run fails now?
2. Tell me about one failure this month that was faster (or slower) to sort out than before.
3. Were the suggested causes useful? When were they wrong?
4. What did you stop doing, or stop running?
5. What is missing that would stop you recommending it?
6. Would you recommend QASight to another team, and what would you tell them?

## Template

```markdown
# <Team> at <Company>: <one-line result, e.g. "red builds fixed in half the time">

**Team:** <size, what they build>  **Tests:** <runners, number of tests, runs per day>
**Before:** <tools>  **Switched:** <date>

## The problem
<In their words, from the first interview.>

## What they did
<Upload from CI / ReportPortal agents pointed at QASight / imported history / marked causes / Jira.>

## The numbers
<Paste the table from qasight case-study.>

## What changed
<Two or three stories from the second interview, with quotes.>

## What's next
<What they asked for.>
```

Keep the numbers as `qasight case-study` printed them, with the period they cover; don't round them up.
