import base64
import os
import stat
import unittest

from helpers import EngineCase


class Read(EngineCase):
    def test_numbered_lines_and_paging(self):
        p = self.write("big.txt", "".join("line %d\n" % i for i in range(1, 51)))
        out = self.ok("fs_read", path=p, limit=10)
        self.assertIn("     1\tline 1", out)
        self.assertIn("    10\tline 10", out)
        self.assertNotIn("line 11", out)
        self.assertIn("more: offset=11", out)
        out = self.ok("fs_read", path=p, offset=11, limit=5)
        self.assertIn("    11\tline 11", out)

    def test_relative_and_tilde_paths(self):
        home = os.environ.get("HOME", "/root")
        name = "eai-test-%d.txt" % os.getpid()
        with open(os.path.join(home, name), "w") as f:
            f.write("hi\n")
        self.addCleanup(os.unlink, os.path.join(home, name))
        self.assertIn("hi", self.ok("fs_read", path=name))
        self.assertIn("hi", self.ok("fs_read", path="~/" + name))

    def test_binary_is_refused_but_base64_works(self):
        p = self.path("b.bin")
        with open(p, "wb") as f:
            f.write(b"\x00\x01\x02binary")
        self.assertIn("binary", self.err("fs_read", path=p))
        out = self.ok("fs_read", path=p, encoding="base64")
        self.assertEqual(base64.b64decode(out.split("\n", 1)[1]), b"\x00\x01\x02binary")

    def test_directory_is_listed(self):
        self.write("d/a.txt")
        self.assertIn("a.txt", self.ok("fs_read", path=self.path("d")))

    def test_missing_file_and_empty_file(self):
        self.assertIn("does not exist", self.err("fs_read", path=self.path("nope")))
        p = self.write("empty.txt", "")
        self.assertIn("empty", self.ok("fs_read", path=p))

    def test_long_line_is_cut(self):
        p = self.write("long.txt", "x" * 5000 + "\n")
        self.assertIn("line cut", self.ok("fs_read", path=p))

    def test_invalid_utf8_does_not_crash(self):
        p = self.path("l1.txt")
        with open(p, "wb") as f:
            f.write(b"caf\xe9\n")
        self.assertIn("caf", self.ok("fs_read", path=p))


class Write(EngineCase):
    def test_create_with_parents_and_mode(self):
        p = self.path("a", "b", "c.txt")
        self.assertIn("Created", self.ok("fs_write", path=p, content="hello", mode="0600"))
        self.assertEqual(self.read(p), "hello")
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)

    def test_overwrite_keeps_permissions(self):
        p = self.write("x.sh", "old")
        os.chmod(p, 0o755)
        self.assertIn("Overwrote", self.ok("fs_write", path=p, content="new"))
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o755)
        self.assertEqual(self.read(p), "new")

    def test_append_and_backup(self):
        p = self.write("log.txt", "one\n")
        self.ok("fs_write", path=p, content="two\n", append=True)
        self.assertEqual(self.read(p), "one\ntwo\n")
        out = self.ok("fs_write", path=p, content="fresh", backup=True)
        self.assertIn(".bak.", out)
        baks = [n for n in os.listdir(self.work) if ".bak." in n]
        self.assertEqual(len(baks), 1)
        self.assertEqual(self.read(self.path(baks[0])), "one\ntwo\n")

    def test_base64_content(self):
        p = self.path("bin")
        self.ok("fs_write", path=p, content=base64.b64encode(b"\x00\xffabc").decode(), encoding="base64")
        self.assertEqual(self.read(p, "rb"), b"\x00\xffabc")

    def test_writes_through_a_symlink(self):
        real = self.write("real.txt", "a")
        link = self.path("link.txt")
        os.symlink(real, link)
        self.ok("fs_write", path=link, content="b")
        self.assertTrue(os.path.islink(link))
        self.assertEqual(self.read(real), "b")

    def test_no_temp_files_left_and_directory_refused(self):
        self.ok("fs_write", path=self.path("t.txt"), content="x")
        self.assertEqual(os.listdir(self.work), ["t.txt"])
        self.assertIn("is a directory", self.err("fs_write", path=self.work, content="x"))

    def test_bad_mode(self):
        self.assertIn("octal", self.err("fs_write", path=self.path("m"), content="x", mode="rwx"))

    def test_create_dirs_false(self):
        self.assertIn("does not exist", self.err("fs_write", path=self.path("zz", "f"), content="x", create_dirs=False))


