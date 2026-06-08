/* ===========================================================
   OmniGameArena project page — interactions (data-driven)
   =========================================================== */
const qs = (s, r = document) => r.querySelector(s);
const qsa = (s, r = document) => [...r.querySelectorAll(s)];
const SELF_V = (document.currentScript && document.currentScript.src.split("?v=")[1]) || "0";
const VID_V = "full"; // bump only when videos are re-encoded, so version bumps don't refetch all videos

// videos live in track/regime/game/model subfolders (idc/<game>/<model>); filename fields are
// [track|idc]_<game>_<model>_... so model is always field 2 — full path derivable here (single source of truth).
const VREG = { or3d: "solo", or2d: "solo", last: "solo", mshoot: "solo", scene: "solo", cue: "solo", craft: "solo", sky: "pvp", crystal: "pvp", midline: "pvp", shared: "coop", handoff: "coop" };
const vdir = (file) => { const p = file.replace(/\.mp4$/, "").split("_"); return p[0] === "idc" ? `idc/${p[1]}/${p[2]}` : `${p[0]}/${VREG[p[1]]}/${p[1]}/${p[2]}`; };
const vsrc = (file) => `assets/videos/${vdir(file)}/${file}?v=${VID_V}#t=0.1`;
const vsrcFlat = (track, slug, mkey, suf = "") => `assets/videos/${track}/${VREG[slug]}/${slug}/${track}_${slug}_${mkey}${suf}.mp4?v=${VID_V}#t=0.1`;
// every mp4 filename ends with its score, e.g. _375 = 0.375 (null score -> no suffix). Must match build_*.py.
const vsuf = (s) => (s == null ? "" : "_" + String(Math.round(s * 1000)).padStart(3, "0"));
const FLAT_GAMES = new Set(["pdq/or2d", "pdq/or3d", "pdq/last", "pdq/mshoot", "pdq/scene", "pdq/cue", "pdq/craft", "pdq/shared", "pdq/handoff", "lcrt/last", "lcrt/mshoot", "lcrt/craft", "lcrt/shared"]);  // pdq solo + pdq coop + lcrt solo (last/mshoot/craft) + lcrt coop (shared): flat files <track>_<slug>_<model>[_p1|_p2].mp4 directly under the slug folder (no per-model subfolder, no score suffix); add more as folders are converted

const REG_LABEL = { solo: "Solo", pvp: "PvP", coop: "Coop" };
const TRACK_LABEL = { pdq: "Quality (PDQ)", lcrt: "Real-time (LCRT)" };
const LB_MAX = 0.48; // common scale across tracks/regimes

/* ---------- suite gallery (static) ---------- */
const games = {
  solo: [
    { name: "ObstacleRun2D", focus: "Reactive platforming", image: "assets/game-shots-clean/game-001.png", desc: "Reacting quickly to oncoming obstacles and gaps, then jumping to clear them in a side-scrolling platformer." },
    { name: "ObstacleRun3D", focus: "3D parkour", image: "assets/game-shots-clean/game-000.png", desc: "Visual grounding and spatial navigation toward a finish line while avoiding 3D obstacles." },
    { name: "LastStand", focus: "Survival under hazards", image: "assets/game-shots-clean/game-003.png", desc: "Stay alive on a hazardous platform through perception, navigation, and planning." },
    { name: "MonsterShoot", focus: "Sustained aiming", image: "assets/game-shots-clean/game-004.png", desc: "Enemy detection, aiming, firing, and damage avoidance in a survival shooter." },
    { name: "SceneEscape", focus: "Task-chain puzzles", image: "assets/game-shots-clean/game-005.png", desc: "Scene exploration, NPC task tracking, memory, and long-horizon planning." },
    { name: "CueChase", focus: "Cue-guided search", image: "assets/game-shots-clean/game-006.png", desc: "Parsing text cues and NPC hints to visually locate and reach the described target in the scene." },
    { name: "SoloCraft", focus: "Logistics & delivery", image: "assets/game-shots-clean/game-007.png", desc: "Resource collection, item preparation, and sequencing for order fulfillment." },
  ],
  pvp: [
    { name: "SkyDuel", focus: "Direct 1v1 combat", image: "assets/game-shots-clean/pvp-skyduel.png", desc: "Opponent tracking, strikes, evasions, and adversarial control in a direct duel." },
    { name: "CrystalGuard", focus: "Attack & defense", image: "assets/game-shots-clean/pvp-crystalguard.png", desc: "Two sides race to destroy the opponent's crystal while defending their own." },
    { name: "MidlineClash", focus: "Competitive resource race", image: "assets/game-shots-clean/game-009.png", desc: "Planning, navigation, and adversarial pressure while racing for shared midline resources, with each side at its own workbench and order counter." },
  ],
  coop: [
    { name: "SharedFloor", focus: "Symmetric cooperation", image: "assets/game-shots-clean/game-011.png", desc: "Agents share objectives and capabilities, rewarding efficient division of labor." },
    { name: "HandoffRun", focus: "Asymmetric coordination", image: "assets/game-shots-clean/game-012.png", desc: "Distinct roles must pass items and synchronize across separated areas." },
  ],
};
const gallery = qs("[data-gallery]");
const SUITE_ORDER = ["solo", "pvp", "coop"];
// all 12 games shown at once; each card carries its regime (drives the category pill + the highlight filter)
function renderGames() {
  if (!gallery) return;
  gallery.innerHTML = SUITE_ORDER.flatMap((reg) =>
    games[reg].map((g) => `
      <article class="card" data-reg="${reg}">
        <div class="card-media">
          <img class="shot" src="${g.image}" alt="${g.name} gameplay screenshot." loading="lazy">
          <span class="reg-pill">${REG_LABEL[reg]}</span>
        </div>
        <div class="card-body">
          <h3>${g.name}</h3>
          <div class="focus">${g.focus}</div>
          <p>${g.desc}</p>
        </div>
      </article>`)
  ).join("");
}

