"""Every module of the package imports (a broken module must never reach a release)."""

import importlib
import pkgutil

import pytest

import treeaicoach

MODULES = sorted(m.name for m in pkgutil.iter_modules(treeaicoach.__path__, "treeaicoach.")
                 if not m.name.endswith("__main__"))


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name):
    importlib.import_module(name)
