import tempfile
import unittest
import importlib.util
from pathlib import Path

_path=Path(__file__).parents[1]/'arm-overlay'/'apply_path_fix.py'
_spec=importlib.util.spec_from_file_location('arm_path_fix',_path)
apply_path_fix=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(apply_path_fix)


class ArmPathPatchTests(unittest.TestCase):
    def test_finalizer_does_not_duplicate_job_path_folder(self):
        self.assertEqual(apply_path_fix.destination_dir(
            '/media/completed/tv/Show/Season 02', 'Show/Season 02'),
            '/media/completed/tv/Show/Season 02')
        self.assertEqual(apply_path_fix.destination_dir(
            '/media/completed/tv/Show/Season 02', 'Season 02'),
            '/media/completed/tv/Show/Season 02')
        self.assertEqual(apply_path_fix.destination_dir(
            '/media/completed/tv/Show/Season 02', 'Extras'),
            '/media/completed/tv/Show/Season 02/Extras')

    def test_patch_is_version_checked_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'VERSION').write_text('19.1.0\n')
            naming=root/'arm/ripper/naming.py';naming.parent.mkdir(parents=True)
            naming.write_text('''import os\ndef _move_track(track, raw_path, final_dir, rendered_map):\n    rendered_folder = r.get("rendered_folder", '')\n    dest_dir = os.path.join(final_dir, rendered_folder) if rendered_folder else final_dir\n    os.makedirs(dest_dir, exist_ok=True)\n    shutil.move(src, os.path.join(dest_dir, dest_name))\n''')
            job=root/'arm/models/job.py';job.parent.mkdir(parents=True,exist_ok=True)
            job.write_text('''class Job:\n    def eject(self):\n        self.drive.eject()\n        self.ejected = True\n''')
            apply_path_fix.apply(root)
            patched=naming.read_text()
            self.assertIn('dest_dir = _rendered_destination_dir(final_dir, rendered_folder)',patched)
            self.assertIn('os.chmod(dest_dir',patched)
            self.assertIn('AUTO_EJECT failed:',job.read_text())
            apply_path_fix.apply(root)
            self.assertEqual(naming.read_text(),patched)


if __name__=='__main__':
    unittest.main()