/* ---------- generic toggle wiring ---------- */
function wire(selector, attr, onPick) {
  const btns = qsa(selector);
  btns.forEach((b) => b.addEventListener("click", () => {
    btns.forEach((x) => { const on = x === b; x.classList.toggle("active", on); x.setAttribute("aria-selected", String(on)); });
    onPick(b.dataset[attr]);
  }));
}

/* ---------- fetched data ---------- */
let LB = null, REC = null;

/* ---------- leaderboard (track + regime) ---------- */
const lb = qs("[data-leaderboard]"), lbSub = qs("[data-lb-sub]"), pvpDetail = qs("[data-pvp-detail]"), legendEl = qs("[data-lb-legend]");
let lbTrack = "pdq", lbRegime = "solo";
// each model gets its own color; the legend (grouped by class) is what tells you the category.
const MODEL_COLOR = {
  "GPT-5.5": "#4878d0", "GPT-5.4": "#956cb4",
  "Claude Opus 4.6": "#ee854a", "Claude Opus 4.7": "#d65f5f", "Claude Sonnet 4.6": "#82c6e2",
  "Gemini 3.1 Pro Preview": "#6acc64", "Gemini 3.1 Flash-Lite Preview": "#d5bb67",
  "Kimi K2.5": "#dc7ec0", "Qwen3.5-397B-A17B": "#8c613c", "Qwen3.5-122B-A10B": "#b3a0d8",
  "NitroGen": "#989ca3", "Open-P2P": "#6fb89a",
};
const CAT_LABEL = { closed: "Commercial VLM", open: "Open-weight VLM", policy: "Specialized policy" };
function renderLeaderboard() {
  if (!lb || !LB) return;
  const rows = (LB[lbTrack] || {})[lbRegime] || [];
  if (lbSub) lbSub.textContent = `Mean normalized score · ${REG_LABEL[lbRegime]} · ${TRACK_LABEL[lbTrack]}`;
  if (pvpDetail) {
    pvpDetail.hidden = lbRegime !== "pvp";
    const pdqFig = qs("[data-pvp-pdq]")?.closest(".fig"), lcrtFig = qs("[data-pvp-lcrt]")?.closest(".fig");
    if (pdqFig) pdqFig.hidden = lbTrack !== "pdq";
    if (lcrtFig) lcrtFig.hidden = lbTrack !== "lcrt";
  }
  const maxScore = Math.max(0.001, ...rows.map((r) => r.score || 0));  // adaptive: longest bar ~ full width per view, so small-number tabs do not look like lonely stubs
  lb.innerHTML = rows.map((r, i) => {
    const rank = i + 1, medal = rank <= 3 ? ` medal g${rank}` : "";
    const w = Math.max(3, (r.score / maxScore) * 96).toFixed(1);
    return `
      <div class="lb-row">
        <span class="lb-rank${medal}">${rank}</span>
        <img class="lb-logo" src="assets/paper/icons/${r.logo}.png" alt="" aria-hidden="true">
        <span class="lb-name" title="${r.name}">${r.name}</span>
        <div class="lb-track"><div class="lb-fill ${r.cls}" style="width:${w}%${MODEL_COLOR[r.name] ? `;background:${MODEL_COLOR[r.name]}` : ""}"></div></div>
        <span class="lb-val">${r.score.toFixed(3)}</span>
      </div>`;
  }).join("");
  if (legendEl) {
    legendEl.innerHTML = ["closed", "open", "policy"].map((cls) => {
      const items = rows.filter((r) => r.cls === cls);
      if (!items.length) return "";
      return `<div class="leg-row"><span class="leg-cat">${CAT_LABEL[cls]}</span><div class="leg-items">${items.map((r) =>
        `<span><i style="background:${MODEL_COLOR[r.name] || "#9aa1aa"}"></i>${r.name}</span>`).join("")}</div></div>`;
    }).join("");
  }
}

