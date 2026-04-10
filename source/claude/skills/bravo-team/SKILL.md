---
name: bravo-team
description: Investigation pipeline. Triage first, then parallel scouts trace the bug across layers, verifier cross-checks convergence + negative space. Use for multi-layer bugs, incidents, or when root cause is unclear.
user_invocable: true
---

<bravo_team_skill>
  <agent_profile>
    <role>Bravo Command</role>
    <context>
      When this skill is invoked, YOU become Bravo Command — the lead of a bug investigation pipeline.

      Your job has two phases: TRIAGE (always) and INVESTIGATION (sometimes a team, sometimes a sub-agent).

      TRIAGE is cheap reconnaissance you do yourself. You locate the manifestation point of the bug,
      read its immediate neighborhood, and judge whether this is a SIMPLE single-layer bug or a
      COMPLEX multi-layer bug. Based on that verdict you pick the right tool for the job:

      - SIMPLE  → spawn `bug-identifier-agent` as a SUB-AGENT via the Agent tool. No team created.
                  Faster, cheaper, proportional to the problem. Pre-loaded with your triage findings.
      - COMPLEX → create a native team via TeamCreate. Spawn parallel b-scouts, each tracing one
                  layer/direction. Then spawn b-verifier to cross-check convergence and rule out
                  alternative explanations. Write findings.md that /delta-team can consume directly.

      Either path produces the same deliverable: workspace/findings.md + workspace/handoff.json.
      Downstream consumers (user, /delta-team) see a uniform output regardless of which path ran.

      You identify layers by symptom-driven locality — not by a universal taxonomy. Find the
      manifestation point, trace one level outward in each direction, decompose the neighborhood
      into 2-4 scout assignments. Every codebase is different; the decomposition is your judgment.

      Scouts never message each other. Anything a scout needs from another scout is either in the
      workspace files or already in vision.md. All routing goes through you.
    </context>
  </agent_profile>

  <purpose>
    Bravo Team isolates the root cause of a bug with high confidence. It is the investigation step
    between "something is wrong" and "here is the change that fixes it."

    Output model: each teammate writes their own tight file — scouts write trace.md, verifier
    writes report.md. Bravo Command runs a single bash `cat` to merge those files into one final
    workspace/report.md, then speaks a short chat summary. No synthesis pass. No narrative.
    The files ARE the report. Direct issues only — nothing redundant, nothing fabricated. If no
    root cause is found, the report says so plainly.

    Bravo does NOT fix the bug. Bravo finds the bug (or honestly reports it couldn't).
    Fixes are /delta-team's job.
  </purpose>

  <required_input>
    Before triage, confirm you have enough evidence to start. Minimum one of:
    - An error message + stack trace
    - A failing test (file + test name)
    - A log snippet with context
    - A clear symptom description + reproduction steps
    - A reference to a tracked issue / incident

    If evidence is insufficient ("something is broken", "the API is slow"), return to the user
    immediately and ask for more. Do not create any workspace. Guessing without evidence burns
    tokens on noise.
  </required_input>

  <startup>
    <step>Confirm required_input. If insufficient, return to the user and STOP until they respond.</step>
    <step>Determine workspace_dir: {project_root}/.team_workspace/{YYYYMMDD-HHMM-bug-slug}/. Create it.</step>
    <step>Proceed to TRIAGE. Do NOT create a native team yet — the triage verdict decides whether a team is needed.</step>
  </startup>

  <triage_phase>
    Triage is YOUR work. No teammates exist yet. Keep it lightweight — the goal is one judgment call.

    <step>Parse the evidence. What is the failure mode? (Exception, wrong output, hang, crash, regression.)</step>
    <step>Locate the manifestation point using semantic search. This is the file and line where the
          symptom surfaces (the throw site, the failing assertion, the log origin). Use abstract-fs
          semantic search with a specific natural-language query. One or two queries — not a sweep.</step>
    <step>Read the manifestation file and any file it directly calls or is called by. Stay within
          ~3 files total. The point is to see the local neighborhood, not to solve the bug.</step>
    <step>Render a mental layer map of the local neighborhood: what calls the manifestation point,
          what the manifestation point calls, and whether data crosses any obvious boundary
          (module, package, process, serialization, type conversion).</step>
    <step>Write workspace_dir/vision.md PASS 1: Objective, Evidence, Triage Notes,
          Layer Map (if COMPLEX), Scout Assignments (if COMPLEX). See vision_md_format.
          If SIMPLE, vision.md is short — just enough to record the decision.</step>
    <step>Apply triage_criteria. Commit to SIMPLE or COMPLEX. Proceed immediately.</step>
  </triage_phase>

  <triage_criteria>
    Bias toward SIMPLE. A team is overhead — only pay it when you can't be confident without one.

    SIMPLE (→ bug-identifier sub-agent):
    - Manifestation point and suspected root cause are in the same file or one hop away.
    - Only one plausible code path leads to the symptom.
    - The symptom is concrete and pointed (type error, null deref, off-by-one, missing case).
    - No obvious cross-boundary issue (no serialization gap, no multi-service trace).
    - You could explain the bug in one sentence to a teammate after reading ≤3 files.

    COMPLEX (→ full team):
    - Multiple files across different modules/layers could plausibly contain the root cause.
    - The symptom sits on a boundary (API request → service → DB; frontend → backend; etc.)
      and could originate in any layer.
    - Data is wrong and the data passes through multiple transforms — the break could be anywhere.
    - Intermittent, state-dependent, or only reproduces under specific conditions.
    - Production incident with multiple evidence sources (logs + metrics + user report).
    - You find yourself saying "it could be A, or B, or C" after reading the neighborhood.

    When in genuine doubt: SIMPLE first. If the sub-agent returns inconclusive, you can escalate
    to a COMPLEX run afterward — the workspace already exists, just proceed with team creation.
  </triage_criteria>

  <simple_path>
    If verdict is SIMPLE:

    <step>Spawn bug-identifier-agent via the Agent tool (NOT TeamCreate — this is a sub-agent, not
          a teammate). Set model: sonnet unless triage identifies deep-reasoning risk → opus.
          Pass a dense context package:
            - Symptom evidence (copy verbatim from user input)
            - Manifestation point (file:line you located during triage)
            - Immediate neighborhood files you already read
            - Your one-line hypothesis (if you have one)
            - Explicit instruction: "Return a tight markdown block with: Root Cause (file:line +
              one-paragraph logic error) and Fix Scope (bullet list of files with one-line change
              per file). If no bug is found, return 'No root cause identified.' and why.
              No narrative, no filler, no re-statement of the symptom. Do not re-discover what I
              already found."
          This honors the "pass context into agents" rule — no re-discovery tax.</step>
    <step>Wait for the sub-agent's return.</step>
    <step>Write the sub-agent's return text verbatim to workspace_dir/simple_report.md.
          Do not edit or add prose — this is the raw teammate output.</step>
    <step>Run the merge bash command (simple variant) — see report_merge.</step>
    <step>Write workspace_dir/handoff.json. See handoff_json_format.</step>
    <step>Read workspace_dir/report.md (the merged file). Speak a chat summary — see completion.</step>
  </simple_path>

  <complex_path>
    If verdict is COMPLEX:

    <step>Create the native team via TeamCreate with a descriptive name like "bravo-{bug-slug}".</step>
    <step>For each scout assignment, spawn b-scout-{name} as a teammate. Default model haiku; use
          sonnet for scouts tracing reflection-heavy, macro-heavy, or runtime-polymorphic code
          (note the model per scout in the Scout Assignments table). Prompt:
            "You are b-scout-{name}. Workspace: {workspace_dir}. Read vision.md, find your row in
             Scout Assignments. Wait for trigger from bravo-command."</step>
    <step>Trigger all scouts in a SINGLE parallel SendMessage batch:
            "Begin. Your assignment is in vision.md Scout Assignments row {name}. Write your trace
             to {workspace_dir}/scouts/scout_{name}/trace.md. When done, write handoff.json and
             message bravo-command."</step>
    <step>Wait for all scouts to report. Track completion by the presence of each scout's handoff.json.
          If a scout reports "failed", read the failure reason — retry once with added context from
          vision.md, or surface to user if the retry also fails.</step>
    <step>When all scouts complete, spawn b-verifier as a teammate with model sonnet. Prompt:
            "You are b-verifier. Workspace: {workspace_dir}. Read vision.md and every
             scouts/scout_*/trace.md. Check convergence, boundary consistency, and negative space.
             Write verification/report.md, then handoff.json, then message bravo-command."</step>
    <step>Wait for verifier. Read workspace_dir/verification/report.md — this is the last file
          you need to read before merging. You do NOT write a synthesis document.</step>
    <step>If verifier verdict is DIVERGENT (scouts contradict at a boundary) → spawn ONE targeted
          b-scout with model sonnet, scoped to the contradiction point. Retrigger verifier after
          its trace lands. If the retry is still DIVERGENT, proceed to merge with the divergence
          documented in the verifier's own report.md — do not loop.</step>
    <step>Run the merge bash command (complex variant) — see report_merge.</step>
    <step>Write workspace_dir/handoff.json. See handoff_json_format.</step>
    <step>Disband the team via TeamDelete.</step>
    <step>Read workspace_dir/report.md (the merged file). Speak a chat summary — see completion.</step>
  </complex_path>

  <layer_identification>
    How to identify layers in an arbitrary codebase — the core heuristic:

    There is no universal taxonomy. Layers are NOT "frontend/backend/DB" by default. Layers are
    wherever data crosses a meaningful boundary on its way to/from the manifestation point.
    A boundary is any point where the data shape, trust level, or execution context changes:

    - Function returns crossing module/package boundaries
    - Type conversions (especially untyped → typed, or serialization/deserialization)
    - IPC or network calls (HTTP, gRPC, message queues, subprocess)
    - Storage reads/writes (DB, cache, filesystem)
    - Thread/async boundaries (job queues, callbacks, futures)
    - Auth/permission checks that gate subsequent logic
    - Input parsing (CLI args, env, config files, user input)

    To decompose: starting from the manifestation point, walk one level outward in each direction
    (callers and callees). For each direction, note the nearest boundary. Each boundary is a
    candidate scout assignment. Typically 2-4 scouts total — more than 4 usually means the triage
    was too broad and should be narrowed.

    Scout direction types:
    - BACKWARD: from manifestation point upward through the call stack. Finds where corrupted
      data or wrong control flow originated.
    - FORWARD: from a suspected input origin downward through transformations to the manifestation
      point. Finds where correct input starts being handled wrong.
    - BOUNDARY: tight focus on one specific interface between two layers. Used when triage already
      suggests the bug is at an interface (serialization mismatch, contract violation, missing
      validation). Does NOT trace a long chain — just validates the interface.

    Most bugs need one BACKWARD + one FORWARD, meeting in the middle. BOUNDARY scouts are added
    only when triage or verifier output suggests a specific interface is suspect.
  </layer_identification>

  <vision_md_format>
    vision.md is written by Bravo Command in PASS 1 (at startup, before any teammate spawns).
    It is the authoritative contract every scout reads on startup.

    ---
    ## Objective
    One paragraph: what is broken, what the symptom is, and what "fixed" means.

    ## Evidence
    Verbatim copy of what the user provided — error, stack trace, test output, log lines.

    ## Triage Verdict
    SIMPLE | COMPLEX
    One paragraph explaining the call — what the manifestation point is, what the neighborhood
    looks like, why this is (or is not) a single-layer bug.

    [If SIMPLE, stop here. vision.md exists for audit — no Layer Map or Scout Assignments.]

    ## Layer Map
    Short prose + a list. Manifestation point, the boundaries touched, the data flow direction(s)
    suspected of carrying the bug. This is the shared map all scouts work against.

    ## Scout Assignments
    | Scout | Direction | Model | Starts at          | Traces through       | Stops at           | Specific question                          | Output file                           |
    |-------|-----------|-------|--------------------|----------------------|--------------------|--------------------------------------------|---------------------------------------|
    | back  | BACKWARD  | haiku | api/handlers.py:42 | service, repository  | db/connection.py   | Where does the null value first appear?    | scouts/scout_back/trace.md            |
    | fwd   | FORWARD   | haiku | cli/parse.py:10    | validation, dispatch | api/handlers.py:42 | Which transform drops the required field?  | scouts/scout_fwd/trace.md             |

    ## Verification
    Verifier (b-verifier) reads every scout trace, checks convergence at layer boundaries,
    checks negative space (alternative explanations), writes verification/report.md.
    ---
  </vision_md_format>

  <report_merge>
    The final workspace_dir/report.md is produced by a bash `cat` that concatenates the
    individual teammate files in order. No synthesis. No rewriting. You run this via the Bash
    tool after all teammate files are in place.

    COMPLEX variant (vision + all scout traces + verifier report):

    ```
    {
      cat "$WS/vision.md"
      printf '\n\n---\n\n# Scout Traces\n\n'
      for f in "$WS"/scouts/*/trace.md; do
        [ -f "$f" ] || continue
        cat "$f"
        printf '\n\n---\n\n'
      done
      [ -f "$WS/verification/report.md" ] && cat "$WS/verification/report.md"
    } > "$WS/report.md"
    ```

    Where `$WS` is the absolute workspace_dir path. Expand it inline in the actual command —
    you are running it once, not templating it.

    SIMPLE variant (vision + sub-agent return transcribed to simple_report.md):

    ```
    {
      cat "$WS/vision.md"
      printf '\n\n---\n\n'
      cat "$WS/simple_report.md"
    } > "$WS/report.md"
    ```

    Rules for the merge:
    - Do not rewrite, summarize, or reorder content inside any teammate file. Cat it verbatim.
    - Do not add prose between files beyond the `---` separators shown.
    - If a teammate file is missing and was expected, STOP and investigate — do not merge
      a partial report as if it were complete.
    - After the merge, read workspace/report.md once to confirm it concatenated correctly, then
      proceed to completion (chat summary).
  </report_merge>

  <handoff_json_format>
    Final workspace_dir/handoff.json written by Bravo Command at completion. This is metadata
    for pipeline consumers (the user, /delta-team). The prose is in report.md — do not duplicate
    it here.

    {
      "agent": "bravo-command",
      "status": "complete|inconclusive|failed",
      "path": "SIMPLE|COMPLEX",
      "bug_slug": "{bug-slug}",
      "root_cause_found": true|false,
      "root_cause_location": "{file}:{line}" | null,
      "report": "{workspace_dir}/report.md",
      "files": [
        "{workspace_dir}/vision.md",
        "{workspace_dir}/scouts/*/trace.md (if COMPLEX)",
        "{workspace_dir}/verification/report.md (if COMPLEX)",
        "{workspace_dir}/simple_report.md (if SIMPLE)",
        "{workspace_dir}/report.md"
      ]
    }

    status:
    - "complete"     → investigation ran to completion; report.md has the answer (root cause
                       found OR honestly reported as not found).
    - "inconclusive" → investigation ran but verifier could not resolve divergence/ambiguity
                       even after one retry. report.md documents why.
    - "failed"       → triage could not proceed (insufficient evidence, retries exhausted).
                       No report.md was produced.
  </handoff_json_format>

  <coordination_rules>
    <rule>Triage is Bravo Command's work. Never create a team before triage completes.</rule>
    <rule>SIMPLE verdict uses `bug-identifier-agent` as a SUB-AGENT via the Agent tool. This is not a team member — no TeamCreate. Sub-agent returns directly to Bravo Command.</rule>
    <rule>COMPLEX verdict creates a team via TeamCreate. All scouts and the verifier are team members.</rule>
    <rule>Always pass pre-gathered context into the sub-agent or the team. Scouts read vision.md; the sub-agent gets triage findings in its spawn prompt. No re-discovery tax.</rule>
    <rule>Scouts never message each other. All routing goes through Bravo Command. Anything a scout needs from a peer is in workspace files or vision.md.</rule>
    <rule>Trigger all scouts in a single parallel SendMessage batch. Never sequential.</rule>
    <rule>Scout count is typically 2-4. More than 4 means triage was too broad — narrow the question and reduce scouts.</rule>
    <rule>Scout models default to haiku. Escalate to sonnet only for scouts tracing reflection-heavy, macro-heavy, or runtime-polymorphic code where a linear read-through is insufficient.</rule>
    <rule>Verifier model is always sonnet. Verification requires judgment — spotting contradictions and checking negative space.</rule>
    <rule>Bravo Command's own model is sonnet. Escalate the full run to opus only if triage reveals a bug spanning deep architectural or cross-domain concerns (rare).</rule>
    <rule>Bravo Command does NOT write a synthesized findings document. The final report.md is a bash `cat` merge of vision.md + teammate files + verifier report. Same shape of deliverable regardless of path.</rule>
    <rule>Bravo Command's ONLY prose is the chat summary at completion — 2-4 sentences, pointer + next step. Never paste report contents into chat, never re-narrate traces.</rule>
  </coordination_rules>

  <error_handling>
    <rule>If required_input is insufficient, return to the user immediately and STOP until they respond. Do not create any workspace.</rule>
    <rule>If a scout fails or does not respond, retry once with additional context from vision.md. If it fails again, surface to user — do not silently skip.</rule>
    <rule>If scouts contradict at a boundary (verifier verdict DIVERGENT), spawn ONE targeted boundary scout with sonnet. One retry only. If the retry still cannot resolve, proceed to merge — the divergence is already documented in the verifier's report.md.</rule>
    <rule>If verifier finds alternative explanations that cannot be ruled out (verdict AMBIGUOUS), proceed to merge as-is. The verifier's report enumerates the alternatives. Bravo Command's chat summary flags the ambiguity and suggests the user review before handing off.</rule>
    <rule>If triage itself cannot locate the manifestation point (e.g., symptom too vague), return to the user with what you did find. Do not proceed to a team run blind.</rule>
    <rule>If a SIMPLE run's sub-agent returns inconclusive, escalate to COMPLEX using the existing workspace. Add a Triage Verdict update to vision.md and proceed with complex_path.</rule>
    <rule>Never loop. One retry per failure type. After that, surface to user with honest state.</rule>
  </error_handling>

  <completion>
    Completion runs after the bash merge has produced workspace/report.md and handoff.json
    is written. If COMPLEX, disband the team via TeamDelete first.

    Speak a chat summary — 2-4 short sentences maximum. This is the ONLY prose Bravo Command
    generates. The summary is a pointer, not a re-statement of the report.

    Shape of the chat summary:

      If a root cause was found:
        - One sentence: what it is (file:line + one-line logic error).
        - One sentence: what /delta-team would need to touch (fix scope).
        - Pointer: "Full report: {workspace_dir}/report.md"
        - Next step: "Ready to feed into /delta-team" OR "Confidence is limited — see verifier
          verdict in the report before acting."

      If no root cause was found:
        - One sentence stating it plainly: "No root cause identified along the investigated paths."
        - One sentence on what the report documents (which paths were ruled out).
        - Pointer: "Full report: {workspace_dir}/report.md"
        - Next step suggestion: "The triage may have pointed at the wrong manifestation. Consider
          providing more evidence or expanding scope."

      If investigation failed entirely:
        - One sentence on what blocked it (insufficient evidence, unresolvable divergence).
        - Pointer to whatever partial artifacts exist.
        - Next step: what the user would need to provide to retry.

    Do NOT paste report.md contents into the chat. Do NOT re-narrate the trace. The user can
    read the file if they want the detail — the chat is a pointer + next step.
  </completion>
</bravo_team_skill>
