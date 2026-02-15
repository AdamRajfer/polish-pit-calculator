# Agent Instructions

## Skills

Skills are instruction bundles stored as `SKILL.md` files.

### Local Skills

- `skill-practicing`
  - Purpose: maintain `AGENTS.md`, local skill metadata, and docs/config hygiene.
  - File: `skills/skill-practicing/SKILL.md`
- `pit-tax-reporter`
  - Purpose: implement/refactor PIT reporter logic and validate strict full-output tests.
  - File: `skills/pit-tax-reporter/SKILL.md`

## Skill Usage Rules

1. Trigger a skill when:
- The user names it explicitly.
- The task clearly matches its description.
2. If multiple skills match:
- Use the minimal set that fully covers the task.
- State the order in one line.
3. Load progressively:
- Read `SKILL.md` first.
- Read only referenced files needed for the current request.
4. Keep context lean:
- Do not bulk-load all references.
- Avoid deep reference chains unless blocked.
5. Fallback:
- If a skill is missing or unclear, say so briefly and continue with best effort.