class Edit(EngineCase):
    def test_replace_unique(self):
        p = self.write("c.conf", "port=80\nhost=a\n")
        out = self.ok("fs_edit", path=p, old_string="port=80", new_string="port=8080")
        self.assertIn("-port=80", out)
        self.assertIn("+port=8080", out)
        self.assertEqual(self.read(p), "port=8080\nhost=a\n")

    def test_not_found_gives_a_hint(self):
        p = self.write("c.conf", "    listen 80;\n    server_name x;\n")
        msg = self.err("fs_edit", path=p, old_string="  listen 8080;", new_string="x")
        self.assertIn("not found", msg)
        self.assertIn("closest line is 1", msg)

    def test_not_unique_lists_lines(self):
        p = self.write("c", "a\nb\na\n")
        msg = self.err("fs_edit", path=p, old_string="a", new_string="z")
        self.assertIn("occurs 2 times", msg)
        self.assertIn("lines 1, 3", msg)
        self.assertEqual(self.read(p), "a\nb\na\n")
        self.ok("fs_edit", path=p, old_string="a", new_string="z", replace_all=True)
        self.assertEqual(self.read(p), "z\nb\nz\n")

    def test_several_edits_are_all_or_nothing(self):
        p = self.write("c", "one\ntwo\nthree\n")
        self.ok("fs_edit", path=p, edits=[{"old_string": "one", "new_string": "1"}, {"old_string": "three", "new_string": "3"}])
        self.assertEqual(self.read(p), "1\ntwo\n3\n")
        self.err("fs_edit", path=p, edits=[{"old_string": "1", "new_string": "uno"}, {"old_string": "missing", "new_string": "x"}])
        self.assertEqual(self.read(p), "1\ntwo\n3\n")

    def test_identical_and_empty_strings(self):
        p = self.write("c", "x\n")
        self.assertIn("identical", self.err("fs_edit", path=p, old_string="x", new_string="x"))
        self.assertIn("empty", self.err("fs_edit", path=p, old_string="", new_string="y"))

    def test_crlf_files_keep_their_line_endings(self):
        p = self.path("win.txt")
        with open(p, "wb") as f:
            f.write(b"a\r\nb\r\n")
        self.ok("fs_edit", path=p, old_string="a\nb", new_string="a\nc")
        self.assertEqual(self.read(p, "rb"), b"a\r\nc\r\n")

    def test_keeps_mode_and_backup(self):
        p = self.write("run.sh", "echo 1\n")
        os.chmod(p, 0o750)
        out = self.ok("fs_edit", path=p, old_string="1", new_string="2", backup=True)
        self.assertIn("Backup", out)
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o750)

    def test_binary_and_missing(self):
        p = self.path("b")
        with open(p, "wb") as f:
            f.write(b"\x00abc")
        self.assertIn("binary", self.err("fs_edit", path=p, old_string="abc", new_string="x"))
        self.assertIn("does not exist", self.err("fs_edit", path=self.path("none"), old_string="a", new_string="b"))


