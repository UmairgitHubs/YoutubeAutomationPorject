(function () {
  const $ = (sel) => document.querySelector(sel);
  const state = { slots: [], open: null, locked: false };

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      method: opts.method || "GET",
      credentials: "same-origin",
      headers: opts.body ? { "Content-Type": "application/json" } : undefined,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Try again");
    return data;
  }

  const ART = {
    alien: "/resimler_aa/aa_37a.png",
    blur: "/resimler_aa/aa_16.png",
    jig: "/resimler_aa/aa_40.png",
  };

  function render(slots) {
    state.slots = slots;
    $("#trio").innerHTML = slots
      .map((slot) => {
        const theme = String(slot.series || "").toLowerCase();
        const art = ART[theme] || ART.jig;
        const puzzle = slot.puzzle;
        const picture = `<img class="poster" src="${art}" alt="" />`;
        if (!puzzle) {
          return `<article class="card is-empty is-${theme}">
            <span class="frame">${picture}<span class="card-note">All done</span></span>
            <span class="card-name">${slot.title}</span>
          </article>`;
        }
        return `<button type="button" class="card is-${theme}" data-id="${puzzle.id}">
          <span class="frame">${picture}<span class="tap">Play</span></span>
          <span class="card-name">${slot.title}</span>
        </button>`;
      })
      .join("");
  }

  async function load() {
    const data = await api("/api/join/board");
    const slots = data.slots || [];
    render(slots);
    const err = $("#board-err");
    const waiting = slots.some((slot) => slot.puzzle);
    err.hidden = waiting;
    err.textContent = waiting ? "" : "New puzzles are on the way.";
  }

  function closeStage() {
    if ($("#stage").open) $("#stage").close();
  }

  async function openPuzzle(id) {
    const slot = state.slots.find((item) => item.puzzle && item.puzzle.id === id);
    if (!slot) return;
    const puzzle = slot.puzzle;
    state.open = puzzle;
    state.locked = false;
    $("#stage-name").textContent = slot.title;
    $("#stage-question").textContent = puzzle.question || "What is the answer?";
    $("#stage-msg").hidden = true;
    $("#stage-ok").hidden = true;
    const letters = puzzle.letters || ["A", "B", "C"];
    const box = $("#letters");
    box.classList.toggle("is-three", letters.length < 4);
    box.hidden = false;
    box.innerHTML = letters
      .map(
        (letter) =>
          `<button type="button" class="letter letter-${letter.toLowerCase()}" data-letter="${letter}">${letter}</button>`
      )
      .join("");
    const video = $("#stage-video");
    if (puzzle.hasVideo && puzzle.episodeId) {
      video.hidden = false;
      video.src = `/api/join/channel/video/${encodeURIComponent(puzzle.episodeId)}`;
      video.play().catch(() => {});
    } else {
      video.hidden = true;
      video.removeAttribute("src");
      video.load();
    }
    $("#stage").showModal();
    try {
      await api(`/api/join/channel/puzzles/${puzzle.id}/start`, { method: "POST", body: {} });
    } catch (err) {
      $("#stage-msg").hidden = false;
      $("#stage-msg").textContent = err.message;
    }
  }

  async function pickLetter(letter) {
    if (!state.open || state.locked) return;
    state.locked = true;
    $("#letters").querySelectorAll("button").forEach((btn) => {
      btn.disabled = true;
    });
    try {
      const result = await api(`/api/join/channel/puzzles/${state.open.id}/answer`, {
        method: "POST",
        body: { choice: letter },
      });
      const msg = $("#stage-msg");
      msg.hidden = false;
      msg.textContent = result.correct ? "Yes!" : result.message || "Try the next one";
      $("#stage-ok").hidden = false;
    } catch (err) {
      state.locked = false;
      $("#letters").querySelectorAll("button").forEach((btn) => {
        btn.disabled = false;
      });
      $("#stage-msg").hidden = false;
      $("#stage-msg").textContent = err.message;
    }
  }

  $("#trio").addEventListener("click", (event) => {
    const card = event.target.closest("[data-id]");
    if (!card) return;
    openPuzzle(Number(card.dataset.id)).catch((err) => {
      const box = $("#board-err");
      box.hidden = false;
      box.textContent = err.message;
    });
  });

  $("#letters").addEventListener("click", (event) => {
    const btn = event.target.closest("[data-letter]");
    if (!btn) return;
    pickLetter(btn.dataset.letter);
  });

  $("#stage-back").addEventListener("click", () => closeStage());
  $("#stage-ok").addEventListener("click", () => closeStage());
  $("#stage").addEventListener("close", () => {
    const answered = state.locked;
    const video = $("#stage-video");
    video.pause();
    video.removeAttribute("src");
    video.load();
    state.open = null;
    state.locked = false;
    if (answered) load().catch(() => {});
  });

  function openAdmin() {
    $("#admin-err").hidden = true;
    $("#admin-box").showModal();
  }

  $("#admin-open").addEventListener("click", openAdmin);
  $("#admin-back").addEventListener("click", () => $("#admin-box").close());
  $("#admin-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const err = $("#admin-err");
    err.hidden = true;
    const res = await fetch("/api/studio/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({
        username: form.username.value,
        password: form.password.value,
      }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      err.hidden = false;
      err.textContent = data.detail || "Name or password is not right.";
      return;
    }
    location.href = "/desk";
  });

  if (new URLSearchParams(location.search).get("admin") === "1") {
    history.replaceState({}, "", "/");
    openAdmin();
  }

  load().catch((err) => {
    const box = $("#board-err");
    box.hidden = false;
    box.textContent = err.message;
  });
})();
