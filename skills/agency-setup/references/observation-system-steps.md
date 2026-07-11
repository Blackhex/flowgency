# Observation System Steps — Universal Template

Copy this file verbatim to `agents/shared/prompts/_observation-system-steps.md` in every project.
The only part that changes is the **Universal Boundaries** section at the bottom, which
should be customized per-project to list the specific actions agents cannot take.

---

## Observation System — Standard Steps

After completing your observation tasks, perform these steps in order:

> **Memory vs. Observations:** Your `memory.md` stores persistent knowledge — user corrections, preferences, and stable facts. Observations are ephemeral findings from a single run, meant to converge into proposals. Don't write preferences or corrections as observations; those go in memory. Don't write one-run observations in memory; those are observations.

### 1. Scan for floated observations
Read all files in `agents/shared/observations/` where `float: true` and `status: open`.
If any floated observation relates to something you've observed in your domain, link your observation to it
by adding its filename to your observation's `linked_observations` and vice versa.

### 2. Check for duplicates before writing observations
Before writing any new observation:
- Read all non-archived observation files in `agents/shared/observations/`
- If an observation describing the SAME signal already exists:
  - If from you: UPDATE the existing observation with new data (update the date and body)
  - If from another agent: LINK to it via `linked_observations` in both files
  - Do NOT create a duplicate

### 3. Write new observations (if any)
If your observation tasks surfaced something notable, write an observation file.
Filename: `agents/shared/observations/{your-agent-name}-{YYYYMMDD}-{HHMMSS}-{slug}.md`

Use this template:
```
---
agent: {your-agent-name}
date: {ISO 8601 timestamp}
category: {category}
status: open
float: false
linked_observations: []
linked_proposal: ~
ttl_days: 7
---

{Description of the observation. Be specific: include numbers, dates, names.}
```

Set `float: true` if you think other agents might have related observations but you
can't form an actionable conclusion on your own.

### 4. Promote to proposal (if warranted)
If you have 2+ connected observations (yours or linked from others) that converge on an
actionable issue, create a proposal.

**Check first:** You may have at most 3 proposals in non-terminal status
(`investigating` or `proposed`). If you already have 3, complete or abandon one first.

Creating a proposal is a multi-step process that happens within a single run:

#### 4a. Draft the proposal
Filename: `agents/shared/proposals/{YYYY-MM-DD}-{slug}.md`

Write the draft with `status: investigating` using this template:
```
---
origin_agent: {your-agent-name}
date: {YYYY-MM-DD}
status: investigating
observations:
  - {observation-filename-1}
  - {observation-filename-2}
feedback_requested: []
feedback_received: []
ttl_days: 14
questions:
  - id: {short_id}
    type: boolean
    prompt: "{Question for the user to decide on}"
---

## Proposal: {title}

### Evidence
{Summarize the connected observations and why they converge}

### Investigation
{Your deeper analysis}

### Proposed Action
{What you recommend doing, with specifics}

### Agent Feedback
{Populated in step 4b}

### Recommendation
{Written in step 4c after collecting feedback}
```

Update the linked observations' `status` to `connected` and set their `linked_proposal`.

#### 4b. Collect feedback from other agents
Determine which agents have relevant domain expertise for this proposal. List them in
`feedback_requested`. Then, for each agent:

1. Read the agent's identity file (`agents/{agent-name}/CLAUDE.md` for Claude/Linux or
  `agents/{agent-name}/AGENTS.md` for Copilot/Windows) to understand their role
2. Spawn the agent as a **subagent** with a prompt like:
   > "Review the proposal at `agents/shared/proposals/{filename}`. From your domain perspective,
   > provide 2-4 sentences: Does this make sense? What risks or opportunities do you see?
   > Write your feedback directly — do not modify the proposal file."
3. Take the subagent's response and write it under `### Agent Feedback` with a heading for each agent:
   ```
   ### Agent Feedback

   #### {agent-name}
   {Their feedback response}

   #### {other-agent-name}
   {Their feedback response}
   ```
4. Add each agent's name to `feedback_received` in the frontmatter

**All feedback must be collected in this run.** By the time a proposal reaches the inbox,
every requested agent's feedback should be present. Do not leave proposals in an intermediate
state waiting for feedback from future dispatch runs.

#### 4c. Finalize the proposal
With all feedback collected:
1. Write the `### Recommendation` section synthesizing the evidence, your analysis, and agent feedback
2. Set `status: proposed`
3. Add `questions` to the frontmatter — these are the decisions the user needs to make
   (use `boolean` for approve/defer/reject, `choice` for multi-option, `free-response` for open-ended)

The proposal is now ready for the user's inbox.

## Universal Boundaries

**Customize this section per project.** Default boundaries:

You may NOT:
- Push git commits or create PRs
- Restart or modify systemd services, Windows services, Scheduled Tasks, or containers
- Run destructive commands (`rm -rf`, `Remove-Item -Recurse -Force`,
  `git reset --hard`, etc.)
- Edit files outside the project directory

**Agent-specific permissions override these defaults** — check your selected identity
file (`CLAUDE.md` or `AGENTS.md`) for what you ARE allowed to do. For example, the
builder agent can edit source code, while read-only agents cannot.

If your proposal requires an action beyond your permissions, propose it — do not do it yourself.
