---
name: delta-team-research
description: Add-on for /delta-team. Inserts a parallel d-recon phase before Vector. You decide how many d-recon agents based on distinct knowledge domains in the task.
user_invocable: true
---

<research_addon>
  <purpose>
    This add-on instructs you on how to run a research phase as part of a /delta-team pipeline.
    Invoke this when the task has genuine unknowns that need external lookup before a ticket can be written:
    unfamiliar APIs, library breaking changes, competitor patterns, architectural precedents, etc.
    Do NOT invoke for tasks where the codebase itself is the only source of truth.
  </purpose>

  <how_many_researchers>
    Spawn one researcher per distinct knowledge domain — not per question.
    Examples:
    - "Migrate from library A to B, update docs, check breaking changes" → 2 researchers: (1) A-to-B migration guide + breaking changes, (2) new API signatures.
    - "How does Stripe handle webhooks, and what does our current provider's API look like?" → 2 researchers.
    - "What changed in React 19?" → 1 researcher.
    Never spawn more than needed. If one researcher can cover two topics without losing depth, keep it one.
  </how_many_researchers>

  <vision_md_instructions>
    When writing vision.md with research enabled, the Agents section must specify for each d-recon agent:
    - Exact research question (scoped, not vague)
    - Output file name: workspace/research_{topic}.md
    - Who to message when done: d-vector
    - What Vector waits for: list all research_{topic}.md files

    The Vector entry must say: "Wait for [N] research files: [list them]. Do not proceed until all exist."
  </vision_md_instructions>

  <researcher_spawn_prompt>
    Each d-recon spawn prompt must include:
    - workspace_dir and vision.md path (they read it for their output filename and message target)
    - The specific research question
    - "Write findings to workspace/{filename} per vision.md. Message d-vector when done."
    Do not give d-recon agents more than one question each. Keep scope tight.
  </researcher_spawn_prompt>

  <pipeline_shape>
    With /delta-team-research, the vision.md Pipeline section looks like:

      Recon A (research_{a}.md) ↘
      Recon B (research_{b}.md)  → Vector → [delta-command: schedule] → raptor-1 ↘
      [Recon N]                  ↗                                                 → [delta-command] → [Done]
                                                                        raptor-2 ↗

    Researchers run in parallel. Ticket agent waits for all of them. After ticket approval,
    delta-command writes the Execution Schedule and executors run in parallel where their depends_on
    allows it. All executors report to delta-command.
  </pipeline_shape>
</research_addon>
