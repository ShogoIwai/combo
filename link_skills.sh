#!/bin/bash
# Expose combo/skills/ to both harnesses as PERSONAL (user-global) skills.
#
# Why user-global, not launch-root project scope:
#   <launch-root>/.claude/skills (and .agents/skills) is a single per-harness
#   search slot, so whoever links it last wins. That slot is reserved for the
#   per-workspace skill set, which is switched independently for each launch
#   root. combo/ skills are meant to be used one level above that — generic,
#   always-available procedures regardless of which workspace is linked there.
#   So they live in the user-global path (~/.claude/skills, ~/.agents/skills),
#   where they never collide with whatever owns the launch-root slot.
#
# Because the user-global skills dir also holds other personal skills, the link
# is made PER SKILL (not at the skills-dir level). Re-run this whenever a skill
# is ADDED, REMOVED, or RENAMED under combo/skills/: each run first prunes our
# own stale links (those pointing back into this combo/skills) and then refreshes
# the live ones. Only links we own are touched; other personal skills are left.
#
# Usage:  <launch-root>/combo/link_skills.sh
set -eu
SRC=$(cd "$(dirname "$0")/skills" && pwd)   # <launch-root>/combo/skills
for DEST in "$HOME/.claude/skills" "$HOME/.agents/skills"; do
  mkdir -p "$DEST"
  # Prune stale links that point into this combo/skills but no longer resolve.
  for link in "$DEST"/*; do
    [ -L "$link" ] || continue
    target=$(readlink "$link")
    case "$target" in
      "$SRC"/*) [ -d "$target" ] || { rm -f "$link"; echo "pruned: $link"; } ;;
    esac
  done
  for skill in "$SRC"/*/; do
    [ -d "$skill" ] || continue
    link="$DEST/$(basename "$skill")"
    if [ -e "$link" ] && [ ! -L "$link" ]; then
      echo "error: $link exists and is not a symlink — skipping" >&2
      continue
    fi
    ln -sfn "${skill%/}" "$link"
    echo "linked: $link -> ${skill%/}"
  done
done
