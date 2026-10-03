(function () {
  const STORAGE_KEY = "puzmania.settings";

  const state = {
    view: "overview",
    settings: loadSettings(),
    episodes: structuredClone(EPISODES),
    queueIds: queuedIds(EPISODES),
    history: structuredClone(HISTORY),
    platforms: structuredClone(PLATFORMS),
    gdrive: { configured: false },
    openai: { configured: false, model: "gpt-4o-mini" },
    campaigns: [],
    winners: [],
    leaderboard: [],
    clickStats: { clicks: 0, clickMembers: 0 },
    nextIntroDate: "",
    members: 0,
    joinUrl: "/join",
    addFiles: [],
    libraryFilter: "all",
    historyFilter: "all",
    search: "",
    useApi: false,
  };

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  function loadSettings() {
    try {
      return { ...DEFAULT_SETTINGS, ...JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}") };
    } catch {
      return { ...DEFAULT_SETTINGS };
    }
  }

  function saveSettings() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state.settings));
  }

  function applyBootstrap(data) {
    state.useApi = true;
    state.episodes = data.episodes;
    state.queueIds = data.queue;
    state.settings = data.settings;
    state.history = data.history;
    state.platforms = data.platforms;
    state.gdrive = data.gdrive || { configured: false };
    state.openai = data.openai || { configured: false, model: "gpt-4o-mini" };
    state.campaigns = data.campaigns || [];
    state.winners = data.winners || [];
    state.leaderboard = data.leaderboard || [];
    state.clickStats = data.clickStats || { clicks: 0, clickMembers: 0 };
    state.nextIntroDate = data.nextIntroDate || "";
    state.members = data.members || 0;
    state.joinUrl = data.joinUrl || "/join";
    const notice = document.querySelector(".notice p");
    if (notice) {
      notice.textContent = "Desk is connected. Use ADD to queue videos. They post automatically each morning to the platforms you chose.";
    }
  }

  async function refresh() {
    if (!state.useApi) {
      renderAll();
      return;
    }
    applyBootstrap(await loadBootstrap());
    fillSettings();
    renderAll();
  }

  function queuedIds(list) {
    return list.filter((ep) => overallStatus(ep) !== "published").map((ep) => ep.id);
  }

  function overallStatus(ep) {
    const statuses = [ep.youtube.status, ep.tiktok.status, ep.instagram.status];
    const active = statuses.filter((s) => s !== "skipped");
    if (!active.length) return "skipped";
    if (active.includes("failed")) return "attention";
    if (active.every((s) => s === "success")) return "published";
    return "queued";
  }

  function epById(id) {
    return state.episodes.find((e) => e.id === id);
  }

  function tone(id) {
    return Number(id.replace(/\D/g, "")) % 5;
  }

  function num(id) {
    return id.replace("EP", "");
  }

  function formatDay(iso) {
    const d = new Date(`${iso}T12:00:00`);
    return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  }

  function formatStamp(iso) {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function toast(title, body, tone = "") {
    const el = document.createElement("div");
    el.className = tone ? `toast toast-${tone}` : "toast";
    el.innerHTML = `<b>${esc(title)}</b><span>${esc(body)}</span>`;
    $("#toasts").appendChild(el);
    setTimeout(() => el.remove(), tone === "bad" ? 8000 : 5200);
  }

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function openPublishOverlay(ep) {
    $("#publish-title").textContent = ep.title;
    $("#publish-kicker").textContent = state.settings.dryRun ? "Dry-run publish" : "Publishing";
    $("#publish-status").textContent = "Starting…";
    $("#publish-bar-fill").style.width = "4%";
    $("#publish-steps").innerHTML = "";
    const box = $("#publish-result");
    box.hidden = true;
    box.className = "publish-result";
    box.textContent = "";
    $("#publish-dismiss").hidden = true;
    $("#publish-overlay").hidden = false;
  }

  function closePublishOverlay() {
    $("#publish-overlay").hidden = true;
  }

  function renderPublishJob(job) {
    $("#publish-status").textContent = job.message || "Working…";
    $("#publish-bar-fill").style.width = `${job.percent || 0}%`;
    $("#publish-steps").innerHTML = (job.steps || [])
      .map((step) => {
        const extra = step.status === "running" && step.percent != null ? ` · ${step.percent}%` : "";
        return `<li class="publish-step is-${esc(step.status || "pending")}">
          <span class="publish-step-mark"></span>
          <div>
            <strong>${esc(step.label)}</strong>
            <small>${esc(step.detail || "")}${esc(extra)}</small>
          </div>
        </li>`;
      })
      .join("");
  }

  function finishPublishOverlay(job) {
    const result = job.result || {};
    const failed = job.state === "error" || result.overall === "attention";
    const box = $("#publish-result");
    const summary = job.error || result.summary || (failed ? "Publish finished with errors." : "Publish finished.");
    box.hidden = false;
    box.className = `publish-result ${failed ? "is-bad" : "is-ok"}`;
    box.textContent = result.dry_run && !failed ? `Dry-run finished.\n${summary}` : summary;
    $("#publish-dismiss").hidden = false;
    $("#publish-bar-fill").style.width = failed ? `${job.percent || 100}%` : "100%";
  }

  async function pollPublishJob(jobId) {
    for (let i = 0; i < 900; i += 1) {
      const job = await api(`/jobs/${jobId}`);
      renderPublishJob(job);
      if (job.state === "success" || job.state === "error") return job;
      await sleep(450);
    }
    throw new Error("Publish is taking too long. Check History for the latest status.");
  }

  function setView(name) {
    state.view = name;
    $$(".view").forEach((view) => {
      const on = view.id === `view-${name}`;
      view.hidden = !on;
      view.classList.toggle("is-active", on);
      view.setAttribute("aria-hidden", on ? "false" : "true");
    });
    $$(".nav-item").forEach((btn) => btn.classList.toggle("is-active", btn.dataset.view === name));
    const active = $(`#view-${name}`);
    $("#page-title").textContent = active.dataset.title;
    $("#view-kicker").textContent = active.dataset.kicker;
    location.hash = name;
  }

  function nextEpisode() {
    const queued = state.queueIds.map(epById).find((e) => e && overallStatus(e) === "queued");
    return queued || epById(state.queueIds[0]);
  }

  function attentionCount() {
    return state.episodes.filter((e) => overallStatus(e) === "attention").length;
  }

  function publishedCount() {
    return state.episodes.filter((e) => overallStatus(e) === "published").length;
  }

  function pillFor(status) {
    if (status === "success") return `<span class="pill pill-ok">Success</span>`;
    if (status === "failed") return `<span class="pill pill-bad">Failed</span>`;
    if (status === "skipped") return `<span class="pill pill-pending">Skipped</span>`;
    return `<span class="pill pill-pending">Pending</span>`;
  }

  function coverHTML(ep, extraClass = "") {
    return `
      <div class="${extraClass} tone-${tone(ep.id)}">
        <span class="cover-series">${SERIES.show}</span>
        <span class="cover-ep">${num(ep.id)}</span>
      </div>
    `;
  }

  function renderOverview() {
    const next = nextEpisode();
    if (next) {
      $("#hero-id").textContent = next.id;
      $("#hero-title").textContent = next.title;
      $("#hero-meta").textContent = `Queued for ${formatDay(next.scheduled)} · ${state.settings.publishTime} · ${next.duration}`;
      $("#hero-desc").textContent = next.description;
      $("#hero-cover-ep").textContent = num(next.id);
      $("#hero-cover").className = `hero-cover tone-${tone(next.id)}`;
    }

    $("#stat-row").innerHTML = [
      [String(state.episodes.length), "In library"],
      [String(state.queueIds.length), "In queue"],
      [String(publishedCount()), "Fully published"],
      [String(attentionCount()), "Needs attention"],
    ]
      .map(([n, l]) => `<div class="stat"><b>${n}</b><span>${l}</span></div>`)
      .join("");

    const today = state.episodes.find((e) => e.id === "EP008") || state.episodes.find((e) => overallStatus(e) === "attention");
    const run = today
      ? [
          ["youtube", today.youtube],
          ["tiktok", today.tiktok],
          ["instagram", today.instagram],
        ]
      : [];

    $("#today-run").innerHTML = run
      .map(([name, p]) => {
        const label = p.status === "failed" ? p.error || "Failed" : p.id ? `Posted · ${p.id}` : "Waiting";
        return `<li class="run-item">
          <span class="plat ${name}">${name}</span>
          <span>${label}</span>
          ${pillFor(p.status)}
        </li>`;
      })
      .join("");

    const pill = $("#today-pill");
    if (today && overallStatus(today) === "attention") {
      pill.textContent = "Needs attention";
      pill.className = "pill pill-warn";
    } else {
      pill.textContent = "Idle until morning";
      pill.className = "pill pill-pending";
    }

    $("#coming-list").innerHTML = state.queueIds
      .slice(0, 4)
      .map((id) => {
        const ep = epById(id);
        const st = overallStatus(ep);
        const label = st === "attention" ? "Attention" : "Queued";
        const pill = st === "attention" ? "pill-warn" : "pill-pending";
        return `<div class="coming-item" data-open="${ep.id}">
          <span class="coming-num">${num(ep.id)}</span>
          <div>
            <div class="coming-title">${ep.title}</div>
            <div class="coming-sub">${ep.id} · ${formatDay(ep.scheduled)}</div>
          </div>
          <span class="pill ${pill}">${label}</span>
        </div>`;
      })
      .join("");
  }

  function renderQueue() {
    $("#nav-queue-count").textContent = state.queueIds.length;
    $("#queue-body").innerHTML = state.queueIds
      .map((id, index) => {
        const ep = epById(id);
        return `<tr>
          <td class="mono">${String(index + 1).padStart(2, "0")}</td>
          <td>
            <div class="ep-cell">
              <strong>${ep.title}</strong>
              <small>${ep.id} · ${ep.duration} · ${ep.filename}</small>
            </div>
          </td>
          <td>${formatDay(ep.scheduled)}</td>
          <td>${pillFor(ep.youtube.status)}</td>
          <td>${pillFor(ep.tiktok.status)}</td>
          <td>${pillFor(ep.instagram.status)}</td>
          <td>
            <div class="row-actions">
              <button class="icon-btn" data-move="${id}" data-dir="-1" ${index === 0 ? "disabled" : ""} title="Move up">↑</button>
              <button class="icon-btn" data-move="${id}" data-dir="1" ${index === state.queueIds.length - 1 ? "disabled" : ""} title="Move down">↓</button>
              <button class="icon-btn" data-open="${id}" title="Open">i</button>
              <button class="icon-btn" data-skip="${id}" title="Skip">×</button>
            </div>
          </td>
        </tr>`;
      })
      .join("");
  }

  function renderLibrary() {
    const q = state.search.trim().toLowerCase();
    const items = state.episodes.filter((ep) => {
      const st = overallStatus(ep);
      if (state.libraryFilter === "published" && st !== "published") return false;
      if (state.libraryFilter === "queued" && st !== "queued") return false;
      if (state.libraryFilter === "attention" && st !== "attention") return false;
      if (!q) return true;
      return [ep.id, ep.title, ep.tags.join(" "), ep.filename].join(" ").toLowerCase().includes(q);
    });

    $("#library-grid").innerHTML = items
      .map((ep) => {
        const st = overallStatus(ep);
        const label = st === "published" ? "Published" : st === "attention" ? "Needs attention" : "In queue";
        const pillClass = st === "published" ? "pill-ok" : st === "attention" ? "pill-warn" : "pill-pending";
        return `<article class="card" data-open="${ep.id}">
          ${coverHTML(ep, "card-cover")}
          <div class="card-body">
            <small>${ep.id}</small>
            <h3>${ep.title}</h3>
            <div class="dots" aria-label="Platform status">
              <span class="dot ${ep.youtube.status}" title="YouTube"></span>
              <span class="dot ${ep.tiktok.status}" title="TikTok"></span>
              <span class="dot ${ep.instagram.status}" title="Instagram"></span>
            </div>
            <p style="margin-top:10px"><span class="pill ${pillClass}">${label}</span></p>
          </div>
        </article>`;
      })
      .join("");
  }

  function renderHistory() {
    const rows = state.history.filter((row) => state.historyFilter === "all" || row.platform === state.historyFilter);
    $("#timeline").innerHTML = rows
      .map(
        (row) => `<li class="time-item ${row.result}">
          <div class="time-when">${formatStamp(row.at)} · <span class="plat ${row.platform}">${row.platform}</span> · ${row.episode}</div>
          <div class="time-detail">${row.detail}</div>
        </li>`
      )
      .join("");
  }

  function connectLabel(id, connected) {
    if (id === "youtube") return connected ? "Reconnect channel" : "Connect channel";
    if (id === "tiktok") return connected ? "Reconnect account" : "Connect account";
    return connected ? "Disconnect" : "Connect later";
  }

  function isoToday() {
    const d = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }

  function daysBetween(start, end) {
    const a = new Date(`${start}T12:00:00`);
    const b = new Date(`${end}T12:00:00`);
    return Math.floor((b - a) / 86400000) + 1;
  }

  function renderAddPreview() {
    const preview = $("#add-preview");
    const list = $("#add-video-list");
    if (!preview || !list) return;
    const files = state.addFiles;
    list.innerHTML = files.map((f) => `<li>${esc(f.name)} · ${Math.max(1, Math.round(f.size / 1024))} KB</li>`).join("");
    const start = $("#add-start")?.value;
    const end = $("#add-end")?.value;
    const perDay = Number($("#add-per-day")?.value || 1);
    const loop = $("#add-loop")?.checked;
    if (!files.length || !start || !end) {
      preview.textContent = "Select videos to see the schedule.";
      return;
    }
    const days = daysBetween(start, end);
    if (days < 1) {
      preview.textContent = "End date must be on or after the start date.";
      return;
    }
    const slots = days * perDay;
    const repeats = loop && files.length < slots;
    preview.textContent = repeats
      ? `${files.length} video(s) will fill ${slots} posts across ${days} day(s) (${perDay}/day), repeating from the start when the list runs out. Every third day the winner clip (large name on the video) is placed in front of that day’s video.`
      : `${Math.min(files.length, slots)} video(s) will be queued across ${days} day(s) (${perDay}/day).`;
  }

  function renderCampaigns() {
    const box = $("#campaign-list");
    if (!box) return;
    if (!state.campaigns.length) {
      box.innerHTML = "";
      return;
    }
    box.innerHTML = `<h3 class="kicker">Batches on the list</h3>` + state.campaigns.map((c) => {
      const plats = (c.platforms || []).join(", ");
      return `<article class="campaign-row">
        <div>
          <strong>${esc(c.name)}</strong>
          <small>${esc(c.id)} · ${esc(c.startDate)} → ${esc(c.endDate)} · ${c.videosPerDay}/day · ${esc(plats)}</small>
        </div>
        <span>${c.slotCount} posts</span>
        <span class="pill ${c.status === "active" ? "pill-ok" : "pill-pending"}">${esc(c.status)}</span>
      </article>`;
    }).join("");
  }

  function renderWinners() {
    const stats = $("#winners-stats");
    const list = $("#winner-list");
    const board = $("#leaderboard-list");
    const link = $("#join-site-link");
    const pill = $("#next-intro-pill");
    if (link) link.href = state.joinUrl || "/join";
    if (pill) pill.textContent = state.nextIntroDate ? `Next intro ${state.nextIntroDate}` : "Next intro —";
    const mode = state.settings.winnerMode === "clicks" ? "Real clicks" : "Seed names";
    if (stats) {
      stats.innerHTML = [
        [String(state.members || 0), "Members"],
        [String(state.clickStats.clickMembers || 0), "Caught from Shorts"],
        [String(state.clickStats.clicks || 0), "Short taps"],
        [mode, "Winner source"],
      ].map(([n, l]) => `<div class="stat"><b>${esc(n)}</b><span>${esc(l)}</span></div>`).join("");
    }
    if (list) {
      if (!state.winners.length) {
        list.innerHTML = `<li>No video winner yet. The first publish day writes a name from the seed list onto the winner clip.</li>`;
      } else {
        list.innerHTML = state.winners.map((w) => `<li>
          <div>
            <b>${esc(w.username)}</b>
            <div class="coming-sub">${esc(w.date)} · ${esc(w.source || "seed")} · ${esc(w.episodeId || "queued")}</div>
          </div>
          <span>${esc(w.line || `This weeks winner is ${w.username} with ${w.points}`)}</span>
        </li>`).join("");
      }
    }
    if (board) {
      if (!state.leaderboard.length) {
        board.innerHTML = `<li>No real members with Short taps yet. Keep winner source on random names until people click through.</li>`;
      } else {
        board.innerHTML = state.leaderboard.map((w) => `<li>
          <div>
            <b>${esc(w.username)}</b>
            <div class="coming-sub">${w.clicks} tap${w.clicks === 1 ? "" : "s"} × ${state.settings.pointsMultiplier || 2500}</div>
          </div>
          <span>${w.points} pts</span>
        </li>`).join("");
      }
    }
    const select = $("#feature-member");
    if (select) {
      const options = [`<option value="">Pick a real member…</option>`]
        .concat(state.leaderboard.map((w) => `<option value="${w.memberId}">${esc(w.username)} · ${w.points} pts</option>`));
      select.innerHTML = options.join("");
    }
  }

  function renderPlatforms() {
    $("#platform-grid").innerHTML = state.platforms.map((p) => {
      const cfg = state.settings.platforms[p.id] || p;
      return `<article class="plat-card">
        <span class="pill ${cfg.connected ? "pill-ok" : "pill-warn"}">${cfg.connected ? "Connected" : "Not connected"}</span>
        <h3>${p.name}</h3>
        <div class="api">${p.api} · ${p.method}</div>
        <p>${p.note}</p>
        <button class="btn btn-ghost" type="button" data-connect="${p.id}">${connectLabel(p.id, cfg.connected)}</button>
        <div class="switch">
          <span>Include in daily run</span>
          <button class="toggle ${cfg.enabled ? "is-on" : ""}" type="button" data-toggle="${p.id}" aria-pressed="${cfg.enabled}"></button>
        </div>
      </article>`;
    }).join("") + openaiCard() + gdriveCard();
  }

  function openaiCard() {
    const o = state.openai || {};
    const connected = !!o.configured;
    return `<article class="plat-card">
        <span class="pill ${connected ? "pill-ok" : "pill-warn"}">${connected ? "Key loaded" : "Not configured"}</span>
        <h3>ChatGPT captions</h3>
        <div class="api">OpenAI · ${o.model || "gpt-4o-mini"}</div>
        <p>Before each post, the desk grabs frames from the episode video and asks ChatGPT to write a PUZMANIA Shorts caption (series, episode, winners list, hashtags).</p>
        <p class="hint">${connected ? "OPENAI_API_KEY is set in .env. A rejected key must be replaced with a new secret from platform.openai.com." : "Add OPENAI_API_KEY to .env, then restart the server."}</p>
      </article>`;
  }

  function gdriveCard() {
    const g = state.gdrive || {};
    const connected = !!g.configured;
    const extra = g.lastResult ? ` Last sync: ${g.lastResult}` : "";
    return `<article class="plat-card">
        <span class="pill ${connected ? "pill-ok" : "pill-warn"}">${connected ? "Connected" : "Not configured"}</span>
        <h3>Google Drive</h3>
        <div class="api">Library source · episode folders</div>
        <p>Drop packages like <code>ALIEN_06/video.mp4</code> + <code>metadata.json</code> in a Drive folder, share it with the service account, then set <code>GDRIVE_FOLDER_ID</code> in <code>.env</code>. The app copies files here and posts from disk. Instagram still cannot fetch a Drive share link.</p>
        <p class="hint">${connected ? `Folder …${g.folderHint || ""}.${extra}` : "Not set — local episodes\\ folder is used."}</p>
        <button class="btn btn-ghost" type="button" data-gdrive-sync ${connected ? "" : "disabled"}>${connected ? "Sync from Drive" : "Add credentials in .env"}</button>
      </article>`;
  }

  function fillSettings() {
    const form = $("#settings-form");
    form.publishTime.value = state.settings.publishTime;
    form.timezone.value = state.settings.timezone;
    form.episodesPerRun.value = state.settings.episodesPerRun;
    form.retryAttempts.value = state.settings.retryAttempts;
    form.retryBackoff.value = state.settings.retryBackoff;
    form.notifyEmail.checked = state.settings.notifyEmail;
    form.notifySlack.checked = state.settings.notifySlack;
    form.alertOnFailure.checked = state.settings.alertOnFailure;
    form.dryRun.checked = state.settings.dryRun;
    if (form.winnerMode) form.winnerMode.value = state.settings.winnerMode || "seed";
    if (form.pointsMultiplier) form.pointsMultiplier.value = state.settings.pointsMultiplier || 2500;
    if (form.winnerIntroDays) form.winnerIntroDays.value = state.settings.winnerIntroDays || 3;
    updateDryBadge();
  }

  function updateDryBadge() {
    const badge = $("#dry-badge");
    if (state.settings.dryRun) {
      badge.textContent = "Dry run";
      badge.className = "badge badge-dry";
    } else {
      badge.textContent = "Live";
      badge.className = "badge badge-live";
    }
  }

  function openDrawer(id) {
    const ep = epById(id);
    if (!ep) return;
    $("#drawer-id").textContent = ep.id;
    $("#drawer-title").textContent = ep.title;
    $("#drawer-cover").className = `drawer-cover tone-${tone(ep.id)}`;
    $("#drawer-cover").innerHTML = `<span class="cover-series">${SERIES.show}</span><span class="cover-ep">${num(ep.id)}</span>`;
    $("#drawer-meta").innerHTML = `
      <div><dt>File</dt><dd>${ep.filename}</dd></div>
      <div><dt>Duration</dt><dd>${ep.duration}</dd></div>
      <div><dt>Scheduled</dt><dd>${formatDay(ep.scheduled)}</dd></div>
      <div><dt>Tags</dt><dd>${ep.tags.map((t) => `#${t}`).join("  ")}</dd></div>
      <div><dt>Copy</dt><dd>${ep.description}</dd></div>
    `;
    $("#drawer-platforms").innerHTML = ["youtube", "tiktok", "instagram"]
      .map((p) => {
        const row = ep[p];
        return `<div class="p-row">
          <span class="plat ${p}">${p}</span>
          ${pillFor(row.status)}
          <code>${row.id || row.error || "—"}</code>
        </div>`;
      })
      .join("");

    const inQueue = state.queueIds.includes(ep.id);
    const publishLabel = state.settings.dryRun ? "Dry-run publish" : "Publish now";
    $("#drawer-actions").innerHTML = `
      <button class="btn btn-primary" type="button" data-simulate="${ep.id}">${publishLabel}</button>
      ${inQueue ? `<button class="btn btn-ghost" type="button" data-skip="${ep.id}">Skip this run</button>` : ""}
      ${overallStatus(ep) === "attention" ? `<button class="btn btn-ghost" type="button" data-retry="${ep.id}">Re-trigger failed</button>` : ""}
    `;
    $("#drawer").hidden = false;
  }

  function closeDrawer() {
    $("#drawer").hidden = true;
  }

  async function moveQueue(id, dir) {
    if (state.useApi) {
      await api("/queue/reorder", { method: "POST", body: { episode_id: id, direction: Number(dir) } });
      await refresh();
      toast("Queue updated", `${id} moved ${dir < 0 ? "up" : "down"}.`);
      return;
    }
    const i = state.queueIds.indexOf(id);
    const j = i + Number(dir);
    if (i < 0 || j < 0 || j >= state.queueIds.length) return;
    const copy = [...state.queueIds];
    [copy[i], copy[j]] = [copy[j], copy[i]];
    state.queueIds = copy;
    renderAll();
    toast("Queue updated", `${id} moved ${dir < 0 ? "up" : "down"}.`);
  }

  async function skipEpisode(id) {
    if (state.useApi) {
      await api(`/episodes/${id}/skip`, { method: "POST" });
      closeDrawer();
      await refresh();
      toast("Episode skipped", `${id} will not leave with the next scheduled run.`);
      return;
    }
    const ep = epById(id);
    ["youtube", "tiktok", "instagram"].forEach((p) => {
      if (ep[p].status === "pending") ep[p].status = "skipped";
    });
    state.queueIds = state.queueIds.filter((x) => x !== id);
    closeDrawer();
    renderAll();
    toast("Episode skipped", `${id} will not leave with the next scheduled run.`);
  }

  async function simulatePublish(id) {
    const ep = epById(id) || nextEpisode();
    if (!ep) return;
    if (state.useApi) {
      if (!$("#publish-overlay").hidden && $("#publish-dismiss").hidden) return;
      closeDrawer();
      openPublishOverlay(ep);
      try {
        const started = await api(`/episodes/${ep.id}/publish/start`, { method: "POST" });
        renderPublishJob(started);
        const job = await pollPublishJob(started.id);
        finishPublishOverlay(job);
        await refresh();
        const result = job.result || {};
        const failed = job.state === "error" || result.overall === "attention";
        const detail = (job.error || result.summary || "Done.").replaceAll("\n", " · ");
        toast(
          failed ? "Publish failed" : result.dry_run ? "Dry-run finished" : "Published",
          detail,
          failed ? "bad" : "ok"
        );
      } catch (err) {
        $("#publish-status").textContent = err.message;
        const box = $("#publish-result");
        box.hidden = false;
        box.className = "publish-result is-bad";
        box.textContent = err.message;
        $("#publish-dismiss").hidden = false;
        toast("Publish failed", err.message, "bad");
      }
      return;
    }
    if (!state.settings.dryRun) {
      toast("APIs not connected", "Live mode is on, but there are no credentials yet. Staying local.");
    }
    const enabled = state.settings.platforms;
    ["youtube", "tiktok", "instagram"].forEach((p) => {
      if (!enabled[p].enabled) {
        if (ep[p].status === "pending") ep[p].status = "skipped";
        return;
      }
      if (ep[p].status === "success") return;
      ep[p] = { status: "success", id: `${p.slice(0, 2)}_${Date.now().toString().slice(-6)}` };
    });
    if (overallStatus(ep) === "published" || overallStatus(ep) === "attention") {
      state.queueIds = state.queueIds.filter((x) => x !== ep.id);
    }
    closeDrawer();
    renderAll();
    toast("Publish simulated", `${ep.id} updated locally.`);
  }

  async function retryFailed(id) {
    if (state.useApi) {
      await api(`/episodes/${id}/retry`, { method: "POST" });
      closeDrawer();
      await refresh();
      toast("Queued for retry", `${id} failed platforms are pending again.`);
      return;
    }
    const ep = epById(id);
    ["youtube", "tiktok", "instagram"].forEach((p) => {
      if (ep[p].status === "failed") ep[p].status = "pending";
    });
    if (!state.queueIds.includes(id)) state.queueIds.unshift(id);
    closeDrawer();
    renderAll();
    toast("Queued for retry", `${id} failed platforms are pending again. No API call was made.`);
  }

  function tickClock() {
    const now = new Date();
    $("#live-clock").textContent = now.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    $("#live-date").textContent = now.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "short" });

    const [hh, mm] = state.settings.publishTime.split(":").map(Number);
    const next = new Date(now);
    next.setHours(hh, mm, 0, 0);
    if (next <= now) next.setDate(next.getDate() + 1);
    const ms = next - now;
    const h = Math.floor(ms / 3600000);
    const m = Math.floor((ms % 3600000) / 60000);
    $("#countdown").textContent = `${h}h ${m}m remaining`;
    $("#next-run-label").textContent = next.toLocaleString(undefined, { day: "numeric", month: "short" }) + " · " + state.settings.publishTime;
  }

  function renderAll() {
    $("#nav-queue-count").textContent = state.queueIds.length;
    renderOverview();
    renderQueue();
    renderLibrary();
    renderHistory();
    renderPlatforms();
    renderCampaigns();
    renderWinners();
    renderAddPreview();
    updateDryBadge();
  }

  document.addEventListener("click", (e) => {
    const nav = e.target.closest("[data-view]");
    if (nav && nav.dataset.view) {
      setView(nav.dataset.view);
      return;
    }
    if (e.target.closest("[data-close-drawer]")) closeDrawer();
    if (e.target.closest("#publish-dismiss")) closePublishOverlay();
    const open = e.target.closest("[data-open]");
    if (open) openDrawer(open.dataset.open);
    if (e.target.closest("[data-action='open-next']")) {
      const n = nextEpisode();
      if (n) openDrawer(n.id);
    }
    const fail = (err) => toast("Request failed", err.message);
    const move = e.target.closest("[data-move]");
    if (move) moveQueue(move.dataset.move, move.dataset.dir).catch(fail);
    const skip = e.target.closest("[data-skip]");
    if (skip) skipEpisode(skip.dataset.skip).catch(fail);
    const sim = e.target.closest("[data-simulate]");
    if (sim) simulatePublish(sim.dataset.simulate).catch(fail);
    const retry = e.target.closest("[data-retry]");
    if (retry) retryFailed(retry.dataset.retry).catch(fail);
    if (e.target.closest("[data-action='simulate-publish']")) simulatePublish().catch(fail);
    const toggle = e.target.closest("[data-toggle]");
    if (toggle) {
      const id = toggle.dataset.toggle;
      const next = !state.settings.platforms[id].enabled;
      state.settings.platforms[id].enabled = next;
      if (state.useApi) {
        api(`/platforms/${id}`, { method: "PATCH", body: { enabled: next } })
          .then(() => refresh())
          .catch((err) => toast("Could not update platform", err.message));
      } else {
        saveSettings();
        renderPlatforms();
      }
      toast(next ? "Platform armed" : "Platform paused", `${id} ${next ? "will" : "will not"} be included in the daily run.`);
    }
    const connect = e.target.closest("[data-connect]");
    if (connect) {
      const id = connect.dataset.connect;
      if (id === "youtube" || id === "tiktok") {
        window.location.href = `/api/auth/${id}/start`;
        return;
      }
      toast("Credentials later", `${id} still needs its official developer app.`);
    }
    const driveSync = e.target.closest("[data-gdrive-sync]");
    if (driveSync) {
      if (!state.useApi) {
        toast("Backend offline", "Drive sync needs the Puzmania server.");
        return;
      }
      if (!state.gdrive?.configured) {
        toast("Drive not configured", "Set GDRIVE_FOLDER_ID and the service account file in .env.");
        return;
      }
      driveSync.disabled = true;
      api("/gdrive/sync", { method: "POST" })
        .then((result) => {
          const g = result.gdrive || {};
          const msg = g.summary || g.reason || g.error || `Scanned ${result.scanned?.length || 0} episode(s).`;
          toast(g.ok === false ? "Drive sync failed" : "Drive synced", msg);
          return refresh();
        })
        .catch((err) => toast("Drive sync failed", err.message))
        .finally(() => {
          driveSync.disabled = false;
        });
      return;
    }
  });

  $("#library-search").addEventListener("input", (e) => {
    state.search = e.target.value;
    renderLibrary();
  });

  $$("[data-filter]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.libraryFilter = btn.dataset.filter;
      $$("[data-filter]").forEach((b) => b.classList.toggle("is-active", b === btn));
      renderLibrary();
    });
  });

  $$("[data-hist]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.historyFilter = btn.dataset.hist;
      $$("[data-hist]").forEach((b) => b.classList.toggle("is-active", b === btn));
      renderHistory();
    });
  });

  $("#btn-preview-run").addEventListener("click", async () => {
    if (state.useApi) {
      try {
        const preview = await api("/runs/preview", { method: "POST" });
        const first = preview.episodes[0];
        toast("Preview", first ? `${first.id} would publish at ${preview.publishTime} to ${preview.platforms.join(", ")}.` : "Queue is empty.");
      } catch (err) {
        toast("Preview failed", err.message);
      }
      return;
    }
    const next = nextEpisode();
    toast("Preview only", next ? `${next.id} would publish at ${state.settings.publishTime} to enabled platforms.` : "Queue is empty.");
  });

  $("#btn-reset-queue").addEventListener("click", async () => {
    if (state.useApi) {
      await api("/queue/reset", { method: "POST" });
      await refresh();
      toast("Order restored", "Queue is back to scheduled dates.");
      return;
    }
    state.queueIds = state.episodes.filter((ep) => overallStatus(ep) === "queued" || overallStatus(ep) === "attention").map((ep) => ep.id);
    renderAll();
    toast("Order restored", "Queue is back to scheduled dates.");
  });

  $("#settings-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const form = e.currentTarget;
    state.settings.publishTime = form.publishTime.value;
    state.settings.timezone = form.timezone.value;
    state.settings.episodesPerRun = Number(form.episodesPerRun.value);
    state.settings.retryAttempts = Number(form.retryAttempts.value);
    state.settings.retryBackoff = form.retryBackoff.value;
    state.settings.notifyEmail = form.notifyEmail.checked;
    state.settings.notifySlack = form.notifySlack.checked;
    state.settings.alertOnFailure = form.alertOnFailure.checked;
    state.settings.dryRun = form.dryRun.checked;
    if (form.winnerMode) state.settings.winnerMode = form.winnerMode.value;
    if (form.pointsMultiplier) state.settings.pointsMultiplier = Number(form.pointsMultiplier.value);
    if (form.winnerIntroDays) state.settings.winnerIntroDays = Number(form.winnerIntroDays.value);
    if (state.useApi) {
      api("/settings", { method: "PUT", body: state.settings })
        .then((saved) => {
          state.settings = saved;
          updateDryBadge();
          tickClock();
          $("#settings-saved").hidden = false;
          toast("Settings saved", "Stored in the Puzmania database.");
        })
        .catch((err) => toast("Could not save settings", err.message));
      return;
    }
    saveSettings();
    updateDryBadge();
    tickClock();
    const note = $("#settings-saved");
    note.hidden = false;
    toast("Settings saved", "Stored in this browser.");
  });

  const addVideos = $("#add-videos");
  const addDrop = $("#add-drop");
  const addCaptions = $("#add-captions");
  if (addVideos) {
    addVideos.addEventListener("change", () => {
      state.addFiles = [...(addVideos.files || [])];
      renderAddPreview();
    });
  }
  if (addCaptions) {
    addCaptions.addEventListener("change", () => {
      const name = addCaptions.files?.[0]?.name;
      const label = $("#add-caption-name");
      if (label) label.textContent = name ? `Using ${name}` : "Optional. CSV with title,description or one caption per video.";
    });
  }
  if (addDrop) {
    ["dragenter", "dragover"].forEach((type) => {
      addDrop.addEventListener(type, (e) => {
        e.preventDefault();
        addDrop.classList.add("is-over");
      });
    });
    ["dragleave", "drop"].forEach((type) => {
      addDrop.addEventListener(type, (e) => {
        e.preventDefault();
        addDrop.classList.remove("is-over");
      });
    });
    addDrop.addEventListener("drop", (e) => {
      const incoming = [...(e.dataTransfer?.files || [])].filter((f) => /\.(mp4|mov|m4v|webm)$/i.test(f.name));
      if (!incoming.length) return;
      state.addFiles = incoming;
      renderAddPreview();
    });
  }
  ["add-start", "add-end", "add-per-day", "add-loop"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("input", renderAddPreview);
    if (el) el.addEventListener("change", renderAddPreview);
  });
  const startInput = $("#add-start");
  const endInput = $("#add-end");
  if (startInput && !startInput.value) startInput.value = isoToday();
  if (endInput && !endInput.value) {
    const d = new Date();
    d.setDate(d.getDate() + 6);
    const pad = (n) => String(n).padStart(2, "0");
    endInput.value = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }

  $("#add-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!state.useApi) {
      toast("Start the desk", "The Puzmania server must be running to add videos.");
      return;
    }
    const files = state.addFiles;
    if (!files.length) {
      toast("No videos", "Select one or more videos first.");
      return;
    }
    const form = new FormData();
    files.forEach((file) => form.append("videos", file, file.name));
    const caption = $("#add-captions")?.files?.[0];
    if (caption) form.append("captions", caption, caption.name);
    form.append("platforms", document.querySelector("input[name='add-platforms']:checked")?.value || "youtube_instagram");
    form.append("start_date", $("#add-start").value);
    form.append("end_date", $("#add-end").value);
    form.append("videos_per_day", $("#add-per-day").value || "1");
    form.append("loop", $("#add-loop").checked ? "true" : "false");
    form.append("name", $("#add-name").value || "");
    const status = $("#add-status");
    const submit = $("#add-submit");
    submit.disabled = true;
    if (status) status.textContent = "Adding to the list…";
    try {
      const result = await apiForm("/campaigns", form);
      await refresh();
      const queued = result.queued || result.episodes?.length || 0;
      toast("Added to the list", `${queued} post(s) will go out automatically.`);
      if (status) status.textContent = `${queued} post(s) queued.`;
      state.addFiles = [];
      if (addVideos) addVideos.value = "";
      if (addCaptions) addCaptions.value = "";
      renderAddPreview();
      setView("queue");
    } catch (err) {
      if (status) status.textContent = err.message;
      toast("Could not add videos", err.message, "bad");
    } finally {
      submit.disabled = false;
    }
  });

  $("#feature-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!state.useApi) {
      toast("Start the desk", "The Puzmania server must be running to name a winner.");
      return;
    }
    const memberId = Number($("#feature-member")?.value || 0);
    const status = $("#feature-status");
    if (!memberId) {
      if (status) status.textContent = "Pick a member from the click-through list.";
      return;
    }
    try {
      const row = await api("/winners/feature", { method: "POST", body: { memberId } });
      if (status) status.textContent = row.line || "Named for the next intro.";
      toast("Next intro winner", row.line || row.username);
      await refresh();
    } catch (err) {
      if (status) status.textContent = err.message;
      toast("Could not set winner", err.message, "bad");
    }
  });

  $("#btn-desktop-shortcut")?.addEventListener("click", async () => {
    if (!state.useApi) {
      toast("Start the desk", "The Puzmania server must be running to create the icon.");
      return;
    }
    try {
      const result = await api("/desktop-shortcut", { method: "POST" });
      toast("Desktop icon created", result.path || "Look on your desktop for Puzmania.");
    } catch (err) {
      toast("Could not create icon", err.message, "bad");
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeDrawer();
  });

  const initial = (location.hash || "#overview").replace("#", "");
  if ($(`#view-${initial}`)) setView(initial);
  else setView("overview");

  fillSettings();
  renderAll();
  tickClock();
  setInterval(tickClock, 1000);

  loadBootstrap()
    .then((data) => {
      applyBootstrap(data);
      fillSettings();
      renderAll();
    })
    .catch(() => {
      state.useApi = false;
    });
})();
