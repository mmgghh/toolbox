"""Compatibility shim.

All packaging metadata now lives in ``pyproject.toml``. This file only exists
so that ``python setup.py``-style invocations and very old pip versions keep
working.
"""

from setuptools import setup

setup()
