(function () {
  const state = {
    me: null,
    puzzles: [],
    startedAt: 0,
    remain: 90,
    timer: null,
    playedToday: false,
    channel: [],
    channelTimer: null,
    playing: null,
    locked: false,
  };
  const params = new URLSearchParams(location.search);
  const track = {
    src: params.get("src") || params.get("from") || "",
    ep: params.get("ep") || "",
  };

  const $ = (sel) => document.querySelector(sel);

  async function api(path, opts = {}) {
    const url = new URL(`/api/join${path}`, location.origin);
    if (track.src) url.searchParams.set("src", track.src);
    if (track.ep) url.searchParams.set("ep", track.ep);
    const res = await fetch(url.pathname + url.search, {
      method: opts.method || "GET",
      headers: opts.body ? { "Content-Type": "application/json" } : undefined,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.statusText);
    return data;
  }

  function showErr(id, err) {
    const el = $(id);
    el.hidden = !err;
    el.textContent = err || "";
  }

  function renderAuth() {
    const joined = Boolean(state.me);
    $("#auth-card").hidden = joined;
    $("#btn-start").hidden = !joined || state.playedToday;
    const channelPts = Number(state.me?.channelPoints || 0);
    const winPts = Number(state.me?.points || 0);
    if ($("#channel-score")) {
      $("#channel-score").textContent = `${channelPts} channel points`;
    }
    if (joined) {
      $("#channel-copy").textContent =
        `Welcome, ${state.me.username}. Your channel total is ${channelPts}. Open a video — the 100 timer starts. One tap. Right answers add points. Wrong answers never take them away.`;
      $("#play-copy").textContent = state.playedToday
        ? `Nice work, ${state.me.username}. You have ${winPts} winner points. Come back tomorrow for a new riddle set.`
        : `Welcome, ${state.me.username}. You have ${winPts} winner points. You have 90 seconds for three daily riddles. One try today.`;
    } else {
      $("#channel-copy").textContent =
        "Join to watch the videos and tap one answer. A 100-point timer starts. Faster correct answers score more. A miss never takes points away.";
    }
  }

  function renderWinners(data) {
    const today = data.todayWinner;
    $("#today-winner").textContent = today
      ? today.line
      : "No video winner yet. Join after tapping a Short to enter.";
    $("#winners-list").innerHTML = (data.winners || [])
      .map((w) => `<li><strong>${w.username}</strong> · ${w.points || 0} pts · ${w.date}</li>`)
      .join("");
  }

  function tick() {
    state.remain = Math.max(0, state.remain - 1);
    const m = Math.floor(state.remain / 60);
    const s = String(state.remain % 60).padStart(2, "0");
    $("#timer").textContent = `${m}:${s}`;
    if (state.remain <= 0) {
      clearInterval(state.timer);
      submitAnswers();
    }
  }

  async function startPuzzles() {
    showErr("#play-err");
    const data = await api("/puzzles");
    state.puzzles = data.puzzles || [];
    state.remain = data.seconds || 90;
    state.startedAt = Date.now();
    $("#timer").hidden = false;
    $("#btn-start").hidden = true;
    $("#btn-submit").hidden = false;
    $("#puzzle-list").innerHTML = state.puzzles
      .map((p, i) => {
        const choices = (p.choices || [])
          .map(
            (c) =>
              `<label><input type="radio" name="p${i}" value="${c.replaceAll('"', "&quot;")}" /> ${c}</label>`
          )
          .join("");
        return `<li><p>${p.prompt}</p>${choices}</li>`;
      })
      .join("");
    clearInterval(state.timer);
    tick();
    state.timer = setInterval(tick, 1000);
  }

  async function submitAnswers() {
    clearInterval(state.timer);
    const answers = state.puzzles.map((_, i) => {
      const picked = document.querySelector(`input[name="p${i}"]:checked`);
      return picked ? picked.value : "";
    });
    const elapsed = Date.now() - state.startedAt;
    const result = await api("/puzzles/submit", { method: "POST", body: { answers, elapsedMs: elapsed } });
    state.playedToday = true;
    $("#btn-submit").hidden = true;
    $("#timer").hidden = true;
    const ok = $("#play-ok");
    ok.hidden = false;
    ok.textContent = result.youWon
      ? `You scored ${result.score}/${result.maxScore}. You are today’s winner — your name can appear on tomorrow’s videos.`
      : `You scored ${result.score}/${result.maxScore}. Today’s winner is ${result.winner?.username || "still being decided"}.`;
    renderAuth();
    const winners = await api("/winners");
    renderWinners({ todayWinner: winners.today, winners: winners.list });
  }

  document.querySelectorAll("[data-auth]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("[data-auth]").forEach((b) => b.classList.toggle("is-on", b === btn));
      $("#join-form").hidden = btn.dataset.auth !== "join";
      $("#login-form").hidden = btn.dataset.auth !== "login";
    });
  });

  $("#join-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    showErr("#auth-err");
    const form = e.currentTarget;
    try {
      const data = await api("/signup", {
        method: "POST",
        body: {
          username: form.username.value,
          age: Number(form.age.value),
          password: form.password.value,
        },
      });
      state.me = data.me;
      loadChannel().catch(() => {});
      if (data.geo?.country) {
        const geo = $("#geo-line");
        geo.hidden = false;
        geo.textContent = `We see this visit from ${data.geo.region ? `${data.geo.region}, ` : ""}${data.geo.country}.`;
      }
      renderAuth();
    } catch (err) {
      showErr("#auth-err", err.message);
    }
  });

  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    showErr("#auth-err");
    const form = e.currentTarget;
    try {
      const data = await api("/login", { method: "POST", body: { username: form.username.value, password: form.password.value } });
      state.me = data.me;
      renderAuth();
      loadChannel().catch(() => {});
    } catch (err) {
      showErr("#auth-err", err.message);
    }
  });

  function renderChannel() {
    const list = $("#channel-list");
    const filters = $("#channel-filters");
    if (!list) return;
    const series = ["ALL", ...new Set(state.channel.map((p) => p.series).filter(Boolean))];
    if (filters && !filters.dataset.ready) {
      const labels = { ALL: "All videos", JIG: "Jigsaw", ALIEN: "Alien Monkeys", BLUR: "Blur" };
      filters.innerHTML = series
        .map((name) => `<button type="button" data-series="${name}" class="${name === "ALL" ? "is-on" : ""}">${labels[name] || name}</button>`)
        .join("");
      filters.dataset.ready = "1";
      filters.addEventListener("click", (ev) => {
        const btn = ev.target.closest("[data-series]");
        if (!btn) return;
        filters.querySelectorAll("button").forEach((b) => b.classList.toggle("is-on", b === btn));
        paintCatalog(btn.dataset.series);
      });
    }
    const on = filters?.querySelector("button.is-on")?.dataset.series || "ALL";
    paintCatalog(on);
  }

  function paintCatalog(series) {
    const list = $("#channel-list");
    const labels = { JIG: "Jigsaw", ALIEN: "Alien Monkeys", BLUR: "Blur" };
    const rows = state.channel.filter((p) => series === "ALL" || p.series === series);
    if (!rows.length) {
      list.innerHTML = `<p class="hint">The video library is still loading. Come back in a moment.</p>`;
      return;
    }
    list.innerHTML = rows
      .map((p) => {
        const mark = p.solved ? (p.correct ? "Solved" : "Tried") : "Play";
        const seriesName = labels[p.series] || p.series || "Puzzle";
        return `<button type="button" class="clip ${p.solved ? "is-done" : ""}" data-id="${p.id}" data-ep="${p.episodeId || ""}">
          <strong>${p.title || p.episodeId}</strong>
          <span>${seriesName} · ${mark}${p.pointsAwarded ? ` · +${p.pointsAwarded}` : ""}</span>
        </button>`;
      })
      .join("");
  }

  async function loadChannel() {
    const data = await api("/channel");
    state.channel = data.puzzles || [];
    if (state.me) state.me.channelPoints = data.channelPoints;
    renderChannel();
    renderAuth();
    if (track.ep && state.me) {
      const hit = state.channel.find((p) => (p.episodeId || "").toUpperCase() === track.ep.toUpperCase());
      if (hit && !hit.solved) openPlayer(hit.id);
    }
  }

  async function openPlayer(puzzleId) {
    if (!state.me) {
      showErr("#channel-err", "Join or log in first, then pick a video.");
      return;
    }
    const puzzle = state.channel.find((p) => p.id === puzzleId);
    if (!puzzle) return;
    if (puzzle.solved) {
      showErr("#channel-err", "You already played that video. Pick a new one.");
      return;
    }
    showErr("#channel-err");
    showErr("#player-err");
    $("#player-ok").hidden = true;
    state.locked = false;
    const start = await api(`/channel/puzzles/${puzzleId}/start`, { method: "POST", body: {} });
    const startedMs = Date.parse(start.startedAt) || Date.now();
    const elapsed = Math.max(0, Math.floor((Date.now() - startedMs) / 1000));
    const remain = Math.max(1, (start.timerStart || 100) - elapsed);
    state.playing = { ...puzzle, remain, startedMs };
    $("#player-prompt").textContent = puzzle.prompt;
    $("#player-timer").textContent = String(state.playing.remain);
    const video = $("#player-video");
    if (puzzle.hasVideo && puzzle.episodeId) {
      video.src = `/api/join/channel/video/${encodeURIComponent(puzzle.episodeId)}`;
      video.play().catch(() => {});
    } else {
      video.removeAttribute("src");
      video.load();
    }
    $("#player-choices").innerHTML = (puzzle.choices || [])
      .map((c) => `<button type="button" data-choice="${c.replaceAll('"', "&quot;")}">${c}</button>`)
      .join("");
    $("#player").showModal();
    clearInterval(state.channelTimer);
    state.channelTimer = setInterval(() => {
      if (!state.playing || state.locked) return;
      state.playing.remain = Math.max(1, state.playing.remain - 1);
      $("#player-timer").textContent = String(state.playing.remain);
    }, 1000);
  }

  async function pickChoice(choice) {
    if (!state.playing || state.locked) return;
    state.locked = true;
    clearInterval(state.channelTimer);
    $("#player-choices").querySelectorAll("button").forEach((b) => (b.disabled = true));
    try {
      const result = await api(`/channel/puzzles/${state.playing.id}/answer`, {
        method: "POST",
        body: { choice },
      });
      if (result.me) state.me = result.me;
      const box = result.correct ? $("#player-ok") : $("#player-err");
      const other = result.correct ? $("#player-err") : $("#player-ok");
      other.hidden = true;
      box.hidden = false;
      box.textContent = result.message || (result.correct ? "Yes!" : "You missed this time");
      const card = state.channel.find((p) => p.id === state.playing.id);
      if (card) {
        card.solved = true;
        card.correct = !!result.correct;
        card.pointsAwarded = result.pointsAwarded || 0;
      }
      renderChannel();
      renderAuth();
    } catch (err) {
      state.locked = false;
      showErr("#player-err", err.message);
    }
  }

  $("#btn-start").addEventListener("click", () => startPuzzles().catch((err) => showErr("#play-err", err.message)));
  $("#btn-submit").addEventListener("click", () => submitAnswers().catch((err) => showErr("#play-err", err.message)));
  $("#channel-list")?.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-id]");
    if (!btn) return;
    openPlayer(Number(btn.dataset.id)).catch((err) => showErr("#channel-err", err.message));
  });
  $("#player-choices")?.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-choice]");
    if (!btn) return;
    pickChoice(btn.dataset.choice);
  });
  $("#player")?.addEventListener("close", () => {
    clearInterval(state.channelTimer);
    const video = $("#player-video");
    video.pause();
    video.removeAttribute("src");
    video.load();
    state.playing = null;
    state.locked = false;
  });

  api("/bootstrap")
    .then((data) => {
      state.me = data.me;
      state.playedToday = !!data.playedToday;
      if (data.minAge) $("#join-form").age.min = data.minAge;
      if (data.caughtFromShort || track.src) {
        const banner = $("#from-short");
        if (banner) {
          banner.hidden = false;
          banner.textContent = state.me
            ? `We caught this visit from a PUZMANIA Short. You have ${state.me.points || 0} winner points.`
            : "You came from a PUZMANIA Short — join with a username to keep your points.";
        }
      }
      if (data.geo?.country) {
        const geo = $("#geo-line");
        geo.hidden = false;
        geo.textContent = data.geo.region
          ? `This visit looks like ${data.geo.region}, ${data.geo.country}.`
          : `This visit looks like ${data.geo.country}.`;
      }
      renderAuth();
      renderWinners(data);
      loadChannel().catch(() => {});
    })
    .catch(() => {});
})();
