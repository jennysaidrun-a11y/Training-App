// Tap a slide photo to see it full screen and zoom in: pinch or double-tap on a
// phone, scroll wheel or double-click on a computer, or the + and - buttons.
// Drag to look around once zoomed. Esc, the X or a tap outside the photo closes it.
(function () {
  const T = window.T || {};
  const label = (k, en) => T[k] || en;
  const MAX = 5;

  function open(src, alt) {
    const img = Object.assign(document.createElement("img"), { src, alt: alt || "", draggable: false });
    const stage = document.createElement("div");
    stage.className = "zoom-stage";
    stage.append(img);
    const btn = (cls, text, aria, fn) => {
      const b = Object.assign(document.createElement("button"), { type: "button", className: "zoom-btn " + cls, textContent: text });
      b.setAttribute("aria-label", aria); b.addEventListener("click", (e) => { e.stopPropagation(); fn(); });
      return b;
    };
    const box = document.createElement("div");
    box.className = "zoom-box";
    box.setAttribute("role", "dialog"); box.setAttribute("aria-modal", "true"); box.setAttribute("aria-label", label("zoom_picture", "Picture"));
    const tools = document.createElement("div");
    tools.className = "zoom-tools";
    let scale = 1, x = 0, y = 0;
    const apply = () => {
      const r = stage.getBoundingClientRect(), lim = (v, size) => Math.max(-(size * (scale - 1)) / 2, Math.min((size * (scale - 1)) / 2, v));
      x = lim(x, r.width); y = lim(y, r.height);
      img.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
      box.classList.toggle("zoomed", scale > 1);
    };
    const zoomTo = (s) => { scale = Math.max(1, Math.min(MAX, s)); if (scale === 1) x = y = 0; apply(); };
    tools.append(btn("out", "−", label("zoom_out", "Zoom out"), () => zoomTo(scale / 1.5)),
                 btn("in", "+", label("zoom_in", "Zoom in"), () => zoomTo(scale * 1.5)),
                 btn("close", "✕", label("close", "Close"), close));
    box.append(stage, tools);
    const before = document.activeElement;
    document.body.append(box);
    document.body.classList.add("zoom-open");
    tools.querySelector(".close").focus();

    function close() {
      box.remove(); document.body.classList.remove("zoom-open"); document.removeEventListener("keydown", keys);
      if (before && before.focus) before.focus();
    }
    function keys(e) {
      if (e.key === "Escape") close();
      else if (e.key === "+" || e.key === "=") zoomTo(scale * 1.5);
      else if (e.key === "-") zoomTo(scale / 1.5);
      else if (e.key === "Tab") { e.preventDefault(); const bs = [...tools.children]; bs[(bs.indexOf(document.activeElement) + 1) % bs.length].focus(); }
    }
    document.addEventListener("keydown", keys);
    box.addEventListener("click", (e) => { if (e.target === box || (e.target === stage && scale === 1)) close(); });
    stage.addEventListener("wheel", (e) => { e.preventDefault(); zoomTo(scale * (e.deltaY < 0 ? 1.2 : 1 / 1.2)); }, { passive: false });
    stage.addEventListener("dblclick", () => zoomTo(scale > 1 ? 1 : 2.5));

    // Pinch with two fingers, drag with one (pointer events work for touch, pen and mouse).
    const pts = new Map();
    let startDist = 0, startScale = 1, last = null, lastTap = 0;
    stage.addEventListener("pointerdown", (e) => {
      stage.setPointerCapture(e.pointerId);
      pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pts.size === 2) { const [a, b] = [...pts.values()]; startDist = Math.hypot(a.x - b.x, a.y - b.y); startScale = scale; }
      last = { x: e.clientX, y: e.clientY };
      if (e.pointerType === "touch" && pts.size === 1) {
        const now = Date.now(); if (now - lastTap < 300) zoomTo(scale > 1 ? 1 : 2.5); lastTap = now;
      }
    });
    stage.addEventListener("pointermove", (e) => {
      if (!pts.has(e.pointerId)) return;
      pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pts.size === 2) { const [a, b] = [...pts.values()]; zoomTo(startScale * Math.hypot(a.x - b.x, a.y - b.y) / (startDist || 1)); }
      else if (scale > 1 && last) { x += e.clientX - last.x; y += e.clientY - last.y; apply(); }
      last = { x: e.clientX, y: e.clientY };
    });
    const end = (e) => { pts.delete(e.pointerId); last = pts.size ? [...pts.values()][0] : null; };
    stage.addEventListener("pointerup", end); stage.addEventListener("pointercancel", end);
  }

  const zoomable = (t) => t instanceof HTMLImageElement && t.classList.contains("slide-img") && !t.closest(".zoom-box");
  document.addEventListener("click", (e) => { if (zoomable(e.target)) { e.preventDefault(); open(e.target.src, e.target.alt); } });
  document.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && zoomable(e.target)) { e.preventDefault(); open(e.target.src, e.target.alt); }
  });
  // Photos are added as slides change: make each one reachable by keyboard and screen reader.
  const mark = (root) => root.querySelectorAll?.("img.slide-img:not([tabindex])").forEach((img) => {
    img.tabIndex = 0; img.setAttribute("role", "button"); img.setAttribute("aria-label", label("zoom_picture", "Zoom in on the picture"));
  });
  new MutationObserver((ms) => ms.forEach((m) => m.addedNodes.forEach((n) => n.nodeType === 1 && (mark(n), n.matches?.("img.slide-img") && mark(n.parentNode)))))
    .observe(document.documentElement, { childList: true, subtree: true });
  mark(document);
})();