class ListFindSearch(EngineCase):
    def setUp(self):
        super().setUp()
        self.write("src/main.py", "import os\nprint('hello world')\n")
        self.write("src/util.py", "def helper():\n    return 42\n")
        self.write("docs/readme.md", "# Hello\nworld\n")
        self.write(".hidden/secret.txt", "hello hidden\n")
        self.write("node_modules/pkg/index.js", "hello node\n")

    def test_list(self):
        out = self.ok("fs_list", path=self.work)
        self.assertIn("src/", out)
        self.assertIn("docs/", out)
        self.assertNotIn(".hidden", out)
        self.assertIn(".hidden", self.ok("fs_list", path=self.work, hidden=True))

    def test_list_recursive_glob_sort(self):
        out = self.ok("fs_list", path=self.work, depth=3, glob="*.py")
        self.assertIn("src/main.py", out)
        self.assertNotIn("readme.md", out)
        out = self.ok("fs_list", path=self.work, depth=3, sort="size", limit=2)
        self.assertIn("first 2 shown", out)

    def test_find(self):
        out = self.ok("fs_find", path=self.work, name="*.PY")  # case-insensitive
        self.assertIn("main.py", out)
        self.assertIn("util.py", out)
        self.assertIn("2 matches", out)
        self.assertIn("docs", self.ok("fs_find", path=self.work, type="d", name="docs"))
        self.assertIn("0 matches", self.ok("fs_find", path=self.work, name="*.rs"))
        big = self.ok("fs_find", path=self.work, name="*.py", min_size=30)  # main.py is 31 bytes, util.py 28
        self.assertIn("main.py", big)
        self.assertNotIn("util.py", big)
        self.assertIn("main.py", self.ok("fs_find", path=self.work, name="main*", modified_within_minutes=5))
        self.assertIn("0 matches", self.ok("fs_find", path=self.work, older_than_days=1))

    def test_search_skips_ignored_by_default(self):
        out = self.ok("fs_search", pattern="hello", path=self.work)
        self.assertIn("main.py", out)
        self.assertNotIn("node_modules", out)
        out = self.ok("fs_search", pattern="hello", path=self.work, include_ignored=True)
        self.assertIn("node_modules", out)
        self.assertIn(".hidden", out)

    def test_search_options(self):
        self.assertIn("No matches", self.ok("fs_search", pattern="HELLO", path=self.work))
        self.assertIn("main.py", self.ok("fs_search", pattern="HELLO", path=self.work, ignore_case=True))
        self.assertIn("main.py", self.ok("fs_search", pattern=r"hello \w+", path=self.work))
        self.assertIn("No matches", self.ok("fs_search", pattern=r"hello \w+", path=self.work, fixed_string=True))
        self.assertIn("util.py", self.ok("fs_search", pattern="helper", path=self.work, glob="*.py"))
        self.assertIn("No matches", self.ok("fs_search", pattern="helper", path=self.work, glob="*.md"))
        out = self.ok("fs_search", pattern="return 42", path=self.work, context=1)
        self.assertIn("def helper", out)

    def test_search_bad_regex_and_single_file(self):
        self.assertIn("regular expression", self.err("fs_search", pattern="(", path=self.work).replace("regex parse error", "regular expression")
                      .replace("search failed", "regular expression"))
        self.assertIn("main.py", self.ok("fs_search", pattern="print", path=self.path("src", "main.py")))

    def test_search_python_fallback(self):
        from ervisio_ai.tools import files
        from ervisio_ai import proc
        real = proc.which
        proc.which = lambda n: None if n == "rg" else real(n)
        try:
            out = self.ok("fs_search", pattern="hello", path=self.work)
            self.assertIn("main.py", out)
            self.assertNotIn("node_modules", out)
            self.assertIn("util.py", self.ok("fs_search", pattern="HELPER", path=self.work, ignore_case=True, glob="*.py"))
            self.assertIn("regular expression", self.err("fs_search", pattern="(", path=self.work))
        finally:
            proc.which = real
        self.assertTrue(files)

    def test_stat(self):
        p = self.write("s.txt", "abc")
        out = self.ok("fs_stat", path=p, hash=True)
        self.assertIn("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", out)
        self.assertIn("file", out)
        os.symlink(p, self.path("ln"))
        self.assertIn("symlink", self.ok("fs_stat", path=self.path("ln")))
        self.assertIn("does not exist", self.err("fs_stat", path=self.path("zzz")))


