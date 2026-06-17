#!/bin/bash
# combo/skills/link.sh — expose the whole combo/skills dir to both harnesses.
# 2 links per launch root; re-run only on a fresh environment, not per skill.
# Adding a skill = drop the dir under combo/skills/ (the skills dir itself is the link).
set -eu
REP=$(cd "$(dirname "$0")/../.." && pwd)   # resolves to <launch root>
ln -sfn "$REP/combo/skills" "$REP/.claude/skills"
ln -sfn "$REP/combo/skills" "$REP/.agents/skills"
echo "linked: $REP/.claude/skills, $REP/.agents/skills -> $REP/combo/skills"
