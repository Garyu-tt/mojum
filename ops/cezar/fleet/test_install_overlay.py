"""Fail-closed installer tests for the third-party Cezar web shell."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

P=Path(__file__).resolve().parent
SRC=P/"install_overlay.py"
if not SRC.is_file():
    SRC=Path("/opt/cezar-telegram-staging/install_overlay.py")
spec=importlib.util.spec_from_file_location("cezar_overlay_install",SRC)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class InstallOverlayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.dist=root/"dist";self.dist.mkdir(mode=0o700)
        (self.dist/"assets").mkdir(mode=0o700)
        self.data=root/"backups";self.data.mkdir(mode=0o700)
        self.backup=self.data/"original.html"
        self.index=self.dist/"index.html"
        self.index.write_text("<!doctype html><body><main id='app'></main></body></html>")
        self.index.chmod(0o600)
        self.js=root/"overlay.js"
        self.js.write_text("/* Cezar Fleet visibility. */\nconsole.log('safe')\n")

    def test_installer_preserves_original_and_is_idempotent(self):
        r=m.install(self.dist,self.js,self.backup)
        self.assertTrue(r["installed"])
        self.assertFalse(r["was_present"])
        self.assertIn(m.MARKER,self.index.read_text())
        self.assertEqual(self.backup.read_text(),"<!doctype html><body><main id='app'></main></body></html>")
        self.assertEqual((self.dist/"assets"/m.JS_NAME).read_bytes(),self.js.read_bytes())
        self.assertEqual(self.backup.stat().st_mode&0o777,0o600)
        self.assertEqual(self.index.stat().st_mode&0o777,0o600)
        again=m.install(self.dist,self.js,self.backup)
        self.assertTrue(again["was_present"])
        self.assertEqual(self.index.read_text().count(m.MARKER),1)

    def test_unrelated_upgrade_shell_refuses_stale_backup(self):
        m.install(self.dist,self.js,self.backup)
        self.index.write_text("<html><body><new>major upgrade</new></body></html>")
        self.index.chmod(0o600)
        with self.assertRaises(m.DeployRefused):
            m.install(self.dist,self.js,self.backup)

    def test_symlinked_output_asset_rejected(self):
        p=self.dist/"assets"/m.JS_NAME
        p.symlink_to(self.js)
        with self.assertRaises(m.DeployRefused):
            m.install(self.dist,self.js,self.backup)
        self.assertFalse(self.backup.exists())

    def test_public_dist_rejected(self):
        self.dist.chmod(0o755)
        with self.assertRaises(m.DeployRefused):
            m.install(self.dist,self.js,self.backup)

    def test_missing_body_refused(self):
        self.index.write_text("<body>not closed")
        self.index.chmod(0o600)
        with self.assertRaises(m.DeployRefused):
            m.install(self.dist,self.js,self.backup)

    def test_wrong_or_missing_source_refused(self):
        self.js.write_text("alert('unknown')")
        with self.assertRaises(m.DeployRefused):
            m.install(self.dist,self.js,self.backup)

    def test_asset_update_preserves_original_backup(self):
        m.install(self.dist,self.js,self.backup)
        self.js.write_text("/* Cezar Fleet visibility. */\nconst updated=42;\n")
        m.install(self.dist,self.js,self.backup)
        self.assertIn("updated=42",(self.dist/"assets"/m.JS_NAME).read_text())
        self.assertNotIn(m.MARKER,self.backup.read_text())


    def test_legacy_immutable_asset_url_migrates_without_losing_backup(self):
        m.install(self.dist,self.js,self.backup)
        original_backup=self.backup.read_bytes()
        self.index.write_text(
            self.index.read_text().replace(m.MARKER,m.LEGACY_MARKER))
        self.index.chmod(0o600)
        response=m.install(self.dist,self.js,self.backup)
        self.assertTrue(response["was_present"])
        self.assertTrue(response["cache_version_changed"])
        self.assertIn(m.MARKER,self.index.read_text())
        self.assertNotIn(m.LEGACY_MARKER,self.index.read_text())
        self.assertEqual(original_backup,self.backup.read_bytes())
        again=m.install(self.dist,self.js,self.backup)
        self.assertFalse(again["cache_version_changed"])
        self.assertEqual(self.index.read_text().count(m.MARKER),1)

    def test_mixed_legacy_and_current_markers_fail_closed(self):
        m.install(self.dist,self.js,self.backup)
        before=self.index.read_text()
        self.index.write_text(
            before.replace(m.MARKER,m.MARKER+"\n"+m.LEGACY_MARKER))
        self.index.chmod(0o600)
        with self.assertRaisesRegex(m.DeployRefused,"duplicate"):
            m.install(self.dist,self.js,self.backup)
        self.assertEqual(self.index.read_text().count(m.LEGACY_MARKER),1)



if __name__=="__main__":
    unittest.main()
