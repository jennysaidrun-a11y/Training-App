// Lesson player: video with pause-and-ask questions, then the lesson sections
// with checkpoint questions, then the end quiz. The first answer to each
// question is what counts; after a wrong answer you see why and try again.
(function () {
  const L = window.LESSON;
  const root = document.getElementById("player");
  const firstTry = {};           // question index -> first choice picked
  const asked = new Set();

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

  function paragraphs(text) {
    const box = el("div", { class: "text" });
    for (const para of text.trim().split(/\n\s*\n/)) {
      const lines = para.split("\n").map((s) => s.trim()).filter(Boolean);
      if (lines.every((s) => /^\d+\.\s/.test(s))) {
        const ol = el("ol");
        lines.forEach((s) => ol.append(el("li", {}, s.replace(/^\d+\.\s*/, ""))));
        box.append(ol);
      } else {
        box.append(el("p", {}, lines.join(" ")));
      }
    }
    return box;
  }

  // Shows one question in `host`; resolves once it's answered correctly.
  function ask(i, host) {
    asked.add(i);
    const q = L.questions[i];
    return new Promise((done) => {
      const feedback = el("div", { class: "feedback", role: "status" });
      const choices = el("div", { class: "choices" });
      const box = el("div", { class: "question" }, el("p", { class: "q" }, q.q), choices, feedback);
      q.choices.forEach((c, ci) => {
        const b = el("button", { class: "choice", type: "button" }, c);
        b.addEventListener("click", async () => {
          if (!(i in firstTry)) firstTry[i] = ci;
          choices.querySelectorAll("button").forEach((x) => (x.disabled = true));
          const r = await fetch(`/api/lesson/${L.id}/check`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ question: i, choice: ci }),
          }).then((x) => x.json());
          feedback.replaceChildren();
          b.classList.add(r.correct ? "right" : "wrong");
          feedback.append(el("p", {}, (r.correct ? "Right. " : "Not quite. ") + (r.why || "")));
          if (r.correct) {
            feedback.append(el("button", { type: "button", class: "next", onclick: () => { box.remove(); done(); } }, "Continue"));
          } else {
            feedback.append(el("button", { type: "button", class: "quiet", onclick: () => {
              b.classList.remove("wrong");
              feedback.replaceChildren();
              choices.querySelectorAll("button").forEach((x) => (x.disabled = false));
            } }, "Try again"));
          }
        });
        choices.append(b);
      });
      host.append(box);
      box.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }

  // ---- Video -----------------------------------------------------------------

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

  // A small wrapper so YouTube and plain video files behave the same.
  async function makeVideo(url, host) {
    const id = youtubeId(url);
    if (id) {
      const slot = el("div", { id: "yt" });
      host.append(el("div", { class: "video" }, slot));
      await loadYouTubeApi();
      return new Promise((resolve) => {
        const p = new YT.Player("yt", {
          videoId: id,
          playerVars: { rel: 0, modestbranding: 1 },
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
    const v = el("video", { controls: "", preload: "metadata", src: url });
    host.append(el("div", { class: "video" }, v));
    return {
      time: () => v.currentTime,
      pause: () => v.pause(),
      play: () => v.play(),
      onEnd: (fn) => v.addEventListener("ended", fn),
    };
  }

  function playVideo(host) {
    return new Promise(async (done) => {
      const timed = L.questions.map((q, i) => ({ ...q, i })).filter((q) => q.at != null).sort((a, b) => a.at - b.at);
      const player = await makeVideo(L.video, host);
      const qhost = el("div");
      const skip = el("button", { type: "button", class: "quiet" }, "Continue to the lesson");
      host.append(qhost, el("p", { class: "muted" }, "The video pauses to ask questions. ", skip));
      let busy = false, finished = false;
      const finish = () => { if (!finished) { finished = true; clearInterval(timer); done(); } };
      skip.addEventListener("click", finish);
      player.onEnd(() => { if (!busy) finish(); });
      const timer = setInterval(async () => {
        if (busy) return;
        const next = timed.find((q) => !asked.has(q.i) && player.time() >= q.at);
        if (!next) return;
        busy = true;
        player.pause();
        await ask(next.i, qhost);
        busy = false;
        player.play();
      }, 300);
    });
  }

  // ---- Lesson flow ------------------------------------------------------------

  async function run() {
    root.replaceChildren();
    if (L.video) {
      const vbox = el("section", { class: "step" });
      root.append(vbox);
      await playVideo(vbox);
    }
    for (let s = 0; s < L.sections.length; s++) {
      const sec = L.sections[s];
      const box = el("section", { class: "step" }, el("h2", {}, sec.heading), paragraphs(sec.text));
      root.append(box);
      box.scrollIntoView({ behavior: "smooth", block: "start" });
      const checks = L.questions.map((q, i) => ({ ...q, i })).filter((q) => q.after_section === s && !asked.has(q.i));
      for (const q of checks) await ask(q.i, box);
      const isLast = s === L.sections.length - 1;
      await new Promise((go) => {
        const b = el("button", { type: "button", class: "next" }, isLast ? "On to the quiz" : "Next");
        b.addEventListener("click", () => { b.remove(); go(); });
        box.append(b);
      });
    }
    const rest = L.questions.map((q, i) => i).filter((i) => !asked.has(i));
    if (rest.length) {
      const quiz = el("section", { class: "step" }, el("h2", {}, "Quiz"));
      root.append(quiz);
      for (const i of rest) await ask(i, quiz);
    }
    await finishLesson();
  }

  async function finishLesson() {
    const r = await fetch(`/api/lesson/${L.id}/finish`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ worker: window.WORKER, answers: firstTry }),
    }).then((x) => x.json());
    const box = el("section", { class: "step result " + (r.passed ? "pass" : "fail") },
      el("h2", {}, r.passed ? "Passed" : "Not passed yet"),
      el("p", {}, `You got ${r.right} of ${r.total} right the first time (${r.score}%). You need ${r.pass_mark}% to pass.`));
    if (!r.passed) box.append(el("button", { type: "button", class: "next", onclick: () => location.reload() }, "Take it again"));
    else if (window.WORKER != null) box.append(el("a", { class: "button", href: `/me/${window.WORKER}` }, "Back to my lessons"));
    root.append(box);
    box.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  const start = el("button", { type: "button", class: "next big", onclick: run }, "Start");
  root.append(el("p", {}, `${L.sections.length} short sections and ${L.questions.length} questions.`), start);
})();
