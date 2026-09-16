"""The LLM selects a URL before any browser page is opened."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from privacy_guard.agent_runtime import build_agent, quiet_browser_use
from privacy_guard.gateway import ModelGateway
from privacy_guard.models import TaskRequest
from privacy_guard.tasks import TaskManager
from privacy_guard.vault import Vault
from tests.test_agent_runtime import runtime_stub


@pytest.mark.parametrize("start_url,target_id,goal,expected", [
    (None, None, "Find headphones on Amazon", None),
    (None, None, "Visit https://example.test/shop", "https://example.test/shop"),
    ("https://chosen.test/", None, "Find headphones", "https://chosen.test/"),
    (None, "owned", "Summarize this page", None),
])
async def test_task_opens_site_discovery_or_explicit_target(tmp_path, start_url, target_id, goal, expected):
    vault = Vault(tmp_path)
    vault.initialize("synthetic-long-passphrase")
    vault.put_record("Private value", "text", "PRIVATE_QUERY_CANARY")
    gateway = ModelGateway()
    gateway.api_key = "synthetic-key"
    browser = SimpleNamespace(
        status=lambda: {"running": False}, launch=AsyncMock(),
        new_page=AsyncMock(side_effect=lambda url: {"target_id": "new", "url": url}),
        tabs=AsyncMock(return_value=[{"target_id": "owned", "url": "https://existing.test/"}]),
    )
    manager = TaskManager(vault, browser, gateway)
    manager.launch = lambda task: None
    task = await manager.start(TaskRequest(goal=goal + " PRIVATE_QUERY_CANARY", mode="remote",
                                          start_url=start_url, target_id=target_id))
    if expected:
        browser.new_page.assert_awaited_once_with(expected)
        browser.launch.assert_awaited_once()
    else:
        browser.new_page.assert_not_awaited()
        assert task["target_id"] == target_id
        if target_id is None:
            browser.launch.assert_not_awaited()
    assert "PRIVATE_QUERY_CANARY" not in str(task)
    assert manager.tasks[task["id"]]["_discover_site"] == (not start_url and not target_id and expected is None)


async def test_discovery_without_key_does_not_launch_browser(tmp_path):
    vault = Vault(tmp_path)
    vault.initialize("synthetic-long-passphrase")
    browser = SimpleNamespace(launch=AsyncMock(), new_page=AsyncMock())
    manager = TaskManager(vault, browser, ModelGateway())
    with pytest.raises(ValueError, match="Configure"):
        await manager.start(TaskRequest(goal="Find a laptop", mode="remote"))
    browser.launch.assert_not_awaited()
    browser.new_page.assert_not_awaited()


async def test_research_navigation_uses_owned_tab_and_blocks_private_or_invented_urls(tmp_path, monkeypatch):
    quiet_browser_use()
    import browser_use.browser.profile as profile

    monkeypatch.setattr(profile, "get_display_size", lambda: None)
    from browser_use import BrowserSession, Tools
    from browser_use.agent.views import ActionResult
    from browser_use.tools.views import NavigateAction

    # Capture the one native navigation function before the guarded registry removes it.
    original_init = Tools.__init__
    navigate = AsyncMock(return_value=ActionResult(extracted_content="Opened"))

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.registry.registry.actions["navigate"].function = navigate

    monkeypatch.setattr(Tools, "__init__", init)
    runtime = runtime_stub(tmp_path)
    runtime.task.update(_goal="Find wireless headphones on Amazon", target_id="owned", _review_text=True)
    runtime.check_target = AsyncMock()
    runtime.manager.approval = AsyncMock()
    runtime.manager.vault.put_record("Private name", "text", "PRIVATE_QUERY_CANARY")
    session = BrowserSession(cdp_url="http://127.0.0.1:9222", use_cloud=False, captcha_solver=False)
    agent = build_agent(runtime, session)
    assert "search_web" not in agent.tools.registry.registry.actions
    open_site = agent.tools.registry.registry.actions["navigate"].function
    assert (await open_site(params=NavigateAction(url="https://PRIVATE_QUERY_CANARY.test/"))).error
    assert (await open_site(params=NavigateAction(url="https://www.google.com/search?q=headphones"))).error
    navigate.assert_not_awaited()
    for url in ("https://www.google.com/", "https://www.google.co.in/",
                "https://www.bing.com/",
                "https://duckduckgo.com/", "https://search.brave.com/"):
        assert not (await open_site(params=NavigateAction(url=url))).error
        assert navigate.call_args.kwargs["params"].new_tab is False
    navigate.reset_mock()
    assert not (await open_site(params=NavigateAction(url="https://www.amazon.in/"))).error
    assert (await open_site(params=NavigateAction(url="https://www.amazon.in/invented/path"))).error
    with pytest.raises(PermissionError, match="local service"):
        await open_site(params=NavigateAction(url="https://127.0.0.1/"))
    navigate.assert_awaited_once()
    params = navigate.call_args.kwargs["params"]
    assert params.url == "https://www.amazon.in/"
    assert params.new_tab is False
    runtime.task["_visited_urls"] = ["https://www.amazon.in/observed/service"]
    assert not (await open_site(params=NavigateAction(url=runtime.task["_visited_urls"][0]))).error
