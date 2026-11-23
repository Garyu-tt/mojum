"""Hermetic regression tests for archive member path admission."""
import unittest

from mojum.archive_path_admission import (
    ArchivePathError,
    admit_member_paths,
    validate_member_path,
)


class StringSubclass(str):
    pass


class ArchivePathAdmissionTests(unittest.TestCase):
    def test_valid_nested_paths_preserve_order(self):
        names = ("segments/0001.jsonl", "roots/0001.sha256", "manifest.json")
        self.assertEqual(admit_member_paths(iter(names)), names)

    def test_literal_escape_like_characters_are_valid(self):
        self.assertEqual(validate_member_path("x0f7"), "x0f7")
        self.assertEqual(validate_member_path("folder/x1f7.txt"), "folder/x1f7.txt")

    def test_bad_paths(self):
        bad = (
            "", "/etc/passwd", "../escape", "a/../b", "./a", "a//b",
            "a/", "C:/Windows/file", "C:relative", "\\\\server\\share",
            "a\\b", "a/./b", "a/..", "a/evil.", "a/evil ",
            "a/\x00b", "a/\nb", "a/\x7fb", "a" * 1025,
            "x/" + "z" * 256, "a/\ud800",
        )
        for path in bad:
            with self.subTest(path=repr(path)), self.assertRaises(ArchivePathError):
                validate_member_path(path)

    def test_non_strings(self):
        for path in (None, 3, b"a", StringSubclass("safe/path")):
            with self.subTest(path=path), self.assertRaises(ArchivePathError):
                validate_member_path(path)

    def test_duplicate_and_casefold_collision(self):
        for names in (("a", "a"), ("A", "a"), ("Dir/A", "dir/a")):
            with self.subTest(names=names), self.assertRaises(ArchivePathError):
                admit_member_paths(names)

    def test_file_directory_collision_both_orders(self):
        for names in (("a", "a/b"), ("a/b", "a"), ("A", "a/b")):
            with self.subTest(names=names), self.assertRaises(ArchivePathError):
                admit_member_paths(names)

    def test_siblings_are_allowed(self):
        self.assertEqual(admit_member_paths(("a/b", "a/c")), ("a/b", "a/c"))

    def test_member_limit(self):
        with self.assertRaises(ArchivePathError):
            admit_member_paths(("a", "b"), max_members=1)
        with self.assertRaises(ArchivePathError):
            admit_member_paths(("a",), max_members=True)
        with self.assertRaises(ArchivePathError):
            admit_member_paths(("a",), max_members=4097)

    def test_reject_string_as_manifest(self):
        with self.assertRaises(ArchivePathError):
            admit_member_paths("a/b")


if __name__ == "__main__":
    unittest.main()
