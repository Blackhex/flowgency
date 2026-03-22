# Clue System Steps — Universal Template

Copy this file verbatim to `agents/shared/prompts/_clue-system-steps.md` in every project.
The only part that changes is the **Universal Boundaries** section at the bottom, which
should be customized per-project to list the specific actions agents cannot take.

---

## Clue System — Standard Steps

After completing your observation tasks, perform these steps in order:

> **Memory vs. Clues:** Your `memory.md` stores persistent knowledge — corrections Chris gave you, preferences, stable facts. Clues are ephemeral observations from a single run, meant to converge into curiosities. Don't write preferences or corrections as clues; those go in memory. Don't write one-run observations in memory; those are clues.

### 1. Check for feedback requests
Read all files in `agents/shared/curiosities/`. For any curiosity where:
- `status` is `feedback`
- Your agent name appears in `feedback_requested` but NOT in `feedback_received`

Write your feedback under the `### Agent Feedback` section in the appropriate heading.
Then add your agent name to `feedback_received` in the frontmatter.
Keep feedback to 2-4 sentences: does this proposal make sense from your domain? Risks? Opportunities?

### 2. Scan for floated clues
Read all files in `agents/shared/clues/` where `float: true` and `status: open`.
If any floated clue relates to something you've observed in your domain, link your clue to it
by adding its filename to your clue's `linked_clues` and vice versa.

### 3. Check for duplicates before writing clues
Before writing any new clue:
- Read all non-archived clue files in `agents/shared/clues/`
- If a clue describing the SAME signal already exists:
  - If from you: UPDATE the existing clue with new data (update the date and body)
  - If from another agent: LINK to it via `linked_clues` in both files
  - Do NOT create a duplicate

### 4. Write new clues (if any)
If your observation tasks surfaced something notable, write a clue file.
Filename: `agents/shared/clues/{your-agent-name}-{YYYYMMDD}-{HHMMSS}-{slug}.md`

Use this template:
```
---
agent: {your-agent-name}
date: {ISO 8601 timestamp}
category: {category}
status: open
float: false
linked_clues: []
linked_curiosity: ~
ttl_days: 7
---

{Description of the observation. Be specific: include numbers, dates, names.}
```

Set `float: true` if you think other agents might have related observations but you
can't form an actionable conclusion on your own.

### 5. Promote to curiosity (if warranted)
If you have 2+ connected clues (yours or linked from others) that converge on an
actionable issue, create a curiosity file.

**Check first:** You may have at most 3 curiosities in non-terminal status
(`investigating` or `feedback`). If you already have 3, complete or abandon one first.

Filename: `agents/shared/curiosities/{YYYY-MM-DD}-{slug}.md`

Use this template:
```
---
origin_agent: {your-agent-name}
date: {YYYY-MM-DD}
status: investigating
clues:
  - {clue-filename-1}
  - {clue-filename-2}
feedback_requested: []
feedback_received: []
ttl_days: 14
---

## Curiosity: {title}

### Evidence
{Summarize the connected clues and why they converge}

### Investigation
{Your deeper analysis}

### Proposed Action
{What you recommend doing, with specifics}

### Agent Feedback
{Leave headings for each agent you'll request feedback from}

### Recommendation
{Left blank until all feedback is collected}
```

After writing the Investigation and Proposed Action, update `status` to `feedback`
and list the agents whose input you need in `feedback_requested`.

Update the linked clues' `status` to `connected` and set their `linked_curiosity`.

### 6. Finalize proposed curiosities
If a curiosity you originated has `feedback_received` matching `feedback_requested`,
write the `### Recommendation` section synthesizing all feedback. Set `status: proposed`.

## Universal Boundaries

**Customize this section per project.** Default boundaries:

You may NOT:
- Push git commits or create PRs
- Restart or modify systemd services or containers
- Run destructive bash commands (`rm -rf`, `git reset --hard`, etc.)
- Edit files outside the project directory

**Agent-specific permissions override these defaults** — check your CLAUDE.md for what
you ARE allowed to do. For example, the builder agent can edit source code, while
read-only agents cannot.

If your curiosity requires an action beyond your permissions, propose it — do not do it yourself.