/* ---------- recordings (track + game; grid or matchup) ---------- */
const vidTabs = qs("[data-vid-tabs]"), vidContent = qs("[data-vid-content]");
let vidTrack = "pdq", vidRegime = "Solo", vidIdx = 0, muA = 0, muB = 1;
const curGames = () => ((REC && REC[vidTrack]) || []).filter((g) => g.regime === vidRegime);

function vCard(track, slug, mkey, name, logo, score, autoplay, fileScore) {
  const fs = fileScore != null ? fileScore : score;  // video file keyed by fileScore (vid); caption shows score (paper Table 3 avg)
  const src = FLAT_GAMES.has(`${track}/${slug}`) ? vsrcFlat(track, slug, mkey)
            : vsrc(`${track}_${slug}_${mkey}${vsuf(fs)}.mp4`);
  return `
      <figure class="vg-card">
        <video src="${src}" controls ${autoplay ? "autoplay " : ""}muted loop playsinline preload="metadata" aria-label="${name} playing ${slug}"></video>
        <figcaption class="vg-meta">
          <span class="who"><img src="assets/paper/icons/${logo}.png" alt="">${name}</span>
          ${score != null ? `<span class="sc"><i class="sc-tag">avg</i>${score.toFixed(3)}</span>` : ""}
        </figcaption>
      </figure>`;
}

// coop self-play: both player perspectives (same gameplay, different reasoning)
function cpv(src, tag, autoplay) {
  const p = tag.includes("2") ? "cpv-p2" : "cpv-p1";  // color-code Player 1 vs Player 2
  return `<div class="cpv ${p}"><span class="cpv-lab">${tag}</span><video src="${src}" controls ${autoplay ? "autoplay " : ""}muted loop playsinline preload="metadata"></video></div>`;
}
function coopCard(track, slug, m) {
  const fs = m.vid != null ? m.vid : m.score;
  const flat = FLAT_GAMES.has(`${track}/${slug}`);
  // flat: pdq_<slug>_<model>_p1.mp4 / _p2.mp4 (no score, directly under the slug folder). non-flat (legacy): Player 1 has no suffix.
  const src = (suf) => flat ? vsrcFlat(track, slug, m.key, suf) : vsrc(`${track}_${slug}_${m.key}${suf}${vsuf(fs)}.mp4`);
  return `
      <figure class="vg-card">
        ${m.p2 ? `<button class="cpv-sync" type="button" title="Play both perspectives from the start">⟲ Play both</button>` : ""}
        <div class="cpv-pair">${cpv(flat ? src("_p1") : src(""), "Player 1", true)}${m.p2 ? cpv(src("_p2"), "Player 2", true) : ""}</div>
        <figcaption class="vg-meta">
          <span class="who"><img src="assets/paper/icons/${m.logo}.png" alt="">${m.name}</span>
          ${m.score != null ? `<span class="sc"><i class="sc-tag">avg</i>${m.score.toFixed(3)}</span>` : ""}
        </figcaption>
      </figure>`;
}

// restart and play both perspectives of one match together (one-shot; you can still scrub each freely afterward)
function playBothFromStart(scope) {
  if (!scope) return;
  scope.querySelectorAll("video").forEach((v) => { v.currentTime = 0; v.play().catch(() => {}); });
}

function renderVidTabs() {
  if (!vidTabs || !REC) return;
  vidTabs.innerHTML = curGames().map((g, i) =>
    `<button class="tab${i === vidIdx ? " active" : ""}" type="button" data-i="${i}">${g.name}</button>`
  ).join("");
  qsa(".tab", vidTabs).forEach((b) => b.addEventListener("click", () => { vidIdx = Number(b.dataset.i); muA = 0; muB = 1; renderVidGame(); }));
}

function renderVidGame() {
  if (!vidContent || !REC) return;
  const gs = curGames();
  const g = gs[vidIdx] || gs[0];
  if (!g) { vidContent.innerHTML = ""; return; }
  qsa(".tab", vidTabs).forEach((b, j) => b.classList.toggle("active", j === vidIdx));
  if (g.regime === "PvP") { renderPairwiseMatchup(g); return; }
  const cards = g.coop
    ? g.models.map((m) => coopCard(vidTrack, g.slug, m)).join("")
    : g.models.map((m) => vCard(vidTrack, g.slug, m.key, m.name, m.logo, m.score, true, m.vid)).join("");  // autoplay all (incl. policy baselines)
  const cols2 = g.models.length === 4 ? " vgrid-2" : "";  // 4 models (LCRT solo/coop): clean 2x2 instead of an orphaned 3+1 row
  vidContent.innerHTML = `<div class="vgrid${cols2}">${cards}</div>`;
  vidContent.querySelectorAll(".cpv-sync").forEach((btn) =>
    btn.addEventListener("click", () => playBothFromStart(btn.closest(".vg-card").querySelector(".cpv-pair"))));
}

