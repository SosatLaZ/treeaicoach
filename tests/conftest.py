"""Shared test fixtures."""
import pytest


@pytest.fixture(autouse=True)
def _ai_daily_quota_in_memory(monkeypatch):
    """Tests never touch (nor exhaust) the real persisted daily AI quota of the user data folder."""
    from treeaicoach import ai_advisor
    monkeypatch.setattr(ai_advisor, "DAILY_QUOTA", ai_advisor.DailyQuota(""))


@pytest.fixture(autouse=True)
def _no_published_layout_between_tests():
    """layout.publish() is process-global (the overlay thread's layout, read by toasts / badges): a
    test that drives the overlay left its layout for the next test of the same worker, so
    test_layout's slot checks failed only after test_build_strip (order dependence under -n 4)."""
    from treeaicoach import layout
    layout.publish(None)
    yield
    layout.publish(None)