class Manage(EngineCase):
    def test_mkdir_touch_copy_move_delete(self):
        self.ok("fs_manage", action="mkdir", path=self.path("a", "b"))
        self.assertTrue(os.path.isdir(self.path("a", "b")))
        self.ok("fs_manage", action="touch", path=self.path("f.txt"))
        self.write("src.txt", "data")
        self.ok("fs_manage", action="copy", path=self.path("src.txt"), dest=self.path("a"))  # into a folder, like cp
        self.assertEqual(self.read(self.path("a", "src.txt")), "data")
        self.ok("fs_manage", action="move", path=self.path("src.txt"), dest=self.path("moved.txt"))
        self.assertFalse(os.path.exists(self.path("src.txt")))
        self.assertTrue(os.path.exists(self.path("moved.txt")))
        self.ok("fs_manage", action="delete", path=self.path("moved.txt"))
        self.assertFalse(os.path.exists(self.path("moved.txt")))

    def test_overwrite_protection(self):
        self.write("a", "1")
        self.write("b", "2")
        self.assertIn("already exists", self.err("fs_manage", action="copy", path=self.path("a"), dest=self.path("b")))
        self.ok("fs_manage", action="copy", path=self.path("a"), dest=self.path("b"), overwrite=True)
        self.assertEqual(self.read(self.path("b")), "1")

    def test_directories_need_recursive(self):
        self.write("d/x.txt", "x")
        self.assertIn("recursive", self.err("fs_manage", action="copy", path=self.path("d"), dest=self.path("d2")))
        self.ok("fs_manage", action="copy", path=self.path("d"), dest=self.path("d2"), recursive=True)
        self.assertTrue(os.path.exists(self.path("d2", "x.txt")))
        self.err("fs_manage", action="delete", path=self.path("d"))  # not empty
        self.assertTrue(os.path.isdir(self.path("d")))
        self.ok("fs_manage", action="delete", path=self.path("d"), recursive=True)
        self.assertFalse(os.path.exists(self.path("d")))

    def test_chmod_chown_symlink(self):
        self.write("d/a", "")
        self.ok("fs_manage", action="chmod", path=self.path("d"), mode="700", recursive=True)
        self.assertEqual(stat.S_IMODE(os.stat(self.path("d", "a")).st_mode), 0o700)
        self.ok("fs_manage", action="chown", path=self.path("d"), owner="root", recursive=True)
        self.ok("fs_manage", action="symlink", path=self.path("l"), dest=self.path("d"))
        self.assertEqual(os.readlink(self.path("l")), self.path("d"))
        self.assertIn("mode is required", self.err("fs_manage", action="chmod", path=self.path("d")))
        self.err("fs_manage", action="chown", path=self.path("d"), owner="no-such-user-xyz")


class Archive(EngineCase):
    def test_tar_and_zip_roundtrip(self):
        self.write("proj/a.txt", "A")
        self.write("proj/sub/b.txt", "B")
        for name in ("out.tar.gz", "out.zip", "out.tar", "out.tar.xz", "out.tar.bz2"):
            arc = self.path(name)
            self.ok("fs_archive", action="create", archive=arc, sources=[self.path("proj")])
            self.assertIn("proj/sub/b.txt", self.ok("fs_archive", action="list", archive=arc))
            dest = self.path("x-" + name)
            self.ok("fs_archive", action="extract", archive=arc, dest=dest)
            self.assertEqual(self.read(os.path.join(dest, "proj", "sub", "b.txt")), "B")

    def test_extraction_refuses_path_traversal(self):
        import io
        import tarfile
        import zipfile
        arc = self.path("evil.tar")
        with tarfile.open(arc, "w") as t:
            data = b"pwned"
            info = tarfile.TarInfo("../escaped.txt")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        self.assertIn("escapes", self.err("fs_archive", action="extract", archive=arc, dest=self.path("out")))
        self.assertFalse(os.path.exists(self.path("escaped.txt")))
        z = self.path("evil.zip")
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("../../zip-escaped.txt", "x")
        self.assertIn("escapes", self.err("fs_archive", action="extract", archive=z, dest=self.path("out2")))

    def test_extraction_refuses_links_outside(self):
        import tarfile
        arc = self.path("link.tar")
        with tarfile.open(arc, "w") as t:
            info = tarfile.TarInfo("evil")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            t.addfile(info)
        self.assertIn("outside", self.err("fs_archive", action="extract", archive=arc, dest=self.path("o")))


class Diff(EngineCase):
    def test_diff(self):
        a, b = self.write("a", "x\ny\n"), self.write("b", "x\nz\n")
        out = self.ok("fs_diff", a=a, b=b)
        self.assertIn("-y", out)
        self.assertIn("+z", out)
        self.assertIn("No differences", self.ok("fs_diff", a=a, text_b="x\ny\n"))
        self.assertIn("Give b", self.err("fs_diff", a=a))


if __name__ == "__main__":
    unittest.main()
