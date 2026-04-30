"""setup.py shim for pip < 21.3 compatibility.

All project metadata lives in pyproject.toml. This shim exists only so
that pip versions older than 21.3 (e.g. macOS bundled pip 21.2.4 on
Python 3.9) don't reject editable installs with "setup.py not found".

Modern pip ignores this file entirely and reads pyproject.toml directly.
"""
from setuptools import setup

setup()
