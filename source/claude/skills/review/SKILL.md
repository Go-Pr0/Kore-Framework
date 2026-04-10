---
name: review
description: Add-on for /delta-team. Inserts a review phase after all d-raptors complete. Apex checks correctness, consistency, and scope adherence — and can trigger targeted fix passes.
user_invocable: true
---

<review_addon>
  <purpose>
    This add-on instructs you on how to run a review phase as part of a /delta-team pipeline.
    Invoke this when the implementation warrants an independent read after execution. The reviewer
    DYNAMICALLY picks quick vs full mode based on diff scope — quick is the default, full only
    escalates for large or sensitive changes. See d-apex.md for the exact scope heuristics.
  </purpose>

  <reviewer_scope>
    The reviewer reads:
    - workspace/vision.md (original objective + Execution Schedule)
    - workspace/ticket.json (what was supposed to happen, including depends_on wave graph)
    - All wave handoff.json files (what actually happened, per wave)
    - git diff HEAD --stat and full diff
    - In QUICK mode: only files already visible via the diff
    - In FULL mode: additionally, surrounding context files (callers, type definitions, tests, adjacent modules)

    The reviewer checks:
    - Does the implementation satisfy the ticket's conceptual_summary and technical_requirements?
    - Does the core logic actually land as described in each wave's handoff?
    - Are there cross-wave consistency issues (e.g., a parallel wave changed a shared type the other wave didn't account for)?
    - Are there obvious correctness bugs, missing edge cases, or scope gaps?
    - Did any executor silently drift from the plan?
  </reviewer_scope>

  <reviewer_output>
    The reviewer writes workspace/review.md:

    ## Verdict: PASS | PASS_WITH_NOTES | FAIL

    ## Mode: QUICK | FULL — with a one-line reason

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
    After receiving the reviewer's message, team-lead decides:
    - PASS: report to user, done.
    - PASS_WITH_NOTES: report to user with notes inline, done.
    - FAIL: spawn a targeted fix executor scoped only to the flagged files.
      The fix executor spawn prompt must include: vision.md path, ticket.json path, workspace/review.md path,
      and the list of flagged files. It does NOT re-run the full plan — it addresses review issues only.
      After fix executor completes, team-lead spawns the reviewer again for a second pass.
    - If the reviewer fails twice on the same issue, surface it to the user rather than looping.
  </fix_pass_behavior>

  <pipeline_shape>
    With /review, the vision.md Pipeline section looks like (with parallel waves shown as fan-out):

      Vector → [team-lead: schedule] → d-raptor-1 ↘
                                                  → [team-lead] → Apex → [Fix Raptor if FAIL] → [team-lead] → [Done]
                                       d-raptor-2 ↗

    All executors report to team-lead. Team-lead spawns the reviewer once every wave is complete.
    Reviewer's next_agent is "team-lead".
  </pipeline_shape>
</review_addon>