function renderMatchup(g) {
  return renderPairwiseMatchup(g);
  const ms = g.pvpModels;
  if (muA >= ms.length) muA = 0;
  if (muB >= ms.length) muB = 1 % ms.length;
  if (muA === muB) muB = (muA + 1) % ms.length;
  const pairScore = (mkey, oppKey) => {
    const p = g.pairs.find((x) => (x.a === mkey && x.b === oppKey) || (x.a === oppKey && x.b === mkey));
    if (!p) return null;
    return p.a === mkey ? p.sa : p.sb;
  };
  const A = ms[muA], B = ms[muB];
  const picks = (side, sel) => ms.map((m, i) =>
    `<button class="mpick${i === sel ? " active" : ""}" type="button" data-i="${i}"><img src="assets/paper/icons/${m.logo}.png" alt="">${m.name}</button>`).join("");
  const view = (m, opp) => `
      <figure class="vg-card">
        <video src="${vsrc(`${vidTrack}_${g.slug}_${m.key}_v_${opp.key}${vsuf(gscore[m.key])}.mp4`)}" controls autoplay muted loop playsinline preload="metadata" aria-label="${m.name} vs ${opp.name}"></video>
        <figcaption class="vg-meta"><span class="who"><img src="assets/paper/icons/${m.logo}.png" alt="">${m.name}</span><span class="sc">${gscore[m.key] != null ? '<i class="sc-tag">avg</i>' + gscore[m.key].toFixed(3) : "–"}</span></figcaption>
      </figure>`;
  vidContent.innerHTML = `
    <div class="mu-pickers">
      <div class="mu-pick"><span class="mu-lab">Player&nbsp;1</span><div class="mu-btns" data-side="a">${picks("a", muA)}</div></div>
      <div class="mu-pick"><span class="mu-lab">Player&nbsp;2</span><div class="mu-btns" data-side="b">${picks("b", muB)}</div></div>
    </div>
    <div class="mu-views">${view(A, B)}${view(B, A)}</div>`;
  qsa(".mu-btns .mpick", vidContent).forEach((b) => b.addEventListener("click", () => {
    const side = b.parentElement.dataset.side, i = Number(b.dataset.i);
    if (side === "a") { muA = i; if (muB === muA) muB = (muA + 1) % ms.length; }
    else { muB = i; if (muA === muB) muA = (muB + 1) % ms.length; }
    renderMatchup(g);
  }));
}

function renderPairwiseMatchup(g) {
  const ms = g.pvpModels;
  if (muA >= ms.length) muA = 0;
  if (muB >= ms.length) muB = 1 % ms.length;
  if (muA === muB) muB = (muA + 1) % ms.length;
  const A = ms[muA], B = ms[muB];
  const picks = (sel) => ms.map((m, i) =>
    `<button class="mpick${i === sel ? " active" : ""}" type="button" data-i="${i}"><img src="assets/paper/icons/${m.logo}.png" alt="">${m.name}</button>`).join("");
  // Games with per-match scores (midline) use DIRECTED videos: Player 1 (A) vs Player 2 (B) -> <A>_v_<B>,
  // A's screen = _p1, B's screen = _p2, plus Win/Loss + Episode Score badges. Other PvP games (sky/crystal)
  // have one video per unordered pair named in canonical (alphabetical) order, with no per-match score.
  const directed = !!g.matches;
  const div = g.scoreDiv || 50;
  const pts = directed ? g.matches[`${A.key}_v_${B.key}`] : null;  // [A points, B points] raw; normalized = /div
  const pa = pts ? pts[0] : null, pb = pts ? pts[1] : null;
  const view = (m, opp, mine, theirs) => {
    let pair, screen;
    if (directed) { pair = `${A.key}_v_${B.key}`; screen = m.key === A.key ? "_p1" : "_p2"; }
    else { const [first, second] = [m.key, opp.key].sort(); pair = `${first}_v_${second}`; screen = m.key === first ? "_p1" : "_p2"; }
    const pnum = m.key === A.key ? 1 : 2;  // left card = Player 1 (A), right card = Player 2 (B)
    let res = "";
    if (mine != null && theirs != null) {
      const r = mine > theirs ? ["win", "Win"] : mine < theirs ? ["loss", "Loss"] : ["draw", "Draw"];
      res = `<span class="pvp-badges"><span class="pvp-res pvp-${r[0]}">${r[1]}</span><span class="pvp-ep"><i class="pvp-ep-tag">Episode Score</i>${(mine / div).toFixed(3)}</span></span>`;
    }
    return `
      <figure class="vg-card">
        <video src="${vsrcFlat(vidTrack, g.slug, pair, screen)}" controls autoplay muted loop playsinline preload="metadata" aria-label="${m.name}, Player ${pnum} vs ${opp.name}"></video>
        <figcaption class="vg-meta"><span class="who"><span class="mu-plab mu-plab-p${pnum}">Player&nbsp;${pnum}</span><img src="assets/paper/icons/${m.logo}.png" alt="">${m.name}</span>${res}</figcaption>
      </figure>`;
  };
  vidContent.innerHTML = `
    <div class="mu-pickers">
      <div class="mu-pick"><span class="mu-lab">Player&nbsp;1</span><div class="mu-btns" data-side="a">${picks(muA)}</div></div>
      <div class="mu-pick"><span class="mu-lab">Player&nbsp;2</span><div class="mu-btns" data-side="b">${picks(muB)}</div></div>
    </div>
    <div class="mu-views">${view(A, B, pa, pb)}<button class="mu-vs" type="button" title="Play both from the start">vs</button>${view(B, A, pb, pa)}</div>`;
  const vsBtn = vidContent.querySelector(".mu-vs");
  if (vsBtn) vsBtn.addEventListener("click", () => playBothFromStart(vidContent.querySelector(".mu-views")));
  qsa(".mu-btns .mpick", vidContent).forEach((b) => b.addEventListener("click", () => {
    const side = b.parentElement.dataset.side, i = Number(b.dataset.i);
    if (side === "a") { muA = i; if (muB === muA) muB = (muA + 1) % ms.length; }
    else { muB = i; if (muA === muB) muA = (muB + 1) % ms.length; }
    renderPairwiseMatchup(g);
  }));
}

