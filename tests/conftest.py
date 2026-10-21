"""Shared test fixtures."""
import pytest


@pytest.fixture(autouse=True)
def _ai_daily_quota_in_memory(monkeypatch):
    """Tests never touch (nor exhaust) the real persisted daily AI quota of the user data folder."""
    from treeaicoach import ai_advisor
    monkeypatch.setattr(ai_advisor, "DAILY_QUOTA", ai_advisor.DailyQuota(""))
