# Team Workspace Convention

Every native team run creates a workspace directory at:

  {project_root}/.team_workspace/{YYYYMMDD-HHMM-task-slug}/

Create this path before spawning any agent. Pass it to every teammate in their spawn prompt.

Structure within a workspace:
  vision.md              — pipeline contract + Execution Schedule (written by team lead)
  ticket.json            — written by vector
  research_{topic}.md    — written by each recon agent
  wave_{N}/
    handoff.json         — written by raptor for wave N
  review.md              — written by apex
  handoff.json           — final handoff written by delta-command at completion

No agent may create files outside the workspace directory except when editing actual production code files in the project.
