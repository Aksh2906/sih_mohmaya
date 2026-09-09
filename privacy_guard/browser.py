"""Local Browser Use/CDP connection and atomic private-value execution boundary."""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

SHUTDOWN_LOCK_TIMEOUT = 2.0


class BrowserError(RuntimeError):
    """An intentionally value-free error suitable for task status."""


def _origin(url: str) -> str:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        raise BrowserError("unsupported_origin") from None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise BrowserError("unsupported_origin")
    suffix = f":{port}" if port and port != (443 if parsed.scheme == "https" else 80) else ""
    hostname = parsed.hostname.lower()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    return f"{parsed.scheme}://{hostname}{suffix}"


# Runs in an isolated world. Page scripts cannot replace this state's node map.
_OBSERVE_JS = r"""function() {
  let s = globalThis.__privacyGuard;
  if (!s) {
    s = globalThis.__privacyGuard = {nonce: crypto.randomUUID(), revision: 0, serial: 0, nodes: []};
    s.observer = new MutationObserver(() => s.revision++);
    s.observer.observe(document.documentElement, {subtree:true, childList:true, attributes:true, characterData:true});
    document.addEventListener('input', () => s.revision++, true);
    document.addEventListener('change', () => s.revision++, true);
    window.addEventListener('scroll', () => s.revision++, true);
    window.addEventListener('resize', () => s.revision++, true);
  }
  if (s.observer.takeRecords().length) s.revision++;
  s.serial++;
  s.nodes = [];
  const clean = x => String(x || '').replace(/\s+/g, ' ').trim();
  const visible = e => {
    const r = e.getBoundingClientRect(), c = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && c.visibility !== 'hidden' && c.display !== 'none';
  };
  const fields = [];
  let truncated_fields = false;
  for (const e of document.querySelectorAll('input,textarea,select,button,a[href],[role="button"]')) {
    if (!visible(e) || e.type === 'hidden') continue;
    if (fields.length >= 250) { truncated_fields = true; break; }
    const r = e.getBoundingClientRect();
    const labelled = (e.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => document.getElementById(id)?.textContent || '').join(' ');
    const label = clean(e.labels?.length ? [...e.labels].map(x => x.textContent).join(' ') : labelled || e.getAttribute('aria-label') || e.getAttribute('placeholder') || e.textContent || e.name || e.id);
    const tag = e.tagName.toLowerCase();
    const index = s.nodes.push(e);
    const inputType = String(e.type || '').toLowerCase();
    const submit = (tag === 'button' && (!inputType || inputType === 'submit')) || (tag === 'input' && ['submit','image'].includes(inputType));
    fields.push({index, label:label.slice(0,500), tag, input_type:inputType,
      name:e.name || '', autocomplete:e.autocomplete || '',
      value: inputType === 'password' ? '' : String(e.value || ''),
      options: tag === 'select' ? [...e.options].slice(0,150).map(o => ({value:o.value,label:clean(o.textContent),disabled:o.disabled})) : [],
      rect:{x:r.x,y:r.y,width:r.width,height:r.height},
      disabled:!!e.disabled, readonly:!!e.readOnly, required:!!e.required,
      is_submit:submit, href:tag === 'a' ? e.href : null,
      click_risk:submit ? 'submit' : 'unknown'});
  }
  s.url = location.href;
  s.epoch = `${s.nonce}:${s.revision}:${s.serial}`;
  return {url:s.url,title:document.title,epoch:s.epoch,fields,
    text:(document.body?.innerText || '').slice(0,100000),
    width:innerWidth,height:innerHeight,device_scale_factor:devicePixelRatio,
    unsupported_frames:document.querySelectorAll('iframe,frame').length,
    unsupported_components:[...document.querySelectorAll('*')].some(e => e.shadowRoot || e.tagName.includes('-')),
    truncated_fields};
}"""


