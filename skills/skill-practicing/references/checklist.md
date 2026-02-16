# Skill Practicing Checklist

Use this checklist when updating agent instructions, skill metadata, and docs/config hygiene.

## Scope

1. Identify changed instruction files:
- `AGENTS.md`
- `skills/*/SKILL.md`
- `skills/*/agents/openai.yaml`
2. Identify changed guidance/config files:
- `README.md`
- `CHANGELOG.md`
- `CONTRIBUTING.md`
- `SECURITY.md`
- `pyproject.toml`

## Hygiene Audit

1. Verify commands in docs match current tooling (`uv`, `pytest`, `pre-commit`).
2. Verify release/version guidance matches project versioning strategy.
3. Verify security/contact guidance is clear and current.
4. Verify no stale references to removed modules, files, or entrypoints.
5. For CLI/menu flow changes, verify README flow and `CHANGELOG.md` `Unreleased` notes are updated.

## Leak Audit

1. Search docs/config for:
- API tokens
- private keys
- passwords
- personal credentials
2. Keep public project metadata only (author/package metadata is allowed).
3. Ensure examples use placeholders, not real secrets.

## Skill Metadata Audit

1. Keep `SKILL.md` frontmatter `description` trigger-oriented.
2. Keep body concise and procedural.
3. Keep `agents/openai.yaml` UI text short and consistent with skill purpose.
4. Keep resource links valid (no missing referenced files).

## Validation

1. Validate each touched skill:
```bash
python3 $CODEX_HOME/skills/.system/skill-creator/scripts/quick_validate.py skills/<skill-name>
```
2. Run repository checks:
```bash
uv run pytest -q
pre-commit run --all-files
```