/* ---------- IDC: learned skill + replay + variant transfer ---------- */
const idcGameTabs = qs("[data-idc-game]"), idcModelBtns = qs("[data-idc-model]"), idcContent = qs("[data-idc-content]");
let IDC = null, idcG = 0, idcM = 0;

const esc = (s) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const mdInline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
function renderSkill(md) {
  if (!md) return "<p class='idc-note'>No skill exported for this pair.</p>";
  let html = "", inList = false;
  for (const raw of md.split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("# ")) { if (inList) { html += "</ul>"; inList = false; } continue; }
    if (/^best measured/i.test(line)) { html += `<p class="idc-note">${mdInline(line)}</p>`; continue; }
    if (line.startsWith("- ")) { if (!inList) { html += "<ul>"; inList = true; } html += `<li>${mdInline(line.slice(2))}</li>`; }
    else { if (inList) { html += "</ul>"; inList = false; } html += `<p>${mdInline(line)}</p>`; }
  }
  if (inList) html += "</ul>";
  return html;
}
function renderIdcGameTabs() {
  if (!idcGameTabs || !IDC) return;
  idcGameTabs.innerHTML = IDC.map((g, i) => `<button class="tab${i === idcG ? " active" : ""}" type="button" data-i="${i}">${g.name}</button>`).join("");
  qsa(".tab", idcGameTabs).forEach((b) => b.addEventListener("click", () => { idcG = Number(b.dataset.i); idcM = 0; renderIdcModelBtns(); renderIdcContent(); }));
}
function renderIdcModelBtns() {
  if (!idcModelBtns || !IDC) return;
  idcModelBtns.innerHTML = IDC[idcG].models.map((m, i) => `<button class="mpick${i === idcM ? " active" : ""}" type="button" data-i="${i}"><img src="assets/paper/icons/${m.logo}.png" alt="">${m.name}</button>`).join("");
  qsa(".mpick", idcModelBtns).forEach((b) => b.addEventListener("click", () => { idcM = Number(b.dataset.i); renderIdcContent(); }));
}
// paper IDC palette + markers (Claude 4.6 blue ○, Claude 4.7 amber □, GPT-5.5 teal △, Gemini pink ★)
const IDC_STYLE = {
  opus46: { color: "#2f7fc1", marker: "circle" },
  opus47: { color: "#e8a23a", marker: "square" },
  gpt55: { color: "#2ea88c", marker: "triangle" },
  geminipro: { color: "#db6fa6", marker: "star" },
};
function cvMarker(cx, cy, shape, color, big) {
  const r = big ? 4.2 : 3, fill = big ? color : "#fff", sw = 1.5;
  cx = +cx.toFixed(1); cy = +cy.toFixed(1);
  if (shape === "square") return `<rect x="${cx - r}" y="${cy - r}" width="${2 * r}" height="${2 * r}" fill="${fill}" stroke="${color}" stroke-width="${sw}"/>`;
  if (shape === "triangle") return `<polygon points="${cx},${cy - r - 0.5} ${cx - r},${cy + r - 0.5} ${cx + r},${cy + r - 0.5}" fill="${fill}" stroke="${color}" stroke-width="${sw}"/>`;
  if (shape === "star") {
    const R = big ? 5.5 : 4, ri = R * 0.4;
    let pp = "";
    for (let k = 0; k < 10; k++) { const a = -Math.PI / 2 + k * Math.PI / 5, rr = k % 2 ? ri : R; pp += `${(cx + rr * Math.cos(a)).toFixed(1)},${(cy + rr * Math.sin(a)).toFixed(1)} `; }
    return `<polygon points="${pp.trim()}" fill="${fill}" stroke="${color}" stroke-width="${sw}" stroke-linejoin="round"/>`;
  }
  return `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${fill}" stroke="${color}" stroke-width="${sw}"/>`;
}
function renderCurve(curve, key) {
  const pts = (curve || []).map((v, i) => ({ i, v })).filter((p) => p.v != null);
  if (!pts.length) return { svg: "", note: "" };
  const st = IDC_STYLE[key] || { color: "#2f6cad", marker: "circle" };
  const W = 460, H = 160, pl = 32, pr = 20, pt = 26, pb = 24, n = curve.length - 1;
  const ymax = Math.max(0.2, ...pts.map((p) => p.v));
  const X = (i) => pl + (i / n) * (W - pl - pr);
  const Y = (v) => H - pb - (v / ymax) * (H - pt - pb);
  const first = pts[0], last = pts[pts.length - 1];
  const peak = pts.reduce((a, b) => (b.v > a.v ? b : a));
  const poly = pts.map((p) => `${X(p.i).toFixed(1)},${Y(p.v).toFixed(1)}`).join(" ");
  const marks = pts.map((p) => cvMarker(X(p.i), Y(p.v), st.marker, st.color, p === peak)).join("");
  const anchor = (p) => (p.i >= n - 0.5 ? "end" : p.i <= 0.5 ? "start" : "middle");
  const dx = (p) => (anchor(p) === "end" ? -5 : anchor(p) === "start" ? 5 : 0);
  const startLab = `<text x="${(X(first.i) + dx(first)).toFixed(1)}" y="${(Y(first.v) + 15).toFixed(1)}" class="cv-start" text-anchor="${anchor(first)}">start ${first.v.toFixed(2)}</text>`;
  const peakLab = peak !== first ? `<text x="${(X(peak.i) + dx(peak)).toFixed(1)}" y="${(Y(peak.v) - 9).toFixed(1)}" class="cv-lab" fill="${st.color}" text-anchor="${anchor(peak)}">peak R${peak.i} &middot; ${peak.v.toFixed(2)}</text>` : "";
  const svg = `<svg viewBox="0 0 ${W} ${H}" class="idc-curve" role="img" aria-label="Score per reflection round">
      <line x1="${pl}" y1="${H - pb}" x2="${W - pr}" y2="${H - pb}" class="cv-axis"/>
      <line x1="${pl}" y1="${pt}" x2="${pl}" y2="${H - pb}" class="cv-axis"/>
      <polyline points="${poly}" class="cv-line" style="stroke:${st.color}"/>${marks}${startLab}${peakLab}
      <text x="${pl}" y="${H - 7}" class="cv-tick">R0</text>
      <text x="${W - pr}" y="${H - 7}" class="cv-tick" text-anchor="end">R${n}</text>
      <text x="${pl - 5}" y="${pt + 7}" class="cv-tick" text-anchor="end">${ymax.toFixed(1)}</text>
      <text x="${pl - 5}" y="${H - pb}" class="cv-tick" text-anchor="end">0</text>
    </svg>`;
  let note;
  if (peak.i >= n - 1 && last.v >= first.v - 0.02) note = `Climbs from ${first.v.toFixed(2)} (R${first.i}) and is still best at the final round R${peak.i} (${peak.v.toFixed(2)}).`;
  else if (peak.v - last.v > 0.04) note = `Best at R${peak.i} (${peak.v.toFixed(2)}), then slips back to ${last.v.toFixed(2)} by R${n} &mdash; the last round is not the best.`;
  else note = `Goes from ${first.v.toFixed(2)} (R${first.i}) to ${last.v.toFixed(2)} (R${n}), peaking at R${peak.i} (${peak.v.toFixed(2)}).`;
  return { svg, note };
}