# Validation and dispatch share a single renderer task. No arbitrary JS is accepted.
_EXECUTE_JS = r"""function(expected, action, privateValue) {
  const s = globalThis.__privacyGuard;
  if (!s) return {ok:false,error:'stale_observation'};
  if (s.observer.takeRecords().length) s.revision++;
  const current = `${s.nonce}:${s.revision}:${s.serial}`;
  if (expected.epoch !== current || location.href !== expected.url || location.origin !== expected.origin)
    return {ok:false,error:'stale_observation'};
  if (action.type === 'wait' || action.type === 'done') return {ok:true,action:action.type};
  if (action.type === 'scroll') {
    window.scrollBy({top:action.delta_y,left:0,behavior:'instant'});
    s.revision++;
    return {ok:true,action:'scroll'};
  }
  const e = s.nodes[action.index - 1];
  if (!e || !e.isConnected || e.disabled || e.readOnly) return {ok:false,error:'invalid_target'};
  const r = e.getBoundingClientRect(), style = getComputedStyle(e);
  if (!r.width || !r.height || style.display === 'none' || style.visibility === 'hidden')
    return {ok:false,error:'invalid_target'};
  if (action.type === 'input_ref') {
    const tag = e.tagName.toLowerCase(), type = (e.type || '').toLowerCase();
    if (!(tag === 'textarea' || (tag === 'input' && ['text','email','tel','url','search','number','date','month','week','time','datetime-local'].includes(type))))
      return {ok:false,error:'unsupported_input_type'};
    const proto = tag === 'textarea' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(e, privateValue);
    e.dispatchEvent(new Event('input',{bubbles:true}));
    e.dispatchEvent(new Event('change',{bubbles:true}));
    s.revision++;
    return {ok:e.value === privateValue,action:'input_ref',error:e.value === privateValue ? null : 'value_not_accepted'};
  }
  if (action.type === 'select_ref') {
    if (e.tagName !== 'SELECT' || e.multiple) return {ok:false,error:'unsupported_select'};
    const matches = [...e.options].filter(o => !o.disabled && (o.value === privateValue || o.textContent.trim() === privateValue));
    if (matches.length !== 1) return {ok:false,error:'option_not_unique'};
    e.value = matches[0].value;
    e.dispatchEvent(new Event('input',{bubbles:true}));
    e.dispatchEvent(new Event('change',{bubbles:true}));
    s.revision++;
    return {ok:true,action:'select_ref'};
  }
  if (action.type === 'click') {
    if (!action.approved) return {ok:false,error:'approval_required'};
    if (e.tagName === 'A' && (e.target === '_blank' || new URL(e.href).origin !== location.origin))
      return {ok:false,error:'unsupported_navigation'};
    if (e.form && new URL(e.formAction || e.form.action || location.href).origin !== location.origin)
      return {ok:false,error:'unsupported_navigation'};
    const top = document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
    if (!top || !(top === e || e.contains(top))) return {ok:false,error:'target_not_visible'};
    e.click();
    s.revision++;
    return {ok:true,action:'click'};
  }
  return {ok:false,error:'unsupported_action'};
}"""


