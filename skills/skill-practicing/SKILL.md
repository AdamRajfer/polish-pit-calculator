---
name: skill-practicing
description: Create or update agent instructions and local skills for this repository. Use when asked to modify AGENTS.md, add or refine skills under skills/, audit README/CHANGELOG/SECURITY/CONTRIBUTING/pyproject.toml for stale guidance, or perform no-secrets documentation hygiene checks.
---

# Skill Practicing

## Overview

Keep agent guidance and skill metadata consistent, current, and safe.
Apply this workflow whenever repository instructions or skills are touched.

## Workflow

1. Confirm scope and affected files.
2. Audit guidance files:
- Check `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`, and `pyproject.toml` against current behavior and commands.
3. Audit for leaks:
- Check for hardcoded tokens, keys, private URLs, and personal secrets in docs/config files.
4. Update agent instructions:
- Keep `AGENTS.md` aligned with available local skills and trigger rules.
5. Update skill metadata:
- Keep `SKILL.md` frontmatter descriptions trigger-oriented and specific.
- Keep `agents/openai.yaml` short and UI-focused.
6. Validate:
- Run `quick_validate.py` for modified skills.
- Run repository quality checks before finishing.

## Output Standard

Provide:
- What was audited.
- What was changed.
- Why guidance changed.
- What checks were run and their result.

## References

Use `references/checklist.md` for command-level audit and validation steps.
