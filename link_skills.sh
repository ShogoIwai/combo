#!/bin/bash
# Register the combo/skills/ dir into both harnesses' search paths.
# Symlinks combo/skills/ into <launch-root>'s Claude Code (.claude/skills)
# and sub-agent (.agents/skills) search paths.
#
# Usage:  <launch-root>/combo/link_skills.sh
#         (run once per cloned environment; no need to re-run when adding a skill)
# Adding a skill = just drop <skill-name>/SKILL.md under combo/skills/
#                  (the skills dir itself is the link, so no per-skill link needed).
set -eu
REP=$(cd "$(dirname "$0")/.." && pwd)   # <launch-root>
mkdir -p "$REP/.claude" "$REP/.agents"
ln -sfn "$REP/combo/skills" "$REP/.claude/skills"
ln -sfn "$REP/combo/skills" "$REP/.agents/skills"
echo "linked: $REP/.claude/skills, $REP/.agents/skills -> $REP/combo/skills"
