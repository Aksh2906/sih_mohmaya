"""Visual feedback in the isolated browser world; never dispatches page input."""

CURSOR_INSTALL_JS = r"""
  if (!globalThis.__privacyGuardCursor?.host.isConnected) {
    const host = document.createElement('div');
    host.setAttribute('aria-hidden', 'true');
    host.style.cssText = 'all:initial!important;position:fixed!important;left:0!important;top:0!important;width:0!important;height:0!important;overflow:visible!important;pointer-events:none!important;z-index:2147483647!important';
    const root = host.attachShadow({mode:'open'});
    root.innerHTML = `<style>
      :host, * { pointer-events:none!important; }
      .pointer { position:absolute;left:0;top:0;will-change:transform; }
      svg { width:28px;height:34px;overflow:visible;filter:drop-shadow(0 2px 3px #0005); }
      span { position:absolute;left:22px;top:26px;background:#2563eb;color:white;
        border:1px solid #ffffff88;border-radius:12px;padding:3px 9px;
        font:600 12px/18px system-ui,sans-serif;white-space:nowrap; }
      .ring { position:absolute;left:-15px;top:-15px;width:30px;height:30px;
        border:2px solid #3b82f6;border-radius:50%;opacity:0; }
    </style><div class="pointer"><div class="ring"></div>
      <svg viewBox="0 0 28 34"><path d="M3 2L3 26L9 21L14 31L19 28L14 18L23 18Z" fill="#2563eb" stroke="white" stroke-width="2" stroke-linejoin="round"/></svg>
      <span>Agent</span></div>`;
    document.documentElement.appendChild(host);
    const pointer = root.querySelector('.pointer');
    pointer.style.transform = 'translate(36px, 72px)';
    globalThis.__privacyGuardCursor = {host, pointer, ring:root.querySelector('.ring'), x:36, y:72};
  }
"""

CURSOR_MOVE_JS = r"""function(action) {
  const c = globalThis.__privacyGuardCursor, s = globalThis.__privacyGuard;
  if (!c?.host.isConnected || !s) return {ok:true};
  let x = innerWidth * 0.75, y = innerHeight * 0.65;
  const node = s.nodes[action.index - 1];
  if (node?.isConnected) {
    const rect = node.getBoundingClientRect();
    x = rect.x + rect.width / 2; y = rect.y + rect.height / 2;
    let win = node.ownerDocument.defaultView;
    while (win !== window && win.frameElement) {
      const frame = win.frameElement, box = frame.getBoundingClientRect();
      x += box.x + frame.clientLeft; y += box.y + frame.clientTop; win = win.parent;
    }
  }
  x = Math.max(8, Math.min(innerWidth - 35, x));
  y = Math.max(8, Math.min(innerHeight - 55, y));
  c.pointer.getAnimations().forEach(a => a.cancel());
  c.pointer.animate([
    {transform:`translate(${c.x}px, ${c.y}px)`},
    {transform:`translate(${x}px, ${y}px)`}
  ], {duration:320, easing:'ease-in-out', fill:'forwards'});
  c.ring.getAnimations().forEach(a => a.cancel());
  c.ring.animate([{opacity:0,transform:'scale(.5)'},{opacity:.8,offset:.5},
    {opacity:0,transform:'scale(1.6)'}], {delay:320,duration:160,fill:'forwards'});
  c.x = x; c.y = y;
  return {ok:true};
}"""
