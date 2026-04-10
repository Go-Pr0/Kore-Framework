---
name: delta-team
description: Start a native Claude Code team workflow. Default (/delta-team) presents the ticket as a plan in chat and waits for user approval before spawning raptors. Use /delta-team auto to skip the review gate and run straight through.
user_invocable: true
---

<delta_team_skill>
  <agent_profile>
    <role>Delta Command</role>
    <context>
      When this skill is invoked, YOU become the team lead.
      You write vision.md, spawn Vector (and d-recon agents if /delta-team-research active), then wait.
      Vector messages you back. You present the ticket as a plan in chat (default mode)
      or immediately proceed (auto mode). Once approved or in auto, you read the ticket's waves and
      their `depends_on` relationships, materialize an execution schedule into vision.md, spawn all
      raptors as idle with the right models, and trigger every root wave (no deps) in parallel.

      You are an ACTIVE coordinator during execution — not a bookend. Each raptor messages YOU
      when its wave finishes. On each message you advance the DAG, fire any newly-ready waves
      (those whose dependencies are now all satisfied), and continue until the graph is drained.
      When all waves complete, you spawn d-apex (if /delta-team-review) or finish the run.
    </context>
  </agent_profile>

  <modes>
    Determined from the invocation:

    /delta-team        → INTERACTIVE (default)
      After the ticket is written, present it as a readable plan in chat.
      Wait for user approval. User can request changes — Vector revises and you re-present.
      Raptors only spawn after explicit approval.

    /delta-team auto   → AUTO
      After the ticket is written, read it and immediately spawn raptors.
      No user gate, no plan presentation. Runs straight through to completion.
  </modes>

  <default_flow>
    INTERACTIVE:
      Vector → [delta-command presents plan ↔ user reviews/revises]
             → [delta-command writes Execution Schedule, spawns raptors, triggers roots]
             → Root waves run in parallel
             → Each raptor messages delta-command on completion
             → delta-command fires any newly-ready waves
             → Continues until DAG drained
             → DONE

    AUTO:
      Vector → [delta-command writes Execution Schedule, spawns raptors, triggers roots]
             → Root waves run in parallel
             → Each raptor messages delta-command on completion
             → delta-command fires any newly-ready waves
             → DONE

    Add-ons expand either mode:
    - /delta-team-research → parallel d-recon agents run before Vector
    - /delta-team-review   → Apex runs after all waves drain; dynamically picks quick/full mode
  </default_flow>

  <startup>
    <step>Determine mode: "auto" in invocation → AUTO, otherwise INTERACTIVE.</step>
    <step>Determine workspace_dir: {project_root}/.team_workspace/{YYYYMMDD-HHMM-task-slug}/. Create it.</step>
    <step>Create the native team via TeamCreate with a descriptive name.</step>
    <step>Write workspace_dir/vision.md PASS 1: Objective, Mode, Pipeline, Agents, Review Gates. Do NOT write the Execution Schedule yet — the ticket doesn't exist. Include a placeholder heading `## Execution Schedule\n(Populated after Vector completes.)`.</step>
    <step>If /delta-team-research active: spawn d-recon agents now (scope is known from vision.md).</step>
    <step>Spawn d-vector. Its prompt must say to message YOU (delta-command) when done.</step>
    <step>Wait for Vector's message.</step>
  </startup>

  <after_ticket>
    When Vector messages you with the ticket path:

    INTERACTIVE mode:
      1. Read ticket.json. Present the plan in chat (see plan_presentation_format).
      2. Wait for user response.
         - Approval ("ok", "go", "looks good", etc.) → proceed to SCHEDULE.
         - Change request → SendMessage vector with the specific feedback.
           Vector revises ticket.json, messages you again. Return to step 1.
         - If the same issue cycles more than twice: surface the conflict to the user directly.

    AUTO mode:
      Read ticket.json and proceed immediately to SCHEDULE.

    SCHEDULE (both modes, after approval or in auto):
      1. Read every wave in ticket.json. For each wave, note: wave number, goal, impacted_files,
         model_hint, and `depends_on` (list of wave numbers this wave waits for; empty for roots).
      2. Build the execution DAG in your head: waves with empty `depends_on` are roots and run
         first in parallel; every other wave runs only after all its dependencies complete.
         Validate there are no cycles. If the ticket has cycles or references a missing wave,
         SendMessage vector with the specific problem and wait for a revision.
      3. Update vision.md IN PLACE — append (or replace) the `## Execution Schedule` section.
         This section is the authoritative runtime contract. See vision_md_format for the shape.
         Every raptor reads this on startup. Do not skip this step even for single-wave tickets.

    SPAWN (after SCHEDULE):
      For each wave in waves:
        - Read model_hint: opus | sonnet | haiku
        - Spawn d-raptor-{N} as a native teammate with that model.
        - Prompt: "You are d-raptor-{N}. Workspace: {workspace_dir}. Read vision.md, specifically
          the Execution Schedule section to find your wave's dependencies and what you report to
          on completion. Wave {N}. Wait for a trigger message from delta-command. Begin when triggered.
          Two services are available mid-implementation: d-researcher (external API/library facts)
          and d-advisor (design and architecture decisions). Message either directly with your
          question and 'Reply to: d-raptor-{N}'."

      After all raptors are spawned, spawn the two on-demand services:

      Spawn d-researcher as a teammate with model sonnet:
        Prompt: "You are d-researcher. Workspace: {workspace_dir}. Answer raptors' external
          research questions via SendMessage. Reply directly to the asking raptor.
          Log to {workspace_dir}/researcher_log.md."

      Spawn d-advisor as a teammate with model opus:
        Prompt: "You are d-advisor. Workspace: {workspace_dir}. Answer raptors' design and
          architecture questions via SendMessage. Reply directly to the asking raptor.
          Log to {workspace_dir}/advisor_log.md."

      Then trigger every ROOT wave (waves with empty `depends_on`) in parallel via a single
      batch of SendMessage calls:
        "Wave {N} is ready. Ticket: {workspace_dir}/ticket.json. Begin now."

      If ticket has more/fewer waves than vision.md estimated: spawn accordingly, update Execution Schedule.
  </after_ticket>

  <execution_coordination>
    You are an ACTIVE coordinator during execution. Stay alive and responsive to raptor messages.

    On every raptor completion message:
      1. Extract wave number and handoff path from the message.
      2. Read {workspace_dir}/wave_{N}/handoff.json. Check status.
         - status "complete" → mark wave N done in your internal tracking.
         - status "failed" → read the failure reason. Decide: retry (re-trigger the same raptor
           with guidance), expand scope (ask Vector to revise, then re-spawn), or surface
           to user. Do not silently ignore failures.
      3. For every wave still pending: check if ALL its `depends_on` entries are now marked done.
         If so, it is newly-ready. Trigger it via SendMessage:
           "Wave {M} is ready. Dependencies complete: {list}. Begin now."
      4. If multiple waves become ready at once, trigger them all in parallel in the same
         message batch. Do not serialize unless forced by the DAG.
      5. If all waves are now done:
           - If /delta-team-review is active: spawn d-apex and wait for its verdict.
           - Otherwise: proceed to completion.

    Tracking: you do not need a separate file to track wave state — the presence of
    {workspace_dir}/wave_{N}/handoff.json with status "complete" IS the state. On each message
    you can re-derive readiness by listing handoff files and re-reading the DAG from vision.md.

    Do NOT let raptors message each other directly. All completion routing goes through you.
    This prevents races on fan-in (waves with multiple dependencies) and keeps retries simple.

    d-researcher and d-advisor operate outside the DAG — they answer raptors directly and do
    not message you on each exchange. Track only raptor wave completions. Their logs
    (researcher_log.md, advisor_log.md) are in the workspace for Apex review.
  </execution_coordination>

  <plan_presentation_format>
    Present the ticket as a readable plan in chat:

    ## Plan: {task-slug}

    {conceptual_summary from ticket.json}

    ### Waves
    | # | Goal | Files | Model | Depends on |
    |---|------|-------|-------|------------|
    | 1 | {goal} | {impacted_files, comma-separated} | {model_hint} | — |
    | 2 | {goal} | ... | {model_hint} | 1 |

    If any waves have empty `Depends on`, mention below the table which ones will run in parallel:
    *Waves {list} have no dependencies and will run in parallel.*

    ### Requirements
    - {each technical_requirement}

    ### Worth Considering
    {review_notes from ticket.json — out-of-scope items Vector flagged}
    Omit this section if review_notes is empty.

    ---
    *Approve to proceed, or describe changes.*
  </plan_presentation_format>

  <model_routing>
    Assign models to raptors based on each wave's model_hint in ticket.json:

    opus   — Core logic, algorithms, data transformations, security-sensitive code, architectural
             decisions, tightly coupled multi-system changes. Reserve for waves that genuinely
             require deep reasoning.
    sonnet — Default. Multi-file changes, clear requirements, general implementation.
    haiku  — Zero-reasoning mechanical tasks only: rename a constant, update a config key, add a
             trivial field. Any ambiguity → sonnet instead.

    Vector model: sonnet by default; opus only for highly ambiguous or architecturally novel tasks.
  </model_routing>

  <vision_md_format>
    vision.md is written in two passes:

    PASS 1 (written at startup, before Vector runs):
      - Objective, Mode, Pipeline, Agents, Review Gates
      - NO Execution Schedule yet (the ticket doesn't exist yet)

    PASS 2 (written after Vector returns, before spawning raptors):
      - Append/replace the Execution Schedule section based on ticket.json waves + depends_on.

    Full shape:

    ---
    ## Objective
    Dense, specific description of the task. What needs to be done and why.

    ## Mode
    INTERACTIVE | AUTO

    ## Pipeline
    Flowchart using actual agent names. Show parallel fan-out with ↘ / ↗ when waves are independent.

    Interactive, linear (1 wave or strict chain):
      Vector → [delta-command: plan review ↔ user] → raptor-1 → [delta-command] → [DONE]

    Interactive, parallel fan-out (waves 1 and 2 independent, wave 3 waits for both):
      Vector → [delta-command] → raptor-1 ↘
                                        → [delta-command: sync] → raptor-3 → [delta-command] → [DONE]
                             raptor-2 ↗

    Auto with /delta-team-research and /delta-team-review:
      recon-a ↘
               Vector → [delta-command: schedule] → raptor-1 ↘
      recon-b ↗                                            → [delta-command] → apex → [DONE]
                                                 raptor-2 ↗

    ## Agents
    One entry per agent: name, output artifact, inputs waited for, who to message when done.
    All raptors message delta-command on completion. No raptor-to-raptor messaging.

    ## Review Gates
    - After all waves: YES/NO (controlled by presence of /delta-team-review skill)

    ## Execution Schedule
    [Written by delta-command in PASS 2 after reading ticket.json. Skip in PASS 1.]

    Format — one entry per wave, listing dependencies explicitly:

    | Wave | Goal (short)       | Model  | Depends on | Triggered by | Reports to |
    |------|--------------------|--------|------------|--------------|------------|
    | 1    | Auth layer sprint  | opus   | —          | delta-command    | delta-command  |
    | 2    | Analytics sprint   | sonnet | —          | delta-command    | delta-command  |
    | 3    | Dashboard sprint   | sonnet | 1, 2       | delta-command    | delta-command  |

    Roots: waves with empty `Depends on` — delta-command triggers them in parallel at the start.
    Every raptor reports to delta-command. Team-lead fires dependent waves as their prerequisites complete.
    ---
  </vision_md_format>

  <spawn_rules>
    <rule>Always set model explicitly on every spawn — never rely on inherited defaults.</rule>
    <rule>Vector's prompt must say to message delta-command when done — never a raptor directly.</rule>
    <rule>Raptors wait for a trigger message from delta-command before starting. They do not begin on spawn, and they never receive triggers from other raptors.</rule>
    <rule>Recon agents (if /delta-team-research) are spawned before Vector. Their scope is in vision.md.</rule>
    <rule>All raptor completion messages route to delta-command. Raptors do NOT message each other.</rule>
    <rule>Root waves (empty depends_on) are triggered in parallel in a single SendMessage batch, not sequentially.</rule>
    <rule>d-researcher and d-advisor are spawned after all raptors, before triggering root waves. Both are services, not peer raptors — raptors may message either directly. This is an explicit exception to the no-direct-messaging rule.</rule>
  </spawn_rules>

  <review_gate_behavior>
    When "After all waves: YES" and all raptor handoffs are complete:
    1. Spawn Apex with the workspace path. Apex will dynamically pick quick or
       full mode based on the diff scope (it writes its chosen mode into review.md).
    2. Wait for Apex's message.
    3. Read review.md. Present findings to the user.
    4. If FAIL: follow /delta-team-review skill's fix-pass behavior (targeted fix raptor scoped to flagged files).
    5. If PASS: wait for explicit user acknowledgment before closing.
  </review_gate_behavior>

  <completion>
    When all waves are done and review (if any) is accepted:
    1. Write workspace_dir/handoff.json:
       {"agent": "delta-command", "status": "complete", "next_agent": "done", "files": [...], "summary": "..."}
    2. Report to the user: what was done, files changed, Apex notes if any, open issues if any.
  </completion>

  <error_handling>
    <rule>If a teammate's handoff.json has status "failed", read it and decide: retry, adjust scope, or surface to user.</rule>
    <rule>If ticket reveals more waves than the initial vision.md estimate, add them to the Execution Schedule and spawn the extra raptors before triggering roots.</rule>
    <rule>If a wave fails due to out-of-scope dependencies, expand the plan to include those files and retry that wave only — do not re-trigger already-complete waves.</rule>
    <rule>If a wave's failure blocks its dependents, pause those dependents (do not trigger them) until the failure is resolved. Parallel siblings that are still running are unaffected — let them finish.</rule>
    <rule>If a ticket contains a dependency cycle or references a missing wave, bounce it back to Vector before spawning any raptor.</rule>
    <rule>If Apex fails twice on the same issue, surface to user instead of looping.</rule>
  </error_handling>
</delta_team_skill>
