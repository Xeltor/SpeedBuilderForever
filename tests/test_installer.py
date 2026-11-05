from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from installer import FILES, HELPER, SOURCE, find_forever, forever_folder, install_helper, running_clients


class InstallerTests(unittest.TestCase):
    def test_detects_running_game_and_installs_idempotently(self):
        with TemporaryDirectory() as directory:
            home = Path(directory)
            parent = home / "Games" / "World of Warcraft"
            forever = parent / "_forever_"
            addons = forever / "Interface" / "AddOns"
            hekili = addons / "Hekili"
            hekili.mkdir(parents=True)
            (hekili / "Hekili.toc").write_text("## Interface: 16001")
            (forever / "Wow.exe").touch()

            process = home / "proc" / "123"
            process.mkdir(parents=True)
            (process / "comm").write_text("Wow.exe\n")
            (process / "cwd").symlink_to(forever, target_is_directory=True)
            running, game_open = running_clients(home / "proc")
            self.assertTrue(game_open)
            self.assertEqual(running, {forever})
            self.assertEqual(find_forever(None, running, home), forever)
            self.assertEqual(find_forever(None, set(), home), forever)
            self.assertEqual(forever_folder(addons), forever)

            self.assertEqual(install_helper(forever), "installed")
            helper = addons / HELPER
            self.assertEqual(install_helper(forever), "current")
            (helper / FILES[0]).write_text("old helper")
            self.assertEqual(install_helper(forever), "updated")
            self.assertEqual((helper / FILES[0]).read_bytes(), (SOURCE / FILES[0]).read_bytes())

            external = home / "external.lua"
            external.write_text("do not replace")
            (helper / FILES[0]).unlink()
            (helper / FILES[0]).symlink_to(external)
            with self.assertRaises(ValueError):
                install_helper(forever)
            self.assertEqual(external.read_text(), "do not replace")

    def test_does_not_select_a_different_classic_beta(self):
        with TemporaryDirectory() as directory:
            other = Path(directory) / "_classic_beta_"
            toc = other / "Interface" / "AddOns" / "Hekili" / "Hekili.toc"
            toc.parent.mkdir(parents=True)
            toc.write_text("## Interface: 50500")
            (other / "Wow.exe").touch()
            self.assertIsNone(forever_folder(other))
