// Media Studio: the app's own video and picture tools, used by the lesson editor and
// the Media Studio page (Lessons > Media studio). Everything here runs in the browser
// except making videos (ffmpeg on the server) and copying files in from Google Drive.
// Every video and picture added or made is kept in the media library until deleted.
(function () {
  const el = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else if (k === "value") n.value = v;
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids) if (kid != null && kid !== false) n.append(kid);
    return n;
  };
  let lessonName = () => "studio";      // the lesson a new file is named after
  const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
  // Every place a file can be added takes a dropped file as well as a tap (app rule).
  function dropZone(target, kind, onFile, onWrong) {
    target.addEventListener("dragover", (e) => { if (!hasFiles(e)) return; e.preventDefault(); e.stopPropagation(); target.classList.add("drop-on"); });
    target.addEventListener("dragleave", (e) => { if (!target.contains(e.relatedTarget)) target.classList.remove("drop-on"); });
    target.addEventListener("drop", (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault(); e.stopPropagation();
      target.classList.remove("drop-on");
      const fs = [...e.dataTransfer.files].filter((x) => [].concat(kind).some((k) => x.type.startsWith(k + "/")));
      if (fs.length) onFile(fs); else if (onWrong) onWrong();
    });
  }
  // Uploads one video or picture; resolves to {kind, link}. onProgress(percent) for big files.
  function upload(f, onProgress, label) {
    const image = f.type.startsWith("image/");
    return new Promise((ok, bad) => {
      const form = new FormData();
      form.append(image ? "image_file" : "video_file", f, f.name || (image ? "picture.jpg" : "video.mp4"));
      form.append("lesson", lessonName());
      if (label) form.append("label", label);
      const xhr = new XMLHttpRequest();
      xhr.open("POST", image ? "/api/manage/image" : "/api/manage/video");
      xhr.upload.onprogress = (ev) => { if (ev.lengthComputable && onProgress) onProgress(Math.round(100 * ev.loaded / ev.total)); };
      xhr.onload = () => {
        let r = {}; try { r = JSON.parse(xhr.responseText); } catch (e) { /* below */ }
        const link = image ? r.image : r.video;
        if (xhr.status === 200 && link) ok({ kind: image ? "image" : "video", link });
        else bad(new Error(r.error || r.detail || "The upload didn't work. Try again."));
      };
      xhr.onerror = () => bad(new Error("The upload didn't go through. Check the connection and try again."));
      xhr.send(form);
    });
  }
  function modal(label, ...kids) {
    const back = el("div", { class: "vedit-back", role: "dialog", "aria-modal": "true", "aria-label": label });
    const card = el("div", { class: "vedit card" }, ...kids);
    back.append(card);
    return back;
  }

  // Google Drive: paste a share link; the server copies the file into the app.
  const isDrive = (u) => /^https:\/\/(drive|docs)\.google\.com\//.test(String(u || "").trim());
  async function fromDrive(link, kind, picked) {
    const res = await fetch("/api/manage/drive", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ link: (link || "").trim(), kind, lesson: lessonName(), ...(picked || {}) }) });
    const r = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(r.error || r.detail || "That Drive file couldn't be copied. Try again.");
    return r;
  }
  // "Choose from Google Drive": Google's own sign-in and file picker, shown once it's set up
  // (window.DRIVE). Google keeps the sign-in; the app only gets a short-lived pass to copy
  // the files you pick (drive.file), which the server uses once and doesn't store.
  const DRIVE_TYPES = { video: "video/mp4,video/quicktime,video/webm,video/x-m4v", image: "image/jpeg,image/png,image/webp,image/gif" };
  let driveToken = null, driveTokenAt = 0;
  const loadScript = (src) => new Promise((ok, bad) => {
    const t = document.createElement("script"); t.src = src; t.async = true; t.onload = ok;
    t.onerror = () => bad(new Error("Google Drive couldn't be reached.")); document.head.append(t);
  });
  const driveReady = window.DRIVE ? Promise.all([loadScript("https://apis.google.com/js/api.js"), loadScript("https://accounts.google.com/gsi/client")])
    .then(() => new Promise((ok) => window.gapi.load("picker", ok))).catch(() => null) : null;
  function driveSignIn() {
    if (driveToken && Date.now() - driveTokenAt < 50 * 60 * 1000) return Promise.resolve(driveToken);
    return new Promise((ok, bad) => {
      window.google.accounts.oauth2.initTokenClient({
        client_id: window.DRIVE.clientId, scope: "https://www.googleapis.com/auth/drive.file",
        callback: (r) => { if (r.error || !r.access_token) bad(new Error("Google sign-in didn't finish.")); else { driveToken = r.access_token; driveTokenAt = Date.now(); ok(driveToken); } },
        error_callback: () => bad(new Error("Google sign-in was closed before it finished.")),
      }).requestAccessToken();   // straight from the tap, so the sign-in window isn't blocked
    });
  }
  // Opens the picker; resolves to [{id, name}] (empty if closed).
  async function pickFromDrive(kinds, multi) {
    if (!(window.google && window.google.picker && window.google.accounts)) throw new Error("Google Drive is still loading. Tap again in a moment.");
    const token = await driveSignIn();
    return new Promise((ok) => {
      const P = window.google.picker;
      const view = new P.DocsView(P.ViewId.DOCS).setMimeTypes(kinds.map((k) => DRIVE_TYPES[k]).join(",")).setIncludeFolders(true).setSelectFolderEnabled(false);
      const b = new P.PickerBuilder().addView(view).setOAuthToken(token).setDeveloperKey(window.DRIVE.apiKey).setAppId(window.DRIVE.appId)
        .setTitle("Choose from Google Drive")
        .setCallback((d) => { if (d.action === P.Action.PICKED) ok(d.docs.map((x) => ({ id: x.id, name: x.name }))); else if (d.action === P.Action.CANCEL) ok([]); });
      if (multi) b.enableFeature(P.Feature.MULTISELECT_ENABLED);
      b.build().setVisible(true);
    });
  }
  // A "Choose from Google Drive" button: picks, copies each file in, calls onCopied(kind, link).
  function driveButton(kinds, multi, onCopied, say, label = "Choose from Google Drive") {
    if (!window.DRIVE) return null;
    const btn = el("button", { type: "button", class: "ghost small drive-btn" }, label);
    btn.addEventListener("click", async () => {
      try {
        const docs = await pickFromDrive(kinds, multi);
        btn.disabled = true;
        for (const [i, d] of docs.entries()) {
          say(`Copying ${docs.length > 1 ? `${i + 1} of ${docs.length}: ` : ""}${d.name} from Google Drive…`);
          const r = await fromDrive("", kinds.length === 1 ? kinds[0] : undefined, { file_id: d.id, token: driveToken, name: d.name });
          onCopied(r.kind, r[r.kind]);
        }
        if (docs.length) say("");
      } catch (e) { say(e.message); }
      finally { btn.disabled = false; }
    });
    return btn;
  }

  // ---- Video editor -------------------------------------------------------------
  // Trim, cut out parts, add more clips or pictures (from the device or Google Drive),
  // mute, turn. The server makes the new MP4 with ffmpeg (free and open source); the
  // clips themselves aren't changed.
  const clock = (t) => { t = Math.max(0, t || 0); const m = Math.floor(t / 60), s = t - m * 60; return `${m}:${s < 10 ? "0" : ""}${s.toFixed(1)}`; };
  function keepPieces(c) {          // same rule as the server (trainer/video.py keep_pieces)
    const dur = c.duration || 0, start = Math.min(c.start || 0, dur), end = c.end == null ? dur : Math.max(start, Math.min(c.end, dur));
    let pieces = [[start, end]];
    for (const [a, b] of [...c.cuts].sort((x, y) => x[0] - y[0])) {
      if (b <= a) continue;
      pieces = pieces.flatMap(([p, q]) => (b <= p || a >= q) ? [[p, q]] : [...(a > p ? [[p, a]] : []), ...(b < q ? [[b, q]] : [])]);
    }
    return pieces.filter(([p, q]) => q - p >= 0.05);
  }
  // o: {video (the clip to start from, optional), onDone(result, keptOfOriginal)}; result is
  // {video, duration}; keptOfOriginal lists the parts kept of o.video when it stayed first.
  function openVideoEditor(o = {}) {
    const original = String(o.video || "").startsWith("/media/") ? o.video : "";
    const clips = original ? [{ video: original, start: 0, end: null, cuts: [], duration: 0 }] : [];
    let active = 0, cutFrom = null, busy = false;
    const opts = { mute: false, turn: 0, extras: new Set(["clean", "level"]) };
    const player = el("video", { class: "vedit-player", controls: true, playsinline: true, preload: "metadata" });
    const still = el("img", { class: "vedit-player vedit-still", alt: "", hidden: true });
    const list = el("div", { class: "vedit-clips" });
    const total = el("span", { class: "vedit-total" });
    const status = el("p", { class: "muted vedit-status", role: "status", "aria-live": "polite" });
    const makeBtn = el("button", { type: "button", class: "btn" }, "Make the video");
    const close = () => { if (!busy) { player.pause(); back.remove(); document.removeEventListener("keydown", onKey); } };
    const onKey = (e) => { if (e.key === "Escape") close(); };

    const say = (m) => { status.textContent = m || ""; };
    // Slice: one clip becomes two at second t (same video, each keeps its own part and cuts).
    function sliceAt(i, t) {
      const c = clips[i], end = c.end == null ? c.duration : c.end;
      if (c.still || !c.duration || t <= (c.start || 0) + 0.1 || t >= end - 0.1) return false;
      if (clips.length >= 20) { say("That's 20 clips, the most one video can have."); return true; }
      const first = { ...c, end: t, cuts: c.cuts.filter(([a]) => a < t).map(([a, b]) => [a, Math.min(b, t)]) };
      const second = { ...c, start: t, cuts: c.cuts.filter(([, b]) => b > t).map(([a, b]) => [Math.max(a, t), b]) };
      clips.splice(i, 1, first, second);
      say(`Sliced at ${clock(t)}. Each part is its own clip now: move it with ↑ ↓, remove it with ✕, or add something between.`);
      return true;
    }
    // Freeze frame: the server takes the picture at t, the picture editor draws on it, and the
    // marked-up picture goes into the video right there (the clip is sliced around it).
    async function grabFrame(i, t) {
      const c = clips[i];
      player.pause(); say("Taking the picture…");
      try {
        const res = await fetch("/api/manage/video/frame", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ video: c.video, t, lesson: lessonName() }) });
        const r = await res.json().catch(() => ({}));
        if (!res.ok || !r.image) throw new Error(r.error || "That frame couldn't be taken.");
        say("");
        openImageEditor({ src: r.image, label: `${o.label || "Video"} frame at ${clock(t)}`, onDone: (link) => {
          if (clips.length >= 19) { say("That's too many clips for one video. Remove one first."); return; }
          const at = sliceAt(i, t) ? i + 1 : (t <= (c.start || 0) + 0.1 ? i : i + 1);
          clips.splice(at, 0, { video: link, still: true, seconds: 4, duration: 4, start: 0, end: null, cuts: [] });
          load(at);
          say("Your drawing shows there for 4 seconds; change the seconds on the picture if you like.");
        } });
      } catch (e) { say(e.message); }
    }

    function load(i) {
      active = i; cutFrom = null;
      const c = clips[i];
      player.hidden = !c || !!c.still; still.hidden = !c || !c.still;
      if (!c) { player.pause(); drawClips(); return; }
      if (c.still) { player.pause(); still.src = c.video; drawClips(); return; }
      if (player.getAttribute("src") !== c.video) player.src = c.video;
      const end = c.end == null ? Infinity : c.end;
      if (player.currentTime < (c.start || 0) || player.currentTime > end) player.currentTime = c.start || 0;
      sample.hidden = true; sample.pause();
      if (!c.duration && !c.asked) {   // the server reads the length too, in case this browser can't
        c.asked = true;
        fetch("/api/manage/video/info?v=" + encodeURIComponent(c.video)).then((r) => r.json()).then((r) => {
          if (r.duration && !c.duration) { c.duration = r.duration; drawClips(); }
        }).catch(() => {});
      }
      drawClips();
    }
    player.addEventListener("error", () => {
      status.textContent = "This browser can't show this video, so you can't pick times here. Safari and Chrome play MP4 best.";
    });
    player.addEventListener("loadedmetadata", () => {
      const c = clips[active];
      if (!c || c.still) return;
      if (!c.duration) { c.duration = player.duration || 0; drawClips(); }
    });
    player.addEventListener("timeupdate", () => {
      if (!clips[active] || clips[active].still) return;
      const c = clips[active], head = list.querySelector(`[data-clip="${active}"] .vedit-head`);
      if (head && c.duration) head.style.left = (100 * player.currentTime / c.duration) + "%";
      // Previewing: skip over the parts that will be cut.
      if (!player.paused && c.duration) {
        const t = player.currentTime, keep = keepPieces(c);
        if (keep.length && !keep.some(([a, b]) => t >= a - 0.05 && t < b)) {
          const next = keep.find(([a]) => a > t);
          if (next) player.currentTime = next[0]; else player.pause();
        }
      }
    });
    function bar(c, i) {
      const b = el("div", { class: "vedit-bar", title: "Tap to jump there" });
      if (c.duration) {
        for (const [a, q] of keepPieces(c)) b.append(el("span", { class: "keep", style: `left:${100 * a / c.duration}%;width:${100 * (q - a) / c.duration}%` }));
        if (i === active) b.append(el("span", { class: "vedit-head" }));
      }
      b.addEventListener("click", (e) => {
        if (i !== active) { load(i); return; }
        if (c.still) return;
        const r = b.getBoundingClientRect();
        if (c.duration) player.currentTime = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * c.duration;
      });
      return b;
    }
    function drawClips() {
      makeBtn.disabled = busy || !clips.length;
      if (!clips.length) list.replaceChildren(el("p", { class: "muted" }, "Add a video or a picture below to start."));
      else list.replaceChildren(...clips.map((c, i) => {
        const here = () => (i === active ? player.currentTime : 0);
        const secs = el("input", { type: "number", min: "0.5", max: "120", step: "0.5", value: c.seconds, class: "short", "aria-label": "Seconds to show the picture" });
        secs.addEventListener("change", () => { c.seconds = c.duration = Math.min(120, Math.max(0.5, +secs.value || 5)); setTimeout(drawClips); });
        const tools = c.still ? el("div", { class: "row vedit-tools" }, el("label", {}, "Show this picture for ", secs, " seconds"),
            el("button", { type: "button", class: "ghost small", onclick: () => openImageEditor({ src: c.video, onDone: (link) => { c.video = link; load(i); } }) }, "✏️ Mark up"))
          : i === active ? el("div", { class: "row vedit-tools" },
          el("button", { type: "button", class: "ghost small", onclick: () => { c.start = here(); if (c.end != null && c.end < c.start) c.end = null; drawClips(); } }, "⇤ Start here"),
          el("button", { type: "button", class: "ghost small", onclick: () => { c.end = Math.max(here(), c.start || 0); drawClips(); } }, "End here ⇥"),
          cutFrom == null
            ? el("button", { type: "button", class: "ghost small", onclick: () => { cutFrom = here(); drawClips(); } }, "✂ Cut from here…")
            : el("button", { type: "button", class: "small", onclick: () => { const a = Math.min(cutFrom, here()), b = Math.max(cutFrom, here()); if (b - a >= 0.1) c.cuts.push([a, b]); cutFrom = null; drawClips(); } }, `…to here (from ${clock(cutFrom)})`),
          cutFrom != null ? el("button", { type: "button", class: "ghost small", onclick: () => { cutFrom = null; drawClips(); } }, "Never mind") : null,
          el("button", { type: "button", class: "ghost small", title: "Split this clip in two here, so you can move, remove or put something between the parts",
            onclick: () => { if (!sliceAt(i, here())) say("Stop the video somewhere in the middle of the clip, then slice."); else load(i + 1); } }, "⧸ Slice here"),
          el("button", { type: "button", class: "ghost small", title: "Take this picture from the video, draw on it, and show it here for a few seconds",
            onclick: () => grabFrame(i, here()) }, "📸 Draw on this frame"))
          : el("button", { type: "button", class: "ghost small", onclick: () => load(i) }, "Edit this clip");
        const cuts = c.cuts.length ? el("div", { class: "vedit-cuts" }, ...c.cuts.map(([a, b], k) => el("span", { class: "pill" }, `Cut ${clock(a)}–${clock(b)} `,
          el("button", { type: "button", class: "x", "aria-label": "Put this part back", onclick: () => { c.cuts.splice(k, 1); drawClips(); } }, "✕")))) : null;
        const kept = keepPieces(c).reduce((n, [a, b]) => n + b - a, 0);
        return el("div", { class: "vedit-clip" + (i === active ? " on" : ""), "data-clip": i },
          el("div", { class: "row" },
            el("b", {}, c.still ? `Picture${clips.length > 1 ? ` (clip ${i + 1})` : ""}` : clips.length > 1 ? `Clip ${i + 1}` : "Your video"),
            el("span", { class: "muted" }, c.still ? `${clock(c.seconds)} long` : c.duration ? `Keeps ${clock(c.start || 0)} to ${clock(c.end == null ? c.duration : c.end)} · ${clock(kept)} long` : "Loading…"),
            el("span", { class: "grow" }),
            i > 0 ? el("button", { type: "button", class: "ghost small", "aria-label": "Move clip earlier", onclick: () => { clips.splice(i - 1, 0, clips.splice(i, 1)[0]); load(i - 1); } }, "↑") : null,
            i < clips.length - 1 ? el("button", { type: "button", class: "ghost small", "aria-label": "Move clip later", onclick: () => { clips.splice(i + 1, 0, clips.splice(i, 1)[0]); load(i + 1); } }, "↓") : null,
            clips.length > 1 || !original ? el("button", { type: "button", class: "ghost small danger", "aria-label": "Remove clip", onclick: () => { clips.splice(i, 1); load(Math.max(0, Math.min(active, clips.length - 1))); } }, "✕") : null),
          bar(c, i), tools, cuts);
      }));
      const all = clips.reduce((n, c) => n + keepPieces(c).reduce((m, [a, b]) => m + b - a, 0), 0);
      total.textContent = clips.length && clips.every((c) => c.duration) ? `New video: ${clock(all)} long` : "";
    }

    // Another clip or picture at the end: tap to choose or drag one onto the box (app rule),
    // or paste a Google Drive link.
    const pick = el("input", { type: "file", accept: "video/mp4,video/quicktime,video/webm,video/x-m4v,video/*,image/jpeg,image/png,image/webp,image/gif" });
    const addBox = el("label", { class: "pic-drop upload vedit-add" }, pick, el("span", {}, "+ Add a video or picture at the end"),
      el("span", { class: "help" }, "Tap to choose one, or drag it here. A picture shows for 5 seconds; you can change that."));
    pick.addEventListener("change", () => { if (pick.files[0]) addClip(pick.files[0]); pick.value = ""; });
    dropZone(addBox, ["video", "image"], (fs) => fs.forEach(addClip), () => { status.textContent = "That isn't a video or picture. MP4 or JPG works best."; });
    const pushClip = (kind, link) => {
      clips.push(kind === "image" ? { video: link, still: true, seconds: 5, duration: 5, start: 0, end: null, cuts: [] }
        : { video: link, start: 0, end: null, cuts: [], duration: 0 });
      status.textContent = ""; load(clips.length - 1);
    };
    const driveLink = el("input", { placeholder: "Paste a Google Drive link to a video or picture", "aria-label": "Google Drive link" });
    const driveBtn = el("button", { type: "button", class: "ghost small" }, "Add from Google Drive");
    driveBtn.addEventListener("click", async () => {
      if (!driveLink.value.trim()) { driveLink.focus(); return; }
      driveBtn.disabled = true; status.textContent = "Copying from Google Drive… a big video can take a few minutes.";
      try { const r = await fromDrive(driveLink.value); pushClip(r.kind, r[r.kind]); driveLink.value = ""; }
      catch (e) { status.textContent = e.message; }
      finally { driveBtn.disabled = false; }
    });
    const pickBtn = driveButton(["video", "image"], true, (k, v) => pushClip(k, v), (m) => { status.textContent = m; });
    if (pickBtn) { pickBtn.className = "btn blue small drive-btn"; driveBtn.textContent = "Add link"; driveLink.placeholder = "or paste a Google Drive link"; }
    const driveRow = el("div", { class: "vedit-drive-wrap" },
      el("div", { class: "row" }, pickBtn, libraryButton(["video", "image"], true, (picked) => picked.forEach((p) => pushClip(p.kind, p.link)))),
      el("div", { class: "row vedit-drive" }, driveLink, driveBtn));
    function addClip(f) {
      if (f.type.startsWith("image/")) {
        const form = new FormData();
        form.append("image_file", f);
        form.append("lesson", lessonName());
        status.textContent = `Uploading ${f.name}…`;
        fetch("/api/manage/image", { method: "POST", body: form }).then(async (res) => {
          const r = await res.json().catch(() => ({}));
          if (!res.ok || !r.image) throw new Error(r.error || r.detail || "That picture couldn't be added.");
          pushClip("image", r.image);
        }).catch((e) => { status.textContent = e.message === "Failed to fetch" ? "The upload didn't go through. Check the connection and try again." : e.message; });
        return;
      }
      const form = new FormData();
      form.append("video_file", f);
      form.append("lesson", lessonName());
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/manage/video");
      xhr.upload.onprogress = (ev) => { if (ev.lengthComputable) status.textContent = `Uploading ${f.name}: ${Math.round(100 * ev.loaded / ev.total)}%`; };
      xhr.onload = () => {
        let r = {}; try { r = JSON.parse(xhr.responseText); } catch (e) { /* below */ }
        if (xhr.status === 200 && r.video) pushClip("video", r.video);
        else status.textContent = r.error || r.detail || "The upload didn't work. Try again.";
      };
      xhr.onerror = () => { status.textContent = "The upload didn't go through. Check the connection and try again."; };
      xhr.send(form);
    }

    // Effects: toggle buttons right under the player. Each one changes the preview at once
    // (picture filters, turning and sound filters in the browser), so you can see and hear it
    // before making the video. "Exact preview" makes 5 real seconds on the server with ffmpeg.
    const EFFECTS = [["mute", "🔇 No sound", "Removes all sound"],
      ["clean", "🎧 Clean up sound", "Less background noise, hum and hiss"],
      ["level", "🔊 Even volume", "Quiet and loud parts end up the same loudness"],
      ["fade", "🌅 Fade in and out", "A gentle start and finish"],
      ["bright", "☀️ Brighter, sharper", "Helps dim factory lighting"],
      ["steady", "🤚 Steady shaky video", "For phone videos filmed by hand. Shows in the exact preview and the final video only."]];
    let compare = false, audio = null;
    const fx = {};
    const isOn = (k) => (k === "mute" ? opts.mute : opts.extras.has(k));
    function soundChain() {        // built on the first tap (browsers need a tap before sound filters)
      if (audio || !window.AudioContext) return;
      try {
        const ctx = new AudioContext(), src = ctx.createMediaElementSource(player);
        const hp = ctx.createBiquadFilter(), lp = ctx.createBiquadFilter(), comp = ctx.createDynamicsCompressor(), gain = ctx.createGain();
        hp.type = "highpass"; lp.type = "lowpass";
        src.connect(hp).connect(lp).connect(comp).connect(gain).connect(ctx.destination);
        audio = { ctx, hp, lp, comp, gain };
      } catch (e) { audio = null; }
    }
    function applyPreview() {
      const on = (k) => !compare && isOn(k);
      const deg = opts.turn && !compare ? opts.turn : 0;
      const t = deg ? `rotate(${deg}deg)${deg % 180 ? " scale(0.56)" : ""}` : "";
      player.style.transform = still.style.transform = t;
      player.style.filter = still.style.filter = on("bright") ? "brightness(1.08) contrast(1.1) saturate(1.15)" : "";
      player.muted = on("mute");
      if (typeof voSync === "function" && fx.voice) voSync();
      if (audio) {
        audio.hp.frequency.value = on("clean") ? 80 : 10;
        audio.lp.frequency.value = on("clean") ? 12000 : 22000;
        const lv = on("level");
        audio.comp.threshold.value = lv ? -30 : 0; audio.comp.ratio.value = lv ? 6 : 1;
        audio.gain.gain.value = lv ? 1.8 : 1;
        if (audio.ctx.state === "suspended") audio.ctx.resume();
      }
      for (const [k, btn] of Object.entries(fx)) if (k !== "voice") btn.setAttribute("aria-pressed", isOn(k) ? "true" : "false");
      fx.clean.disabled = fx.level.disabled = opts.mute;
      compareBtn.textContent = compare ? "👁 Showing: original" : "👁 Showing: with effects";
      compareBtn.setAttribute("aria-pressed", compare ? "true" : "false");
    }
    for (const [k, label, help] of EFFECTS) {
      fx[k] = el("button", { type: "button", class: "fx-toggle", title: help, "aria-pressed": "false", onclick: () => {
        if (k === "mute") opts.mute = !opts.mute; else if (opts.extras.has(k)) opts.extras.delete(k); else opts.extras.add(k);
        soundChain(); compare = false; applyPreview();
        say(`${label.replace(/^\S+ /, "")}: ${isOn(k) ? "on" : "off"}. ${k === "steady" ? "Tap Exact preview to see it." : "Play to see and hear it."}`);
      } }, label);
    }
    const turn = el("select", { "aria-label": "Turn the video" },
      ...[[0, "↻ Don't turn"], [90, "↻ Turn right"], [180, "↻ Upside down"], [270, "↺ Turn left"]].map(([v, t]) => el("option", { value: v }, t)));
    turn.addEventListener("change", () => { opts.turn = +turn.value; compare = false; applyPreview(); });
    const compareBtn = el("button", { type: "button", class: "ghost small", title: "Switch between your effects and the original to compare",
      onclick: () => { soundChain(); compare = !compare; applyPreview(); } });
    // Fade: dim the start of the first clip and the end of the last one while playing.
    player.addEventListener("timeupdate", () => {
      const c = clips[active];
      if (!c || c.still) return;
      let k = 1;
      if (!compare && opts.extras.has("fade")) {
        const end = c.end == null ? c.duration : c.end, t = player.currentTime;
        if (active === 0) k = Math.min(k, Math.max(0, (t - (c.start || 0)) / 0.6));
        if (active === clips.length - 1 && end) k = Math.min(k, Math.max(0, (end - t) / 0.6));
      }
      player.style.opacity = k;
      if (audio) audio.gain.gain.value = (opts.extras.has("level") && !compare ? 1.8 : 1) * k;
    });
    const sample = el("video", { class: "vedit-sample", controls: true, playsinline: true, hidden: true });
    const sampleBtn = el("button", { type: "button", class: "ghost small", title: "Makes 5 real seconds from where the video is stopped, with every effect, exactly as the final video will be" }, "▶ Exact preview (5 s)");
    sampleBtn.addEventListener("click", async () => {
      const c = clips[active];
      if (!c || busy) return;
      const t = c.still ? 0 : player.currentTime;
      sampleBtn.disabled = true; say("Making a 5-second preview with your effects…");
      try {
        const res = await fetch("/api/manage/video/edit", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ preview: true, mute: opts.mute, turn: opts.turn, extras: [...opts.extras],
            clips: [c.still ? { video: c.video, seconds: Math.min(5, c.seconds || 5) } : { video: c.video, start: t, end: Math.min(t + 5, c.end == null ? c.duration : c.end) }] }) });
        const r = await res.json().catch(() => ({}));
        if (!res.ok || !r.video) throw new Error(r.error || "The preview couldn't be made.");
        player.pause(); sample.hidden = false; sample.src = r.video; sample.play().catch(() => {});
        say("This is exactly how it will look and sound.");
      } catch (e) { say(e.message); } finally { sampleBtn.disabled = false; }
    });
    // Voiceover: a natural voice (Piper, free, on the server) reads a script over the whole video,
    // replacing its sound or over it with the sound quieter. It plays along in the preview.
    const vo = { audio: "", mode: "replace", on: false };
    const voScript = el("textarea", { rows: "4", placeholder: "Type or paste what the voice should say", "aria-label": "Voiceover script" });
    const voVoice = el("select", { "aria-label": "Voice" }), voSpeed = el("select", { "aria-label": "Speed" },
      ...[["slow", "Slower"], ["normal", "Normal speed"], ["fast", "Faster"]].map(([v, t]) => el("option", { value: v, selected: v === "normal" ? true : null }, t)));
    fetch("/api/speech/voices").then((r) => r.json()).then((r) => {
      voVoice.replaceChildren(...r.voices.map((v) => el("option", { value: v.id }, v.label)));
      if (!r.ready) voMake.disabled = true, say("The voice isn't set up on this copy of the app yet.");
    }).catch(() => {});
    const voAudio = el("audio", { controls: true, hidden: true });
    const voMake = el("button", { type: "button", class: "small" }, "Make the voice");
    const voFrom = el("button", { type: "button", class: "ghost small", title: "Writes down what's said in this clip, so you can fix it up and have the voice read it clearly" }, "📝 Start from what's said in the clip");
    const voReplace = el("button", { type: "button", class: "fx-toggle", "aria-pressed": "true" }, "Replace the video's sound");
    const voMix = el("button", { type: "button", class: "fx-toggle", "aria-pressed": "false" }, "Play over it (sound quieter)");
    const voPanel = el("div", { class: "vo-panel", hidden: true }, voScript,
      el("div", { class: "row" }, voVoice, voSpeed, voMake, voFrom), voAudio,
      el("div", { class: "row fx-row", hidden: true }, voReplace, voMix));
    fx.voice = el("button", { type: "button", class: "fx-toggle", "aria-pressed": "false", title: "A voice reads your script over the video" }, "🎙 Voiceover");
    fx.voice.addEventListener("click", () => {
      voPanel.hidden = false;
      if (vo.audio) { vo.on = !vo.on; voSync(); say(`Voiceover: ${vo.on ? "on" : "off"}. Play to hear it with the video.`); }
      else voScript.focus();
      fx.voice.setAttribute("aria-pressed", vo.on ? "true" : "false");
    });
    const setMode = (m) => { vo.mode = m; voReplace.setAttribute("aria-pressed", m === "replace" ? "true" : "false"); voMix.setAttribute("aria-pressed", m === "mix" ? "true" : "false"); voSync(); };
    voReplace.addEventListener("click", () => setMode("replace"));
    voMix.addEventListener("click", () => setMode("mix"));
    voMake.addEventListener("click", async () => {
      if (!voScript.value.trim()) { voScript.focus(); return; }
      voMake.disabled = true; say("Making the voice…");
      try {
        const res = await fetch("/api/speech", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: voScript.value, voice: voVoice.value, speed: voSpeed.value }) });
        const r = await res.json();
        if (!res.ok) throw new Error(r.error || "The voice couldn't be made.");
        vo.audio = r.audio; vo.on = true; voAudio.src = r.audio; voAudio.hidden = false;
        voPanel.lastChild.hidden = false; fx.voice.setAttribute("aria-pressed", "true"); voSync();
        say(`Voice ready (${clock(r.duration)}). Play the video to hear them together; it goes in when you make the video.`);
      } catch (e) { say(e.message); } finally { voMake.disabled = false; }
    });
    voFrom.addEventListener("click", async () => {
      const c = clips[active];
      if (!c || c.still) { say("Pick a video clip first."); return; }
      voFrom.disabled = true; say("Writing down what's said… this takes about as long as the clip.");
      try {
        const res = await fetch("/api/manage/transcribe", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ video: c.video }) });
        const r = await res.json();
        if (!res.ok) throw new Error(r.error || "That couldn't be written down.");
        voScript.value = r.text || ""; say(r.text ? "Here's what's said. Fix anything, then Make the voice." : "No speech was found in this clip.");
      } catch (e) { say(e.message); } finally { voFrom.disabled = false; }
    });
    // Where the player is on the new video's timeline (kept parts of earlier clips first).
    function timelinePos() {
      let t = 0;
      for (let k = 0; k < active; k++) t += keepPieces(clips[k]).reduce((n, [a, b]) => n + b - a, 0);
      const c = clips[active];
      if (!c || c.still) return t;
      for (const [a, b] of keepPieces(c)) { if (player.currentTime >= b) t += b - a; else { t += Math.max(0, player.currentTime - a); break; } }
      return t;
    }
    function voSync() {
      const live = vo.on && vo.audio && !compare;
      player.volume = live && vo.mode === "mix" ? 0.25 : 1;
      player.muted = (!compare && opts.mute) || (live && vo.mode === "replace");
      if (!live) voAudio.pause();
    }
    player.addEventListener("play", () => { if (vo.on && vo.audio && !compare) { voAudio.currentTime = timelinePos(); voAudio.play().catch(() => {}); } });
    player.addEventListener("pause", () => voAudio.pause());
    player.addEventListener("seeked", () => { if (vo.on && vo.audio && !player.paused) voAudio.currentTime = timelinePos(); });
    const effectsBar = el("div", { class: "vedit-fx" },
      el("div", { class: "row fx-row" }, ...Object.values(fx), turn),
      voPanel,
      el("div", { class: "row" }, compareBtn, sampleBtn), sample);

    makeBtn.addEventListener("click", async () => {
      if (busy || !clips.length || !clips.every((c) => c.duration)) return;
      busy = true; makeBtn.disabled = true; player.pause();
      status.textContent = "Making the video… this can take a minute or two for a long video. Keep this open.";
      try {
        const res = await fetch("/api/manage/video/edit", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ lesson: lessonName(), label: o.label ? `${o.label} (edited)` : "", mute: opts.mute, turn: opts.turn, extras: [...opts.extras],
            ...(vo.on && vo.audio ? { voiceover: { audio: vo.audio, mode: vo.mode } } : {}),
            clips: clips.map((c) => ({ video: c.video, start: c.start || 0, end: c.end, cuts: c.cuts, ...(c.still ? { seconds: c.seconds } : {}) })) }) });
        const r = await res.json().catch(() => ({}));
        if (!res.ok || !r.video) throw new Error(r.error || r.detail || "The video couldn't be made. Try again.");
        const kept = clips[0] && clips[0].video === original && !clips[0].still ? keepPieces(clips[0]) : null;
        busy = false; close();
        if (o.onDone) o.onDone(r, kept);
      } catch (e) {
        busy = false; makeBtn.disabled = false; status.textContent = e.message;
      }
    });

    const back = el("div", { class: "vedit-back", role: "dialog", "aria-modal": "true", "aria-label": "Edit video" },
      el("div", { class: "vedit card" },
        el("div", { class: "row vedit-top" }, el("h2", {}, clips.length ? "Edit video" : "Make a video"), el("span", { class: "grow" }),
          el("button", { type: "button", class: "ghost small", onclick: close }, "Close")),
        el("div", { class: "vedit-stage" }, player, still),
        effectsBar,
        el("p", { class: "help" }, "Stop the video where you want, then tap a button: trim, cut out a part, slice it in two, or draw on that frame. The gold bar is what stays."),
        list, addBox, driveRow,
        el("div", { class: "row vedit-foot" }, total, el("span", { class: "grow" }),
          el("button", { type: "button", class: "ghost", onclick: close }, "Cancel"), makeBtn),
        status));
    back.addEventListener("click", (e) => { if (e.target === back) close(); });
    document.addEventListener("keydown", onKey);
    document.body.append(back);
    applyPreview();
    load(0);
  }

  // ---- Picture editor -------------------------------------------------------------
  // Marker, highlighter, arrow, box, circle, numbered markers, text, blur (hide faces or
  // labels) and crop, turn, undo. Works with a finger, a pencil or a mouse, and saves
  // a new picture (the original stays in the library).
  const COLORS = [["#e02424", "Red"], ["#f5c518", "Yellow"], ["#16a34a", "Green"], ["#2563eb", "Blue"], ["#111111", "Black"], ["#ffffff", "White"]];
  const TOOLS = [["pen", "✏️", "Marker"], ["hi", "🖍️", "Highlighter"], ["arrow", "➜", "Arrow"], ["box", "▭", "Box"], ["ring", "◯", "Circle"],
    ["num", "①", "Numbers"], ["text", "T", "Text"], ["blur", "▦", "Blur"], ["crop", "⛶", "Crop"]];
  function drawOp(c, op, base, u) {
    const w = u * [1.5, 3, 5][op.size];
    c.save();
    c.lineCap = c.lineJoin = "round";
    c.strokeStyle = c.fillStyle = op.color;
    c.lineWidth = w;
    const [a, b] = [op.a, op.b || op.a];
    if (op.tool === "pen" || op.tool === "hi") {
      if (op.tool === "hi") { c.globalAlpha = 0.35; c.lineWidth = w * 4; c.lineCap = "butt"; }
      c.beginPath();
      op.pts.forEach(([x, y], i) => (i ? c.lineTo(x, y) : c.moveTo(x, y)));
      if (op.pts.length === 1) c.lineTo(op.pts[0][0] + 0.1, op.pts[0][1]);
      c.stroke();
    } else if (op.tool === "arrow") {
      const ang = Math.atan2(b[1] - a[1], b[0] - a[0]), head = Math.max(w * 4, 12);
      c.beginPath(); c.moveTo(a[0], a[1]); c.lineTo(b[0] - Math.cos(ang) * head * 0.6, b[1] - Math.sin(ang) * head * 0.6); c.stroke();
      c.beginPath(); c.moveTo(b[0], b[1]);
      c.lineTo(b[0] - head * Math.cos(ang - 0.45), b[1] - head * Math.sin(ang - 0.45));
      c.lineTo(b[0] - head * Math.cos(ang + 0.45), b[1] - head * Math.sin(ang + 0.45));
      c.closePath(); c.fill();
    } else if (op.tool === "box") {
      c.strokeRect(Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1]));
    } else if (op.tool === "ring") {
      c.beginPath();
      c.ellipse((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, Math.abs(b[0] - a[0]) / 2 || 1, Math.abs(b[1] - a[1]) / 2 || 1, 0, 0, Math.PI * 2);
      c.stroke();
    } else if (op.tool === "num") {
      const r = u * [5, 8, 12][op.size];
      c.beginPath(); c.arc(a[0], a[1], r, 0, Math.PI * 2); c.fill();
      c.lineWidth = Math.max(2, r / 6); c.strokeStyle = "#fff"; c.stroke();
      c.fillStyle = op.color === "#ffffff" || op.color === "#f5c518" ? "#111" : "#fff";
      c.font = `bold ${Math.round(r * 1.2)}px system-ui, sans-serif`; c.textAlign = "center"; c.textBaseline = "middle";
      c.fillText(String(op.n), a[0], a[1] + r * 0.05);
    } else if (op.tool === "text") {
      const px = Math.round(u * [7, 11, 17][op.size]);
      c.font = `bold ${px}px system-ui, sans-serif`; c.textBaseline = "top";
      c.lineWidth = Math.max(3, px / 5);
      c.strokeStyle = op.color === "#111111" ? "#fff" : "#111";
      op.text.split("\n").forEach((line, i) => { c.strokeText(line, a[0], a[1] + i * px * 1.2); c.fillText(line, a[0], a[1] + i * px * 1.2); });
    } else if (op.tool === "blur") {
      const x = Math.min(a[0], b[0]), y = Math.min(a[1], b[1]), bw = Math.abs(b[0] - a[0]), bh = Math.abs(b[1] - a[1]);
      if (bw > 2 && bh > 2) {
        const block = Math.max(6, u * 4), tiny = document.createElement("canvas");
        tiny.width = Math.max(1, Math.round(bw / block)); tiny.height = Math.max(1, Math.round(bh / block));
        tiny.getContext("2d").drawImage(base, x, y, bw, bh, 0, 0, tiny.width, tiny.height);
        c.imageSmoothingEnabled = false;
        c.drawImage(tiny, 0, 0, tiny.width, tiny.height, x, y, bw, bh);
      }
    }
    c.restore();
  }
  // o: {src, label (optional, names the new copy), onDone(link)}
  function openImageEditor(o) {
    let base = null, ops = [], tool = "pen", color = COLORS[0][0], size = 1, nextNum = 1, drag = null, busy = false, changed = false;
    const history = [];
    const view = el("canvas", { class: "iedit-canvas", "aria-label": "Picture to mark up" });
    const ctx = view.getContext("2d");
    const status = el("p", { class: "muted vedit-status", role: "status", "aria-live": "polite" });
    const unit = () => Math.max(base.width, base.height) / 400;
    const snapshot = () => { history.push({ ops: ops.slice(), base, nextNum }); changed = true; undoBtn.disabled = false; };
    function bake() {                       // the picture with every drawing on it
      const c = document.createElement("canvas");
      c.width = base.width; c.height = base.height;
      const g = c.getContext("2d");
      g.drawImage(base, 0, 0);
      for (const op of ops) drawOp(g, op, base, unit());
      return c;
    }
    function redraw() {
      if (!base) return;
      if (view.width !== base.width || view.height !== base.height) { view.width = base.width; view.height = base.height; }
      ctx.clearRect(0, 0, view.width, view.height);
      ctx.drawImage(base, 0, 0);
      for (const op of ops) drawOp(ctx, op, base, unit());
      if (drag && drag.tool !== "crop") drawOp(ctx, drag, base, unit());
      if (drag && drag.tool === "crop" && drag.b) {
        const [a, b] = [drag.a, drag.b], x = Math.min(a[0], b[0]), y = Math.min(a[1], b[1]), w = Math.abs(b[0] - a[0]), h = Math.abs(b[1] - a[1]);
        ctx.save(); ctx.fillStyle = "rgba(0,0,0,.5)";
        ctx.fillRect(0, 0, view.width, y); ctx.fillRect(0, y + h, view.width, view.height - y - h);
        ctx.fillRect(0, y, x, h); ctx.fillRect(x + w, y, view.width - x - w, h);
        ctx.strokeStyle = "#fff"; ctx.lineWidth = Math.max(2, unit()); ctx.setLineDash([unit() * 4, unit() * 3]); ctx.strokeRect(x, y, w, h);
        ctx.restore();
      }
    }
    const img = new Image();
    img.onload = () => {
      const k = Math.min(1, 2000 / Math.max(img.naturalWidth, img.naturalHeight));
      base = document.createElement("canvas");
      base.width = Math.max(1, Math.round(img.naturalWidth * k)); base.height = Math.max(1, Math.round(img.naturalHeight * k));
      base.getContext("2d").drawImage(img, 0, 0, base.width, base.height);
      redraw();
    };
    img.onerror = () => { status.textContent = "That picture couldn't be opened."; };
    img.src = o.src;

    const at = (e) => {
      const r = view.getBoundingClientRect();
      return [Math.max(0, Math.min(view.width, (e.clientX - r.left) * view.width / r.width)),
              Math.max(0, Math.min(view.height, (e.clientY - r.top) * view.height / r.height))];
    };
    view.addEventListener("pointerdown", (e) => {
      if (!base || busy) return;
      e.preventDefault();
      const p = at(e);
      if (tool === "num") { snapshot(); ops.push({ tool, color, size, a: p, n: nextNum++ }); redraw(); return; }
      if (tool === "text") {
        const t = (window.prompt("Text to put on the picture") || "").trim();
        if (t) { snapshot(); ops.push({ tool, color, size, a: p, text: t }); redraw(); }
        return;
      }
      view.setPointerCapture(e.pointerId);
      drag = tool === "pen" || tool === "hi" ? { tool, color, size, pts: [p] } : { tool, color, size, a: p, b: p };
      redraw();
    });
    view.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const p = at(e);
      if (drag.pts) drag.pts.push(p); else drag.b = p;
      redraw();
    });
    const finish = () => {
      if (!drag) return;
      const d = drag; drag = null;
      const big = d.pts || Math.hypot(d.b[0] - d.a[0], d.b[1] - d.a[1]) > unit() * 3;
      if (d.tool === "crop") {
        const x = Math.round(Math.min(d.a[0], d.b[0])), y = Math.round(Math.min(d.a[1], d.b[1]));
        const w = Math.round(Math.abs(d.b[0] - d.a[0])), h = Math.round(Math.abs(d.b[1] - d.a[1]));
        if (w > 10 && h > 10) {
          snapshot();
          const flat = bake(), c = document.createElement("canvas");
          c.width = w; c.height = h;
          c.getContext("2d").drawImage(flat, x, y, w, h, 0, 0, w, h);
          base = c; ops = [];
          setTool("pen");
        }
      } else if (big) { snapshot(); ops.push(d); }
      redraw();
    };
    view.addEventListener("pointerup", finish);
    view.addEventListener("pointercancel", () => { drag = null; redraw(); });

    function turn() {
      if (!base) return;
      snapshot();
      const flat = bake(), c = document.createElement("canvas");
      c.width = flat.height; c.height = flat.width;
      const g = c.getContext("2d");
      g.translate(c.width, 0); g.rotate(Math.PI / 2); g.drawImage(flat, 0, 0);
      base = c; ops = [];
      redraw();
    }
    const undoBtn = el("button", { type: "button", class: "ghost small", disabled: true, onclick: () => {
      const h = history.pop();
      if (!h) return;
      ops = h.ops; base = h.base; nextNum = h.nextNum;
      undoBtn.disabled = !history.length;
      redraw();
    } }, "↶ Undo");
    const toolBtns = TOOLS.map(([key, ico, label]) => el("button", { type: "button", class: "tool", "data-tool": key, title: label,
      "aria-pressed": key === tool ? "true" : "false", onclick: () => setTool(key) }, el("span", { "aria-hidden": "true" }, ico), el("small", {}, label)));
    function setTool(key) {
      tool = key;
      toolBtns.forEach((b) => b.setAttribute("aria-pressed", b.dataset.tool === key ? "true" : "false"));
      status.textContent = { crop: "Drag over the part to keep.", blur: "Drag over faces, names or labels to hide them.",
        num: "Tap to drop numbers in order: 1, 2, 3…", text: "Tap where the text should go." }[key] || "";
      if (key === "hi" && color === COLORS[0][0]) setColor(COLORS[1][0]);
    }
    const swatches = COLORS.map(([c, name]) => el("button", { type: "button", class: "swatch", style: `background:${c}`, title: name, "aria-label": name,
      "aria-pressed": c === color ? "true" : "false", onclick: () => setColor(c) }));
    function setColor(c) { color = c; swatches.forEach((s) => s.setAttribute("aria-pressed", s.title === COLORS.find((x) => x[0] === c)[1] ? "true" : "false")); }
    const sizeSel = el("select", { "aria-label": "Line size" }, ...["Thin", "Medium", "Thick"].map((t, i) => el("option", { value: i, selected: i === 1 ? true : null }, t)));
    sizeSel.addEventListener("change", () => { size = +sizeSel.value; });
    const saveBtn = el("button", { type: "button", class: "btn" }, "Save picture");
    const close = () => { if (busy) return; if (changed && !window.confirm("Close without saving the changes?")) return; back.remove(); document.removeEventListener("keydown", onKey); };
    const onKey = (e) => {
      if (e.key === "Escape") close();
      if ((e.ctrlKey || e.metaKey) && e.key === "z") { e.preventDefault(); undoBtn.click(); }
    };
    saveBtn.addEventListener("click", () => {
      if (!base || busy) return;
      busy = true; saveBtn.disabled = true; status.textContent = "Saving…";
      const png = /\.(png|gif)$/i.test(o.src);
      bake().toBlob(async (blob) => {
        try {
          const r = await upload(new File([blob], png ? "marked-up.png" : "marked-up.jpg", { type: png ? "image/png" : "image/jpeg" }), null,
            o.label ? `${o.label} (marked up)` : "Marked-up picture");
          busy = false; changed = false; close();
          if (o.onDone) o.onDone(r.link);
        } catch (e) { busy = false; saveBtn.disabled = false; status.textContent = e.message; }
      }, png ? "image/png" : "image/jpeg", 0.9);
    });
    const back = modal("Mark up a picture",
      el("div", { class: "row vedit-top" }, el("h2", {}, "Mark up picture"), el("span", { class: "grow" }),
        el("button", { type: "button", class: "ghost small", onclick: close }, "Close")),
      el("div", { class: "iedit-tools" }, ...toolBtns),
      el("div", { class: "row iedit-opts" }, el("div", { class: "iedit-swatches" }, ...swatches), sizeSel, el("span", { class: "grow" }),
        undoBtn, el("button", { type: "button", class: "ghost small", onclick: turn }, "↻ Turn")),
      el("div", { class: "iedit-stage" }, view),
      status,
      el("div", { class: "row vedit-foot" }, el("span", { class: "muted small" }, "Saves a new copy. The original stays in the media library."),
        el("span", { class: "grow" }), el("button", { type: "button", class: "ghost", onclick: close }, "Cancel"), saveBtn));
    back.addEventListener("click", (e) => { if (e.target === back) close(); });
    document.addEventListener("keydown", onKey);
    document.body.append(back);
    setTool("pen");
  }

  // ---- Media library --------------------------------------------------------------
  const sizeText = (b) => (b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b >= 1e6 ? `${Math.round(b / 1e6)} MB` : `${Math.max(1, Math.round(b / 1e3))} KB`);
  async function listMedia() {
    const res = await fetch("/api/manage/media");
    const r = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(r.error || "The media library couldn't be loaded.");
    return r;
  }
  function preview(item) {
    return item.kind === "video"
      ? el("video", { src: item.link + "#t=0.5", preload: "metadata", muted: true, playsinline: true, class: "media-thumb" })
      : el("img", { src: item.link, alt: "", loading: "lazy", class: "media-thumb" });
  }
  // A picker over the library. o: {kinds: ["video","image"], multi, onPick([{kind, link}])}
  function openLibrary(o) {
    const chosen = new Set();
    const grid = el("div", { class: "media-grid" }, el("p", { class: "muted" }, "Loading…"));
    const useBtn = el("button", { type: "button", class: "btn", disabled: true }, "Use selected");
    const close = () => { back.remove(); document.removeEventListener("keydown", onKey); };
    const onKey = (e) => { if (e.key === "Escape") close(); };
    let items = [];
    listMedia().then((r) => {
      items = r.items.filter((x) => !o.kinds || o.kinds.includes(x.kind));
      if (!items.length) { grid.replaceChildren(el("p", { class: "muted" }, "Nothing here yet. Add videos and pictures in Lessons › Media, or upload them here in the lesson.")); return; }
      grid.replaceChildren(...items.map((it) => {
        const tile = el("button", { type: "button", class: "media-tile pick", "aria-pressed": "false" }, preview(it),
          el("span", { class: "media-name" }, it.label), el("span", { class: "muted small" }, `${it.kind === "video" ? "Video" : "Picture"} · ${sizeText(it.size)}`));
        tile.addEventListener("click", () => {
          if (!o.multi) { close(); o.onPick([{ kind: it.kind, link: it.link }]); return; }
          if (chosen.has(it)) chosen.delete(it); else chosen.add(it);
          tile.setAttribute("aria-pressed", chosen.has(it) ? "true" : "false");
          useBtn.disabled = !chosen.size;
          useBtn.textContent = chosen.size ? `Use ${chosen.size} selected` : "Use selected";
        });
        return tile;
      }));
    }).catch((e) => grid.replaceChildren(el("p", { class: "muted" }, e.message)));
    useBtn.addEventListener("click", () => { close(); o.onPick([...chosen].map((it) => ({ kind: it.kind, link: it.link }))); });
    const back = modal("Media library",
      el("div", { class: "row vedit-top" }, el("h2", {}, "Media library"), el("span", { class: "grow" }),
        el("button", { type: "button", class: "ghost small", onclick: close }, "Close")),
      grid,
      o.multi ? el("div", { class: "row vedit-foot" }, el("span", { class: "grow" }), el("button", { type: "button", class: "ghost", onclick: close }, "Cancel"), useBtn) : null);
    back.addEventListener("click", (e) => { if (e.target === back) close(); });
    document.addEventListener("keydown", onKey);
    document.body.append(back);
  }
  const libraryButton = (kinds, multi, onPick, label = "From media library") =>
    el("button", { type: "button", class: "ghost small lib-btn", onclick: () => openLibrary({ kinds, multi, onPick }) }, label);

  // ---- Media Studio page ------------------------------------------------------------
  function mountPage(root) {
    let filter = "all", query = "", data = { items: [] };
    const grid = el("div", { class: "media-grid" });
    const status = el("p", { class: "muted vedit-status", role: "status", "aria-live": "polite" });
    const storage = el("p", { class: "muted small" });
    const say = (m) => { status.textContent = m || ""; };
    async function refresh(msg) {
      try { data = await listMedia(); } catch (e) { say(e.message); return; }
      storage.textContent = `${data.items.length} file${data.items.length === 1 ? "" : "s"} · ${sizeText(data.total_bytes || 0)} used` +
        (data.free_bytes != null ? ` · ${sizeText(data.free_bytes)} free` : "");
      draw();
      if (msg !== undefined) say(msg);
    }
    function draw() {
      const q = query.toLowerCase();
      const shown = data.items.filter((it) => (filter === "all" || it.kind === filter) && (!q || it.label.toLowerCase().includes(q) || it.used_by.join(" ").toLowerCase().includes(q)));
      if (!shown.length) { grid.replaceChildren(el("p", { class: "muted" }, data.items.length ? "Nothing matches." : "No videos or pictures yet. Add some above.")); return; }
      grid.replaceChildren(...shown.map((it) => {
        const closeMenu = (e) => e.target.closest("details").removeAttribute("open");
        const actions = el("div", { class: "pic-tools" },
          it.kind === "video"
            ? el("button", { type: "button", class: "ghost small", onclick: () => openVideoEditor({ video: it.link, label: it.label, onDone: () => refresh("Saved as a new video. The original is still here.") }) }, "✂ Edit")
            : el("button", { type: "button", class: "ghost small", onclick: () => openImageEditor({ src: it.link, label: it.label, onDone: () => refresh("Saved as a new picture. The original is still here.") }) }, "✏️ Mark up"),
          el("details", { class: "more-menu" }, el("summary", { class: "btn ghost small", "aria-label": `More for ${it.label}` }, "⋯"),
            el("div", { class: "menu" },
              el("button", { type: "button", onclick: async (e) => {
                closeMenu(e);
                const name = (window.prompt("Name", it.label) || "").trim();
                if (!name || name === it.label) return;
                await post("/api/manage/media/rename", { link: it.link, label: name }); refresh("");
              } }, "Rename"),
              el("a", { href: it.link, download: it.label, onclick: closeMenu }, "Download"),
              el("button", { type: "button", class: "danger", title: it.used_by.length ? "Used in a lesson, so it can't be deleted" : "Delete",
                disabled: it.used_by.length ? true : null, onclick: async (e) => {
                  closeMenu(e);
                  if (!window.confirm(`Delete "${it.label}"? This can't be undone.`)) return;
                  try { await post("/api/manage/media/delete", { link: it.link }); refresh("Deleted."); } catch (err) { say(err.message); }
                } }, "Delete"))));
        return el("figure", { class: "media-tile" }, preview(it),
          el("figcaption", {}, el("span", { class: "media-name" }, it.label),
            el("span", { class: "muted small" }, `${it.kind === "video" ? "Video" : "Picture"} · ${sizeText(it.size)} · ${it.added || ""}`),
            el("span", { class: "small" }, it.used_by.length ? `In: ${it.used_by.join(", ")}` : "Not in a lesson yet")),
          actions);
      }));
    }
    async function post(url, body) {
      const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const r = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(r.error || r.detail || "That didn't work. Try again.");
      return r;
    }
    async function addFiles(files) {
      for (const [i, f] of [...files].entries()) {
        try { await upload(f, (p) => say(`Uploading ${files.length > 1 ? `${i + 1} of ${files.length}: ` : ""}${f.name}: ${p}%`)); }
        catch (e) { say(`${f.name}: ${e.message}`); return; }
      }
      refresh(`Added ${files.length} file${files.length === 1 ? "" : "s"}.`);
    }
    const pick = el("input", { type: "file", multiple: true, accept: "video/mp4,video/quicktime,video/webm,video/x-m4v,video/*,image/jpeg,image/png,image/webp,image/gif" });
    pick.addEventListener("change", () => { if (pick.files.length) addFiles([...pick.files]); pick.value = ""; });
    const drop = el("label", { class: "pic-drop upload studio-drop" }, pick,
      el("span", {}, el("b", {}, "Drop videos and pictures here"), " or tap to choose"));
    dropZone(drop, ["video", "image"], addFiles, () => say("Only videos and pictures can go in the library."));
    const driveLink = el("input", { placeholder: "Paste a Google Drive link", "aria-label": "Google Drive link" });
    const driveAdd = el("button", { type: "button", class: "ghost small" }, "Add link");
    driveAdd.addEventListener("click", async () => {
      if (!driveLink.value.trim()) { driveLink.focus(); return; }
      driveAdd.disabled = true; say("Copying from Google Drive…");
      try { await fromDrive(driveLink.value); driveLink.value = ""; refresh("Copied from Google Drive."); }
      catch (e) { say(e.message); } finally { driveAdd.disabled = false; }
    });
    const chips = el("div", { class: "chips-nav media-filter", role: "tablist" }, ...[["all", "All"], ["video", "Videos"], ["image", "Pictures"]].map(([k, t]) => {
      const a = el("button", { type: "button", class: k === filter ? "on" : "", onclick: () => { filter = k; chips.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b === a)); draw(); } }, t);
      return a;
    }));
    const search = el("input", { type: "search", placeholder: "Search by name or lesson", "aria-label": "Search the library" });
    search.addEventListener("input", () => { query = search.value; draw(); });
    // Page header: one main button (Add files), the rest beside it, like a normal files app.
    const head = document.getElementById("studio-actions") || el("div", { class: "row" });
    head.replaceChildren(
      el("button", { type: "button", class: "ghost small", onclick: () => openVideoEditor({ onDone: () => refresh("New video saved.") }) }, "🎬 Make a video"),
      driveButton(["video", "image"], true, () => {}, (m) => { if (m) say(m); else refresh("Copied from Google Drive."); }, "From Google Drive") || "",
      el("button", { type: "button", class: "small", onclick: () => pick.click() }, "+ Add files"));
    root.replaceChildren(
      ...(head.isConnected ? [] : [head]),
      el("div", { class: "studio-add" }, drop, el("div", { class: "row vedit-drive" }, driveLink, driveAdd)),
      status,
      el("div", { class: "row studio-bar" }, chips, search, el("span", { class: "grow" }), storage),
      grid);
    refresh();
  }

  window.Studio = { el, modal, dropZone, upload, isDrive, fromDrive, driveButton, openVideoEditor, openImageEditor, openLibrary, libraryButton,
                    mountPage, clock, keepPieces, setLesson: (fn) => { lessonName = fn; } };
})();
