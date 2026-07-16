# Observation Pipeline Skill Guidance

Use this reference when writing an observation-oriented Agent Skill in the global Agent Library. The Agent Skill is selected by an instance routine; do not copy this reference into a project prompt directory.

## Outcome

The routine inspects its assigned domain, avoids duplicate observations, links related signals, and proposes action only when evidence converges. Agency supplies the configured group workspace, projected blueprint, selected skill, runtime policy, and semantic memory snapshot.

## Recommended steps

1. Read current non-terminal observations in the group's pipeline records.
2. Compare each new signal with existing observations. Update or link an existing record instead of creating a duplicate.
3. Write a specific observation with the configured instance name, ISO-8601 date, category, status, links, and TTL.
4. Float a signal only when another domain may contribute evidence.
5. Create a proposal only when connected observations support a concrete decision. Include explicit questions and a configured writable `execution_agent`.
6. Keep durable preferences and stable facts in the memory snapshot selected by the routine. Keep one-run findings in observations.

## Boundaries

Blueprint instructions define reusable role behavior. Config defines identity, integration, capabilities, runtime policy, routines, and semantic memory selectors. The skill must not discover another instance from a native file, widen runtime authority, alter Agency configuration, or bypass durable job submission.