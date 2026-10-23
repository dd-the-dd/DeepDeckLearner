"""Factory imports may never recover or pause the real user's workspace."""

import subprocess
import sys


def test_import_does_not_initialize_default_profile_but_uvicorn_entry_point_still_works(tmp_path):
    script = r"""
import importlib, sys
from pathlib import Path
from deepdeck_learner import status

root = Path(sys.argv[1]) / 'default-profile'
root.mkdir()
status.project_root = lambda: root
module = importlib.import_module('deepdeck_learner.app')
assert module.project_root() == root, 'Default factory must be confined to the test profile'
assert not (root / '.deepdeck').exists(), 'Import initialized the default profile'
assert 'app' not in vars(module)

isolated = Path(sys.argv[1]) / 'explicit-profile'
isolated.mkdir()
explicit = module.create_app(isolated)
assert not (root / '.deepdeck').exists(), 'Explicit factory touched default profile'
assert any(getattr(route, 'path', '') == '/api/v1/health' for route in explicit.routes)

default = module.app
assert (root / '.deepdeck/learner.db').is_file()
assert module.app is default
assert any(getattr(route, 'path', '') == '/api/v1/health' for route in default.routes)
try:
    module.nonexistent_attribute
except AttributeError:
    pass
else:
    raise AssertionError('Unknown module attribute must raise AttributeError')
print('Factory imports isolated; uvicorn :app initializes once on explicit access.')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, timeout=45
    )
    assert result.returncode == 0, result.stderr