function renderDelta(curve, key) {
  if (!curve || curve.length < 2 || curve[0] == null) return { svg: "", note: "" };
  const base = curve[0];
  const pts = curve.map((v, i) => ({ i, d: v == null ? null : +(v - base).toFixed(3) })).filter((p) => p.d != null);
  if (pts.length < 2) return { svg: "", note: "" };
  const st = IDC_STYLE[key] || { color: "#2f6cad", marker: "circle" };
  const W = 460, H = 150, pl = 40, pr = 20, pt = 16, pb = 22, n = curve.length - 1;
  const amax = Math.max(0.1, ...pts.map((p) => Math.abs(p.d)));
  const X = (i) => pl + (i / n) * (W - pl - pr);
  const mid = pt + (H - pt - pb) / 2;
  const Y = (d) => mid - (d / amax) * ((H - pt - pb) / 2);
  const poly = pts.map((p) => `${X(p.i).toFixed(1)},${Y(p.d).toFixed(1)}`).join(" ");
  const dpeak = pts.reduce((a, b) => (b.d > a.d ? b : a));  // highest point solid, rest hollow
  const marks = pts.map((p) => cvMarker(X(p.i), Y(p.d), st.marker, st.color, p === dpeak)).join("");
  const last = pts[pts.length - 1];
  const sign = (x) => (x >= 0 ? "+" : "−") + Math.abs(x).toFixed(2);
  const svg = `<svg viewBox="0 0 ${W} ${H}" class="idc-curve" role="img" aria-label="Score change vs cold-start R0">
      <line x1="${pl}" y1="${mid.toFixed(1)}" x2="${W - pr}" y2="${mid.toFixed(1)}" class="cv-zero"/>
      <line x1="${pl}" y1="${pt}" x2="${pl}" y2="${H - pb}" class="cv-axis"/>
      <polyline points="${poly}" class="cv-line" style="stroke:${st.color}"/>${marks}
      <text x="${pl - 5}" y="${(mid + 3).toFixed(1)}" class="cv-tick" text-anchor="end">0</text>
      <text x="${pl - 5}" y="${pt + 6}" class="cv-tick" text-anchor="end">+${amax.toFixed(2)}</text>
      <text x="${pl - 5}" y="${(H - pb).toFixed(1)}" class="cv-tick" text-anchor="end">−${amax.toFixed(2)}</text>
      <text x="${pl}" y="${H - 7}" class="cv-tick">R0</text>
      <text x="${W - pr}" y="${H - 7}" class="cv-tick" text-anchor="end">R${n}</text>
    </svg>`;
  const note = `Net ${sign(last.d)} by R${n} vs the R0 baseline; biggest gain ${sign(dpeak.d)} at R${dpeak.i}.`;
  return { svg, note };
}

