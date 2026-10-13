---
name: litereview
description: Iterate on a pull request with LiteReview until its current commit earns 5/5, then request a hosted preview for the human reviewer. Use when asked to run the LiteReview fix-and-review loop.
---

# LiteReview loop

Use the configured LiteReview MCP and authenticated GitHub access. Default to
`code-review` and `preview`; honor the user's chosen reviewers. This workflow
requests paid reviews and posts to GitHub. Work within the user's authorization
for commits, pushes and comments. Never merge or change reviewer configuration
as part of the loop.

## Review the current commit

1. Identify the PR and read its current head SHA from GitHub. Read the LiteReview
   operator guide and discover the reviewer's ID. Keep the PR's existing work.
2. Find its latest runs using `litereview_list_runs`, filtered by repository, PR
   and agent. Read candidate runs with `litereview_get_run`. Match the captured
   head exactly and use the primary result, not a fusion branch or private
   comparison. A score from an older commit does not qualify.
3. If that reviewer is already running on this head, wait for it. Otherwise use
   `litereview_request_review` with a fresh `request_id`, or post
   `@litereview code-review` if MCP cannot request it. Reuse the same request ID
   on uncertain retries; do not submit through both paths. A newer head replaces
   the previous active review for that reviewer and PR.
4. Read findings, carried-forward finding assessments, checks and limitations.
   Inspect the cited code and evidence. Fix valid issues, run relevant checks,
   and commit/push the fix when authorized. For a false positive, reply in its
   GitHub review thread with specific code or test evidence; wait for the
   reviewer's decision. Do not dismiss a thread merely to achieve a score.
5. Request another review after the fixes or verified rebuttals. Check GitHub's
   head again before using its verdict. Incremental reviews must reassess prior
   open findings; “no new findings” alone is insufficient.

Use the server's `verdict.state="ready"` and `verdict.score=5`, with a completed
primary job, no limitations and the matching head. Do not parse a model's prose
score or a green completion icon as readiness. If scoring is disabled, report
that configuration gap instead of inventing a score. A 5/5 means this configured
review found no remaining actionable issues; it is not a guarantee of correctness.

Stop and report the exact obstacle if progress requires unavailable credentials,
user decisions, unauthorized changes, or an exhausted user budget. Stop if the
same dispute or infrastructure failure repeats without new evidence or progress;
do not keep buying identical reviews. There is no arbitrary output-token cap.

## Give the human a preview

Once the exact current head qualifies, request `@litereview preview` through MCP
or GitHub and wait for its primary result. Read `preview_url` and, if available,
`litereview_get_preview`. Confirm that the preview covers the same current head.
If code changes, return to review before presenting it as ready.

Share the final 5/5 review link, commit SHA and hosted preview link. Mention any
preview limitations honestly. Do not fabricate video, execution evidence or
successful checks. A failed preview does not erase the completed code review;
report the failure and its recovery path. Preview pages and media require
workspace sign-in even when an OSS reviewer can be invoked publicly.