class BrowserDriver:
    def __init__(self, data_dir: Path, extension_dir: Path, headless: bool = False):
        self.data_dir = Path(data_dir).resolve()
        self.extension_dir = Path(extension_dir).resolve()
        self.headless = headless
        self._session: Any = None
        self._process: asyncio.subprocess.Process | None = None
        self._cdp_url: str | None = None
        self._contexts: dict[str, tuple[Any, int]] = {}
        self._observations: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._generation = 0
        # Optional synchronous cancellation predicate set by the coordinator.
        self.can_execute: Callable[[], bool] = lambda: True

    @property
    def running(self) -> bool:
        return self._session is not None and self._process is not None and self._process.returncode is None

    def invalidate(self) -> None:
        """Synchronous Stop hook: invalidate work already awaiting CDP reads."""
        self._generation += 1
        self._observations.clear()

    def status(self) -> dict[str, Any]:
        return {"running": self.running, "headless": self.headless, "engine": "browser-use-cdp"}

    def _browser_executable(self) -> Path:
        override = os.environ.get("GUARD_BROWSER_EXECUTABLE")
        if override:
            candidate = Path(override).expanduser().resolve()
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
            raise BrowserError("browser_executable_not_found")
        root = self.data_dir / "browsers"
        patterns = [
            "chromium-*/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
            "chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
            "chromium-*/chrome-mac-arm64/Chromium.app/Contents/MacOS/Chromium",
            "chromium-*/chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
            "chromium-*/chrome-linux64/chrome",
            "chromium-*/chrome-linux/chrome",
            "chromium-*/chrome-win64/chrome.exe",
            "chromium-*/chrome-win/chrome.exe",
        ]
        for pattern in patterns:
            candidates = sorted(root.glob(pattern), reverse=True)
            if candidates:
                return candidates[0]
        raise BrowserError("browser_not_installed_run_scripts_browser_install")

    async def launch(self) -> dict[str, Any]:
        async with self._lock:
            if self.running:
                return {"running": True, "headless": self.headless, "engine": "browser-use-cdp"}
            executable = self._browser_executable()
            if not (self.extension_dir / "manifest.json").is_file():
                raise BrowserError("extension_not_built")
            self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            profile = self.data_dir / "browser-profile"
            profile.mkdir(mode=0o700, parents=True, exist_ok=True)
            port_file = profile / "DevToolsActivePort"
            # This file belongs only to our dedicated profile, never the user's browser.
            if port_file.exists():
                port_file.unlink()
            args = [
                str(executable),
                f"--user-data-dir={profile}",
                "--remote-debugging-port=0",
                "--remote-debugging-address=127.0.0.1",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-sync",
                "--disable-breakpad",
                "--window-size=1280,900",
                f"--disable-extensions-except={self.extension_dir}",
                f"--load-extension={self.extension_dir}",
            ]
            if self.headless:
                args.append("--headless=new")
            args.append("about:blank")
            self._process = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            try:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if self._process.returncode is not None:
                        raise BrowserError("browser_launch_failed")
                    if port_file.exists():
                        lines = port_file.read_text().splitlines()
                        if (
                            len(lines) >= 2
                            and lines[0].isdigit()
                            and lines[1].startswith("/devtools/browser/")
                        ):
                            self._cdp_url = f"ws://127.0.0.1:{int(lines[0])}{lines[1]}"
                            break
                    await asyncio.sleep(0.1)
                if not self._cdp_url:
                    raise BrowserError("browser_launch_timeout")
                os.environ["ANONYMIZED_TELEMETRY"] = "false"
                os.environ["BROWSER_USE_CLOUD_SYNC"] = "false"
                os.environ["BROWSER_USE_LOGGING_LEVEL"] = "critical"
                os.environ["BROWSER_USE_SETUP_LOGGING"] = "false"
                os.environ["BROWSER_USE_VERBOSE_OBSERVABILITY"] = "false"
                os.environ["LMNR_LOGGING_LEVEL"] = "critical"
                for namespace in ("browser_use", "cdp_use", "bubus", "websockets"):
                    logging.getLogger(namespace).setLevel(logging.CRITICAL)
                from browser_use import BrowserSession

                class TextObservationSession(BrowserSession):
                    async def get_browser_state_summary(self, include_screenshot=False, **kwargs):
                        # The native Agent requests screenshots even with
                        # use_vision=False. Capture pixels only at our explicit
                        # reviewed checkpoint; actor.page.screenshot is separate.
                        return await super().get_browser_state_summary(include_screenshot=False, **kwargs)

                self._session = TextObservationSession(
                    cdp_url=self._cdp_url,
                    is_local=False,
                    use_cloud=False,
                    enable_default_extensions=False,
                    captcha_solver=False,
                    disable_security=False,
                    accept_downloads=False,
                    auto_download_pdfs=False,
                    highlight_elements=False,
                    dom_highlight_elements=False,
                    cross_origin_iframes=False,
                    keep_alive=True,
                )
                # Low-level connect intentionally does not start Browser Use watchdogs/tools.
                await asyncio.wait_for(self._session.connect(), timeout=20)
                return {"running": True, "headless": self.headless, "engine": "browser-use-cdp"}
            except asyncio.CancelledError:
                await self._shutdown_unlocked()
                raise
            except BrowserError:
                await self._shutdown_unlocked()
                raise
            except Exception:  # noqa: BLE001 -- third-party errors may contain private browser data
                await self._shutdown_unlocked()
                raise BrowserError("browser_connection_failed") from None

    async def tabs(self) -> list[dict[str, Any]]:
        if not self.running:
            return []
        try:
            result = await self._session.cdp_client.send.Target.getTargets()
            return [
                {"target_id": t["targetId"], "url": t.get("url", ""), "title": t.get("title", "")}
                for t in result.get("targetInfos", [])
                if t.get("type") == "page" and not t.get("url", "").startswith("chrome-extension://")
            ]
        except Exception:  # noqa: BLE001 -- replace all CDP errors with a value-free code
            raise BrowserError("browser_disconnected") from None

    async def new_page(self, url: str) -> dict[str, Any]:
        """Trusted coordinator operation; this is never exposed as a model tool."""
        _origin(url)
        async with self._lock:
            if not self.running:
                raise BrowserError("browser_not_running")
            try:
                page = await self._session.new_page(url)
                return {"target_id": page._target_id, "url": url, "title": ""}
            except Exception:  # noqa: BLE001 -- replace all CDP errors with a value-free code
                raise BrowserError("navigation_failed") from None

    async def _context(self, target_id: str, fresh: bool = False) -> tuple[Any, int]:
        if not self.running or not isinstance(target_id, str):
            raise BrowserError("browser_not_running")
        targets = await self.tabs()
        if not any(t["target_id"] == target_id for t in targets):
            raise BrowserError("target_not_found")
        old = self._contexts.get(target_id)
        if fresh or old is None:
            from browser_use.actor.page import Page

            page = old[0] if old is not None else Page(self._session, target_id)
            session_id = await page.session_id
            tree = await self._session.cdp_client.send.Page.getFrameTree(session_id=session_id)
            frame_id = tree["frameTree"]["frame"]["id"]
            world = await self._session.cdp_client.send.Page.createIsolatedWorld(
                params={"frameId": frame_id, "worldName": "privacy-guard-local"}, session_id=session_id
            )
            self._contexts[target_id] = (page, world["executionContextId"])
        return self._contexts[target_id]

    async def _call(self, page: Any, context_id: int, function: str, *args: Any) -> dict[str, Any]:
        result = await asyncio.wait_for(
            self._session.cdp_client.send.Runtime.callFunctionOn(
                params={
                    "functionDeclaration": function,
                    "executionContextId": context_id,
                    "arguments": [{"value": arg} for arg in args],
                    "returnByValue": True,
                    "awaitPromise": False,
                },
                session_id=await page.session_id,
            ),
            timeout=15,
        )
        if result.get("exceptionDetails"):
            raise BrowserError("page_operation_failed")
        value = result.get("result", {}).get("value")
        if not isinstance(value, dict):
            raise BrowserError("invalid_browser_result")
        return value

    async def observe(self, target_id: str, include_screenshot: bool = True) -> dict[str, Any]:
        async with self._lock:
            try:
                page, context = await self._context(target_id, fresh=True)
                raw = await self._call(page, context, _OBSERVE_JS)
                _origin(raw["url"])
                screenshot = (
                    await asyncio.wait_for(page.screenshot(), timeout=15) if include_screenshot else None
                )
                # Detect mutation/navigation during screenshot; do not pair mismatched state/pixels.
                valid = await self._call(
                    page,
                    context,
                    _EXECUTE_JS,
                    {"url": raw["url"], "origin": _origin(raw["url"]), "epoch": raw["epoch"]},
                    {"type": "wait"},
                    None,
                )
                if not valid.get("ok"):
                    raise BrowserError("page_changed_during_observation")
                raw.update(target_id=target_id, screenshot=screenshot)
                self._observations[target_id] = copy.deepcopy(raw)
                return raw
            except BrowserError:
                raise
            except Exception:  # noqa: BLE001 -- replace all CDP errors with a value-free code
                self._contexts.pop(target_id, None)
                raise BrowserError("observation_failed") from None

    async def agent_session(self, target_id: str, destination: str):
        """Start Browser Use's observer/navigation watchdogs on the owned browser."""
        if not self.running:
            raise BrowserError("browser_not_running")
        self._session.browser_profile.allowed_domains = [destination + "/*"]
        await self._session.start()
        await self._session.get_or_create_cdp_session(target_id, focus=True)
        return self._session

    async def agent_field_index(self, target_id: str, backend_node_id: int) -> int:
        """Match a native Browser Use node to our isolated-world execution map."""
        async with self._lock:
            try:
                page, context = await self._context(target_id)
                resolved = await self._session.cdp_client.send.DOM.resolveNode(
                    params={"backendNodeId": backend_node_id, "executionContextId": context},
                    session_id=await page.session_id,
                )
                object_id = resolved["object"]["objectId"]
                result = await self._session.cdp_client.send.Runtime.callFunctionOn(
                    params={
                        "functionDeclaration": "function(){return globalThis.__privacyGuard?.nodes.indexOf(this)+1 || 0;}",
                        "objectId": object_id,
                        "returnByValue": True,
                    },
                    session_id=await page.session_id,
                )
                index = result.get("result", {}).get("value", 0)
                if not isinstance(index, int) or index < 1:
                    raise BrowserError("unsupported_agent_target")
                return index
            except BrowserError:
                raise
            except Exception:
                raise BrowserError("stale_observation") from None

    async def capture_privacy(self, target_id: str, secrets: list[str]):
        """Pair local DOM geometry and screenshot under the same revision check."""
        from .privacy_geometry import PRIVACY_REGIONS_JS

        async with self._lock:
            try:
                page, context = await self._context(target_id, fresh=True)
                raw = await self._call(page, context, _OBSERVE_JS)
                geometry = await self._call(page, context, PRIVACY_REGIONS_JS, secrets)
                screenshot = await asyncio.wait_for(page.screenshot(), timeout=15)
                after_geometry = await self._call(page, context, PRIVACY_REGIONS_JS, secrets)
                valid = await self._call(
                    page,
                    context,
                    _EXECUTE_JS,
                    {"url": raw["url"], "origin": _origin(raw["url"]), "epoch": raw["epoch"]},
                    {"type": "wait"},
                    None,
                )
                if not valid.get("ok") or geometry.get("url") != raw["url"] or geometry != after_geometry:
                    raise BrowserError("page_changed_during_observation")
                raw.update(target_id=target_id, screenshot=screenshot)
                self._observations[target_id] = copy.deepcopy(raw)
                return raw, geometry
            except BrowserError:
                raise
            except Exception:
                raise BrowserError("privacy_capture_failed") from None

    async def execute(
        self,
        target_id: str,
        observation: dict[str, Any],
        action: dict[str, Any],
        resolved_value: str | None = None,
    ) -> dict[str, Any]:
        generation = self._generation
        async with self._lock:
            try:
                cached = self._observations.get(target_id)
                if (
                    not cached
                    or observation.get("epoch") != cached["epoch"]
                    or observation.get("target_id") != target_id
                ):
                    raise BrowserError("stale_observation")
                if cached.get("unsupported_frames"):
                    raise BrowserError("unsupported_embedded_frames")
                kind = action.get("type") or action.get("action")
                if kind not in {"input_ref", "select_ref", "click", "scroll", "wait", "done"}:
                    raise BrowserError("unsupported_action")
                safe_action: dict[str, Any] = {"type": kind}
                if kind in {"input_ref", "select_ref", "click"}:
                    index = action.get("index", action.get("element_index", action.get("element_id")))
                    if (
                        not isinstance(index, int)
                        or isinstance(index, bool)
                        or not 1 <= index <= len(cached["fields"])
                    ):
                        raise BrowserError("invalid_target")
                    safe_action["index"] = index
                if kind in {"input_ref", "select_ref"}:
                    if not isinstance(resolved_value, str) or len(resolved_value) > 10000:
                        raise BrowserError("invalid_private_value")
                    if not isinstance(action.get("value_ref"), str) or not action["value_ref"]:
                        raise BrowserError("missing_value_reference")
                elif resolved_value is not None:
                    raise BrowserError("unexpected_private_value")
                if kind == "click":
                    # Every click requires a coordinator-issued approval in this finite MVP.
                    safe_action["approved"] = action.get("_approved") is True
                if kind == "scroll":
                    delta = action.get("delta_y", 500)
                    if (
                        not isinstance(delta, (int, float))
                        or isinstance(delta, bool)
                        or not -2000 <= delta <= 2000
                    ):
                        raise BrowserError("invalid_scroll")
                    safe_action["delta_y"] = delta
                page, context = await self._context(target_id)
                if generation != self._generation or not self.can_execute():
                    raise BrowserError("task_stopped")
                result = await self._call(
                    page,
                    context,
                    _EXECUTE_JS,
                    {"url": cached["url"], "origin": _origin(cached["url"]), "epoch": cached["epoch"]},
                    safe_action,
                    resolved_value,
                )
                if not result.get("ok"):
                    raise BrowserError(result.get("error", "action_failed"))
                if kind == "wait":
                    await asyncio.sleep(0.15)
                if kind not in {"wait", "done"}:
                    self._observations.pop(target_id, None)
                return {"ok": True, "action": kind, "target_id": target_id}
            except BrowserError:
                raise
            except asyncio.CancelledError:
                self.invalidate()
                raise
            except Exception:  # noqa: BLE001 -- replace all CDP errors with a value-free code
                self._observations.pop(target_id, None)
                raise BrowserError("action_outcome_uncertain") from None

    async def _shutdown_unlocked(self) -> None:
        self.invalidate()
        session, self._session = self._session, None
        process, self._process = self._process, None
        self._cdp_url = None
        self._contexts.clear()
        try:
            if session is not None:
                # This is our own session; disconnect must not schedule reconnection.
                session._intentional_stop = True
                try:
                    with suppress(Exception):
                        await asyncio.wait_for(session.reset(), timeout=5)
                finally:
                    session._intentional_stop = True
                    with suppress(Exception):
                        await asyncio.wait_for(session.event_bus.stop(clear=True, timeout=2), timeout=3)
        finally:
            # Dependency teardown may fail or be cancelled. The owned process must
            # still receive a termination signal; never search for or kill other browsers.
            if process is not None and process.returncode is None:
                with suppress(ProcessLookupError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except TimeoutError:
                    with suppress(ProcessLookupError):
                        process.kill()
                    with suppress(TimeoutError):
                        await asyncio.wait_for(process.wait(), timeout=2)
                except asyncio.CancelledError:
                    with suppress(ProcessLookupError):
                        process.kill()
                    with suppress(TimeoutError):
                        await asyncio.wait_for(process.wait(), timeout=2)
                    raise

    async def shutdown(self) -> None:
        self.invalidate()
        acquired = False
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=SHUTDOWN_LOCK_TIMEOUT)
            acquired = True
        except TimeoutError:
            # An unresponsive CDP read cannot keep the owned browser alive forever.
            await self._shutdown_unlocked()
        except asyncio.CancelledError:
            await self._shutdown_unlocked()
            raise
        else:
            try:
                await self._shutdown_unlocked()
            finally:
                if acquired:
                    self._lock.release()