function renderIdcContent() {
  if (!idcContent || !IDC) return;
  qsa(".tab", idcGameTabs).forEach((b, j) => b.classList.toggle("active", j === idcG));
  qsa(".mpick", idcModelBtns).forEach((b, j) => b.classList.toggle("active", j === idcM));
  const g = IDC[idcG], m = g.models[idcM];
  const cv = renderCurve(m.curve, m.key);
  const dv = renderDelta(m.curve, m.key);
  const coop = g.coop;
  const syncBtn = `<button class="cpv-sync" type="button" title="Play both perspectives from the start">⟲ Play both</button>`;
  const cell = (base, sc, p2) => `<figure class="ivg-cell${coop ? " coop" : ""}">${coop
      ? `${p2 ? syncBtn : ""}<div class="cpv-pair">${cpv(vsrc(base + "_p1.mp4"), "Player 1", true)}${p2 ? cpv(vsrc(base + "_p2.mp4"), "Player 2", true) : ""}</div>`
      : `<video src="${vsrc(base + ".mp4")}" controls autoplay muted loop playsinline preload="metadata"></video>`}<figcaption>${sc != null ? `<i class="sc-tag">avg</i>${sc.toFixed(3)}` : "–"}</figcaption></figure>`;
  const empty = `<div class="ivg-cell empty">not recorded</div>`;
  const rows = m.vars.map((v, i) => {
    const known = v.withSkill != null && v.withoutSkill != null;
    const d = known ? v.withSkill - v.withoutSkill : null, up = d != null && d >= 0;
    const badge = d != null ? ` <span class="ivar-delta ${up ? "up" : "down"}">${up ? "▲" : "▼"} ${(up ? "+" : "−") + Math.abs(d).toFixed(3)}</span>` : "";
    return `${i > 0 ? `<div class="ivg-divider"></div>` : ""}<div class="ivg-lab">${v.var}${badge}</div>
      ${v.ns ? cell(`idc_${g.slug}_${m.key}_${v.vk}_ns`, v.withoutSkill, v.ns2) : empty}
      ${v.bs ? cell(`idc_${g.slug}_${m.key}_${v.vk}_bs`, v.withSkill, v.bs2) : empty}`;
  }).join("");
  const vlab = { v1: "Var 1", v2: "Var 2", v3: "Var 3" };
  let vd = "";
  if (g.varDescs) {
    const ds = ["v1", "v2", "v3"].map((k) => g.varDescs[k]).filter(Boolean);
    vd = ds.length === 3 && ds[0] === ds[1] && ds[1] === ds[2]
      ? `<p class="idc-vardescs"><b>Variants 1 to 3:</b> ${esc(ds[0])}</p>`
      : `<ul class="idc-vardescs">${["v1", "v2", "v3"].filter((k) => g.varDescs[k]).map((k) => `<li><b>${vlab[k]}:</b> ${esc(g.varDescs[k])}</li>`).join("")}</ul>`;
  }
  idcContent.innerHTML = `
    <div class="idc-grid">
      <div class="idc-skill"><div class="idc-skill-inner"><h4>Learned skill <span class="idc-sub">${g.name} &middot; ${m.name}</span></h4>${renderSkill(m.skill)}</div></div>
      <div class="idc-rcol">
        <div class="idc-curvebox"><h4>Origin improvement curve <span class="idc-sub">score per round (R0 = cold-start)</span></h4>${cv.svg}<p class="idc-note">${cv.note}</p></div>
        <div class="idc-curvebox"><h4>Change vs cold-start <span class="idc-sub">&Delta; score relative to R0</span></h4>${dv.svg}<p class="idc-note">${dv.note}</p></div>
      </div>
    </div>
    <div class="idc-transfer">
      <h4>Held-out variant transfer <span class="idc-sub">no skill vs. best skill &mdash; the distilled skill does not always transfer</span></h4>
      <div class="idc-vgrid">
        <div></div><div class="ivg-head">No skill</div><div class="ivg-head">Best skill</div>
        ${rows}
      </div>
      ${vd}
    </div>`;
  idcContent.querySelectorAll(".cpv-sync").forEach((btn) =>
    btn.addEventListener("click", () => playBothFromStart(btn.closest(".ivg-cell").querySelector(".cpv-pair"))));
}

