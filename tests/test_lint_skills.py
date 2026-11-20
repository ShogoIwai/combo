#!/usr/bin/env python3
"""Regression tests for combo/tools/lint_skills.py (run without arguments)."""

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import lint_skills as ls  # noqa: E402

GOOD = "Use when converting X to Y. Triggers — 「X を Y にして」「x to y」."


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def skill(self, front, name="combo-x"):
        d = os.path.join(self.tmp.name, name)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: %s\n%s---\n# body\n" % (name, front))
        return d

    def run_on(self, front, name="combo-x"):
        d = self.skill(front, name)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = ls.main([d])
        return rc, out.getvalue()

    def assert_fail(self, front, tag, name="combo-x"):
        rc, out = self.run_on(front, name)
        self.assertEqual(rc, 1, out)
        self.assertIn(tag, out)

    def test_good_passes(self):
        rc, out = self.run_on("description: %s\nallowed-tools: Read\n" % GOOD)
        self.assertEqual(rc, 0, out)

    def test_repo_skills_pass(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = ls.main([])
        self.assertEqual(rc, 0, out.getvalue())

    def test_name_mismatch_and_prefix(self):
        d = self.skill("description: %s\n" % GOOD, name="combo-y")
        with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: combo-z\ndescription: %s\n---\n" % GOOD)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(ls.main([d]), 1)
        self.assertIn("[K1]", out.getvalue())
        self.assert_fail("description: %s\n" % GOOD, "[K1]", name="x-skill")

    def test_too_long(self):
        self.assert_fail("description: %s %s\n" % (GOOD, "a" * ls.DESC_MAX), "[K2]")

    def test_multiline_and_block(self):
        self.assert_fail("description: >\n  %s\n" % GOOD, "[K2]")
        self.assert_fail("description: %s\n\n  more\n" % GOOD, "[K2]")

    def test_no_trigger(self):
        self.assert_fail("description: Use when converting X to Y.\n", "[K3]")

    def test_yaml_breakers(self):
        for desc in ('"%s' % GOOD, "- %s" % GOOD, "%s:" % GOOD,
                     "Note: %s" % GOOD, "Note:\t%s" % GOOD, "%s #tag" % GOOD):
            self.assert_fail("description: %s\n" % desc, "[K4]")

    def test_local_info(self):
        for extra in ("/home/someone/x", "/mnt/disk/x", "~/rep", "C:\\work",
                      "10.0.0.1", "someone@example.com", "server.local", "wiki.corp"):
            self.assert_fail("description: %s Uses %s.\n" % (GOOD, extra), "[K5]")

    def test_any_absolute_path_in_description(self):
        self.assert_fail("description: %s Saves to /srv/private/build.\n" % GOOD, "[K5]")
        self.assert_fail("description: %s Saves to /private\n" % GOOD, "[K5]")

    def test_duplicate_name_key(self):
        self.assert_fail("name: other-skill\ndescription: %s\n" % GOOD, "[K1]")
        self.assert_fail("\"name\": other-skill\ndescription: %s\n" % GOOD, "[K1]")

    def body_file(self, rel, text):
        d = self.skill("description: %s\n" % GOOD)
        path = os.path.join(d, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = ls.main([d])
        return rc, out.getvalue()

    def test_local_info_in_body_files(self):
        for rel, text in (("SKILL.md.extra", "see /home/alice/private\n"),
                          ("scripts/run.py", "HOST = 'build01.internal'\n"),
                          ("reference/x.md", "mail alice@company.example\n"),
                          ("templates/a.txt", "url http://10.1.2.3/\n"),
                          ("scripts/win.py", "P = r'C:\\Users\\alice\\secret'\n"),
                          ("scripts/mac.sh", "cd /Volumes/private/project\n")):
            rc, out = self.body_file(rel, text)
            self.assertEqual(rc, 1, rel)
            self.assertIn("[K6]", out)

    def test_generic_paths_in_body_files_pass(self):
        rc, out = self.body_file("scripts/run.py",
                                 "#!/usr/bin/env python3\nURL = 'http://127.0.0.1:8188'\n"
                                 "TMP = '/tmp/x'\nLINK = '~/.claude/skills'\n"
                                 "CORPUS = 'corpus.local.tsv'\n")
        self.assertEqual(rc, 0, out)
        with open(os.path.join(self.tmp.name, "combo-x", "blob.bin"), "wb") as fh:
            fh.write(b"\xff\xfe/home/alice")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(ls.main([os.path.join(self.tmp.name, "combo-x")]), 0)

    def test_relative_paths_are_not_local_info(self):
        rc, out = self.run_on("description: %s Writes work/out.md and ../x/home/y.\n" % GOOD)
        self.assertEqual(rc, 0, out)


if __name__ == "__main__":
    unittest.main()
