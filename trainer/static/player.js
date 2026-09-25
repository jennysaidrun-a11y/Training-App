// Lesson player, one screen at a time (the Duolingo pattern):
//   video (pauses for timed questions) -> each section, followed by its
//   checkpoint questions -> the remaining questions -> result.
// Pick an answer, press CHECK, and the footer turns green or red with the
// reason. The first answer to each question is what's scored; after a wrong
// answer you try again until it's right, so everyone leaves knowing it.
(function () {
  const L = window.LESSON;
  const stage = document.getElementById("stage");
  const footer = document.getElementById("footer");
  const verdict = document.getElementById("verdict");
  const actions = document.getElementById("actions");
  const bar = document.getElementById("progress");
  const firstTry = {};
  const asked = new Set();
  let onKey = null;   // keyboard handler for the current screen

  const el = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") n.className = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v);
    }
    for (const kid of kids) if (kid != null) n.append(kid);
    return n;
  };
  const iconFrom = (id) => document.getElementById(id).content.firstElementChild.cloneNode(true);

  document.addEventListener("keydown", (e) => {
    if (onKey && !e.metaKey && !e.ctrlKey && !e.altKey && e.target.tagName !== "INPUT") onKey(e);
  });

  // ---- Steps and progress ---------------------------------------------------
  const steps = [];
  if (L.video) steps.push({ type: "video" });
  L.sections.forEach((s, si) => {
    steps.push({ type: "section", si });
    L.questions.forEach((q, i) => { if (q.after_section === si) steps.push({ type: "question", i }); });
  });
  L.questions.forEach((q, i) => {
    if (q.after_section == null || q.after_section >= L.sections.length) steps.push({ type: "question", i, quiz: true });
  });
  const setProgress = (n) => { bar.style.width = `${Math.max(3, (100 * n) / steps.length)}%`; };

  // ---- Footer ---------------------------------------------------------------
  function setFooter({ mood = "", title = "", text = "", buttons = [] }) {
    footer.className = "footer" + (mood ? " " + mood : "");
    verdict.replaceChildren();
    if (title) {
      verdict.append(
        el("div", { class: "icon" }, iconFrom(mood === "good" ? "icon-check" : "icon-x")),
        el("div", {}, el("h3", {}, title), text ? el("p", {}, text) : null)
      );
    }
    actions.replaceChildren(...buttons);
  }
  const button = (label, onClick, cls = "", disabled = false) => {
    const b = el("button", { type: "button", class: cls }, label);
    b.disabled = disabled;
    b.addEventListener("click", onClick);
    return b;
  };

  // ---- Reading --------------------------------------------------------------
  function reading(text) {
    const box = el("div", { class: "reading" });
    for (const para of text.trim().split(/\n\s*\n/)) {
      const lines = para.split("\n").map((s) => s.trim()).filter(Boolean);
      if (lines.length && lines.every((s) => /^\d+\.\s/.test(s))) {
        box.append(el("ol", {}, ...lines.map((s) => el("li", {}, el("span", {}, s.replace(/^\d+\.\s*/, ""))))));
      } else {
        box.append(el("p", {}, lines.join(" ")));
      }
    }
    return box;
  }

  function showSection(si, next) {
    const s = L.sections[si];
    stage.replaceChildren(el("p", { class: "eyebrow" }, `Part ${si + 1} of ${L.sections.length}`), el("h1", {}, s.heading),
      s.image ? el("img", { class: "slide-img", src: s.image, alt: "" }) : null, reading(s.text));
    setFooter({ buttons: [button("Continue", next)] });
    onKey = (e) => { if (e.key === "Enter") next(); };
  }

  // ---- Questions ------------------------------------------------------------
  // Renders question i into `host`; calls done() once it's answered right.
  function showQuestion(i, host, done, eyebrow) {
    asked.add(i);
    const q = L.questions[i];
    let picked = null;
    const choiceButtons = q.choices.map((c, ci) => {
      const b = el("button", { type: "button", class: "choice" }, el("span", { class: "key" }, String(ci + 1)), el("span", {}, c));
      b.addEventListener("click", () => pick(ci));
      return b;
    });
    const checkBtn = button("Check", () => check(), "", true);
    host.append(eyebrow ? el("p", { class: "eyebrow" }, eyebrow) : null, el("p", { class: "q" }, q.q), el("div", { class: "choices" }, ...choiceButtons));
    setFooter({ buttons: [checkBtn] });

    function pick(ci) {
      picked = ci;
      choiceButtons.forEach((b, k) => b.classList.toggle("picked", k === ci));
      checkBtn.disabled = false;
    }
    async function check() {
      if (picked == null) return;
      if (!(i in firstTry)) firstTry[i] = picked;
      checkBtn.disabled = true;
      let r;
      try {
        r = await fetch(`/api/lesson/${L.id}/check`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: i, choice: picked }),
        }).then((x) => x.json());
      } catch (err) {
        checkBtn.disabled = false;
        setFooter({ mood: "bad", title: "Couldn't reach the app", text: "Check the connection and press Check again.", buttons: [checkBtn] });
        return;
      }
      choiceButtons.forEach((b) => (b.disabled = true));
      const mine = choiceButtons[picked];
      mine.classList.remove("picked");
      mine.classList.add(r.correct ? "right" : "wrong");
      if (r.correct) {
        const go = () => { onKey = null; done(); };
        setFooter({ mood: "good", title: "Nice job!", text: r.why, buttons: [button("Continue", go)] });
        onKey = (e) => { if (e.key === "Enter") go(); };
      } else {
        const retry = () => {
          mine.classList.remove("wrong");
          choiceButtons.forEach((b) => (b.disabled = false));
          picked = null;
          setFooter({ buttons: [checkBtn] });
          checkBtn.disabled = true;
          onKey = keys;
        };
        setFooter({ mood: "bad", title: "Not quite", text: r.why, buttons: [button("Try again", retry, "red")] });
        onKey = (e) => { if (e.key === "Enter") retry(); };
      }
    }
    const keys = (e) => {
      const n = parseInt(e.key, 10);
      if (n >= 1 && n <= choiceButtons.length && !choiceButtons[n - 1].disabled) pick(n - 1);
      else if (e.key === "Enter" && !checkBtn.disabled) check();
    };
    onKey = keys;
  }

  // ---- Video ----------------------------------------------------------------
  function youtubeId(url) {
    const m = url.match(/(?:youtu\.be\/|v=|\/embed\/|\/shorts\/)([\w-]{11})/);
    return m ? m[1] : null;
  }
  function loadYouTubeApi() {
    if (window.YT && window.YT.Player) return Promise.resolve();
    return new Promise((resolve) => {
      window.onYouTubeIframeAPIReady = resolve;
      document.head.append(el("script", { src: "https://www.youtube.com/iframe_api" }));
    });
  }
  // YouTube and plain video files behind one small interface.
  async function makeVideo(url, host) {
    const id = youtubeId(url);
    if (id) {
      host.append(el("div", { class: "video" }, el("div", { id: "yt" })));
      await loadYouTubeApi();
      return new Promise((resolve) => {
        const p = new YT.Player("yt", {
          videoId: id,
          playerVars: { rel: 0, modestbranding: 1, playsinline: 1 },
          events: {
            onReady: () => resolve({
              time: () => p.getCurrentTime(),
              pause: () => p.pauseVideo(),
              play: () => p.playVideo(),
              onEnd: (fn) => p.addEventListener("onStateChange", (e) => e.data === 0 && fn()),
            }),
          },
        });
      });
    }
    const v = el("video", { controls: "", preload: "metadata", playsinline: "", src: url });
    host.append(el("div", { class: "video" }, v));
    return { time: () => v.currentTime, pause: () => v.pause(), play: () => v.play(), onEnd: (fn) => v.addEventListener("ended", fn) };
  }

  async function showVideo(next) {
    const timed = L.questions.map((q, i) => ({ ...q, i })).filter((q) => q.at != null).sort((a, b) => a.at - b.at);
    const qhost = el("div");
    stage.replaceChildren(el("p", { class: "eyebrow" }, "Watch"), el("h1", {}, L.title));
    const player = await makeVideo(L.video, stage);
    stage.append(qhost);
    let busy = false, finished = false;
    const finish = () => { if (!finished) { finished = true; clearInterval(timer); onKey = null; next(); } };
    const idle = () => {
      setFooter({ text: "", buttons: [button(timed.length ? "Skip to the reading" : "Continue", finish, timed.length ? "ghost" : "")] });
    };
    idle();
    player.onEnd(() => { if (!busy) finish(); });
    const timer = setInterval(() => {
      if (busy) return;
      const q = timed.find((t) => !asked.has(t.i) && player.time() >= t.at);
      if (!q) return;
      busy = true;
      player.pause();
      qhost.replaceChildren();
      qhost.className = "video-q";
      showQuestion(q.i, qhost, () => { qhost.replaceChildren(); qhost.className = ""; busy = false; idle(); player.play(); }, "Quick check");
    }, 300);
  }

  // ---- Flow -----------------------------------------------------------------
  function go(n) {
    setProgress(n);
    window.scrollTo({ top: 0 });
    if (n >= steps.length) return finishLesson();
    const step = steps[n];
    const next = () => go(n + 1);
    if (step.type === "video") return showVideo(next);
    if (step.type === "section") return showSection(step.si, next);
    if (asked.has(step.i)) return next();   // already asked during the video
    stage.replaceChildren();
    showQuestion(step.i, stage, next, step.quiz ? "Quiz" : "Check what you read");
  }

  async function finishLesson() {
    onKey = null;
    setFooter({ buttons: [button("Saving…", () => {}, "", true)] });
    let r;
    try {
      r = await fetch(`/api/lesson/${L.id}/finish`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ worker: window.WORKER, answers: firstTry }),
      }).then((x) => x.json());
    } catch (err) {
      setFooter({ mood: "bad", title: "Couldn't save your result", text: "Check the connection and try again.", buttons: [button("Try again", finishLesson, "red")] });
      return;
    }
    stage.replaceChildren(
      el("div", { class: "result " + (r.passed ? "pass" : "fail") },
        el("h1", {}, r.passed ? "Lesson complete!" : "Almost there"),
        el("p", { class: "muted" }, r.passed
          ? (window.WORKER != null ? "Saved to your record." : "Preview only: nothing was saved.")
          : `You need ${r.pass_mark}% on the first try to pass. Take it again; you know the answers now.`),
        el("div", { class: "facts" },
          el("div", { class: "fact gold" }, el("b", {}, `${r.score}%`), el("span", {}, "Score")),
          el("div", { class: "fact green" }, el("b", {}, `${r.right}/${r.total}`), el("span", {}, "Right first try")))));
    const back = () => { location.href = window.BACK; };
    setFooter({ buttons: r.passed ? [button("Continue", back)] : [button("Later", back, "ghost"), button("Try again", () => location.reload())] });
    onKey = (e) => { if (e.key === "Enter") (r.passed ? back() : location.reload()); };
  }

  // Start screen: the template already shows the intro; wire the button.
  const startBtn = document.getElementById("main-btn");
  const start = () => { onKey = null; go(0); };
  startBtn.addEventListener("click", start);
  onKey = (e) => { if (e.key === "Enter") start(); };

  document.getElementById("close").addEventListener("click", (e) => {
    if (bar.style.width && bar.style.width !== "0%" && !confirm("Leave the lesson? Your progress in it won't be saved.")) e.preventDefault();
  });
})();