/* ---------- toast (coming-soon links) ---------- */
const toast = qs("[data-toast]");
let toastTimer;
function showToast(msg) {
  if (!toast) return;
  clearTimeout(toastTimer);
  toast.textContent = msg;
  toast.classList.add("show");
  toastTimer = setTimeout(() => toast.classList.remove("show"), 2400);
}
qsa("[data-soon]").forEach((el) => el.addEventListener("click", (e) => { e.preventDefault(); showToast(`${el.dataset.soon} link coming soon.`); }));

/* ---------- copy bibtex ---------- */
const copyBtn = qs("[data-copy]");
if (copyBtn) copyBtn.addEventListener("click", async () => {
  const code = qs(".bibtex code")?.innerText ?? "";
  try { await navigator.clipboard.writeText(code); copyBtn.textContent = "Copied"; setTimeout(() => (copyBtn.textContent = "Copy"), 1800); }
  catch { showToast("Copy failed — select the text manually."); }
});

/* ---------- wiring ---------- */
wire("#suite .tab", "reg", (reg) => { if (gallery) gallery.dataset.hl = reg; });  // highlight a regime (or "all" = neutral); cards render once, this only toggles emphasis
wire("[data-lb-track] .trk", "track", (t) => { lbTrack = t; renderLeaderboard(); });
wire("[data-lb-tabs] .tab", "reg", (r) => { lbRegime = r; renderLeaderboard(); });
wire("[data-vid-track] .trk", "track", (t) => { vidTrack = t; vidIdx = 0; muA = 0; muB = 1; renderVidTabs(); renderVidGame(); });
wire("[data-vid-regime] .tab", "reg", (r) => { vidRegime = r; vidIdx = 0; muA = 0; muB = 1; renderVidTabs(); renderVidGame(); });

/* ---------- init ---------- */
renderGames();
if (gallery) gallery.dataset.hl = "all";
function lazy(el, fn) {
  if (!el) return;
  let started = false;
  const go = () => { if (!started) { started = true; fn(); } };
  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) { go(); io.disconnect(); } }, { rootMargin: "300px" });
    io.observe(el);
  } else { go(); }
}
Promise.all([
  fetch(`assets/data/lb.json?v=${SELF_V}`).then((r) => r.json()),
  fetch(`assets/data/rec.json?v=${SELF_V}`).then((r) => r.json()),
  fetch(`assets/data/idc.json?v=${SELF_V}`).then((r) => r.json()),
]).then(([lbData, recData, idcData]) => {
  LB = lbData; REC = recData; IDC = idcData;
  renderLeaderboard();
  renderVidTabs();
  renderIdcGameTabs();
  renderIdcModelBtns();
  lazy(vidContent, renderVidGame);
  lazy(idcContent, renderIdcContent);
}).catch((err) => { console.error("data load failed", err); });
