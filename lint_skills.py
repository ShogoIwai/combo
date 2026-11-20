#!/usr/bin/env python3
"""Check that every combo skill's frontmatter helps auto-selection and leaks no local info.

  python3 lint_skills.py            # check combo/skills/*/SKILL.md (exit 0 = PASS, 1 = FAIL)
  python3 lint_skills.py <dir>...   # check the given skill dirs instead

Both harnesses list every skill's `description` inside one shared character budget and
pick a skill from that list alone (the SKILL.md body is not visible before the skill
fires). A long description eats the budget and pushes other skills out of the list as
bare names, which then never get picked from context. Combo skills are also linked into
every repo and the repo is pushed to a remote, so the description must not carry
anything that only makes sense on one machine or one organisation.

Checks:
  K1 frontmatter exists; `name` equals the directory name and starts with `combo-`
  K2 `description` is one line (no block scalar, no indented continuation, even after
     a blank line), at most DESC_MAX characters
  K3 `description` contains at least one trigger phrase in 「…」
  K4 `description` survives a strict YAML parser as a plain scalar: it does not start
     with a quote or YAML indicator, does not end with `:`, and has no `:` + whitespace
     or whitespace + `#` inside
  K5 `description` has no local information: any absolute path, `~/`, Windows drive
     paths, IPv4 addresses, e-mail addresses, or private-network host names
     (*.local / *.lan / *.internal / *.corp / *.intra)
  K6 no file under the skill directory (body, references, scripts, templates; caches
     and binary files are skipped) has home / mount paths, Windows drive paths, non-loopback IPv4 addresses,
     e-mail addresses or private-network host names. Generic absolute paths (`/usr/bin`,
     `/tmp`) and `~/` are allowed in files, since procedures legitimately name them
Organisation names and personal details cannot be told apart from ordinary words by a
pattern, so they are not checked here; review them by hand (README, `description` rules).
No third-party modules (no YAML library); the frontmatter is read line by line.
"""

import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.realpath(__file__))
DESC_MAX = 400
HOST_RE = r"\b[\w-]+(?:\.[\w-]+)*\.(?:local|lan|internal|corp|intra)\b(?!\.?[\w-])"
IPV4_RE = r"\b(?!127\.|0\.0\.0\.0\b)(?:\d{1,3}\.){3}\d{1,3}\b"
MAIL_RE = r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"
FILE_PATTERNS = (
    (r"(?<![\w.~-])/(?:home|mnt|media|Volumes|Users|root|net|proj|export|srv|data)/[\w.-]",
     "home / mount path"),
    (r"\b[A-Za-z]:\\+[\w.-]", "Windows drive path"),
    (IPV4_RE, "IPv4 address"),
    (MAIL_RE, "e-mail address"),
    (HOST_RE, "private host name"),
)
SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv"}
LOCAL_PATTERNS = (
    (r"(?<![\w.~/-])/[A-Za-z_][\w.-]*", "absolute path"),
    (r"(?<![\w.-])~/", "home-relative path"),
    (r"\b[A-Za-z]:\\", "Windows drive path"),
    (IPV4_RE, "IPv4 address"),
    (MAIL_RE, "e-mail address"),
    (HOST_RE, "private host name"),
)


def frontmatter(text):
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    return m.group(1) if m else None


def check_skill(d):
    name_dir = os.path.basename(os.path.normpath(d))
    path = os.path.join(d, "SKILL.md")
    if not os.path.isfile(path):
        return ["[K1] %s: SKILL.md is missing" % name_dir]
    with open(path, encoding="utf-8") as fh:
        fm = frontmatter(fh.read())
    if fm is None:
        return ["[K1] %s: no `---` frontmatter at the top of SKILL.md" % name_dir]
    errors = []
    m = re.search(r"^name:\s*(\S+)\s*$", fm, re.M)
    name = m.group(1) if m else ""
    if len(re.findall(r"^[\"']?name[\"']?\s*:", fm, re.M)) > 1:
        errors.append("[K1] %s: several `name:` lines in the frontmatter" % name_dir)
    if name != name_dir:
        errors.append("[K1] %s: frontmatter name is %r (must equal the directory name)"
                      % (name_dir, name))
    if not name_dir.startswith("combo-"):
        errors.append("[K1] %s: directory name must start with `combo-`" % name_dir)

    lines = fm.split("\n")
    idx = [i for i, l in enumerate(lines) if re.match(r"description:", l)]
    if len(idx) != 1:
        return errors + ["[K2] %s: %d `description:` lines (write exactly one)"
                         % (name_dir, len(idx))]
    i = idx[0]
    desc = lines[i][len("description:"):].strip()
    rest = [l for l in lines[i + 1:] if l.strip()]
    if rest and rest[0][:1].isspace():
        errors.append("[K2] %s: description spans several lines (write it on one line)"
                      % name_dir)
    if not desc or desc in ("|", ">", "|-", ">-", "|+", ">+"):
        return errors + ["[K2] %s: description is empty (no block scalar; one line)"
                         % name_dir]
    if len(desc) > DESC_MAX:
        errors.append("[K2] %s: description is %d chars (max %d; move steps and details "
                      "to the body)" % (name_dir, len(desc), DESC_MAX))
    if not re.search(r"「[^」]+」", desc):
        errors.append("[K3] %s: description has no trigger phrase in 「…」" % name_dir)
    if desc[0] in "\"'`[]{}&*!|>%@,#" or re.match(r"[-?:](\s|$)", desc):
        errors.append("[K4] %s: description starts with %r (no quotes or YAML indicators; "
                      "plain one-line text)" % (name_dir, desc[0]))
    if desc.endswith(":"):
        errors.append("[K4] %s: description ends with ':'" % name_dir)
    for pat, shown in ((r":\s", ": "), (r"\s#", " #")):
        if re.search(pat, desc):
            errors.append("[K4] %s: description contains %r (breaks strict YAML; use a "
                          "full-width colon or an em dash)" % (name_dir, shown))
    for pat, label in LOCAL_PATTERNS:
        hit = re.search(pat, desc)
        if hit:
            errors.append("[K5] %s: description contains a %s (%r)"
                          % (name_dir, label, hit.group(0)))
    return errors + file_errors(d, name_dir)


def file_errors(d, name_dir):
    """K6: scan every text file under the skill directory for local information."""
    errors = []
    for base, dirs, files in os.walk(d):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
        for fn in sorted(files):
            path = os.path.join(base, fn)
            try:
                with open(path, encoding="utf-8") as fh:
                    lines = fh.read().split("\n")
            except (UnicodeDecodeError, OSError):
                continue  # binary or unreadable
            rel = os.path.relpath(path, d)
            for no, line in enumerate(lines, 1):
                for pat, label in FILE_PATTERNS:
                    hit = re.search(pat, line)
                    if hit:
                        errors.append("[K6] %s: %s:%d contains a %s (%r)"
                                      % (name_dir, rel, no, label, hit.group(0)))
    return errors


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if any(a.startswith("-") for a in argv):
        print(__doc__, file=sys.stderr)
        return 2
    dirs = argv or sorted(os.path.dirname(p)
                          for p in glob.glob(os.path.join(ROOT, "skills", "*", "SKILL.md")))
    errors = [e for d in dirs for e in check_skill(d)]
    for e in errors:
        print("FAIL " + e)
    print("skills %d — %s" % (len(dirs), "FAIL %d" % len(errors) if errors else "PASS"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
