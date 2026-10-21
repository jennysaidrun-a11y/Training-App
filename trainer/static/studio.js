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
  // "image" or "video" for a file (iPhone .heic photos often come with no type on Windows).
  const fileKind = (f) => (f.type.startsWith("image/") || /\.(heic|heif)$/i.test(f.name || "") ? "image"
    : f.type.startsWith("video/") || /\.(mp4|m4v|mov|webm)$/i.test(f.name || "") ? "video" : "");
  function dropZone(target, kind, onFile, onWrong) {
    target.addEventListener("dragover", (e) => { if (!hasFiles(e)) return; e.preventDefault(); e.stopPropagation(); target.classList.add("drop-on"); });
    target.addEventListener("dragleave", (e) => { if (!target.contains(e.relatedTarget)) target.classList.remove("drop-on"); });
    target.addEventListener("drop", (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault(); e.stopPropagation();
      target.classList.remove("drop-on");
      const fs = [...e.dataTransfer.files].filter((x) => [].concat(kind).some((k) => fileKind(x) === k));
      if (fs.length) onFile(fs); else if (onWrong) onWrong();
    });
  }
  // Uploads one video or picture; resolves to {kind, link}. onProgress(percent) for big files.
  function upload(f, onProgress, label) {
    const image = fileKind(f) === "image";
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

  // The corner bar for a video being made while the editor is tucked away. It copies the editor's
  // status line and fills as it goes ("1:20 of 4:00", "45%").
  function makingBar(status, reopen) {
    const fill = el("span", { class: "make-pill-fill" });
    const text = el("span", { class: "make-pill-text" });
    const bar = el("button", { type: "button", class: "make-pill", hidden: true, role: "status", "aria-live": "polite", title: "Open the video editor" },
      el("span", { class: "make-pill-track" }, fill), text);
    let onTap = reopen, timer = null;
    bar.addEventListener("click", () => { bar.hidden = true; onTap(); });
    const secs = (t) => t.split(":").reduce((a, b) => a * 60 + parseFloat(b), 0);
    const copy = () => {
      const t = status.textContent || "Making the video…";
      text.textContent = t.replace(/ ?Keep this (window )?open( and in front)?\.?/i, "") + (/on this computer/.test(t) ? " Keep this window open." : "");
      const m = t.match(/(\d+(?::\d+)*(?:\.\d+)?) of (\d+(?::\d+)*(?:\.\d+)?)/), pc = t.match(/(\d+)%/);
      const f = pc ? +pc[1] / 100 : m && secs(m[2]) ? secs(m[1]) / secs(m[2]) : null;
      fill.style.width = f == null ? "" : Math.max(2, Math.min(100, f * 100)) + "%";
      fill.classList.toggle("unknown", f == null);
    };
    new MutationObserver(copy).observe(status, { childList: true, characterData: true, subtree: true });
    return {
      show() { copy(); bar.hidden = false; if (!bar.isConnected) document.body.append(bar); },
      done(msg, tap) {
        if (!bar.isConnected || bar.hidden) return;      // the editor was open: it shows the outcome itself
        text.textContent = msg; fill.style.width = "100%"; fill.classList.remove("unknown");
        bar.classList.toggle("bad", !!tap);
        onTap = tap || (() => {});
        clearTimeout(timer);
        if (!tap) timer = setTimeout(() => bar.remove(), 10000);
      },
    };
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
  const DRIVE_TYPES = { video: "video/mp4,video/quicktime,video/webm,video/x-m4v", image: "image/jpeg,image/png,image/webp,image/gif,image/heic,image/heif" };
  let driveToken = null, driveTokenAt = 0;
  const loadScript = (src) => new Promise((ok, bad) => {
    const t = document.createElement("script"); t.src = src; t.async = true; t.onload = ok;
    t.onerror = () => bad(new Error("Google Drive couldn't be reached.")); document.head.append(t);
  });
  const driveReady = window.DRIVE ? Promise.all([loadScript("https://apis.google.com/js/api.js"), loadScript("https://accounts.google.com/gsi/client")])
    .then(() => new Promise((ok) => window.gapi.load("picker", ok))).catch(() => null) : null;
  let driveEmail = "";
  // wide: the app's own Drive browser, which needs to see your folders (read only), not just picked files.
  let driveWide = false;
  function driveSignIn(force, wide) {
    if (!force && driveToken && Date.now() - driveTokenAt < 50 * 60 * 1000 && (driveWide || !wide)) return Promise.resolve(driveToken);
    return new Promise((ok, bad) => {
      window.google.accounts.oauth2.initTokenClient({
        client_id: window.DRIVE.clientId,
        scope: `https://www.googleapis.com/auth/${wide ? "drive.readonly" : "drive.file"} openid email`,
        ...(force ? { prompt: "select_account" } : {}),
        callback: async (r) => {
          if (r.error || !r.access_token) { bad(new Error("Google sign-in didn't finish.")); return; }
          if (wide && !window.google.accounts.oauth2.hasGrantedAllScopes(r, "https://www.googleapis.com/auth/drive.readonly")) {
            bad(new Error("To browse your Drive here, tick \"See your Google Drive files\" when Google asks. Or use ⋯ › Google's picker."));
            return;
          }
          driveToken = r.access_token; driveTokenAt = Date.now(); driveWide = !!wide;
          // Which Google account this is, so it's clear whose Drive the picker shows.
          try {
            const u = await fetch("https://www.googleapis.com/oauth2/v3/userinfo", { headers: { Authorization: `Bearer ${driveToken}` } });
            driveEmail = u.ok ? ((await u.json()).email || "") : "";
          } catch (e) { driveEmail = ""; }
          ok(driveToken);
        },
        error_callback: () => bad(new Error("Google sign-in was closed before it finished.")),
      }).requestAccessToken();   // straight from the tap, so the sign-in window isn't blocked
    });
  }
  // Opens the picker (several files at once, shown as a list with names and dates, with tabs for
  // My Drive, Shared with me and shared drives); resolves to [{id, name}] (empty if closed).
  async function pickFromDrive(kinds, force) {
    if (!(window.google && window.google.picker && window.google.accounts)) throw new Error("Google Drive is still loading. Tap again in a moment.");
    const token = await driveSignIn(force);
    return new Promise((ok) => {
      const P = window.google.picker, types = kinds.map((k) => DRIVE_TYPES[k]).join(",");
      const view = (label) => {
        const v = new P.DocsView(P.ViewId.DOCS).setMimeTypes(types).setMode(P.DocsViewMode.LIST);
        if (label && v.setLabel) v.setLabel(label);
        return v;
      };
      const b = new P.PickerBuilder()
        .addView(view(kinds.length > 1 ? "Videos and pictures" : kinds[0] === "video" ? "All videos" : "All pictures").setIncludeFolders(false))
        .addView(view("My Drive").setParent("root").setIncludeFolders(true).setSelectFolderEnabled(false))
        .addView(view("Shared with me").setOwnedByMe(false).setIncludeFolders(true).setSelectFolderEnabled(false))
        .addView(view("Shared drives").setEnableDrives(true).setIncludeFolders(true).setSelectFolderEnabled(false))
        .enableFeature(P.Feature.MULTISELECT_ENABLED).enableFeature(P.Feature.SUPPORT_DRIVES)
        .setOAuthToken(token).setDeveloperKey(window.DRIVE.apiKey).setAppId(window.DRIVE.appId)
        .setTitle(driveEmail ? `Google Drive · ${driveEmail}` : "Choose from Google Drive")
        .setCallback((d) => {
          if (d.action === P.Action.PICKED) ok(d.docs.map((x) => ({ id: x.id, name: x.name, url: x.url, mimeType: x.mimeType, sizeBytes: x.sizeBytes, edited: x.lastEditedUtc })));
          else if (d.action === P.Action.CANCEL) ok([]);
        });
      b.build().setVisible(true);
    });
  }
  // After picking: see each file before it's copied in. Google's own player shows the video
  // (the app copies nothing until "Use"), with its length, date, owner and an "Open in Drive" link
  // so you can find where it lives. Resolves to the docs to copy, "again", "switch" or null.
  function reviewPicked(docs, kinds) {
    return new Promise((done) => {
      const chosen = new Set(docs.map((d) => d.id));
      const viewer = el("div", { class: "drive-viewer", hidden: true });
      const useBtn = el("button", { type: "button", class: "btn" });
      const count = () => { useBtn.textContent = chosen.size ? `Use ${chosen.size} ${chosen.size === 1 ? "file" : "files"}` : "Pick at least one"; useBtn.disabled = !chosen.size; };
      const finish = (v) => { back.remove(); document.removeEventListener("keydown", onKey); done(v); };
      const onKey = (e) => { if (e.key === "Escape") finish(null); };
      function watch(d, row) {
        for (const r of list.querySelectorAll(".drive-pick.on")) r.classList.remove("on");
        row.classList.add("on");
        viewer.hidden = false;
        viewer.replaceChildren(
          el("iframe", { src: `https://drive.google.com/file/d/${encodeURIComponent(d.id)}/preview`, allow: "autoplay; fullscreen", allowfullscreen: "", title: d.name }),
          el("p", { class: "help" }, "Google's player. If it says the video is still being processed, Google hasn't made a preview yet; you can still use it."));
      }
      const list = el("div", { class: "drive-picks" }, ...docs.map((d) => {
        const meta = el("span", { class: "muted" }, [d.sizeBytes ? `${(d.sizeBytes / 1048576).toFixed(d.sizeBytes > 1e8 ? 0 : 1)} MB` : "", d.edited ? new Date(d.edited).toLocaleDateString() : ""].filter(Boolean).join(" · "));
        const thumb = el("span", { class: "drive-thumb", "aria-hidden": "true" }, String(d.mimeType || "").startsWith("image/") ? "🖼" : "🎬");
        const box = el("input", { type: "checkbox", checked: true, "aria-label": `Use ${d.name}` });
        box.addEventListener("change", () => { box.checked ? chosen.add(d.id) : chosen.delete(d.id); count(); });
        const row = el("div", { class: "drive-pick" },
          el("label", { class: "drive-pick-main" }, box, thumb, el("span", { class: "drive-pick-name" }, el("b", {}, d.name), meta)),
          el("button", { type: "button", class: "ghost small" }, "▶ Watch"),
          d.url ? el("a", { href: d.url, target: "_blank", rel: "noopener", class: "small" }, "Open in Drive") : "");
        row.querySelector("button").addEventListener("click", () => watch(d, row));
        // Fill in the length, owner and a real thumbnail from Drive (the token only reaches files you picked).
        fetch(`https://www.googleapis.com/drive/v3/files/${encodeURIComponent(d.id)}?supportsAllDrives=true&fields=thumbnailLink,size,modifiedTime,owners(emailAddress,me),videoMediaMetadata(durationMillis),webViewLink`,
          { headers: { Authorization: `Bearer ${driveToken}` } })
          .then((r) => (r.ok ? r.json() : null)).then((f) => {
            if (!f) return;
            const ms = f.videoMediaMetadata && f.videoMediaMetadata.durationMillis;
            const owner = (f.owners || [])[0];
            meta.textContent = [ms ? clock(ms / 1000) : "", f.size ? `${(f.size / 1048576).toFixed(f.size > 1e8 ? 0 : 1)} MB` : "",
              f.modifiedTime ? new Date(f.modifiedTime).toLocaleDateString() : "",
              owner && !owner.me ? `shared by ${owner.emailAddress}` : ""].filter(Boolean).join(" · ");
            if (f.thumbnailLink) {
              const img = el("img", { src: f.thumbnailLink, alt: "", referrerpolicy: "no-referrer" });
              img.onerror = () => img.remove();
              thumb.replaceChildren(img);
            }
          }).catch(() => {});
        return row;
      }));
      count();
      useBtn.addEventListener("click", () => finish(docs.filter((d) => chosen.has(d.id))));
      const back = modal("Check what you picked",
        el("div", { class: "row between" }, el("h2", {}, `Check what you picked (${docs.length})`),
          el("button", { type: "button", class: "ghost small", onclick: () => finish(null), "aria-label": "Close" }, "✕")),
        el("p", { class: "muted" }, driveEmail ? `From the Google Drive of ${driveEmail}. Tap ▶ Watch to see a video, untick anything you don't want.` : "Tap ▶ Watch to see a video, untick anything you don't want."),
        list, viewer,
        el("div", { class: "row" }, useBtn,
          el("button", { type: "button", class: "ghost small", onclick: () => finish("again") }, "← Back to Drive"),
          el("details", { class: "more-menu" }, el("summary", { class: "ghost small", "aria-label": "More" }, "⋯"),
            el("div", { class: "menu" }, el("button", { type: "button", onclick: () => finish("switch") }, "Switch Google account")))));
      back.classList.add("drive-review");
      document.body.append(back);
      document.addEventListener("keydown", onKey);
      if (docs.length === 1 && !String(docs[0].mimeType || "").startsWith("image/")) watch(docs[0], list.firstChild);
    });
  }

  // ---- The app's own Google Drive browser -------------------------------------------------
  // Folders with real thumbnails, ▶ to play a video (Google's own player) before choosing,
  // pick videos and pictures together. Read-only: it lists and previews; files are copied in
  // only when you press Use. Resolves to [{id, name, mimeType}], "picker" (use Google's) or null.
  const FOLDER = "application/vnd.google-apps.folder";
  async function driveApi(path, params) {
    const u = new URL("https://www.googleapis.com/drive/v3/" + path);
    for (const [k, v] of Object.entries(params || {})) u.searchParams.set(k, v);
    const r = await fetch(u, { headers: { Authorization: `Bearer ${driveToken}` } });
    if (r.status === 401) { driveToken = null; throw new Error("Google sign-in ran out. Close this and tap the Drive button again."); }
    if (!r.ok) throw new Error("Google Drive didn't answer. Try again in a moment.");
    return r.json();
  }
  async function browseDrive(kinds, force) {
    if (!(window.google && window.google.accounts)) throw new Error("Google Drive is still loading. Tap again in a moment.");
    await driveSignIn(force, true);
    return new Promise((done) => {
      const wantVideo = kinds.includes("video"), wantImage = kinds.includes("image");
      const types = [wantVideo ? "mimeType contains 'video/'" : "", wantImage ? "mimeType contains 'image/'" : ""].filter(Boolean).join(" or ");
      const chosen = new Map();     // id -> doc
      let trail = [{ id: "root", name: "My Drive" }], mode = "folder", token = "", busy = false;
      const grid = el("div", { class: "dbx-grid" });
      const crumbs = el("div", { class: "dbx-crumbs" });
      const more = el("button", { type: "button", class: "ghost small", hidden: true }, "Show more");
      const note = el("p", { class: "muted dbx-note", role: "status" });
      const viewer = el("div", { class: "dbx-viewer", hidden: true });
      const useBtn = el("button", { type: "button", class: "btn" });
      const search = el("input", { type: "search", placeholder: "Search your Drive", "aria-label": "Search your Drive" });
      const tabs = [["folder", "My Drive"], ["shared", "Shared with me"], ["recent", "Recent"]].map(([m, label]) =>
        el("button", { type: "button", role: "tab", "data-mode": m, onclick: () => { mode = m; search.value = ""; if (m === "folder") trail = [{ id: "root", name: "My Drive" }]; load(); } }, label));
      const count = () => {
        useBtn.textContent = chosen.size ? `Use ${chosen.size} ${chosen.size === 1 ? "file" : "files"}` : "Tick what you want";
        useBtn.disabled = !chosen.size;
      };
      const finish = (v) => { viewer.replaceChildren(); back.remove(); document.removeEventListener("keydown", onKey); done(v); };
      const onKey = (e) => { if (e.key === "Escape") { if (!viewer.hidden) closeViewer(); else finish(null); } };
      const closeViewer = () => { viewer.hidden = true; viewer.replaceChildren(); };
      function preview(f) {
        viewer.hidden = false;
        const isVid = f.mimeType.startsWith("video/");
        const pickBtn = el("button", { type: "button", class: "btn small" }, chosen.has(f.id) ? "✓ Ticked" : "Tick this one");
        pickBtn.addEventListener("click", () => { toggle(f); pickBtn.textContent = chosen.has(f.id) ? "✓ Ticked" : "Tick this one"; });
        viewer.replaceChildren(el("div", { class: "dbx-viewer-card" },
          el("div", { class: "row between" }, el("b", { class: "dbx-viewer-name" }, f.name),
            el("button", { type: "button", class: "ghost small", onclick: closeViewer, "aria-label": "Close preview" }, "✕")),
          isVid ? el("iframe", { src: `https://drive.google.com/file/d/${encodeURIComponent(f.id)}/preview`, allow: "autoplay; fullscreen", allowfullscreen: "", title: f.name })
            : el("img", { src: f.thumbnailLink ? f.thumbnailLink.replace(/=s\d+$/, "=s1600") : "", alt: f.name, referrerpolicy: "no-referrer" }),
          el("div", { class: "row" }, pickBtn, el("span", { class: "muted" }, meta(f)),
            isVid ? el("span", { class: "help" }, "If Google says it's still processing, the video is new; you can still use it.") : "")));
      }
      const meta = (f) => [f.videoMediaMetadata && f.videoMediaMetadata.durationMillis ? clock(f.videoMediaMetadata.durationMillis / 1000).replace(/\.\d$/, "") : "",
        f.size ? `${(f.size / 1048576).toFixed(f.size > 1e8 ? 0 : 1)} MB` : "", f.modifiedTime ? new Date(f.modifiedTime).toLocaleDateString() : ""].filter(Boolean).join(" · ");
      function toggle(f) {
        if (chosen.has(f.id)) chosen.delete(f.id); else chosen.set(f.id, { id: f.id, name: f.name, mimeType: f.mimeType });
        const t = grid.querySelector(`[data-id="${CSS.escape(f.id)}"]`);
        if (t) { t.classList.toggle("on", chosen.has(f.id)); t.querySelector("input").checked = chosen.has(f.id); }
        count();
      }
      function tile(f) {
        if (f.mimeType === FOLDER) {
          return el("button", { type: "button", class: "dbx-tile dbx-folder", onclick: () => { trail.push({ id: f.id, name: f.name }); mode = "folder"; load(); } },
            el("span", { class: "dbx-thumb", "aria-hidden": "true" }, "📁"), el("span", { class: "dbx-name" }, f.name));
        }
        const isVid = f.mimeType.startsWith("video/");
        const thumb = el("span", { class: "dbx-thumb" }, isVid ? "🎬" : "🖼");
        if (f.thumbnailLink) {
          const img = el("img", { src: f.thumbnailLink, alt: "", loading: "lazy", referrerpolicy: "no-referrer" });
          img.onerror = () => img.remove();
          thumb.append(img);
        }
        if (isVid) thumb.append(el("span", { class: "dbx-play", "aria-hidden": "true" }, "▶"));
        const box = el("input", { type: "checkbox", "aria-label": `Tick ${f.name}` });
        box.checked = chosen.has(f.id);
        box.addEventListener("click", (e) => { e.stopPropagation(); toggle(f); });
        const t = el("div", { class: "dbx-tile" + (chosen.has(f.id) ? " on" : ""), "data-id": f.id, tabindex: "0", role: "button", "aria-label": `${isVid ? "Play" : "Look at"} ${f.name}` },
          thumb, el("label", { class: "dbx-tick", onclick: (e) => e.stopPropagation() }, box),
          el("span", { class: "dbx-name" }, f.name), el("span", { class: "dbx-meta muted" }, meta(f)));
        t.addEventListener("click", () => preview(f));
        t.addEventListener("keydown", (e) => { if (e.key === "Enter") preview(f); if (e.key === " ") { e.preventDefault(); toggle(f); } });
        return t;
      }
      async function load(next) {
        if (busy) return;
        busy = true; note.textContent = "Loading…";
        for (const b of tabs) b.setAttribute("aria-selected", String(b.dataset.mode === mode && !search.value.trim()));
        const q = search.value.trim().replace(/['\\]/g, "");
        let query, orderBy = "folder,modifiedTime desc";
        if (q) { query = `name contains '${q}' and (${types} or mimeType = '${FOLDER}')`; orderBy = "modifiedTime desc"; }
        else if (mode === "shared") query = `sharedWithMe and (${types} or mimeType = '${FOLDER}')`;
        else if (mode === "recent") { query = `(${types})`; orderBy = "modifiedTime desc"; }
        else query = `'${trail[trail.length - 1].id}' in parents and (${types} or mimeType = '${FOLDER}')`;
        crumbs.replaceChildren(...(q ? [el("span", {}, `Search: ${q}`)] : mode !== "folder" ? [el("span", {}, mode === "shared" ? "Shared with me" : "Recent")]
          : trail.flatMap((c, i) => [i ? el("span", { class: "muted" }, " › ") : "", i === trail.length - 1 ? el("b", {}, c.name)
            : el("button", { type: "button", class: "link", onclick: () => { trail = trail.slice(0, i + 1); load(); } }, c.name)])));
        try {
          const r = await driveApi("files", { q: query + " and trashed = false", orderBy, pageSize: "60", supportsAllDrives: "true", includeItemsFromAllDrives: "true",
            fields: "nextPageToken,files(id,name,mimeType,thumbnailLink,size,modifiedTime,videoMediaMetadata(durationMillis))", ...(next ? { pageToken: token } : {}) });
          token = r.nextPageToken || "";
          const tiles = (r.files || []).map(tile);
          if (next) grid.append(...tiles); else { grid.replaceChildren(...tiles); grid.scrollTop = 0; }
          more.hidden = !token;
          note.textContent = grid.children.length ? "" : q ? "Nothing found." : "No videos, pictures or folders here.";
        } catch (e) { note.textContent = e.message; }
        busy = false;
      }
      let timer = 0;
      search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => load(), 400); });
      more.addEventListener("click", () => load(true));
      useBtn.addEventListener("click", () => finish([...chosen.values()]));
      count();
      const back = modal("Google Drive",
        el("div", { class: "row between" }, el("h2", {}, "Google Drive"),
          el("button", { type: "button", class: "ghost small", onclick: () => finish(null), "aria-label": "Close" }, "✕")),
        el("p", { class: "muted" }, driveEmail ? `${driveEmail}. Tap a video to play it, tick the ones you want.` : "Tap a video to play it, tick the ones you want."),
        el("div", { class: "row dbx-bar" }, el("div", { class: "subnav dbx-tabs", role: "tablist" }, ...tabs), search),
        crumbs, grid, more, note, viewer,
        el("div", { class: "row" }, useBtn, el("span", { class: "grow" }),
          el("details", { class: "more-menu" }, el("summary", { class: "ghost small", "aria-label": "More" }, "⋯"),
            el("div", { class: "menu" },
              el("button", { type: "button", onclick: () => finish("picker") }, "Use Google's picker instead"),
              el("button", { type: "button", onclick: () => finish("switch") }, "Switch Google account")))));
      back.classList.add("drive-browser");
      document.body.append(back);
      document.addEventListener("keydown", onKey);
      load();
    });
  }

  // A "Choose from Google Drive" button: picks (several at once), shows them to check, then copies
  // each one in and calls onCopied(kind, link); onFinish([{kind, link}]) once all are in.
  function driveButton(kinds, multi, onCopied, say, label = "Choose from Google Drive", onFinish) {
    if (!window.DRIVE) return null;
    const btn = el("button", { type: "button", class: "ghost small drive-btn" }, label);
    btn.addEventListener("click", async () => {
      try {
        // The app's own Drive browser (thumbnails, play before choosing); Google's picker is the backup.
        let docs = await browseDrive(kinds, false);
        while (docs === "switch") docs = await browseDrive(kinds, true);
        if (docs === "picker") {
          docs = await pickFromDrive(kinds, false);
          for (;;) {
            if (!docs.length) return;
            const r = await reviewPicked(docs, kinds);
            if (r === "again") docs = await pickFromDrive(kinds, false);
            else if (r === "switch") docs = await pickFromDrive(kinds, true);
            else { docs = r || []; break; }
          }
        }
        docs = docs || [];
        if (!docs.length) return;
        btn.disabled = true;
        const got = [];
        for (const [i, d] of docs.entries()) {
          say(`Copying ${docs.length > 1 ? `${i + 1} of ${docs.length}: ` : ""}${d.name} from Google Drive…`);
          const r = await fromDrive("", kinds.length === 1 ? kinds[0] : undefined, { file_id: d.id, token: driveToken, name: d.name });
          got.push({ kind: r.kind, link: r[r.kind] });
          onCopied(r.kind, r[r.kind]);
        }
        say("");
        if (onFinish) onFinish(got);
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
    // Several videos or pictures to start with (e.g. picked together from Google Drive): one clip
    // each, to join. Items are /media links (videos) or {kind, link}.
    for (const v of (o.videos || []).slice(0, 20)) {
      const link = typeof v === "string" ? v : v.link, image = typeof v === "object" && v.kind === "image";
      if (!String(link).startsWith("/media/")) continue;
      clips.push(image ? { video: link, still: true, seconds: 5, duration: 5, start: 0, end: null, cuts: [] }
        : { video: link, start: 0, end: null, cuts: [], duration: 0 });
    }
    let active = 0, cutFrom = null, busy = false;
    const opts = { mute: false, turn: 0, extras: new Set(["clean", "level"]) };
    // Auto save: the clips and voiceover lines are kept in this browser as you work, so closing
    // the editor (or the page) loses nothing; opening the same videos again picks up from there.
    const draftKey = "video-draft:" + lessonName() + ":" + JSON.stringify(clips.map((c) => c.video)) + (o.script ? ":" + o.script.length + o.script.slice(0, 40) : "");
    const startClips = structuredClone(clips);
    let draftTimer = 0, draftOn = false;
    // The edit itself (clips, voiceover lines and voice, effects): auto saved as a draft, and kept with
    // the finished video so opening that video again brings the edit back to adjust.
    const project = () => ({ clips: clips.map(({ asked, ...c }) => c), lines: voLines.map(({ text, at, dur }) => ({ text, at, dur })), audio: vo.audio, mode: vo.mode,
      opts: { mute: opts.mute, turn: opts.turn, extras: [...opts.extras] } });
    const draftState = () => JSON.stringify(project());
    let startState = "";
    function keepDraft() {
      if (!draftOn) return;
      clearTimeout(draftTimer);
      draftTimer = setTimeout(() => {
        try {
          const st = draftState();
          if (st === startState) localStorage.removeItem(draftKey);
          else localStorage.setItem(draftKey, JSON.stringify({ at: Date.now(), state: st }));
        } catch (e) { /* storage full or off */ }
      }, 500);
    }
    const dropDraft = () => { clearTimeout(draftTimer); try { localStorage.removeItem(draftKey); } catch (e) { /* ignore */ } };
    const player = el("video", { class: "vedit-player", controls: true, playsinline: true, preload: "metadata" });
    const still = el("img", { class: "vedit-player vedit-still", alt: "", hidden: true });
    const list = el("div", { class: "vedit-clips" });
    const total = el("span", { class: "vedit-total" });
    const status = el("p", { class: "muted vedit-status", role: "status", "aria-live": "polite" });
    const makeBtn = el("button", { type: "button", class: "btn" }, "Make the video");
    const close = () => { if (!busy) { player.pause(); back.remove(); document.removeEventListener("keydown", onKey); } };
    const onKey = (e) => {
      if (back.hidden) return;        // tucked away while the video is made: keys belong to the page
      if (e.key === "Escape") close();
      if (document.querySelector(".iedit-canvas") || /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
      if ((e.ctrlKey || e.metaKey) && e.key === "z") { e.preventDefault(); undo(); }
      else if (e.key === " " && e.target.tagName !== "BUTTON") { e.preventDefault(); playBtn.click(); }
      else if ((e.key === "ArrowLeft" || e.key === ",") && !e.target.closest(".tl-say")) { e.preventDefault(); frameStep(-1); }
      else if ((e.key === "ArrowRight" || e.key === ".") && !e.target.closest(".tl-say")) { e.preventDefault(); frameStep(1); }
    };

    const say = (m) => { status.textContent = m || ""; };
    // Undo: each change to the clips (trim, cut, slice, add, remove, move, picture seconds) is
    // remembered; lengths still loading don't count as changes.
    const undoBtn = el("button", { type: "button", class: "ghost small", disabled: true, title: "Undo (Ctrl+Z)" }, "↶ Undo");
    const past = [];
    const keyOf = (cs) => JSON.stringify(cs.map(({ duration, asked, ...rest }) => (rest.still ? { ...rest, duration } : rest)));
    let lastKey = null, lastCopy = null, lastActive = 0;
    function remember() {
      const key = keyOf(clips);
      if (lastKey !== null && key !== lastKey) { past.push({ copy: lastCopy, active: lastActive }); if (past.length > 50) past.shift(); }
      lastKey = key; lastCopy = structuredClone(clips); lastActive = active;
      undoBtn.disabled = !past.length;
    }
    function undo() {
      const prev = past.pop();
      if (!prev) return;
      const known = Object.fromEntries(clips.filter((c) => c.duration && !c.still).map((c) => [c.video, c.duration]));
      clips.splice(0, clips.length, ...prev.copy.map((c) => (c.still || c.duration ? c : { ...c, duration: known[c.video] || 0 })));
      lastKey = keyOf(clips); lastCopy = structuredClone(clips);
      cutFrom = null;
      load(Math.max(0, Math.min(prev.active, clips.length - 1)));
      say("Undone.");
    }
    undoBtn.addEventListener("click", undo);
    // Play / pause, plus where you are, under the picture.
    const playBtn = el("button", { type: "button", class: "btn small vedit-play" }, "▶ Play");
    const timeLbl = el("span", { class: "muted vedit-time" }, "0:00.0");
    // Play runs through the whole new video: each clip's kept parts, then each picture for its
    // seconds, in timeline order (the voiceover keeps going across them).
    let playing = false, switching = false, stillAt = 0, stillRaf = 0;
    function stopAll() { playing = false; cancelAnimationFrame(stillRaf); player.pause(); if (typeof voAudio !== "undefined") voAudio.pause(); showPlay(); }
    function voPlayAt() { if (vo.on && vo.audio && !compare) { voAudio.currentTime = timelinePos(); voAudio.play().catch(() => {}); } }
    function playClip() {
      const c = clips[active];
      if (!c) { stopAll(); return; }
      if (c.still) {
        const t0 = performance.now() - stillAt * 1000, len = c.seconds || c.duration || 5;
        voPlayAt();
        const tick = () => {
          if (!playing) return;
          stillAt = (performance.now() - t0) / 1000;
          if (stillAt >= len) { stillAt = len; nextClip(); return; }
          moveHead(); timeLbl.textContent = clock(timelinePos());
          stillRaf = requestAnimationFrame(tick);
        };
        stillRaf = requestAnimationFrame(tick);
        showPlay();
      } else {
        const keep = keepPieces(c);
        if (keep.length && player.currentTime >= keep[keep.length - 1][1] - 0.05) player.currentTime = keep[0][0];
        player.play().catch(() => { stopAll(); say("This browser can't play this video. You can still trim it by its times."); });
      }
    }
    function nextClip() {
      cancelAnimationFrame(stillRaf);
      if (active + 1 >= clips.length) { stopAll(); return; }
      switching = true;
      load(active + 1);
      const c = clips[active];
      if (!c.still) { const keep = keepPieces(c); player.currentTime = keep.length ? keep[0][0] : (c.start || 0); }
      switching = false;
      playClip();
    }
    playBtn.addEventListener("click", () => {
      if (playing) { stopAll(); return; }
      if (!clips.length) return;
      const T = totalLen();
      if (T && timelinePos() >= T - 0.05) seekTimeline(0);   // at the end: start again from the top
      playing = true;
      playClip();
    });
    player.addEventListener("pause", () => { if (playing && !switching && !player.ended) { playing = false; showPlay(); } });
    player.addEventListener("ended", () => { if (playing) nextClip(); });
    player.addEventListener("play", () => { playing = true; });   // started from the video's own controls
    const showPlay = () => { playBtn.textContent = playing ? "⏸ Pause" : "▶ Play"; timeLbl.textContent = clock(typeof timelinePos === "function" ? timelinePos() : player.currentTime); };
    for (const ev of ["play", "pause", "ended", "timeupdate", "seeked"]) player.addEventListener(ev, showPlay);

    // ---- Timeline, like video apps: the whole new video left to right, a video track (one block
    // per clip) and a voice track (one block per voiceover line). Drag anywhere on it to scrub (the
    // picture follows as you drag); drag a voice block to move that line, watching the picture.
    const FRAME = 1 / 30;
    let voiceItems = () => [];              // set once the voiceover lines exist (below)
    let onVoiceMoved = () => {};
    const tlVideo = el("div", { class: "tl-track tl-video" });
    const tlVoice = el("div", { class: "tl-track tl-voice" });
    const tlRuler = el("div", { class: "tl-ruler" });
    const tlHead = el("div", { class: "tl-head" }, el("span", { class: "tl-head-time" }));
    const tlBody = el("div", { class: "tl-body" }, tlRuler, tlVideo, tlVoice, tlHead);
    const timeline = el("div", { class: "tl", "aria-label": "Timeline" },
      el("div", { class: "tl-labels" }, el("span", {}, ""), el("span", {}, "🎬"), el("span", {}, "🎙")), tlBody);
    const lenOf = (c) => keepPieces(c).reduce((n, [a, b]) => n + b - a, 0);
    const totalLen = () => clips.reduce((n, c) => n + lenOf(c), 0);
    const pct = (t, T) => `${Math.max(0, Math.min(100, 100 * t / (T || 1)))}%`;
    function drawTimeline() {
      keepDraft();
      const T = Math.max(totalLen(), ...voiceItems().map((v) => v.at + v.dur), 0.1);
      tlBody.dataset.total = T;
      const step = T > 120 ? 30 : T > 40 ? 10 : T > 12 ? 5 : 1;
      const ticks = [];
      for (let t = 0; t <= T + 0.001; t += step) ticks.push(el("span", { class: "tl-tick", style: `left:${pct(t, T)}` }, clock(t).replace(/\.\d$/, "")));
      tlRuler.replaceChildren(...ticks);
      let x = 0;
      tlVideo.replaceChildren(...clips.map((c, i) => {
        const len = lenOf(c), b = el("button", { type: "button", class: "tl-clip" + (i === active ? " on" : "") + (c.still ? " still" : ""),
          style: `left:${pct(x, T)};width:${pct(len, T)}` + (c.still ? `;background-image:url("${encodeURI(c.video)}")` : ""),
          title: `${c.still ? "Picture" : "Clip " + (i + 1)}, ${clock(len)}. Drag it to change the order.` },
          c.still ? "" : `${i + 1}`);
        b.addEventListener("pointerdown", (e) => dragClip(e, b, i));
        x += len;
        return b;
      }));
      tlVoice.replaceChildren(...voiceItems().map((v) => {
        const b = el("div", { class: "tl-say" + (v.overlap ? " overlap" : ""), style: `left:${pct(v.at, T)};width:${pct(Math.max(v.dur, 0.4), T)}`,
          title: `${v.line.text} (starts ${clock(v.at)}). Drag to move it.`, tabindex: "0", role: "button" }, v.line.text || "…");
        b.addEventListener("pointerdown", (e) => dragVoice(e, b, v));
        b.addEventListener("keydown", (e) => {
          if (e.key === "ArrowLeft" || e.key === "ArrowRight") { e.preventDefault(); e.stopPropagation(); v.line.at = Math.max(0, Math.round((v.at + (e.key === "ArrowLeft" ? -0.1 : 0.1)) * 10) / 10); onVoiceMoved(); }
        });
        return b;
      }));
      moveHead();
    }
    function moveHead() {
      const T = +tlBody.dataset.total || 1, t = timelinePos();
      tlHead.style.left = pct(t, T);
      tlHead.firstChild.textContent = clock(t);
    }
    const timeAt = (clientX) => { const r = tlBody.getBoundingClientRect(); return Math.max(0, Math.min(1, (clientX - r.left) / r.width)) * (+tlBody.dataset.total || 0); };
    // Scrub: press anywhere on the timeline and drag; the picture follows.
    tlBody.addEventListener("pointerdown", (e) => {
      if (e.button) return;
      stopAll();
      tlBody.setPointerCapture(e.pointerId);
      const go = (ev) => { seekTimeline(timeAt(ev.clientX)); moveHead(); };
      go(e);
      const up = () => { tlBody.removeEventListener("pointermove", go); tlBody.removeEventListener("pointerup", up); tlBody.removeEventListener("pointercancel", up); };
      tlBody.addEventListener("pointermove", go); tlBody.addEventListener("pointerup", up); tlBody.addEventListener("pointercancel", up);
    });
    // Drag a clip or picture block left or right to change the order; a plain tap jumps there.
    function dragClip(e, b, i) {
      if (e.button) return;
      e.stopPropagation(); e.preventDefault();
      stopAll();
      b.setPointerCapture(e.pointerId);
      const x0 = e.clientX, w = tlBody.getBoundingClientRect().width;
      let moved = false, to = i;
      const mark = el("div", { class: "tl-drop" });
      const move = (ev) => {
        const dx = ev.clientX - x0;
        if (!moved && Math.abs(dx) < 6) return;
        if (!moved) { moved = true; b.classList.add("dragging"); tlVideo.append(mark); }
        b.style.transform = `translateX(${dx}px)`;
        // New place: before the first other clip whose middle is right of the pointer.
        const t = timeAt(ev.clientX), T = +tlBody.dataset.total || 1;
        let start = 0, edge = 0; to = 0;
        clips.forEach((c, k) => {
          if (k === i) return;
          const len = lenOf(c);
          if (start + len / 2 < t) { to += 1; edge = start + len; }
          start += len;
        });
        if (to === 0) edge = 0;
        mark.style.left = pct(edge, T);
      };
      const up = (ev) => {
        b.removeEventListener("pointermove", move); b.removeEventListener("pointerup", up); b.removeEventListener("pointercancel", up);
        mark.remove(); b.classList.remove("dragging"); b.style.transform = "";
        if (!moved) { seekTimeline(timeAt(ev.clientX)); moveHead(); return; }
        if (to === i) { drawTimeline(); return; }
        clips.splice(to, 0, clips.splice(i, 1)[0]);
        load(to);
        say(`Moved to place ${to + 1} of ${clips.length}. Undo puts it back.`);
      };
      b.addEventListener("pointermove", move); b.addEventListener("pointerup", up); b.addEventListener("pointercancel", up);
    }
    function dragVoice(e, b, v) {
      e.stopPropagation(); e.preventDefault();
      stopAll();
      b.setPointerCapture(e.pointerId);
      const grab = timeAt(e.clientX) - v.at;
      let at = v.at, raf = 0;
      b.classList.add("dragging");
      const move = (ev) => {
        at = Math.max(0, Math.round((timeAt(ev.clientX) - grab) * 10) / 10);
        b.style.left = pct(at, +tlBody.dataset.total);
        b.dataset.time = clock(at);
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(() => { seekTimeline(at); moveHead(); });   // see the picture where the line now starts
      };
      const up = () => {
        b.removeEventListener("pointermove", move); b.removeEventListener("pointerup", up); b.removeEventListener("pointercancel", up);
        b.classList.remove("dragging");
        if (at !== v.at) { v.line.at = at; onVoiceMoved(); }
      };
      b.addEventListener("pointermove", move); b.addEventListener("pointerup", up); b.addEventListener("pointercancel", up);
    }
    function frameStep(d) {
      const c = clips[active];
      if (!c || c.still) return;
      player.pause();
      player.currentTime = Math.max(0, Math.min(player.duration || 0, player.currentTime + d * FRAME));
    }
    const frameBack = el("button", { type: "button", class: "ghost small", title: "Back one frame (← or ,)", "aria-label": "Back one frame", onclick: () => frameStep(-1) }, "◀︎|");
    const frameFwd = el("button", { type: "button", class: "ghost small", title: "Forward one frame (→ or .)", "aria-label": "Forward one frame", onclick: () => frameStep(1) }, "|▶︎");
    for (const ev of ["timeupdate", "seeked"]) player.addEventListener(ev, moveHead);
    player.addEventListener("play", function tick() { moveHead(); if (!player.paused) requestAnimationFrame(tick); });
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
    async function grabFrame(i, t, startTool) {
      const c = clips[i];
      player.pause(); say("Taking the picture…");
      try {
        // The picture on screen, straight from the player (instant); the server takes it when
        // this browser can't show the video.
        let src = "";
        if (player.getAttribute("src") === c.video && player.videoWidth && Math.abs(player.currentTime - t) < 0.25) {
          try {
            const k = Math.min(1, 1920 / player.videoWidth), cv = document.createElement("canvas");
            cv.width = Math.round(player.videoWidth * k); cv.height = Math.round(player.videoHeight * k);
            cv.getContext("2d").drawImage(player, 0, 0, cv.width, cv.height);
            src = cv.toDataURL("image/jpeg", 0.92);
          } catch (e) { src = ""; }
        }
        if (!src) {
          const res = await fetch("/api/manage/video/frame", { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ video: c.video, t, lesson: lessonName() }) });
          const r = await res.json().catch(() => ({}));
          if (!res.ok || !r.image) throw new Error(r.error || "That frame couldn't be taken.");
          src = r.image;
        }
        say("Draw on the picture, then save it.");
        openImageEditor({ src, tool: startTool, label: `${o.label || "Video"} frame at ${clock(t)}`, onDone: (link) => {
          if (clips.length >= 19) { say("That's too many clips for one video. Remove one first."); return; }
          const at = sliceAt(i, t) ? i + 1 : (t <= (c.start || 0) + 0.1 ? i : i + 1);
          clips.splice(at, 0, { video: link, still: true, seconds: 4, duration: 4, start: 0, end: null, cuts: [] });
          load(at);
          say("Your drawing shows there for 4 seconds; change the seconds on the picture if you like.");
        } });
      } catch (e) { say(e.message); }
    }

    function load(i) {
      if (i !== active || !switching) stillAt = 0;
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
          if (next) player.currentTime = next[0]; else if (playing) nextClip(); else player.pause();
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
      remember();
      makeBtn.disabled = busy || !clips.length;
      if (!clips.length) list.replaceChildren(el("p", { class: "muted" }, "Add a video or a picture below to start."));
      else list.replaceChildren(...clips.map((c, i) => {
        const here = () => (i === active ? player.currentTime : 0);
        const secs = el("input", { type: "number", min: "0.5", max: "120", step: "0.5", value: c.seconds, class: "short", "aria-label": "Seconds to show the picture" });
        secs.addEventListener("change", () => { c.seconds = c.duration = Math.min(120, Math.max(0.5, +secs.value || 5)); setTimeout(drawClips); });
        const tools = c.still ? el("div", { class: "row vedit-tools" }, el("label", {}, "Show this picture for ", secs, " seconds"),
            el("button", { type: "button", class: "ghost small", onclick: () => openImageEditor({ src: c.video, onDone: (link) => { c.video = link; load(i); } }) }, "✏️ Mark up"))
          : i === active ? (cutFrom == null ? null : el("div", { class: "row vedit-tools" },   // the side toolbox does the rest
            el("button", { type: "button", class: "small", onclick: () => { const a = Math.min(cutFrom, here()), b = Math.max(cutFrom, here()); if (b - a >= 0.1) c.cuts.push([a, b]); cutFrom = null; drawClips(); } }, `…to here (from ${clock(cutFrom)})`),
            el("button", { type: "button", class: "ghost small", onclick: () => { cutFrom = null; drawClips(); } }, "Never mind")))
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
      drawTimeline();
    }

    // Another clip or picture at the end: tap to choose or drag one onto the box (app rule),
    // or paste a Google Drive link.
    const pick = el("input", { type: "file", accept: "video/mp4,video/quicktime,video/webm,video/x-m4v,video/*,image/jpeg,image/png,image/webp,image/gif,image/heic,image/heif,.heic,.heif" });
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
    // The selector: pictures, videos and your edits from the library (or new ones from this computer), added in the order picked.
    const openPicker = () => openLibrary({ kinds: ["video", "image"], multi: true, upload: true, title: "Add to the video",
      onPick: (picked) => { picked.forEach((p) => pushClip(p.kind, p.link)); if (picked.length) say(`Added ${picked.length} to the end. Drag them on the timeline to change the order.`); } });
    const driveRow = el("div", { class: "vedit-drive-wrap" },
      el("div", { class: "row" }, pickBtn, el("button", { type: "button", class: "ghost small lib-btn", onclick: () => openPicker() }, "🖼 Choose pictures & edits")),
      el("div", { class: "row vedit-drive" }, driveLink, driveBtn));
    function addClip(f) {
      if (fileKind(f) === "image") {
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
    // The voiceover is a list of lines. Each line can start at a time you pick while watching
    // (⏱ sets it to where the video is now; − / + nudge it); a line without a time follows the
    // one before. The server speaks each line and puts it at its time (POST /api/manage/speech/track).
    let voLines = [];
    const splitScript = (text) => String(text || "").replace(/^\s*\d+\.\s*/gm, "").split(/\n+|(?<=[.!?])\s+/).map((x) => x.trim()).filter(Boolean);
    const voList = el("div", { class: "vo-lines" });
    const voScript = el("textarea", { rows: "4", placeholder: "Paste a whole script here; it's split into lines you can time", "aria-label": "Voiceover script" });
    const voVoice = el("select", { "aria-label": "Voice" }), voSpeed = el("select", { "aria-label": "Speed" },
      ...[["slow", "Slower"], ["normal", "Normal speed"], ["fast", "Faster"]].map(([v, t]) => el("option", { value: v, selected: v === "normal" ? true : null }, t)));
    fetch("/api/speech/voices").then((r) => r.json()).then((r) => {
      voVoice.replaceChildren(...r.voices.map((v) => el("option", { value: v.id }, v.label)));
      if (!r.ready) voMake.disabled = true, say("The voice isn't set up on this copy of the app yet.");
    }).catch(() => {});
    const voAudio = el("audio", { controls: true, hidden: true });
    const voMake = el("button", { type: "button", class: "small" }, "Make the voice");
    const voFrom = el("button", { type: "button", class: "ghost small", title: "Writes down what's said in this clip, line by line with its times, so the voice says it clearly at the same moments" }, "📝 Start from what's said in the clip");
    const voReplace = el("button", { type: "button", class: "fx-toggle", "aria-pressed": "true" }, "Replace the video's sound");
    const voMix = el("button", { type: "button", class: "fx-toggle", "aria-pressed": "false" }, "Play over it (sound quieter)");
    const voStale = () => { if (vo.audio) { vo.audio = ""; vo.on = false; voAudio.hidden = true; voAudio.removeAttribute("src"); fx.voice.setAttribute("aria-pressed", "false"); voSync(); voMake.textContent = "Make the voice again"; } };
    const setLines = (texts, ats = []) => { voLines = texts.map((text, i) => ({ text, at: ats[i] == null ? null : ats[i], dur: 0 })); voStale(); drawLines(); };
    voiceItems = () => {
      let t = 0;
      return voLines.filter((l) => l.text.trim()).map((l, i, all) => {
        const at = l.at != null ? l.at : l.placed != null ? l.placed : t + (i ? 0.3 : 0);
        const dur = l.dur || Math.max(0.8, l.text.trim().split(/\s+/).length / 2.6);
        t = at + dur;
        const next = all[i + 1];
        return { line: l, at, dur, overlap: !!(next && next.at != null && at + dur > next.at + 0.05) };
      });
    };
    // A line dragged on the timeline: keep the new start, and remake the voice if there was one.
    onVoiceMoved = () => { const had = !!vo.audio; voStale(); drawLines(); if (had) voMake.click(); };
    function drawLines() {
      voList.replaceChildren(...voLines.map((l, i) => {
        const next = voLines[i + 1];
        const overlap = l.dur && next && next.at != null && l.at != null && l.at + l.dur > next.at + 0.05;
        const input = el("input", { value: l.text, "aria-label": `Line ${i + 1}`, placeholder: "What the voice says here" });
        input.addEventListener("input", () => { l.text = input.value; voStale(); drawTimeline(); });
        const nudge = (d) => { l.at = Math.max(0, Math.round(((l.at == null ? timelinePos() : l.at) + d) * 10) / 10); voStale(); drawLines(); };
        return el("div", { class: "vo-line" + (overlap ? " overlap" : "") },
          el("button", { type: "button", class: "ghost small vo-at", title: "Start this line where the video is now",
            onclick: () => { l.at = Math.round(timelinePos() * 10) / 10; voStale(); drawLines(); } }, l.at == null ? "⏱ Set start" : `⏱ ${clock(l.at)}`),
          l.at == null ? null : el("button", { type: "button", class: "ghost small", "aria-label": "Half a second earlier", onclick: () => nudge(-0.5) }, "−"),
          l.at == null ? null : el("button", { type: "button", class: "ghost small", "aria-label": "Half a second later", onclick: () => nudge(0.5) }, "+"),
          input,
          l.at == null ? null : el("button", { type: "button", class: "ghost small", title: "Jump the video to this line", onclick: () => seekTimeline(l.at) }, "▶"),
          el("button", { type: "button", class: "ghost small x", "aria-label": "Remove line", onclick: () => { voLines.splice(i, 1); voStale(); drawLines(); } }, "✕"),
          overlap ? el("span", { class: "vo-warn" }, "Runs into the next line: move the next one later, or shorten this one.") : null);
      }));
      drawTimeline();
    }
    // Jump the player to second t of the new video (finds the clip and the spot in it).
    function seekTimeline(t) {
      let left = t;
      for (let k = 0; k < clips.length; k++) {
        const pieces = keepPieces(clips[k]), len = pieces.reduce((n, [a, b]) => n + b - a, 0);
        if (left <= len || k === clips.length - 1) {
          if (k !== active) load(k);
          if (clips[k].still) { stillAt = Math.max(0, Math.min(left, len)); moveHead(); return; }
          for (const [a, b] of pieces) { if (left <= b - a) { player.currentTime = a + left; return; } left -= b - a; }
          return;
        }
        left -= len;
      }
    }
    const addLine = el("button", { type: "button", class: "ghost small", onclick: () => { voLines.push({ text: "", at: null, dur: 0 }); drawLines(); voList.lastChild.querySelector("input").focus(); } }, "+ Line");
    const splitBtn = el("button", { type: "button", class: "ghost small", onclick: () => { if (voScript.value.trim()) { setLines(splitScript(voScript.value)); voScript.value = ""; pasteBox.open = false; } } }, "Split into lines");
    const pasteBox = el("details", { class: "vo-paste" }, el("summary", {}, "📋 Paste a whole script"), voScript, splitBtn);
    const voPanel = el("div", { class: "vo-panel", hidden: true },
      el("p", { class: "help" }, "Play the video and tap ⏱ on a line to start it right there; − and + move it by half a second. Lines without a time follow the one before."),
      voList, el("div", { class: "row" }, addLine, voFrom), pasteBox,
      el("div", { class: "row" }, voVoice, voSpeed, voMake), voAudio,
      el("div", { class: "row fx-row" }, voReplace, voMix));
    fx.voice = el("button", { type: "button", class: "fx-toggle", "aria-pressed": "false", title: "A voice reads your script over the video" }, "🎙 Voiceover");
    fx.voice.addEventListener("click", () => {
      voPanel.hidden = false;
      if (!voLines.length) { voLines.push({ text: "", at: null, dur: 0 }); drawLines(); }
      if (vo.audio) { vo.on = !vo.on; voSync(); say(`Voiceover: ${vo.on ? "on" : "off"}. Play to hear it with the video.`); }
      else { const first = voList.querySelector("input"); if (first) first.focus(); }
      fx.voice.setAttribute("aria-pressed", vo.on ? "true" : "false");
    });
    const setMode = (m) => {
      vo.mode = m; voReplace.setAttribute("aria-pressed", m === "replace" ? "true" : "false"); voMix.setAttribute("aria-pressed", m === "mix" ? "true" : "false"); voSync();
      say(!vo.audio ? `${m === "replace" ? "The voice will replace the video's sound" : "The voice will play over the video's sound, which goes quieter"}. Make the voice to hear it.`
        : m === "replace" ? "The video's own sound is off; you hear only the voice." : "You hear the voice over the video's sound, turned down.");
    };
    voReplace.addEventListener("click", () => setMode("replace"));
    voMix.addEventListener("click", () => setMode("mix"));
    voMake.addEventListener("click", async () => {
      const lines = voLines.filter((l) => l.text.trim());
      if (!lines.length) { if (!voLines.length) addLine.click(); else voList.querySelector("input").focus(); say("Write what the voice should say first."); return; }
      voMake.disabled = true; say(`Making the voice${lines.length > 1 ? ` (${lines.length} lines)` : ""}…`);
      try {
        const res = await fetch("/api/manage/speech/track", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ lines: lines.map((l) => ({ text: l.text, at: l.at })), voice: voVoice.value, speed: voSpeed.value }) });
        const r = await res.json();
        if (!res.ok) throw new Error(r.error || "The voice couldn't be made.");
        lines.forEach((l, i) => { l.dur = r.lines[i].duration; if (l.at == null) l.placed = r.lines[i].at; });
        drawLines();
        vo.audio = r.audio; vo.on = true; voAudio.src = r.audio; voAudio.hidden = false; voMake.textContent = "Make the voice";
        fx.voice.setAttribute("aria-pressed", "true"); voSync();
        // A video made only of pictures: give the pictures enough time for the whole voice.
        const shown = clips.reduce((n, c) => n + (c.seconds || 0), 0);
        if (clips.length && clips.every((c) => c.still) && r.duration > shown) {
          const each = Math.min(120, Math.ceil((r.duration + 0.5) / clips.length * 2) / 2);
          for (const c of clips) c.seconds = c.duration = each;
          drawClips();
        }
        const late = voList.querySelector(".overlap");
        say(late ? "Voice ready, but a line runs into the next one (marked in red). Move it, then make the voice again."
          : `Voice ready (${clock(r.duration)}). Play the video to hear them together; it goes in when you make the video.`);
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
        // Each line keeps the moment it was said (moved onto the new video's timeline).
        let before = 0;
        for (let k = 0; k < active; k++) before += keepPieces(clips[k]).reduce((n, [a, b]) => n + b - a, 0);
        const toTimeline = (t) => { let x = before; for (const [a, b] of keepPieces(c)) { if (t >= b) x += b - a; else { x += Math.max(0, t - a); break; } } return Math.round(x * 10) / 10; };
        const segs = (r.segments || []).filter((g) => g.text);
        if (segs.length) setLines(segs.map((g) => g.text), segs.map((g) => toTimeline(g.at)));
        say(segs.length ? "Here's what's said, line by line at the same moments. Fix any words, then Make the voice." : "No speech was found in this clip.");
      } catch (e) { say(e.message); } finally { voFrom.disabled = false; }
    });
    // Where the player is on the new video's timeline (kept parts of earlier clips first).
    function timelinePos() {
      let t = 0;
      for (let k = 0; k < active; k++) t += keepPieces(clips[k]).reduce((n, [a, b]) => n + b - a, 0);
      const c = clips[active];
      if (!c) return t;
      if (c.still) return t + stillAt;
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

    // The quick toolbox down the side: one tap, it works where the video is now.
    function toolbox() {
      const tool = (icon, label, help, fn) => el("button", { type: "button", class: "tb-btn", title: help, "aria-label": label, onclick: fn },
        el("span", { class: "tb-ico", "aria-hidden": "true" }, icon), el("span", { class: "tb-lbl" }, label));
      const clip = () => { const c = clips[active]; if (!c || c.still) { say("Tap a video clip on the timeline first."); return null; } return c; };
      // Draw / Text: on a video, the frame you're on becomes a picture shown for a few seconds;
      // on a picture, that picture is marked up in place.
      const markUp = (startTool) => {
        const c = clips[active];
        if (!c) { say("Add a video or a picture first."); return; }
        if (!c.still) { grabFrame(active, player.currentTime, startTool); return; }
        stopAll();
        openImageEditor({ src: c.video, tool: startTool, label: o.label || "Picture", onDone: (link) => { c.video = link; load(active); say("Saved on the picture."); } });
      };
      const cutBtn = tool("⛔", "Cut", "Cut out a part: tap here at the start of the part, then again at its end", () => {
        const c = clip(); if (!c) return;
        const t = player.currentTime;
        if (cutFrom == null) { cutFrom = t; cutBtn.classList.add("on"); say(`Cut starts at ${clock(t)}. Go to where it ends and tap Cut again.`); return; }
        const a = Math.min(cutFrom, t), b = Math.max(cutFrom, t);
        cutFrom = null; cutBtn.classList.remove("on");
        if (b - a >= 0.1) { c.cuts.push([a, b]); say(`Cut out ${clock(a)} to ${clock(b)}.`); } else say("That part was too short to cut.");
        drawClips();
      });
      return el("div", { class: "vedit-toolbox", role: "toolbar", "aria-label": "Tools", "aria-orientation": "vertical" },
        tool("✂️", "Slice", "Split the clip in two here", () => { if (!clip()) return; if (!sliceAt(active, player.currentTime)) say("Stop the video somewhere in the middle of a clip, then slice."); else load(active + 1); }),
        tool("⇤", "Start", "Start the clip here (trim the beginning)", () => { const c = clip(); if (!c) return; c.start = player.currentTime; if (c.end != null && c.end < c.start) c.end = null; drawClips(); }),
        tool("⇥", "End", "End the clip here (trim the end)", () => { const c = clip(); if (!c) return; c.end = Math.max(player.currentTime, c.start || 0); drawClips(); }),
        cutBtn,
        tool("✏️", "Draw", "Draw on this frame or picture", () => markUp("pen")),
        tool("🔤", "Text", "Put a text box on this frame or picture", () => markUp("tbox")),
        tool("🎙", "Voice", "Add a voiceover line that starts here", () => {
          voPanel.hidden = false;
          voLines.push({ text: "", at: Math.round(timelinePos() * 10) / 10, dur: 0 });
          voLines.sort((a, b) => (a.at == null ? 1e9 : a.at) - (b.at == null ? 1e9 : b.at));
          voStale(); drawLines();
          const empty = [...voList.querySelectorAll("input")].find((x) => !x.value);
          if (empty) { empty.scrollIntoView({ block: "center", behavior: "smooth" }); empty.focus(); }
          say("Type what the voice says from here, then Make the voice. Drag its block on the timeline to move it.");
        }),
        tool("➕", "Add", "Choose pictures, videos and your edits to add", openPicker),
        tool("🔇", "Sound", "Turn the video's own sound off or on", () => fx.mute && fx.mute.click()),
        tool("↶", "Undo", "Undo the last change (Ctrl+Z)", undo));
    }

    // Every clip's length, from the server when this browser can't read it (not only the clip on screen).
    async function ensureLengths() {
      await Promise.all(clips.filter((c) => !c.still && !c.duration).map((c) =>
        fetch("/api/manage/video/info?v=" + encodeURIComponent(c.video)).then((r) => r.json()).then((r) => { if (r.duration) c.duration = r.duration; }).catch(() => {})));
      drawClips();
    }
    // Make the video on this computer: the browser plays each kept part into a 1280x720 picture
    // (turned, with the effects and fades) and records it with the sound and voiceover as it plays,
    // then uploads the finished file. It takes as long as the video is long, and the server only
    // tidies the file. The server way (makeOnServer) is the backup.
    const recType = () => (window.MediaRecorder && window.AudioContext && HTMLCanvasElement.prototype.captureStream
      // MP4 (H.264 when the browser names it), so the video plays everywhere; other browsers use the server.
      && ["video/mp4;codecs=avc1.42E01E,mp4a.40.2", "video/mp4;codecs=avc1.4D401F,mp4a.40.2", "video/mp4;codecs=avc1.64001F,mp4a.40.2",
        "video/mp4;codecs=avc1,mp4a.40.2", "video/mp4;codecs=avc1,mp4a", "video/mp4;codecs=avc1,opus", "video/mp4;codecs=avc1", "video/mp4"]
        .find((t) => MediaRecorder.isTypeSupported(t))) || "";
    async function makeHere(step) {
      const type = recType(), W = 1280, H = 720, T = totalLen(), on = (k) => opts.extras.has(k);
      const cv = el("canvas", { width: W, height: H }), g = cv.getContext("2d");
      const ac = new AudioContext();
      await ac.resume();
      const out = ac.createMediaStreamDestination(), master = ac.createGain();
      master.connect(out);
      const vid = el("video", { playsinline: true, preload: "auto" });
      const hp = ac.createBiquadFilter(), lp = ac.createBiquadFilter(), comp = ac.createDynamicsCompressor(), vgain = ac.createGain();
      hp.type = "highpass"; lp.type = "lowpass";
      hp.frequency.value = on("clean") ? 80 : 10; lp.frequency.value = on("clean") ? 12000 : 22000;
      if (on("level")) { comp.threshold.value = -30; comp.ratio.value = 6; } else { comp.threshold.value = 0; comp.ratio.value = 1; }
      const voice = vo.on && vo.audio;
      vgain.gain.value = opts.mute || (voice && vo.mode === "replace") ? 0 : (on("level") ? 1.8 : 1) * (voice ? 0.35 : 1);
      ac.createMediaElementSource(vid).connect(hp).connect(lp).connect(comp).connect(vgain).connect(master);
      let vAud = null;
      if (voice) { vAud = new Audio(vo.audio); vAud.preload = "auto"; ac.createMediaElementSource(vAud).connect(master); }
      const stream = new MediaStream([...cv.captureStream(30).getVideoTracks(), ...out.stream.getAudioTracks()]);
      const rec = new MediaRecorder(stream, { mimeType: type, videoBitsPerSecond: 6e6, audioBitsPerSecond: 160000 });
      const chunks = [];
      rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
      const stopped = new Promise((ok) => { rec.onstop = ok; });
      const wait = (target, ev) => new Promise((ok, bad) => {
        const done = () => { target.removeEventListener(ev, done); target.removeEventListener("error", fail); ok(); };
        const fail = () => { target.removeEventListener(ev, done); bad(new Error("A clip couldn't be played on this computer.")); };
        target.addEventListener(ev, done); target.addEventListener("error", fail);
      });
      let pos = 0;    // seconds of the new video made so far
      function draw(src, sw, sh) {
        const k = on("fade") ? Math.max(0, Math.min(1, pos / 0.6, (T - pos) / 0.6)) : 1;
        g.fillStyle = "#000"; g.fillRect(0, 0, W, H);
        if (!sw || !sh) return;
        const side = opts.turn % 180 !== 0, fw = side ? sh : sw, fh = side ? sw : sh, s = Math.min(W / fw, H / fh);
        g.save();
        g.filter = on("bright") ? "brightness(1.08) contrast(1.1) saturate(1.15)" : "none";
        g.translate(W / 2, H / 2); g.rotate((opts.turn || 0) * Math.PI / 180);
        g.drawImage(src, -sw * s / 2, -sh * s / 2, sw * s, sh * s);
        g.restore();
        if (k < 1) { g.fillStyle = `rgba(0,0,0,${1 - k})`; g.fillRect(0, 0, W, H); }
        master.gain.value = k;
      }
      const say2 = () => step(`${clock(pos)} of ${clock(T)}`);
      const voiceGo = () => { if (vAud && pos < vAud.duration + 0.1) { vAud.currentTime = pos; vAud.play().catch(() => {}); } };
      const voiceStop = () => { if (vAud) vAud.pause(); };
      if (vAud) await Promise.race([wait(vAud, "canplaythrough"), new Promise((ok) => setTimeout(ok, 8000))]);
      rec.start(1000); rec.pause();
      try {
        for (const c of clips) {
          if (c.still) {
            const img = new Image();
            img.src = c.video;
            await img.decode().catch(() => { throw new Error("A picture couldn't be loaded on this computer."); });
            const len = c.seconds || c.duration || 5, from = pos, t0 = performance.now();
            draw(img, img.naturalWidth, img.naturalHeight);
            rec.resume(); voiceGo();
            await new Promise((ok) => {
              const tick = () => {
                pos = from + Math.min(len, (performance.now() - t0) / 1000);
                draw(img, img.naturalWidth, img.naturalHeight); say2();
                if (pos - from >= len) ok(); else requestAnimationFrame(tick);
              };
              requestAnimationFrame(tick);
            });
            rec.pause(); voiceStop(); pos = from + len;
            continue;
          }
          if (vid.getAttribute("src") !== c.video) { vid.src = c.video; await wait(vid, "loadedmetadata"); }
          for (const [a, b] of keepPieces(c)) {
            const from = pos;
            vid.currentTime = a; await wait(vid, "seeked");
            draw(vid, vid.videoWidth, vid.videoHeight);
            rec.resume(); voiceGo();
            await vid.play().catch(() => { throw new Error("A clip couldn't be played on this computer."); });
            await new Promise((ok) => {
              let over = false;
              const end = () => { over = true; };
              vid.addEventListener("ended", end, { once: true });
              const tick = () => {
                pos = from + Math.max(0, Math.min(b, vid.currentTime) - a);
                draw(vid, vid.videoWidth, vid.videoHeight); say2();
                if (over || vid.currentTime >= b - 0.02 || vid.paused) { vid.removeEventListener("ended", end); ok(); }
                else requestAnimationFrame(tick);
              };
              tick();
            });
            vid.pause(); rec.pause(); voiceStop(); pos = from + (b - a);
          }
        }
        rec.resume();
        rec.stop();
        await stopped;
      } finally {
        if (rec.state !== "inactive") rec.stop();
        vid.removeAttribute("src"); voiceStop(); ac.close().catch(() => {});
      }
      const blob = new Blob(chunks, { type: "video/mp4" });
      if (!blob.size) throw new Error("Nothing was recorded. Keep this window in front while it makes the video.");
      const form = new FormData();
      form.append("video_file", blob, "made.mp4");
      form.append("lesson", lessonName());
      form.append("label", o.label ? `${o.label} (edited)` : "Made video");
      form.append("made_here", "1");
      // Saving it: if the app is restarting or the connection drops, it tries again for a few minutes
      // (the finished video stays here meanwhile), so nothing has to be made twice.
      const send = () => new Promise((ok, bad) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", "/api/manage/video");
        xhr.upload.onprogress = (ev) => { if (ev.lengthComputable) step(`saving it to the app, ${Math.round(100 * ev.loaded / ev.total)}%`); };
        xhr.onload = () => {
          let r = {}; try { r = JSON.parse(xhr.responseText); } catch (e) { /* below */ }
          if (xhr.status === 200 && r.video) ok(r.video);
          else { const err = new Error(r.error || r.detail || "The finished video couldn't be saved. Try again."); err.retry = xhr.status >= 500 || !r.error; bad(err); }
        };
        xhr.onerror = () => { const err = new Error("The finished video couldn't be saved. Check the connection and try again."); err.retry = true; bad(err); };
        xhr.send(form);
      });
      for (let tries = 1; ; tries++) {
        try { return { video: await send(), duration: T }; }
        catch (e) {
          if (!e.retry || tries >= 12) throw e;
          step("the app is busy or restarting, trying to save again in 15 seconds");
          await new Promise((ok) => setTimeout(ok, 15000));
        }
      }
    }
    // The server way: ffmpeg on the online server, as a background job it reports on every two seconds.
    async function makeOnServer() {
      status.textContent = "Making the video on the server… this can take a while for a long video. Keep this open.";
      const body = JSON.stringify({ lesson: lessonName(), label: o.label ? `${o.label} (edited)` : "", mute: opts.mute, turn: opts.turn, extras: [...opts.extras],
        ...(vo.on && vo.audio ? { voiceover: { audio: vo.audio, mode: vo.mode } } : {}),
        background: true,
        clips: clips.map((c) => ({ video: c.video, start: c.start || 0, end: c.end, cuts: c.cuts, ...(c.still ? { seconds: c.seconds } : {}) })) });
      const begin = async () => {
        const res = await fetch("/api/manage/video/edit", { method: "POST", headers: { "Content-Type": "application/json" }, body });
        const j = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(j.error || j.detail || "The video couldn't be made. Try again.");
        return j;
      };
      let r = await begin(), restarts = 0;
      let misses = 0;     // a dropped connection just retries
      while (r.job && !r.video) {
        // Asked with wait=1: the server answers when something changes, so a request is always open
        // (Fly keeps the server on while one is).
        let p;
        try { p = await fetch("/api/manage/video/edit/" + r.job + "?wait=1"); }
        catch (e) { if (++misses > 30) throw new Error("Lost touch with the app. Check the connection, then make the video again."); await new Promise((ok) => setTimeout(ok, 2000)); continue; }
        const j = await p.json().catch(() => ({}));
        if (p.status >= 500) {      // the app is restarting: ask again shortly
          if (++misses > 30) throw new Error("Lost touch with the app. Check the connection, then make the video again.");
          await new Promise((ok) => setTimeout(ok, 3000)); continue;
        }
        if (p.status === 404 && restarts++ < 2) {     // the app restarted (an update): start it again by itself
          status.textContent = "The app restarted, so the video is being started again. Keep this open.";
          await new Promise((ok) => setTimeout(ok, 5000));
          try { r = await begin(); } catch (e) { await new Promise((ok) => setTimeout(ok, 10000)); r = await begin(); }
          continue;
        }
        if (!p.ok) throw new Error(j.error || "The video couldn't be made. Try again.");
        misses = 0;
        if (j.state === "failed") throw new Error(j.error || "The video couldn't be made. Try again.");
        if (j.state === "done") r = j; else status.textContent = `Making the video on the server: ${j.step || "working…"} Keep this open.`;
      }
      if (!r.video) throw new Error("The video couldn't be made. Try again.");
      return r;
    }
    // Made on this computer when the browser can (much faster than the small server); the server
    // makes it when this browser can't record, or for "Steady shaky video".
    async function makeVideo(onServer) {
      if (busy) return;
      if (!clips.length) { say("Add a video or a picture first."); return; }
      if (!clips.every((c) => c.duration)) {
        say("Reading the video lengths…");
        await ensureLengths();
        const bad = clips.findIndex((c) => !c.duration);
        if (bad >= 0) { say(`Clip ${bad + 1} can't be read. Remove it (✕) or add it again, then Make the video.`); return; }
      }
      const here = !onServer && recType() && !opts.extras.has("steady");
      busy = true; makeBtn.disabled = true; makeBtn.textContent = "Making… keep this open"; stopAll();
      awayBtn.hidden = false;
      try {
        const r = here
          ? await makeHere((m) => { status.textContent = `Making the video on this computer: ${m}. Keep this window open and in front.`; })
          : await makeOnServer();
        const kept = clips[0] && clips[0].video === original && !clips[0].still ? keepPieces(clips[0]) : null;
        await fetch("/api/manage/media/project", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ link: r.video, project: project() }) }).catch(() => {});
        busy = false; dropDraft(); close();
        pill.done("✓ The video is made and saved in Media.");
        if (o.onDone) o.onDone(r, kept);
      } catch (e) {
        busy = false; makeBtn.disabled = false; makeBtn.textContent = "Make the video"; awayBtn.hidden = true;
        pill.done("The video didn't finish. Tap to see why.", () => { back.hidden = false; });
        status.replaceChildren(e.message + " ", here ? el("button", { type: "button", class: "linkish", onclick: () => makeVideo(true) }, "Make it on the server instead") : "");
        status.scrollIntoView({ block: "center", behavior: "smooth" });
      }
    }
    makeBtn.addEventListener("click", () => makeVideo(false));
    // While it's being made the editor can be tucked away: a small bar in the corner shows how far
    // along it is (tap it to open the editor again), so other work can go on meanwhile.
    const pill = makingBar(status, () => { back.hidden = false; });
    const awayBtn = el("button", { type: "button", class: "ghost", hidden: true, onclick: () => { back.hidden = true; pill.show(); } }, "Keep working ↘");

    const back = el("div", { class: "vedit-back", role: "dialog", "aria-modal": "true", "aria-label": "Edit video" },
      el("div", { class: "vedit card" },
        el("div", { class: "row vedit-top" }, el("h2", {}, clips.length ? "Edit video" : "Make a video"), el("span", { class: "grow" }),
          el("button", { type: "button", class: "ghost small", onclick: close }, "Close")),
        el("div", { class: "vedit-work" }, toolbox(),
          el("div", { class: "vedit-center" },
            el("div", { class: "vedit-stage" }, player, still),
            el("div", { class: "row vedit-transport" }, frameBack, playBtn, frameFwd, timeLbl, el("span", { class: "grow" }), undoBtn),
            timeline)),
        status,
        effectsBar,
        el("p", { class: "help" }, "Drag along the top of the timeline to find a spot, or drag a clip or picture to change the order. Tools are on the left."),
        list, addBox, driveRow,
        el("div", { class: "row vedit-foot" }, total, el("span", { class: "grow" }),
          el("button", { type: "button", class: "ghost", onclick: close }, "Cancel"), awayBtn, makeBtn)));
    document.addEventListener("keydown", onKey);
    document.body.append(back);
    applyPreview();
    load(0);
    ensureLengths();
    // Opened for a slide's voiceover: its text is ready in the script box.
    if (o.script) { setLines(splitScript(o.script)); voPanel.hidden = false; say("The slide's words are in the voiceover below, one line each. Time them with ⏱ if you like, then Make the voice."); }
    startState = draftState();
    const startLines = structuredClone(voLines);
    let restored = false;
    function applyProject(st) {
      clips.splice(0, clips.length, ...st.clips.map((c) => ({ start: 0, end: null, cuts: [], duration: 0, ...c })));
      voLines = (st.lines || []).map((l) => ({ text: String(l.text || ""), at: l.at == null ? null : +l.at, dur: +l.dur || 0 }));
      if (voLines.length) voPanel.hidden = false;
      if (st.audio) { vo.audio = st.audio; vo.mode = st.mode || "replace"; vo.on = true; voAudio.src = st.audio; voAudio.hidden = false; fx.voice.setAttribute("aria-pressed", "true"); voSync(); }
      if (st.opts) {
        opts.mute = !!st.opts.mute; opts.turn = [0, 90, 180, 270].includes(+st.opts.turn) ? +st.opts.turn : 0; turn.value = String(opts.turn);
        opts.extras = new Set((st.opts.extras || []).filter((k) => fx[k])); applyPreview();
      }
      drawLines(); load(0); ensureLengths();
    }
    let kept = null;
    try { kept = JSON.parse(localStorage.getItem(draftKey) || "null"); } catch (e) { kept = null; }
    if (kept && Date.now() - kept.at < 30 * 864e5) {
      try {
        const st = JSON.parse(kept.state);
        if (st.clips && st.clips.length) {
          applyProject(st);
          restored = true;
          status.replaceChildren("Picked up where you left off. ", el("button", { type: "button", class: "linkish", onclick: () => {
            if (!window.confirm("Start over from the original videos? Your changes here will be thrown away.")) return;
            clips.splice(0, clips.length, ...structuredClone(startClips)); voLines = structuredClone(startLines);
            vo.audio = ""; vo.on = false; voAudio.removeAttribute("src"); voAudio.hidden = true; fx.voice.setAttribute("aria-pressed", "false"); voSync();
            dropDraft(); drawLines(); load(0); say("Started over.");
          } }, "Start over"));
        }
      } catch (e) { dropDraft(); }
    }
    draftOn = true;
    // A video made here: open its edit (the clips, cuts, pictures, drawings, voiceover) to adjust,
    // unless this browser already has newer changes to it.
    if (original && !restored) {
      fetch("/api/manage/media/project?link=" + encodeURIComponent(original)).then((r) => r.json()).then((r) => {
        if (!r.project || !(r.project.clips || []).length) return;
        applyProject(r.project);
        startState = draftState();
        status.replaceChildren("Opened with your edits, ready to adjust. ", el("button", { type: "button", class: "linkish", onclick: () => {
          clips.splice(0, clips.length, ...structuredClone(startClips)); voLines = [];
          vo.audio = ""; vo.on = false; voAudio.removeAttribute("src"); voAudio.hidden = true; fx.voice.setAttribute("aria-pressed", "false"); voSync();
          drawLines(); load(0); ensureLengths(); say("Editing the finished video as one clip.");
        } }, "Edit the finished video instead"));
      }).catch(() => {});
    }
  }

  // ---- Picture editor -------------------------------------------------------------
  // Marker, highlighter, arrow, box, circle, numbered markers, text, blur (hide faces or
  // labels) and crop, turn, undo. Works with a finger, a pencil or a mouse, and saves
  // a new picture (the original stays in the library).
  const COLORS = [["#e02424", "Red"], ["#f5c518", "Yellow"], ["#16a34a", "Green"], ["#2563eb", "Blue"], ["#111111", "Black"], ["#ffffff", "White"]];
  const TOOLS = [["move", "↖", "Select"], ["pen", "✏️", "Marker"], ["hi", "🖍️", "Highlighter"], ["arrow", "➜", "Arrow"], ["box", "▭", "Box"], ["ring", "◯", "Circle"],
    ["num", "①", "Numbers"], ["text", "T", "Text"], ["tbox", "🔤", "Text box"], ["blur", "▦", "Blur"], ["crop", "⛶", "Crop"]];
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
      const r = u * [5, 8, 12][op.size] * (op.k || 1);
      c.beginPath(); c.arc(a[0], a[1], r, 0, Math.PI * 2); c.fill();
      c.lineWidth = Math.max(2, r / 6); c.strokeStyle = "#fff"; c.stroke();
      c.fillStyle = op.color === "#ffffff" || op.color === "#f5c518" ? "#111" : "#fff";
      c.font = `bold ${Math.round(r * 1.2)}px system-ui, sans-serif`; c.textAlign = "center"; c.textBaseline = "middle";
      c.fillText(String(op.n), a[0], a[1] + r * 0.05);
    } else if (op.tool === "tbox") {          // text on a filled box: easy to read over any picture
      const px = Math.round(u * [7, 11, 17][op.size] * (op.k || 1)), pad = px * 0.45, lines = op.text.split("\n");
      c.font = `bold ${px}px system-ui, sans-serif`; c.textBaseline = "top";
      const w = Math.max(...lines.map((l) => c.measureText(l).width)) + pad * 2, h = lines.length * px * 1.2 + pad * 2 - px * 0.2;
      c.beginPath(); (c.roundRect ? c.roundRect(a[0], a[1], w, h, px * 0.3) : c.rect(a[0], a[1], w, h)); c.fill();
      c.fillStyle = op.color === "#ffffff" || op.color === "#f5c518" ? "#111" : "#fff";
      lines.forEach((line, i) => c.fillText(line, a[0] + pad, a[1] + pad + i * px * 1.2));
    } else if (op.tool === "text") {
      const px = Math.round(u * [7, 11, 17][op.size] * (op.k || 1));
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
  // Shapes stay editable after they're drawn: the box around one, its handles, and moving or
  // resizing it. A handle is [x, y, what]; dragging it gives the shape a new size.
  const copyOp = (op) => JSON.parse(JSON.stringify(op));
  function opBox(c, op, u) {
    const [a, b] = [op.a, op.b || op.a];
    if (op.pts) {
      const xs = op.pts.map((p) => p[0]), ys = op.pts.map((p) => p[1]), x = Math.min(...xs), y = Math.min(...ys);
      return { x, y, w: Math.max(...xs) - x, h: Math.max(...ys) - y };
    }
    if (op.tool === "num") { const r = u * [5, 8, 12][op.size] * (op.k || 1); return { x: a[0] - r, y: a[1] - r, w: r * 2, h: r * 2 }; }
    if (op.tool === "text" || op.tool === "tbox") {
      const px = Math.round(u * [7, 11, 17][op.size] * (op.k || 1)), lines = op.text.split("\n"), pad = op.tool === "tbox" ? px * 0.45 : 0;
      c.save(); c.font = `bold ${px}px system-ui, sans-serif`;
      const w = Math.max(...lines.map((l) => c.measureText(l).width)) + pad * 2;
      c.restore();
      return { x: a[0], y: a[1], w, h: lines.length * px * 1.2 + pad * 2 - (pad ? px * 0.2 : 0) };
    }
    return { x: Math.min(a[0], b[0]), y: Math.min(a[1], b[1]), w: Math.abs(b[0] - a[0]), h: Math.abs(b[1] - a[1]) };
  }
  function opHandles(c, op, u) {
    if (op.tool === "arrow") return [[...op.a, "a"], [...op.b, "b"]];
    const r = opBox(c, op, u);
    if (op.tool === "text" || op.tool === "tbox" || op.tool === "num") return [[r.x + r.w, r.y + r.h, "grow"]];
    if (op.pts) return [[r.x, r.y, "tl"], [r.x + r.w, r.y, "tr"], [r.x, r.y + r.h, "bl"], [r.x + r.w, r.y + r.h, "br"]];
    return [[op.a[0], op.a[1], "aa"], [op.b[0], op.a[1], "ba"], [op.a[0], op.b[1], "ab"], [op.b[0], op.b[1], "bb"]];
  }
  function opHit(c, op, u, p) {
    const pad = u * 6;
    if (op.tool === "arrow") {
      const [a, b] = [op.a, op.b], dx = b[0] - a[0], dy = b[1] - a[1], len = dx * dx + dy * dy || 1;
      const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len));
      return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy) < pad;
    }
    const r = opBox(c, op, u);
    return p[0] > r.x - pad && p[0] < r.x + r.w + pad && p[1] > r.y - pad && p[1] < r.y + r.h + pad;
  }
  function opMoved(op0, dx, dy) {
    const op = copyOp(op0), sh = (q) => [q[0] + dx, q[1] + dy];
    if (op.pts) op.pts = op.pts.map(sh);
    if (op.a) op.a = sh(op.a);
    if (op.b) op.b = sh(op.b);
    return op;
  }
  function opResized(c, op0, u, what, p, from) {
    const op = copyOp(op0);
    if (what === "a" || what === "b") { op[what] = p; return op; }
    if (what === "grow") {            // words and numbers keep their shape and get bigger or smaller
      const anchor = op.a;
      const was = Math.hypot(from[0] - anchor[0], from[1] - anchor[1]) || 1;
      op.k = Math.max(0.3, Math.min(8, (op0.k || 1) * Math.hypot(p[0] - anchor[0], p[1] - anchor[1]) / was));
      return op;
    }
    if (op.pts) {                     // a marker line stretches from the opposite corner
      const r = opBox(c, op0, u), ox = what[1] === "l" ? r.x + r.w : r.x, oy = what[0] === "t" ? r.y + r.h : r.y;
      const sx = r.w > 1 ? (p[0] - ox) / ((what[1] === "l" ? r.x : r.x + r.w) - ox) : 1;
      const sy = r.h > 1 ? (p[1] - oy) / ((what[0] === "t" ? r.y : r.y + r.h) - oy) : 1;
      op.pts = op0.pts.map(([x, y]) => [ox + (x - ox) * sx, oy + (y - oy) * sy]);
      return op;
    }
    op[what[0]][0] = p[0]; op[what[1]][1] = p[1];     // boxes, circles and blur: that corner follows
    return op;
  }
  // o: {src, label (optional, names the new copy), onDone(link)}
  function openImageEditor(o) {
    let base = null, ops = [], tool = TOOLS.some(([k]) => k === o.tool) ? o.tool : "pen", color = COLORS[0][0], size = 1, nextNum = 1, drag = null, busy = false, changed = false;
    const history = [], future = [];
    let sel = -1, grab = null;              // the shape picked to move or resize, and the drag doing it
    const view = el("canvas", { class: "iedit-canvas", "aria-label": "Picture to mark up" });
    const stage = el("div", { class: "iedit-stage" }, view);
    const ctx = view.getContext("2d");
    const status = el("p", { class: "muted vedit-status", role: "status", "aria-live": "polite" });
    const unit = () => Math.max(base.width, base.height) / 400;
    const state = () => ({ ops: ops.slice(), base, nextNum });
    const snapshot = () => { history.push(state()); future.length = 0; changed = true; steps(); };
    function steps() { undoBtn.disabled = !history.length; redoBtn.disabled = !future.length; delBtn.hidden = sel < 0; }
    function select(i) { sel = i; steps(); }
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
      if (sel >= 0 && ops[sel]) {
        const u = unit(), r = opBox(ctx, ops[sel], u), hs = Math.max(8, u * 5);
        ctx.save(); ctx.lineWidth = Math.max(1.5, u * 0.8);
        if (ops[sel].tool !== "arrow") {
          ctx.setLineDash([u * 3, u * 2]); ctx.strokeStyle = "#fff"; ctx.strokeRect(r.x - u * 2, r.y - u * 2, r.w + u * 4, r.h + u * 4);
          ctx.lineDashOffset = u * 2.5; ctx.strokeStyle = "#2563eb"; ctx.strokeRect(r.x - u * 2, r.y - u * 2, r.w + u * 4, r.h + u * 4);
        }
        ctx.setLineDash([]);
        for (const [x, y] of opHandles(ctx, ops[sel], u)) { ctx.fillStyle = "#fff"; ctx.strokeStyle = "#2563eb"; ctx.fillRect(x - hs / 2, y - hs / 2, hs, hs); ctx.strokeRect(x - hs / 2, y - hs / 2, hs, hs); }
        ctx.restore();
      }
      if (drag && drag.tool === "crop" && drag.b) {
        const [a, b] = [drag.a, drag.b], x = Math.min(a[0], b[0]), y = Math.min(a[1], b[1]), w = Math.abs(b[0] - a[0]), h = Math.abs(b[1] - a[1]);
        ctx.save(); ctx.fillStyle = "rgba(0,0,0,.5)";
        ctx.fillRect(0, 0, view.width, y); ctx.fillRect(0, y + h, view.width, view.height - y - h);
        ctx.fillRect(0, y, x, h); ctx.fillRect(x + w, y, view.width - x - w, h);
        ctx.strokeStyle = "#fff"; ctx.lineWidth = Math.max(2, unit()); ctx.setLineDash([unit() * 4, unit() * 3]); ctx.strokeRect(x, y, w, h);
        ctx.restore();
      }
    }
    let img = null;
    function load(src) {
    img = new Image();
    img.onload = () => {
      const k = Math.min(1, 2000 / Math.max(img.naturalWidth, img.naturalHeight));
      base = document.createElement("canvas");
      base.width = Math.max(1, Math.round(img.naturalWidth * k)); base.height = Math.max(1, Math.round(img.naturalHeight * k));
      base.getContext("2d").drawImage(img, 0, 0, base.width, base.height);
      redraw();
    };
    img.onerror = () => { status.textContent = "That picture couldn't be opened."; };
    img.src = src;
    }
    load(o.src);
    // Mark up a different picture or edit from the library (the marks so far are dropped).
    const chooseBtn = el("button", { type: "button", class: "ghost small", onclick: () => {
      if (busy) return;
      openLibrary({ kinds: ["image"], upload: true, title: "Choose a picture to mark up", onPick: ([it]) => {
        if (!it || it.link === o.src) return;
        if (changed && !window.confirm("Switch pictures? The marks on this one aren't saved.")) return;
        o = { ...o, src: it.link, label: it.label || o.label };
        base = null; ops = []; history.length = 0; future.length = 0; nextNum = 1; changed = false; sel = -1; steps();
        status.textContent = "";
        load(it.link);
      } });
    } }, "🖼 Choose picture");

    const at = (e) => {
      const r = view.getBoundingClientRect();
      return [Math.max(0, Math.min(view.width, (e.clientX - r.left) * view.width / r.width)),
              Math.max(0, Math.min(view.height, (e.clientY - r.top) * view.height / r.height))];
    };
    view.addEventListener("pointerdown", (e) => {
      if (!base || busy) return;
      e.preventDefault();
      const p = at(e), u = unit();
      if (sel >= 0 && ops[sel]) {           // grab a handle to resize, or the shape itself to move it
        const hs = Math.max(8, u * 5) * 1.6;
        const h = opHandles(ctx, ops[sel], u).find(([x, y]) => Math.abs(p[0] - x) < hs && Math.abs(p[1] - y) < hs);
        if (h || opHit(ctx, ops[sel], u, p)) {
          const kept = future.slice();
          snapshot();
          grab = { op0: ops[sel], p0: p, from: h ? [h[0], h[1]] : null, what: h ? h[2] : null, future: kept };
          view.setPointerCapture(e.pointerId);
          return;
        }
      }
      if (tool === "move") {
        let i = ops.length - 1;
        while (i >= 0 && !opHit(ctx, ops[i], u, p)) i--;
        select(i);
        if (i >= 0) { const kept = future.slice(); snapshot(); grab = { op0: ops[i], p0: p, future: kept }; view.setPointerCapture(e.pointerId); }
        else status.textContent = "Tap a box, circle, arrow, text or line to move or resize it.";
        redraw();
        return;
      }
      if (sel >= 0) { select(-1); redraw(); }
      if (tool === "num") { snapshot(); ops.push({ tool, color, size, a: p, n: nextNum++ }); redraw(); return; }
      if (tool === "text" || tool === "tbox") { typeAt(p, e); return; }
      view.setPointerCapture(e.pointerId);
      drag = tool === "pen" || tool === "hi" ? { tool, color, size, pts: [p] } : { tool, color, size, a: p, b: p };
      redraw();
    });
    // Text: a box opens right where you tapped; type, then Enter (Shift+Enter for a new line) or tap away.
    let typing = null;
    function typeAt(p, e) {
      if (typing) typing.done();
      const r = view.getBoundingClientRect(), sr = stage.getBoundingClientRect(), k = r.width / view.width;
      const px = Math.max(14, Math.round(unit() * [7, 11, 17][size] * k));
      const box = el("textarea", { class: "iedit-type", rows: "1", placeholder: "Type here", "aria-label": "Text to put on the picture",
        style: `left:${r.left - sr.left + p[0] * k}px;top:${r.top - sr.top + p[1] * k}px;font-size:${px}px;` +
          (tool === "tbox" ? `background:${color};color:${color === "#ffffff" || color === "#f5c518" ? "#111" : "#fff"}` : `color:${color}`) });
      const op = { tool, color, size, a: p };
      const done = (keep = true) => {
        if (!typing || typing.box !== box) return;
        typing = null; box.remove();
        const t = box.value.replace(/\s+$/, "");
        if (keep && t.trim()) { snapshot(); ops.push({ ...op, text: t }); select(ops.length - 1); redraw(); }
      };
      box.addEventListener("keydown", (ev) => {
        ev.stopPropagation();
        if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); done(); }
        if (ev.key === "Escape") { ev.preventDefault(); done(false); }
      });
      const room = Math.max(80, sr.right - (r.left + p[0] * k) - 8), meter = document.createElement("canvas").getContext("2d");
      meter.font = `bold ${px}px system-ui, sans-serif`;
      const fit = () => {          // the box grows with the words, up to the picture's edge
        const w = Math.max(...box.value.split("\n").map((l) => meter.measureText(l || " ").width), meter.measureText("Type here").width);
        box.style.width = Math.min(room, w + px * 1.2) + "px";
        box.style.height = "auto"; box.style.height = box.scrollHeight + "px";
      };
      box.addEventListener("input", fit);
      setTimeout(fit, 0);
      box.addEventListener("blur", () => done());
      stage.append(box);
      typing = { box, done };
      setTimeout(() => box.focus(), 0);
    }
    view.addEventListener("pointermove", (e) => {
      if (grab) {
        const p = at(e);
        ops[sel] = grab.what ? opResized(ctx, grab.op0, unit(), grab.what, p, grab.from) : opMoved(grab.op0, p[0] - grab.p0[0], p[1] - grab.p0[1]);
        redraw();
        return;
      }
      if (!drag) return;
      const p = at(e);
      if (drag.pts) drag.pts.push(p); else drag.b = p;
      redraw();
    });
    const finish = () => {
      if (grab) {                           // a tap without a drag changes nothing, so it isn't an undo step
        if (ops[sel] === grab.op0) { history.pop(); future.push(...grab.future); steps(); }
        grab = null; redraw(); return;
      }
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
      } else if (big) {
        snapshot(); ops.push(d);
        if (!d.pts) select(ops.length - 1);   // the new shape shows its handles, ready to resize
      }
      redraw();
    };
    view.addEventListener("pointerup", finish);
    view.addEventListener("pointercancel", () => { drag = null; if (grab) { ops[sel] = grab.op0; grab = null; } redraw(); });

    function turn() {
      if (!base) return;
      snapshot();
      const flat = bake(), c = document.createElement("canvas");
      c.width = flat.height; c.height = flat.width;
      const g = c.getContext("2d");
      g.translate(c.width, 0); g.rotate(Math.PI / 2); g.drawImage(flat, 0, 0);
      base = c; ops = []; select(-1);
      redraw();
    }
    const restore = (from, to) => {
      const h = from.pop();
      if (!h) return;
      to.push(state());
      ops = h.ops; base = h.base; nextNum = h.nextNum; sel = -1; changed = true;
      steps(); redraw();
    };
    const undoBtn = el("button", { type: "button", class: "ghost small", disabled: true, title: "Undo (Ctrl+Z)", onclick: () => restore(history, future) }, "↶ Undo");
    const redoBtn = el("button", { type: "button", class: "ghost small", disabled: true, title: "Redo (Ctrl+Y)", onclick: () => restore(future, history) }, "↷ Redo");
    const delBtn = el("button", { type: "button", class: "ghost small", hidden: true, title: "Delete the selected mark (Delete key)", onclick: () => {
      if (sel < 0) return;
      snapshot(); ops.splice(sel, 1); select(-1); redraw();
    } }, "🗑 Delete");
    const toolBtns = TOOLS.map(([key, ico, label]) => el("button", { type: "button", class: "tool", "data-tool": key, title: label,
      "aria-pressed": key === tool ? "true" : "false", onclick: () => setTool(key) }, el("span", { "aria-hidden": "true" }, ico), el("small", {}, label)));
    function setTool(key) {
      tool = key;
      toolBtns.forEach((b) => b.setAttribute("aria-pressed", b.dataset.tool === key ? "true" : "false"));
      if (["pen", "hi", "num", "crop"].includes(key) && sel >= 0) { select(-1); redraw(); }
      status.textContent = { move: "Tap a mark to pick it. Drag it to move, or drag a corner to resize.", crop: "Drag over the part to keep.", blur: "Drag over faces, names or labels to hide them.",
        num: "Tap to drop numbers in order: 1, 2, 3…", text: "Tap where the text should go, then type.",
        tbox: "Tap where the text box should go, then type. Pick its color above." }[key] || "";
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
      if (document.body.lastElementChild !== back) return;     // the library is open on top
      if (e.key === "Escape") { if (sel >= 0) { select(-1); redraw(); } else close(); }
      const k = e.key.toLowerCase();
      if ((e.ctrlKey || e.metaKey) && (k === "y" || (k === "z" && e.shiftKey))) { e.preventDefault(); redoBtn.click(); }
      else if ((e.ctrlKey || e.metaKey) && k === "z") { e.preventDefault(); undoBtn.click(); }
      if ((e.key === "Delete" || e.key === "Backspace") && sel >= 0 && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) { e.preventDefault(); delBtn.click(); }
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
      el("div", { class: "row vedit-top" }, el("h2", {}, "Mark up picture"), chooseBtn, el("span", { class: "grow" }),
        el("button", { type: "button", class: "ghost small", onclick: close }, "Close")),
      el("div", { class: "iedit-tools" }, ...toolBtns),
      el("div", { class: "row iedit-opts" }, el("div", { class: "iedit-swatches" }, ...swatches), sizeSel, el("span", { class: "grow" }),
        delBtn, undoBtn, redoBtn, el("button", { type: "button", class: "ghost small", onclick: turn }, "↻ Turn")),
      stage,
      status,
      el("div", { class: "row vedit-foot" }, el("span", { class: "muted small" }, "Saves a new copy. The original stays in the media library."),
        el("span", { class: "grow" }), el("button", { type: "button", class: "ghost", onclick: close }, "Cancel"), saveBtn));
    document.addEventListener("keydown", onKey);
    document.body.append(back);
    setTool(tool);
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
  // Edits made in the app (videos made in the video editor, marked-up pictures and frames).
  const isEdit = (it) => /\((edited|marked up)\)|marked-up picture|made video| frame at /i.test(it.label) || /-edited-|-frame-/.test(it.link);
  // The selector: every picture, video and edit in the library, newest first, in tabs. With
  // o.multi, tap to tick several; they're used in the order tapped (the number on each). With
  // o.upload, files from this computer can be added right here (tap or drag) and come in ticked.
  function openLibrary(o) {
    const chosen = [];
    let items = [], tab = "all";
    const kinds = o.kinds || ["video", "image"];
    const grid = el("div", { class: "media-grid" }, el("p", { class: "muted" }, "Loading…"));
    const note = el("p", { class: "muted small", role: "status" });
    const useBtn = el("button", { type: "button", class: "btn", disabled: true }, "Use selected");
    const close = () => { back.remove(); document.removeEventListener("keydown", onKey); };
    const onKey = (e) => { if (e.key === "Escape") close(); };
    const TABS = [["all", "All"], ...(kinds.includes("image") ? [["image", "Pictures"]] : []), ...(kinds.includes("video") ? [["video", "Videos"]] : []), ["edit", "My edits"]];
    const tabs = el("nav", { class: "subnav lib-tabs" });
    const drawTabs = () => tabs.replaceChildren(...TABS.map(([k, t]) => el("a", { href: "#", class: tab === k ? "on" : "",
      onclick: (e) => { e.preventDefault(); tab = k; drawTabs(); draw(); } }, t)));
    const pickedNow = () => { useBtn.disabled = !chosen.length; useBtn.textContent = chosen.length ? `Use ${chosen.length} selected` : "Use selected"; };
    function draw() {
      const shown = items.filter((it) => tab === "all" || (tab === "edit" ? isEdit(it) : it.kind === tab));
      if (!shown.length) {
        grid.replaceChildren(el("p", { class: "muted" }, tab === "edit" ? "No edits yet. Videos you make and pictures you mark up show here."
          : items.length ? "Nothing here." : "Nothing here yet. Add videos and pictures from this computer, or in Lessons › Media."));
        return;
      }
      grid.replaceChildren(...shown.map((it) => {
        const n = chosen.indexOf(it);
        const tile = el("button", { type: "button", class: "media-tile pick", "aria-pressed": n >= 0 ? "true" : "false" },
          n >= 0 ? el("span", { class: "pick-num" }, String(n + 1)) : null, preview(it),
          el("span", { class: "media-name" }, it.label),
          el("span", { class: "muted small" }, [it.kind === "video" ? "Video" : "Picture", isEdit(it) ? "Your edit" : "", sizeText(it.size)].filter(Boolean).join(" · ")));
        tile.addEventListener("click", () => {
          if (!o.multi) { close(); o.onPick([{ kind: it.kind, link: it.link, label: it.label }]); return; }
          const at = chosen.indexOf(it);
          if (at >= 0) chosen.splice(at, 1); else chosen.push(it);
          pickedNow(); draw();
        });
        return tile;
      }));
    }
    listMedia().then((r) => { items = r.items.filter((x) => kinds.includes(x.kind)); draw(); })
      .catch((e) => grid.replaceChildren(el("p", { class: "muted" }, e.message)));
    useBtn.addEventListener("click", () => { close(); o.onPick(chosen.map((it) => ({ kind: it.kind, link: it.link }))); });
    let uploader = null;
    if (o.upload) {
      const file = el("input", { type: "file", multiple: true, accept: kinds.map((k) => (k === "video" ? "video/*,video/mp4,video/quicktime,video/webm" : "image/*,.heic,.heif")).join(",") });
      const addFiles = async (fs) => {
        for (const f of fs) {
          note.textContent = `Adding ${f.name}…`;
          try {
            const r = await upload(f, (pc) => { note.textContent = `Adding ${f.name}: ${pc}%`; });
            const it = { link: r.link, kind: r.kind, label: (f.name || "").replace(/\.[^.]+$/, "") || "New file", size: f.size };
            items = [it, ...items.filter((x) => x.link !== it.link)];
            if (!chosen.some((x) => x.link === it.link)) chosen.push(it);
            tab = "all"; drawTabs(); pickedNow(); draw();
            note.textContent = `${f.name} added and selected.`;
            if (!o.multi) { close(); o.onPick([{ kind: it.kind, link: it.link, label: it.label }]); return; }
          } catch (e) { note.textContent = e.message; }
        }
      };
      file.addEventListener("change", () => { addFiles([...file.files]); file.value = ""; });
      uploader = el("label", { class: "pic-drop upload lib-upload" }, file, el("span", {}, "⬆ Add from this computer (tap or drag files here)"));
      dropZone(uploader, kinds, addFiles, () => { note.textContent = "That isn't a video or picture."; });
    }
    const back = modal("Choose pictures and videos",
      el("div", { class: "row vedit-top" }, el("h2", {}, o.title || "Media library"), el("span", { class: "grow" }),
        el("button", { type: "button", class: "ghost small", onclick: close }, "Close")),
      tabs, uploader, note, grid,
      o.multi ? el("div", { class: "row vedit-foot" }, el("span", { class: "muted small grow" }, "Tap to select. They go in the order you tap them."),
        el("button", { type: "button", class: "ghost", onclick: close }, "Cancel"), useBtn) : null);
    document.addEventListener("keydown", onKey);
    document.body.append(back);
    drawTabs();
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
    const pick = el("input", { type: "file", multiple: true, accept: "video/mp4,video/quicktime,video/webm,video/x-m4v,video/*,image/jpeg,image/png,image/webp,image/gif,image/heic,image/heif,.heic,.heif" });
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
