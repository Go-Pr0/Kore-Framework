---
name: review
description: Add-on for /team-lead. Inserts a review phase after all executors complete. Reviewer checks correctness, consistency, and scope adherence — and can trigger targeted fix passes.
user_invocable: true
---

<review_addon>
  <purpose>
    This add-on instructs you on how to run a review phase as part of a /team-lead pipeline.
    Invoke this when the implementation is non-trivial, cross-file, or has correctness risk that warrants
    an independent read after execution. Skip for small single-phase changes you are confident in.
  </purpose>

  <reviewer_scope>
    The reviewer reads:
    - workspace/vision.md (original objective)
    - workspace/ticket.json (what was supposed to happen)
    - All phase handoff.json files (what actually happened, per phase)
    - Every file listed in all phase handoffs (the actual diffs)

    The reviewer checks:
    - Does the implementation satisfy the ticket's conceptual_summary and technical_requirements?
    - Are there cross-phase consistency issues (e.g., executor 1 changed an interface executor 2 didn't account for)?
    - Are there obvious correctness bugs, missing edge cases, or scope gaps?
    - Did any executor silently drift from the plan?
  </reviewer_scope>

  <reviewer_output>
    The reviewer writes workspace/review.md:

    ## Verdict: PASS | PASS_WITH_NOTES | FAIL

    ## Summary
    One paragraph on overall quality.

    ## Issues
    For each issue (if any):
    - Severity: critical | minor
    - File and location
    - What is wrong and why
    - What the fix should be

    ## Approved Files
    List of files that are correct and need no changes.

    Then messages team-lead.
  </reviewer_output>

  <fix_pass_behavior>
    After receiving the reviewer's message:
    - PASS: report to user, done.
    - PASS_WITH_NOTES: report to user with notes inline, done.
    - FAIL: spawn a targeted fix executor scoped only to the flagged files.
      The fix executor spawn prompt must include: vision.md path, ticket.json path, workspace/review.md path,
      and the list of flagged files. It does NOT re-run the full plan — it addresses review issues only.
      After fix executor completes, spawn the reviewer again for a second pass.
    - If the reviewer fails twice on the same issue, surface it to the user rather than looping.
  </fix_pass_behavior>

  <pipeline_shape>
    With /review, the vision.md Pipeline section looks like:

      Ticket Agent → Executor 1 → [Executor N] → Reviewer → [Fix Executor if FAIL] → [Done]

    The last executor's next_agent is "reviewer". Reviewer's next_agent is "team-lead".
  </pipeline_shape>
</review_addon>
