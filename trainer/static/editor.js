// Lesson editor as a slide deck. The rail on the left shows every slide in the
// order a worker sees it (cover, readings with their questions, rules); the
// middle shows the slide being edited, laid out like the worker's screen; the
// panel on the right is Claude, which can draft or change the whole deck.
(function () {
  const D = window.DECK;                     // {id,title,summary,roles,video,slides,citations,sources}
  const ROLES = window.ROLES;
  const rail = document.getElementById("rail");
  const canvas = document.getElementById("canvas");
  const errorsBox = document.getElementById("errors");
  const saveBtn = document.getElementById("save-btn");
  const saveNote = document.getElementById("save-note");
  const deckTitle = document.getElementById("deck-title");
  let current = 0;          // 0 = cover, 1..n = slides, n+1 = rules
  let dirty = false;
  let slideErrors = {};     // deck index -> [messages]
  let checks = {};          // citation ref -> {found, name, error}
  for (const [ref, s] of Object.entries(window.RULE_STATUS || {})) {
    checks[ref] = { found: s.found, name: s.name || "", error: s.last_error || "" };
  }

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
  const touch = () => { dirty = true; saveNote.textContent = "Not saved yet"; };
  const rulesIndex = () => D.slides.length + 1;
  const fmtTime = (s) => (s == null || s === "" ? "" : typeof s === "number" ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}` : String(s));
  const parseTime = (t) => {
    t = String(t).trim();
    if (!t) return null;
    const m = t.match(/^(\d+):([0-5]\d)$/);
    if (m) return +m[1] * 60 + +m[2];
    return /^\d+$/.test(t) ? +t : t;     // anything else goes to the server, which says what's wrong
  };
  function autosize(t) { t.style.height = "auto"; t.style.height = t.scrollHeight + 4 + "px"; }
  function area(attrs) {
    const t = el("textarea", attrs);
    t.addEventListener("input", () => autosize(t));
    requestAnimationFrame(() => autosize(t));
    return t;
  }

  // ---- Rail -------------------------------------------------------------------
  function partNumber(i) {   // i: index in D.slides
    return D.slides.slice(0, i + 1).filter((s) => s.type === "reading").length;
  }
  function thumb(index, kind, label, sub) {
    const b = el("button", { type: "button", class: `thumb ${kind}${index === current ? " on" : ""}${slideErrors[index] ? " has-error" : ""}`,
      onclick: () => show(index), "aria-current": index === current ? "true" : null },
      el("span", { class: "num" }, kind === "cover" ? "★" : kind === "rules" ? "§" : String(index)),
      el("span", { class: "mini" }, el("span", { class: "kind" }, sub), el("span", { class: "txt" }, label || "Untitled")));
    if (kind === "reading" || kind === "question") {
      b.draggable = true;
      b.addEventListener("dragstart", (e) => { e.dataTransfer.setData("text/plain", String(index)); b.classList.add("dragging"); });
      b.addEventListener("dragend", () => b.classList.remove("dragging"));
      b.addEventListener("dragover", (e) => { e.preventDefault(); b.classList.add("drop"); });
      b.addEventListener("dragleave", () => b.classList.remove("drop"));
      b.addEventListener("drop", (e) => {
        e.preventDefault();
        const from = +e.dataTransfer.getData("text/plain");
        if (from >= 1 && from <= D.slides.length && from !== index) move(from, index);
      });
    }
    return b;
  }
  function drawRail() {
    const items = [thumb(0, "cover", D.title || "Untitled lesson", "Cover")];
    D.slides.forEach((s, i) => {
      if (s.type === "reading") items.push(thumb(i + 1, "reading", s.heading, `Part ${partNumber(i)}`));
      else items.push(thumb(i + 1, "question", s.q, s.at != null && s.at !== "" ? `Question · video ${fmtTime(s.at)}` : "Question"));
    });
    items.push(thumb(rulesIndex(), "rules", `${D.citations.length} rule${D.citations.length === 1 ? "" : "s"}`, "Rules & sources"));
    items.push(el("div", { class: "rail-add" },
      el("button", { type: "button", class: "ghost small", onclick: () => add("reading") }, "+ Reading"),
      el("button", { type: "button", class: "ghost small", onclick: () => add("question") }, "+ Question")));
    rail.replaceChildren(...items);
    const on = rail.querySelector(".thumb.on");
    if (on) on.scrollIntoView({ block: "nearest", inline: "nearest" });
    deckTitle.textContent = D.title || "New lesson";
  }

  function add(type) {
    const slide = type === "reading" ? { type, heading: "", text: "" }
      : { type, q: "", choices: ["", ""], answer: 0, why: "", at: null };
    // New slides go right after the one being edited (or at the end from the cover/rules).
    const at = current >= 1 && current <= D.slides.length ? current : D.slides.length;
    D.slides.splice(at, 0, slide);
    touch();
    show(at + 1);
    const first = canvas.querySelector("input, textarea");
    if (first) first.focus();
  }
  function move(from, to) {   // deck indexes (1-based slides)
    const [s] = D.slides.splice(from - 1, 1);
    D.slides.splice(to - 1, 0, s);
    slideErrors = {};
    touch();
    show(to);
  }
  function remove(index) {
    const s = D.slides[index - 1];
    const empty = s.type === "reading" ? !s.heading && !s.text : !s.q && s.choices.every((c) => !c);
    if (!empty && !confirm("Delete this slide?")) return;
    D.slides.splice(index - 1, 1);
    slideErrors = {};
    touch();
    show(Math.min(index, D.slides.length) || 0);
  }

  // ---- Canvas -----------------------------------------------------------------
  function toolbar(index) {
    const n = D.slides.length;
    return el("div", { class: "slide-tools" },
      el("span", { class: "eyebrow" }, `Slide ${index} of ${n}`),
      el("span", { class: "grow" }),
      el("button", { type: "button", class: "ghost small", disabled: index <= 1, onclick: () => move(index, index - 1), title: "Move earlier" }, "↑ Earlier"),
      el("button", { type: "button", class: "ghost small", disabled: index >= n, onclick: () => move(index, index + 1), title: "Move later" }, "↓ Later"),
      el("button", { type: "button", class: "ghost small", onclick: () => { D.slides.splice(index, 0, JSON.parse(JSON.stringify(D.slides[index - 1]))); touch(); show(index + 1); } }, "Duplicate"),
      el("button", { type: "button", class: "ghost small danger", onclick: () => remove(index) }, "Delete"));
  }
  function field(label, input, help) {
    return el("label", { class: "field" }, el("span", { class: "lbl" }, label), help ? el("span", { class: "help" }, help) : null, input);
  }
  function bind(input, obj, key, after) {
    input.addEventListener("input", () => { obj[key] = input.value; touch(); if (after) after(); });
    return input;
  }
  const refreshRailSoon = (() => { let t; return () => { clearTimeout(t); t = setTimeout(drawRail, 250); }; })();

  function coverSlide() {
    const chips = el("div", { class: "chips" },
      ...[{ id: "all", name: "Everyone" }, ...ROLES].map((r) => {
        const box = el("input", { type: "checkbox", value: r.id, checked: D.roles.includes(r.id) });
        box.addEventListener("change", () => {
          D.roles = D.roles.filter((x) => x !== r.id);
          if (box.checked) D.roles.push(r.id);
          touch();
        });
        return el("label", { class: "chip" }, box, el("span", {}, r.name));
      }));
    return el("div", { class: "slide cover" },
      el("p", { class: "eyebrow" }, "Cover"),
      bind(el("input", { class: "as-h1", value: D.title, placeholder: "Lesson title", "aria-label": "Title" }), D, "title", refreshRailSoon),
      bind(el("input", { class: "as-sub", value: D.summary, placeholder: "One line: what workers will learn", "aria-label": "Summary" }), D, "summary"),
      el("div", { class: "field" }, el("span", { class: "lbl" }, "Who takes it"), chips),
      videoField());
  }

  function videoField() {
    const box = el("div", { class: "field video-field" }, el("span", { class: "lbl" }, "Video (optional)"));
    const draw = () => {
      const kids = [];
      if (D.video && D.video.startsWith("/media/")) kids.push(el("video", { class: "preview", src: D.video, controls: true, preload: "metadata" }));
      else if (D.video) kids.push(el("p", { class: "muted" }, "Plays from: ", el("a", { href: D.video, target: "_blank", rel: "noopener" }, D.video)));
      const file = el("input", { type: "file", accept: "video/mp4,video/quicktime,video/webm,video/x-m4v,video/*" });
      file.addEventListener("change", () => file.files[0] && upload(file.files[0]));
      kids.push(el("div", { class: "row" },
        el("label", { class: "upload" }, file, el("span", { class: "btn blue small" }, D.video ? "Replace video" : "Upload a video")),
        D.video ? el("button", { type: "button", class: "ghost small danger", onclick: () => { D.video = ""; touch(); draw(); } }, "Remove") : null));
      const meter = el("div", { class: "meter light", hidden: true }, el("span", { style: "width:0%" }));
      const note = el("p", { class: "muted", hidden: true });
      kids.push(meter, note);
      const link = el("input", { value: D.video.startsWith("/media/") ? "" : D.video, placeholder: "or paste a YouTube link" });
      link.addEventListener("change", () => { D.video = link.value.trim(); touch(); draw(); });
      kids.push(link, el("span", { class: "help" }, "MP4 plays everywhere. On iPhone: Settings › Camera › Formats › Most Compatible."));
      box.replaceChildren(el("span", { class: "lbl" }, "Video (optional)"), ...kids);

      function upload(f) {
        const form = new FormData();
        form.append("video_file", f);
        form.append("lesson", D.id || D.title || "lesson");
        const xhr = new XMLHttpRequest();
        xhr.open("POST", "/api/manage/video");
        meter.hidden = note.hidden = false;
        saveBtn.disabled = true;
        xhr.upload.onprogress = (ev) => {
          if (!ev.lengthComputable) return;
          const pct = Math.round((100 * ev.loaded) / ev.total);
          meter.firstElementChild.style.width = pct + "%";
          note.textContent = `Uploading ${f.name}: ${pct}%`;
        };
        xhr.onload = () => {
          saveBtn.disabled = false;
          let r = {};
          try { r = JSON.parse(xhr.responseText); } catch (e) { /* shown below */ }
          if (xhr.status === 200 && r.video) { D.video = r.video; touch(); draw(); saveNote.textContent = "Video uploaded. Save the lesson to keep it."; }
          else { note.textContent = r.error || r.detail || "The upload didn't work. Try again."; }
        };
        xhr.onerror = () => { saveBtn.disabled = false; note.textContent = "The upload didn't go through. Check the connection and try again."; };
        xhr.send(form);
      }
    };
    draw();
    return box;
  }

  function readingSlide(index) {
    const s = D.slides[index - 1];
    const reads = D.slides.filter((x) => x.type === "reading").length;
    return el("div", { class: "slide reading-slide" },
      el("p", { class: "eyebrow" }, `Part ${partNumber(index - 1)} of ${reads}`),
      bind(el("input", { class: "as-h1", value: s.heading, placeholder: "Heading, e.g. Washing hands", "aria-label": "Heading" }), s, "heading", refreshRailSoon),
      bind(area({ class: "as-body", value: s.text, rows: 6, placeholder: "2-5 short sentences.\n\nFor steps, one per line:\n1. Stop the machine\n2. Lock it out", "aria-label": "Text" }), s, "text"),
      el("p", { class: "help" }, "Leave a blank line between paragraphs. Lines starting 1. 2. 3. show as numbered steps."));
  }

  function questionSlide(index) {
    const s = D.slides[index - 1];
    const list = el("div", { class: "choices edit" });
    const drawChoices = () => {
      list.replaceChildren(...s.choices.map((c, ci) => {
        const right = el("button", { type: "button", class: "mark" + (s.answer === ci ? " on" : ""), title: "Mark as the right answer",
          "aria-label": `Answer ${ci + 1} is right`, "aria-pressed": s.answer === ci ? "true" : "false",
          onclick: () => { s.answer = ci; touch(); drawChoices(); } }, s.answer === ci ? "✓" : String(ci + 1));
        const input = bind(el("input", { value: c, placeholder: ci === s.answer ? "The right answer" : "A wrong answer" }), s.choices, ci);
        const del = s.choices.length > 2 ? el("button", { type: "button", class: "ghost small x", "aria-label": "Remove answer",
          onclick: () => { s.choices.splice(ci, 1); if (s.answer >= s.choices.length || s.answer === ci) s.answer = 0; else if (s.answer > ci) s.answer--; touch(); drawChoices(); } }, "✕") : null;
        return el("div", { class: "choice-edit" + (s.answer === ci ? " right" : "") }, right, input, del);
      }), s.choices.length < 6 ? el("button", { type: "button", class: "ghost small", onclick: () => { s.choices.push(""); touch(); drawChoices(); } }, "+ Answer") : "");
    };
    drawChoices();
    const at = el("input", { value: fmtTime(s.at), placeholder: "e.g. 1:30", class: "short" });
    at.addEventListener("input", () => { s.at = parseTime(at.value); touch(); refreshRailSoon(); });
    const before = D.slides.slice(0, index - 1).some((x) => x.type === "reading");
    return el("div", { class: "slide question-slide" },
      el("p", { class: "eyebrow" }, before ? "Check what you read" : "Quick check during the video"),
      bind(area({ class: "as-q", value: s.q, rows: 2, placeholder: "The question", "aria-label": "Question" }), s, "q", refreshRailSoon),
      list,
      el("p", { class: "help" }, "Tap the number next to the right answer to mark it."),
      field("Why (shown after they answer)", bind(area({ value: s.why, rows: 2, placeholder: "One sentence: why the right answer is right." }), s, "why")),
      field("Ask it during the video (optional)", at, before
        ? "Leave empty to ask it after the reading above. With a time, the video pauses there to ask it."
        : "This question comes before any reading, so give it a time on the video, or move it after a reading."));
  }

  function rulesSlide() {
    const list = el("div", { class: "cites" });
    const drawCites = () => {
      list.replaceChildren(...D.citations.map((ref, i) => {
        const input = el("input", { value: ref, placeholder: "29 CFR 1910.147 or 8 CCR 3314" });
        input.addEventListener("input", () => { D.citations[i] = input.value; touch(); });
        return el("div", { class: "cite" }, badge(ref), input,
          el("button", { type: "button", class: "ghost small x", "aria-label": "Remove rule", onclick: () => { D.citations.splice(i, 1); touch(); drawCites(); drawRail(); } }, "✕"));
      }));
    };
    drawCites();
    const verifyBtn = el("button", { type: "button", class: "ghost small", onclick: async () => {
      verifyBtn.disabled = true; verifyBtn.textContent = "Checking…";
      await verify(D.citations.filter((c) => c.trim()));
      verifyBtn.disabled = false; verifyBtn.textContent = "Check these rules now";
      drawCites();
    } }, "Check these rules now");
    const srcList = el("div", { class: "srcs" });
    const drawSources = () => {
      srcList.replaceChildren(...D.sources.map((s, i) => el("div", { class: "src" },
        bind(el("input", { value: s.title, placeholder: "Title" }), s, "title"),
        bind(el("input", { value: s.url, placeholder: "https://…", type: "url" }), s, "url"),
        el("button", { type: "button", class: "ghost small x", "aria-label": "Remove link", onclick: () => { D.sources.splice(i, 1); touch(); drawSources(); } }, "✕"))));
    };
    drawSources();
    return el("div", { class: "slide rules-slide" },
      el("p", { class: "eyebrow" }, "Rules & sources"),
      el("h2", {}, "Rules it's based on"),
      el("p", { class: "help" }, "Every lesson cites at least one government rule. The app checks them every day and tells you when one changes."),
      list,
      el("div", { class: "row" },
        el("button", { type: "button", class: "ghost small", onclick: () => { D.citations.push(""); touch(); drawCites(); list.lastChild.querySelector("input").focus(); } }, "+ Rule"),
        verifyBtn),
      el("h2", { style: "margin-top:26px" }, "Source links"),
      el("p", { class: "help" }, "Where the material came from, for anyone who wants to read more."),
      srcList,
      el("button", { type: "button", class: "ghost small", onclick: () => { D.sources.push({ title: "", url: "" }); touch(); drawSources(); } }, "+ Link"));
  }

  function badge(ref) {
    const c = checks[(ref || "").trim().toUpperCase().replace(/\s+/g, " ")] || checks[(ref || "").trim()];
    if (!c) return el("span", { class: "pill", title: "Not checked yet" }, "Not checked");
    if (c.found) return el("span", { class: "pill done", title: c.name || "Found at the official source" }, "✓ Found");
    if (c.error && c.found == null) return el("span", { class: "pill warn", title: c.error }, "Couldn't check");
    return el("span", { class: "pill bad", title: c.error || "Not found at the official source" }, "Not found");
  }
  async function verify(refs) {
    if (!refs.length) return;
    try {
      const r = await fetch("/api/manage/verify-citations", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ citations: refs }) }).then((x) => x.json());
      for (const c of r.checks) checks[c.ref] = c;
    } catch (e) { /* badges stay as they were */ }
  }

  function show(index) {
    current = Math.max(0, Math.min(index, rulesIndex()));
    let slide;
    if (current === 0) slide = coverSlide();
    else if (current === rulesIndex()) slide = rulesSlide();
    else slide = D.slides[current - 1].type === "reading" ? readingSlide(current) : questionSlide(current);
    const errs = slideErrors[current];
    canvas.replaceChildren(...[
      current >= 1 && current < rulesIndex() ? toolbar(current) : el("div", { class: "slide-tools" }, el("span", { class: "eyebrow" }, current === 0 ? "Lesson settings" : "Last: what it's based on")),
      errs ? el("div", { class: "note bad" }, ...errs.map((e) => el("div", {}, e))) : null,
      slide,
      el("div", { class: "pager" },
        el("button", { type: "button", class: "ghost small", disabled: current === 0, onclick: () => show(current - 1) }, "‹ Back"),
        el("button", { type: "button", class: "ghost small", disabled: current === rulesIndex(), onclick: () => show(current + 1) }, "Next ›"))].filter(Boolean));
    drawRail();
  }

  // ---- Save -------------------------------------------------------------------
  function payload() {
    const retake = document.getElementById("retake");
    return {
      ...D,
      citations: D.citations.map((c) => c.trim()).filter(Boolean),
      sources: D.sources.filter((s) => s.title.trim() || s.url.trim()),
      // Blank answers are dropped; the right answer keeps pointing at the same text.
      slides: D.slides.map((s) => (s.type === "question" ? { ...s, choices: s.choices.filter((c) => c.trim()),
        answer: (s.choices[s.answer] || "").trim() ? s.choices.slice(0, s.answer).filter((c) => c.trim()).length : -1 } : s)),
      retake: retake ? retake.checked : false,
    };
  }
  async function save() {
    saveBtn.disabled = true;
    saveBtn.textContent = "Saving…";
    let r, ok = false;
    try {
      const res = await fetch(`/api/manage/lesson/${D.id || "new"}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload()) });
      r = await res.json();
      ok = res.ok;
    } catch (e) {
      r = { errors: [{ slide: null, error: "Couldn't reach the app. Check the connection and try again." }] };
    }
    saveBtn.disabled = false;
    saveBtn.textContent = "Save lesson";
    slideErrors = {};
    if (ok) {
      dirty = false;
      errorsBox.hidden = true;
      saveNote.textContent = "Saved ✓";
      if (!D.id) { D.id = r.id; history.replaceState(null, "", `/manage/lesson/${r.id}`); window.DECK_NEW = false; }
      const retake = document.getElementById("retake");
      if (retake) retake.checked = false;
      show(current);
      return;
    }
    const errs = r.errors || [{ slide: null, error: r.detail || "Something went wrong." }];
    const general = [];
    for (const e of errs) {
      const idx = e.slide === 0 ? 0 : e.slide === "rules" ? rulesIndex() : typeof e.slide === "number" ? e.slide : null;
      if (idx == null) general.push(e.error);
      else (slideErrors[idx] = slideErrors[idx] || []).push(e.error);
    }
    const firstBad = Object.keys(slideErrors).map(Number).sort((a, b) => a - b)[0];
    errorsBox.replaceChildren(el("strong", {}, "Nothing was saved yet. "),
      el("span", {}, general.length ? general.join(" ") : `${Object.keys(slideErrors).length} slide(s) need a fix: they're marked in red.`));
    errorsBox.hidden = false;
    saveNote.textContent = "";
    show(firstBad != null ? firstBad : current);
  }
  saveBtn.addEventListener("click", save);
  document.addEventListener("keydown", (e) => { if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); save(); } });
  window.addEventListener("beforeunload", (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } });

  // ---- Claude panel -----------------------------------------------------------
  const assist = document.getElementById("assist");
  const shade = document.getElementById("shade");
  const openSheet = () => { assist.classList.add("open"); shade.hidden = false; };
  const closeSheet = () => { assist.classList.remove("open"); shade.hidden = true; };
  document.getElementById("ask-open").addEventListener("click", openSheet);
  document.getElementById("ask-close").addEventListener("click", closeSheet);
  shade.addEventListener("click", closeSheet);

  if (window.RESEARCH_READY) {
    const chat = document.getElementById("chat");
    const form = document.getElementById("chat-form");
    const input = document.getElementById("chat-input");
    const send = document.getElementById("chat-send");
    const linkUrl = document.getElementById("link-url");
    const linkGo = document.getElementById("link-go");
    const history = [];
    let busy = false;

    input.addEventListener("input", () => autosize(input));
    input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } });
    form.addEventListener("submit", (e) => { e.preventDefault(); ask(input.value); });
    document.querySelectorAll("#suggest .chip-btn").forEach((b) => b.addEventListener("click", () => ask(b.textContent)));
    linkGo.addEventListener("click", () => {
      const url = linkUrl.value.trim();
      if (!/^https?:\/\//.test(url)) { linkUrl.focus(); linkUrl.setCustomValidity("Paste a link starting with https://"); linkUrl.reportValidity(); return; }
      linkUrl.setCustomValidity("");
      ask(`Start from this existing training and turn it into a lesson for our bakery: ${url}`);
    });

    const hasContent = () => D.slides.some((s) => (s.type === "reading" ? s.heading || s.text : s.q));
    function bubble(role, ...kids) {
      const b = el("div", { class: `msg ${role}` }, ...kids);
      chat.append(b);
      chat.scrollTop = chat.scrollHeight;
      return b;
    }
    function draftCard(lesson, resultChecks) {
      for (const c of resultChecks || []) checks[c.ref] = c;
      const reads = lesson.slides.filter((s) => s.type === "reading").length;
      const qs = lesson.slides.length - reads;
      const use = el("button", { type: "button", class: "small" }, "Use this draft");
      use.addEventListener("click", () => {
        if (hasContent() && !confirm("Replace the slides in the editor with Claude's draft? (The video stays.)")) return;
        Object.assign(D, { title: lesson.title, summary: lesson.summary, roles: lesson.roles, slides: JSON.parse(JSON.stringify(lesson.slides)),
          citations: [...lesson.citations], sources: lesson.sources.map((s) => ({ ...s })) });
        slideErrors = {};
        errorsBox.hidden = true;
        touch();
        show(0);
        use.textContent = "In the editor ✓";
        use.disabled = true;
        saveNote.textContent = "Draft in the editor. Read each slide, then save.";
        if (window.matchMedia("(max-width: 1100px)").matches) closeSheet();
      });
      return el("div", { class: "draft" },
        el("b", {}, lesson.title || "Draft lesson"),
        el("div", { class: "muted" }, `${reads} reading${reads === 1 ? "" : "s"}, ${qs} question${qs === 1 ? "" : "s"}`),
        lesson.citations.length ? el("div", { class: "draft-cites" }, ...lesson.citations.map((ref) => el("span", { class: "cite-chip" }, badge(ref), " ", ref))) : el("div", { class: "pill warn" }, "No rules cited yet"),
        use);
    }
    async function ask(text) {
      text = (text || "").trim();
      if (!text || busy) return;
      busy = true;
      send.disabled = linkGo.disabled = true;
      document.getElementById("suggest")?.remove();
      history.push({ role: "user", text });
      bubble("user", text);
      input.value = ""; autosize(input);
      const wait = bubble("claude pending", el("span", { class: "dots" }, el("i"), el("i"), el("i")), " Researching and checking the rules. This can take a minute or two.");
      let r;
      try {
        const res = await fetch("/api/manage/research", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ history, draft: payload() }) });
        r = await res.json();
        if (!res.ok) r = { reply: r.error === "setup" ? "Claude isn't set up yet (no API key)." : r.error || r.detail || "Something went wrong. Try again.", failed: true };
      } catch (e) {
        r = { reply: "Couldn't reach the app. Check the connection and try again.", failed: true };
      }
      wait.remove();
      if (!r.failed) history.push({ role: "assistant", text: r.reply || "" });
      else history.pop();
      const kids = [];
      for (const para of (r.reply || "").split(/\n\s*\n/)) if (para.trim()) kids.push(el("p", {}, para.trim()));
      if (r.lesson) kids.push(draftCard(r.lesson, r.checks));
      if (r.searched && r.searched.length) {
        kids.push(el("details", { class: "read-list" }, el("summary", {}, `Pages Claude read (${r.searched.length})`),
          el("ul", {}, ...r.searched.slice(0, 12).map((u) => el("li", {}, el("a", { href: u, target: "_blank", rel: "noopener" }, u.replace(/^https?:\/\//, "")))))));
      }
      bubble("claude" + (r.failed ? " failed" : ""), ...kids);
      busy = false;
      send.disabled = linkGo.disabled = false;
    }
  }

  show(D.slides.length ? 1 : 0);
  if (!D.slides.length && window.DECK_NEW) {
    D.slides.push({ type: "reading", heading: "", text: "" });
    show(0);
  }
})();
