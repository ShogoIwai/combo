#!/bin/bash
# Expose combo/skills/ to Claude Code and/or Codex as PERSONAL (user-global) skills.
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
# Harness-specific skills also live under combo/skills and are told apart by
# prefix: combo-claude-* is linked into ~/.claude/skills only, combo-codex-* into
# ~/.agents/skills only, and every other combo-* into both. A dir not starting
# with combo- is not linked (one warning per dir).
#
# A link is "ours" when it points at a direct child of this combo/skills. That is
# judged on the link's own target with only its parent dir resolved, so a link
# made through another spelling of the launch root (e.g. a symlinked ~/rep) is
# ours, and so is a link to a skill dir that is itself a symlink to elsewhere.
# Ours are pruned when the target is gone, lacks the combo- prefix, or belongs to
# the other harness (made by hand or by an older version of this script). A
# same-named link that is not ours is never replaced — it is reported and skipped.
#
# Usage:  <launch-root>/combo/link_skills.sh
set -eu
SRC=$(cd "$(dirname "$0")/skills" && pwd -P)   # <launch-root>/combo/skills (resolved)

# wants HARNESS NAME -> 0 if skill NAME belongs in HARNESS (claude|codex)
wants() {
  case "$2" in
    combo-claude-*) [ "$1" = claude ] ;;
    combo-codex-*)  [ "$1" = codex ] ;;
    combo-*)        return 0 ;;
    *)              return 1 ;;
  esac
}

# owner LINK -> prints the link target with its parent dir resolved, or "/" when
# it cannot name a skill dir directly under combo/skills. Trailing "/" and "/."
# are dropped first; a target that is then empty or ends in "." / ".." names a
# dir, not a skill entry, so it is never ours.
owner() {
  raw=$(readlink "$1")
  case "$raw" in /*) ;; *) raw="$(dirname "$1")/$raw" ;; esac
  while :; do
    case "$raw" in
      */.) raw=${raw%/.} ;;
      */) raw=${raw%/} ;;
      *) break ;;
    esac
  done
  case "$raw" in
    ""|.|..|*/.|*/..) echo / ;;
    *) echo "$(readlink -m "$(dirname "$raw")")/$(basename "$raw")" ;;
  esac
}

for skill in "$SRC"/*/; do
  [ -d "$skill" ] || continue
  case "$(basename "$skill")" in
    combo-*) ;;
    *) echo "warning: $SRC/$(basename "$skill") does not start with combo- — not linked" >&2 ;;
  esac
done

for pair in "claude:$HOME/.claude/skills" "codex:$HOME/.agents/skills"; do
  HARNESS=${pair%%:*}
  DEST=${pair#*:}
  mkdir -p "$DEST"
  # Prune our own links that are dangling, unprefixed, or for the other harness.
  for link in "$DEST"/*; do
    [ -L "$link" ] || continue
    target=$(owner "$link")
    [ "$(dirname "$target")" = "$SRC" ] || continue
    if [ ! -d "$target" ] || ! wants "$HARNESS" "$(basename "$target")"; then
      rm -f "$link"; echo "pruned: $link"
    fi
  done
  for skill in "$SRC"/*/; do
    [ -d "$skill" ] || continue
    name=$(basename "$skill")
    wants "$HARNESS" "$name" || continue
    link="$DEST/$name"
    if [ -e "$link" ] && [ ! -L "$link" ]; then
      echo "error: $link exists and is not a symlink — skipping" >&2
      continue
    fi
    if [ -L "$link" ] && [ "$(dirname "$(owner "$link")")" != "$SRC" ]; then
      echo "error: $link is someone else's link ($(readlink "$link")) — skipping" >&2
      continue
    fi
    ln -sfn "$SRC/$name" "$link"
    echo "linked: $link -> $SRC/$name"
  done
done
