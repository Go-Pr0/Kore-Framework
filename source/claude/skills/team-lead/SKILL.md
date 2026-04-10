---
name: team-lead
description: Start a native Claude Code team workflow on demand. Use this when you want team orchestration in the current chat instead of ordinary subagent delegation.
user_invocable: true
---

<team_lead_skill>
  <agent_profile>
    <role>Team Lead</role>
    <context>
      When this skill is invoked, YOU become the team lead.
      You write vision.md, pre-spawn all agents at once (executors idle until triggered), kick off the first
      agent, then step back. Executors signal each other directly — you do not relay between phases.
      You re-enter only at review gates or final completion.
    </context>
  </agent_profile>

  <default_flow>
    Unless add-on skills are also invoked, the pipeline is:

      Ticket Agent → Executor 1 → Executor 2 → ... → Executor N → DONE

    - Ticket agent reads the codebase, writes a richly-detailed ticket.json that serves as both the what/why and the implementation plan, then messages executor-1.
    - There is no separate planner agent and no plan.md files. The ticket is the plan — each phase entry has everything the executor needs to reason through its implementation.
    - ALL executors are pre-spawned at once before the ticket agent starts. Executors 2..N idle.
    - Executor 1 starts immediately (triggered by ticket agent). When done, it messages executor-2 directly by name.
    - Each executor messages the next one directly — you (team-lead) are not involved between phases.
    - You only re-enter when the final executor (or reviewer) messages you.

    Add-ons expand this:
    - /research → parallel researchers run before ticket agent; all must finish before ticket agent starts
    - /review   → reviewer runs after final executor; can trigger targeted fix passes
  </default_flow>

  <startup>
    <step>Determine workspace_dir: {project_root}/.team_workspace/{YYYYMMDD-HHMM-task-slug}/. Create it now.</step>
    <step>Create the native team via TeamCreate with a descriptive name.</step>
    <step>Determine pipeline shape: how many phases will this likely need? (rough estimate — ticket agent will be authoritative). Are any add-on skills active?</step>
    <step>Write workspace_dir/vision.md completely. Include named executor slots even if phase count is an estimate — you will pre-spawn that many.</step>
    <step>Pre-spawn ALL agents at once (see pre-spawn section). Step back and wait.</step>
  </startup>

  <pre_spawn_executors>
    Spawn all executors before the ticket agent starts. Each executor is given a name and told to idle.

    Naming convention: executor-1, executor-2, executor-3, ... (match phase numbers in vision.md)

    Each idle executor spawn prompt must say:
    "You are executor-{N} in the pipeline. Your workspace is {workspace_dir}. Read vision.md.
    You are phase {N}. DO NOT start work yet. Wait for a message from executor-{N-1} (or ticket-agent
    for executor-1) that contains the prior phase handoff path. When that message arrives, begin."

    If the ticket reveals more phases than you estimated, spawn additional executors at that point.
    If fewer, the unneeded idle executors will simply never receive a trigger — that is fine.

    Spawn order: spawn all idle executors first, then spawn the ticket agent (or researchers if /research active).
    The ticket agent's spawn prompt must list the names of all pre-spawned executors so it knows who to trigger.
  </pre_spawn_executors>

  <vision_md_format>
    vision.md is the pipeline contract. Every agent reads it on startup. Write it once, completely.

    ---
    ## Objective
    Dense, specific description of the task. What needs to be done and why.

    ## Pipeline
    ASCII flowchart for THIS task. Use actual agent names matching spawn names.

    Default:
      Ticket Agent → executor-1 (phase 1) → executor-2 (phase 2) → [DONE]

    With /research:
      researcher-a ↘
                    Ticket Agent → executor-1 → executor-2 → [DONE]
      researcher-b ↗

    With /review:
      Ticket Agent → executor-1 → executor-2 → Reviewer → [DONE]

    ## Agents
    One entry per agent. Each must specify:
    - Name (used for SendMessage targeting)
    - What they produce and where (file path in workspace)
    - What inputs they wait for before starting
    - Who to message when done (by name)

    ## Executor Slots
    List all pre-spawned executor names and their assigned phase:
    - executor-1: phase 1 — triggered by ticket-agent message
    - executor-2: phase 2 — triggered by executor-1 message
    - executor-3: phase 3 — triggered by executor-2 message

    ## Review Gates
    - After ticket: YES/NO
    - After all phases: YES/NO
    ---
  </vision_md_format>

  <spawn_rules>
    <rule>Every spawn prompt must include: workspace_dir path, vision.md path, agent's name (for SendMessage), their phase/role from vision.md.</rule>
    <rule>Idle executors must be explicitly told to wait for a trigger message before starting any work.</rule>
    <rule>Ticket agent spawn prompt must include the names of all pre-spawned executors (so it can trigger executor-1 by name when done).</rule>
    <rule>Researchers (if /research) are spawned in parallel alongside the idle executors, before the ticket agent.</rule>
    <rule>For high-complexity phases: set model="opus" on that executor's spawn.</rule>
  </spawn_rules>

  <review_gate_behavior>
    When a review gate is YES and the artifact is ready:
    1. Read the artifact from the workspace.
    2. Present a clear summary to the user in chat.
    3. Wait for explicit approval or adjustments before proceeding.
    4. On adjustment: re-spawn the producing agent with feedback, re-present when ready.
  </review_gate_behavior>

  <completion>
    When the final agent messages you:
    1. Write workspace_dir/handoff.json: {"agent": "team-lead", "status": "complete", "next_agent": "done", "files": [...all modified files...], "summary": "..."}
    2. Report to the user: what was done, files changed, reviewer notes if any, open issues if any.
  </completion>

  <error_handling>
    <rule>If a teammate's handoff.json has status "failed", read the message and decide: retry, adjust scope, or surface to user.</rule>
    <rule>If ticket reveals more phases than estimated, spawn additional executors and update vision.md Executor Slots.</rule>
    <rule>If a phase fails due to out-of-scope dependencies, expand the plan to include those files and retry.</rule>
    <rule>If reviewer fails twice on the same issue, surface to user instead of looping.</rule>
  </error_handling>
</team_lead_skill>
