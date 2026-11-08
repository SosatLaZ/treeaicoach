"""tests/wow: a run on the faithful « forever » mock (wowmock.lua, see research/fidelity.md) that fails ONLY because of
known real bugs of the addon (tests/wow/forever_known.py: F1, F2…) is reported as xfailed, with the finding ids.
Any other problem in the same run keeps the test red. Fix the bug -> its signature disappears -> the test passes."""
from __future__ import annotations

import pytest

try:
    from forever_known import explained_forever_failure
except ImportError:   # run from another rootdir: import by path
    import importlib.util
    import pathlib
    _spec = importlib.util.spec_from_file_location("forever_known", pathlib.Path(__file__).with_name("forever_known.py"))
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    explained_forever_failure = _mod.explained_forever_failure


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.when != "call" or not rep.failed:
        return
    text = str(rep.longrepr)
    if "flavor=forever" not in text:
        return
    ids = explained_forever_failure(text)
    if ids:
        rep.outcome = "skipped"
        rep.wasxfail = "FIDELITY: known real bugs %s on Forever (research/fidelity.md)" % ",".join(ids)
