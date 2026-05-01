"""setup.py — bundles flat-file py-modules with the static/templates/
searxng asset directories so wheel installs (pipx, pip install) get
the files server.py needs at runtime.

`include-package-data` in pyproject.toml only works for proper
packages (dirs with __init__.py). This project uses flat py-modules,
so we explicitly list the asset trees as `data_files` here. They
land at sys.prefix/<dirname> in the venv, and config.py knows to
look there as a fallback.

This file is also the pip <21.3 compat shim (those versions reject
pyproject-only installs without setup.py).
"""
import os
from setuptools import setup


def _walk(root):
    """Return setup data_files entries for every file under `root`."""
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, _, files in os.walk(root):
        if not files:
            continue
        out.append((dirpath, [os.path.join(dirpath, f) for f in files]))
    return out


_data_files = []
for _dir in ("static", "templates", "searxng", "docs/plugin-registry"):
    _data_files.extend(_walk(_dir))


setup(data_files=_data_files)
