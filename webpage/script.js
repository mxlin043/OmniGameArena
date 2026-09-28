/* ===========================================================
   OmniGameArena project page — interactions (data-driven)
   =========================================================== */
const qs = (s, r = document) => r.querySelector(s);
const qsa = (s, r = document) => [...r.querySelectorAll(s)];
const SELF_V = (document.currentScript && document.currentScript.src.split("?v=")[1]) || "0";
const VID_V = "full2"; // bump only when videos are re-encoded, so version bumps don't refetch all videos

// idc videos live in idc/<game>/<model>/; recordings in <track>/<regime>/<game>/
const VREG = { or3d: "solo", or2d: "solo", last: "solo", mshoot: "solo", scene: "solo", cue: "solo", craft: "solo", sky: "pvp", crystal: "pvp", midline: "pvp", shared: "coop", handoff: "coop" };
const vdir = (file) => { const p = file.replace(/\.mp4$/, "").split("_"); return p[0] === "idc" ? `idc/${p[1]}/${p[2]}` : `${p[0]}/${VREG[p[1]]}/${p[1]}/${p[2]}`; };
const VIDEO_REVISIONS = { idc_last_opus46_var3_bs: "75b9fc675884", idc_last_opus46_var2_ns: "5b3309573a87", idc_last_opus46_var2_bs: "adff8d2bf707", idc_last_opus47_var2_ns: "c43b6f37869b", idc_last_opus47_var2_bs: "b2c3e9db81aa", idc_last_gpt55_var2_ns: "0d9a83497be5", idc_last_gpt55_var2_bs: "786532d6b9e1", idc_last_geminipro_var2_ns: "039ea58e821e", idc_last_geminipro_var2_bs: "eca276f9a7d3", idc_last_geminiflash_var1_ns: "e325bff4f0ca", idc_last_geminiflash_var1_bs: "7e12860a2982", idc_last_geminiflash_var2_ns: "11b44ab26656", idc_last_geminiflash_var2_bs: "b60993f1a4c7", idc_last_geminiflash_var3_ns: "da17273be245", idc_last_geminiflash_var3_bs: "b374fad935ab", idc_last_geminiflash_var4_ns: "d57a054099f2", idc_last_geminiflash_var4_bs: "423087ec3118", idc_last_qwen397_var1_ns: "d9b8362ff56e", idc_last_qwen397_var1_bs: "1c9a3ddc8ba4", idc_last_qwen397_var2_ns: "b75494b2ca93", idc_last_qwen397_var2_bs: "e82196c90170", idc_last_qwen397_var3_bs: "0f83e518bc4e", idc_last_qwen397_var3_ns: "4b83a306e7a9", idc_last_qwen397_var4_ns: "a7f507fcb750", idc_last_qwen397_var4_bs: "2ec464b291cc", idc_shared_qwen397_var1_ns_p1: "52abfd80b6aa", idc_shared_qwen397_var1_ns_p2: "2c3743a581e7", idc_shared_qwen397_var1_bs_p1: "2ecbf832c380", idc_shared_qwen397_var1_bs_p2: "155341cd1aa5", idc_shared_qwen397_var2_ns_p1: "7fdd1fe084cf", idc_shared_qwen397_var2_ns_p2: "7332f4218d9d", idc_shared_qwen397_var2_bs_p1: "098ba86df299", idc_shared_qwen397_var2_bs_p2: "7dd2a7c8c897", idc_shared_qwen397_var3_ns_p1: "62773a869a2a", idc_shared_qwen397_var3_ns_p2: "7bf2ed1bdfec", idc_shared_qwen397_var3_bs_p1: "257b8fb974cb", idc_shared_qwen397_var3_bs_p2: "714ad844ea4b", idc_shared_qwen397_var4_ns_p1: "56634eb26852", idc_shared_qwen397_var4_ns_p2: "690b1c53f024", idc_shared_qwen397_var4_bs_p1: "c16e52198c81", idc_shared_qwen397_var4_bs_p2: "fc649169120b", idc_shared_opus47_var1_ns_p1: "d49063949a37", idc_shared_opus47_var1_ns_p2: "ecf5d708eb34", idc_shared_opus47_var1_bs_p1: "e771222f5995", idc_shared_opus47_var1_bs_p2: "0a9422d2dc5c", idc_shared_opus47_var2_ns_p1: "22b147836dfa", idc_shared_opus47_var2_ns_p2: "cbe611021ccc", idc_shared_opus47_var2_bs_p1: "45d47fc4a87a", idc_shared_opus47_var2_bs_p2: "0c1a14026dce", idc_shared_opus47_var3_ns_p1: "3e6349020193", idc_shared_opus47_var3_ns_p2: "3f2019c98cfd", idc_shared_opus47_var3_bs_p1: "f4a8f2c8231e", idc_shared_opus47_var3_bs_p2: "d747e5362ad4", idc_shared_opus47_var4_ns_p1: "4e30dfb53cd5", idc_shared_opus47_var4_ns_p2: "8c2bed8e2a2d", idc_shared_opus47_var4_bs_p1: "d6bcd130131a", idc_shared_opus47_var4_bs_p2: "d644670e0bf7", idc_shared_opus46_var1_ns_p1: "f5f9ce949936", idc_shared_opus46_var1_ns_p2: "33496d8bfd4f", idc_shared_opus46_var1_bs_p1: "2f9f0ec3920a", idc_shared_opus46_var1_bs_p2: "29d815d4daca", idc_shared_opus46_var2_ns_p1: "a2390bb35c0e", idc_shared_opus46_var2_ns_p2: "73d694765f06", idc_shared_opus46_var2_bs_p1: "d8a8cbc80258", idc_shared_opus46_var2_bs_p2: "71baf191b04b", idc_shared_opus46_var3_ns_p1: "2e789bf09529", idc_shared_opus46_var3_ns_p2: "696bae20a1c8", idc_shared_opus46_var3_bs_p1: "90b109781e1a", idc_shared_opus46_var3_bs_p2: "4fe656192b67", idc_shared_opus46_var4_ns_p1: "eed9c4d65259", idc_shared_opus46_var4_ns_p2: "5daadea6a4af", idc_shared_opus46_var4_bs_p1: "4e8eef648958", idc_shared_opus46_var4_bs_p2: "c0e1ee967d40", idc_shared_gpt55_var1_ns_p1: "e381a14c3499", idc_shared_gpt55_var1_ns_p2: "2387aae97f3f", idc_shared_gpt55_var1_bs_p1: "7ae6e681e0b2", idc_shared_gpt55_var1_bs_p2: "13377aae100d", idc_shared_gpt55_var2_ns_p1: "39246d79512b", idc_shared_gpt55_var2_ns_p2: "a93c305de7a7", idc_shared_gpt55_var2_bs_p1: "c7f781a68418", idc_shared_gpt55_var2_bs_p2: "a04154a7ddbd", idc_shared_gpt55_var3_ns_p1: "38685141654e", idc_shared_gpt55_var3_ns_p2: "fcde45892097", idc_shared_gpt55_var3_bs_p1: "7b466acfb9f8", idc_shared_gpt55_var3_bs_p2: "568839fc76d4", idc_shared_gpt55_var4_ns_p1: "325087391dcc", idc_shared_gpt55_var4_ns_p2: "4aa7b9cddebb", idc_shared_gpt55_var4_bs_p1: "0818e2c6dbfc", idc_shared_gpt55_var4_bs_p2: "a40bd04aa875", idc_shared_geminiflash_var1_ns_p1: "f741236c3522", idc_shared_geminiflash_var1_ns_p2: "e5b49eb219d3", idc_shared_geminiflash_var1_bs_p1: "e37fbe24577f", idc_shared_geminiflash_var1_bs_p2: "3cb8bdb0b212", idc_shared_geminiflash_var2_ns_p1: "5f4d80548c09", idc_shared_geminiflash_var2_ns_p2: "f2e26b6e1ec2", idc_shared_geminiflash_var2_bs_p1: "f933f6c3cf7a", idc_shared_geminiflash_var2_bs_p2: "9dac4e6450de", idc_shared_geminiflash_var3_ns_p1: "1d0c38fb054a", idc_shared_geminiflash_var3_ns_p2: "f71bd12f0b58", idc_shared_geminiflash_var3_bs_p1: "6762aebc84d8", idc_shared_geminiflash_var3_bs_p2: "28a3bf694d67", idc_shared_geminiflash_var4_ns_p1: "8f6d0b981c6a", idc_shared_geminiflash_var4_ns_p2: "e307dc6dbc6e", idc_shared_geminiflash_var4_bs_p1: "b1cc6386eee1", idc_shared_geminiflash_var4_bs_p2: "4f5e9575d94e", idc_shared_geminipro_var1_ns_p1: "58227eb178ad", idc_shared_geminipro_var1_ns_p2: "fc2395624644", idc_shared_geminipro_var1_bs_p1: "4330f85b3fcb", idc_shared_geminipro_var1_bs_p2: "1bd4224a37d9", idc_shared_geminipro_var2_ns_p1: "206f1dc240ad", idc_shared_geminipro_var2_ns_p2: "7a86ad5c6fa9", idc_shared_geminipro_var2_bs_p1: "2218c4b1bd7e", idc_shared_geminipro_var2_bs_p2: "1be1c2e3a599", idc_shared_geminipro_var3_ns_p1: "66a8661e439d", idc_shared_geminipro_var3_ns_p2: "d6c51cf1b55e", idc_shared_geminipro_var3_bs_p1: "ba88e9cb6eed", idc_shared_geminipro_var3_bs_p2: "a67fd809c837", idc_shared_geminipro_var4_ns_p1: "4834de548a63", idc_shared_geminipro_var4_ns_p2: "f7816950ea61", idc_shared_geminipro_var4_bs_p1: "63fd1737533e", idc_shared_geminipro_var4_bs_p2: "1b373de93307", idc_midline_geminiflash_var1_ns: "35e00ab6c5d8", idc_midline_geminiflash_var1_bs: "33cf3631856f", idc_midline_geminiflash_var2_ns: "887429f1fdd7", idc_midline_geminiflash_var2_bs: "42d6cf7aa24e", idc_midline_geminiflash_var3_ns: "281940cff674", idc_midline_geminiflash_var3_bs: "dc514b7c8c37", idc_midline_geminiflash_var4_ns: "d866ca91df00", idc_midline_geminiflash_var4_bs: "705a53c946b3", idc_midline_qwen397_var1_ns: "29bafe09a44a", idc_midline_qwen397_var1_bs: "0cc40316c8d6", idc_midline_qwen397_var2_ns: "38ecc02b6320", idc_midline_qwen397_var2_bs: "2593edf8e1ac", idc_midline_qwen397_var3_ns: "dbeb9b91be5a", idc_midline_qwen397_var3_bs: "09129ffc86c9", idc_midline_qwen397_var4_ns: "2dc95271e037", idc_midline_qwen397_var4_bs: "d1a5b452ad3f", idc_midline_gpt55_var1_ns: "fbdb5b25d3b3", idc_midline_gpt55_var1_bs: "1d4d4364d34f", idc_midline_gpt55_var3_ns: "e896a719e798", idc_midline_gpt55_var3_bs: "eafc46148b10", idc_midline_geminipro_var2_ns: "e04e52b72820", idc_midline_geminipro_var2_bs: "4479e2fc190c", idc_midline_geminipro_var4_ns: "c69f2d1af06e", idc_midline_geminipro_var4_bs: "6ca501d7648d", idc_midline_geminipro_var1_ns: "86e9c1935d76", idc_midline_geminipro_var1_bs: "e48e5589d4a7", idc_midline_gpt55_var2_ns: "984ea3169fa9", idc_midline_gpt55_var2_bs: "bef26219e9e6" , idc_midline_geminipro_var3_ns: "e5b4a2804356", idc_midline_geminipro_var3_bs: "fbec11203181", idc_midline_gpt55_var4_ns: "af2de1785afe", idc_midline_gpt55_var4_bs: "4fad0e6684f9" };
const vsrc = (file) => `webpage/assets/videos/${vdir(file)}/${file}?v=${VIDEO_REVISIONS[file.replace(/\.mp4$/, "")] || VID_V}#t=0.1`;
const vsrcFlat = (track, slug, mkey, suf = "") => `webpage/assets/videos/${track}/${VREG[slug]}/${slug}/${track}_${slug}_${mkey}${suf}.mp4?v=${VID_V}#t=0.1`;
const vsuf = (s) => (s == null ? "" : "_" + String(Math.round(s * 1000)).padStart(3, "0"));
const FLAT_GAMES = new Set(["lfm/or2d", "lfm/or3d", "lfm/last", "lfm/mshoot", "lfm/scene", "lfm/cue", "lfm/craft", "lfm/shared", "lfm/handoff", "lcm/last", "lcm/mshoot", "lcm/craft", "lcm/shared"]);

const REG_LABEL = { solo: "Solo", pvp: "PvP", coop: "Coop" };

/* ---------- suite gallery (static) ---------- */
const games = {
  solo: [
    { name: "ObstacleRun2D", focus: "Reactive platforming", image: "webpage/assets/game-shots/game-001.png", desc: "Reacting quickly to oncoming obstacles and gaps, then jumping to clear them in a side-scrolling platformer." },
    { name: "ObstacleRun3D", focus: "3D parkour", image: "webpage/assets/game-shots/game-000.png", desc: "Visual grounding and spatial navigation toward a finish line while avoiding 3D obstacles." },
    { name: "LastStand", focus: "Survival under hazards", image: "webpage/assets/game-shots/game-003.png", desc: "Stay alive on a hazardous platform through perception, navigation, and planning." },
    { name: "MonsterShoot", focus: "Sustained aiming", image: "webpage/assets/game-shots/game-004.png", desc: "Enemy detection, aiming, firing, and damage avoidance in a survival shooter." },
    { name: "SceneEscape", focus: "Task-chain puzzles", image: "webpage/assets/game-shots/game-005.png", desc: "Scene exploration, NPC task tracking, memory, and long-horizon planning." },
    { name: "CueChase", focus: "Cue-guided search", image: "webpage/assets/game-shots/game-006.png", desc: "Parsing text cues and NPC hints to visually locate and reach the described target in the scene." },
    { name: "SoloCraft", focus: "Logistics & delivery", image: "webpage/assets/game-shots/game-007.png", desc: "Resource collection, item preparation, and sequencing for order fulfillment." },
  ],
  pvp: [
    { name: "SkyDuel", focus: "Direct 1v1 combat", image: "webpage/assets/game-shots/pvp-skyduel.png", desc: "Opponent tracking, strikes, evasions, and adversarial control in a direct duel." },
    { name: "CrystalGuard", focus: "Attack & defense", image: "webpage/assets/game-shots/pvp-crystalguard.png", desc: "Two sides race to destroy the opponent's crystal while defending their own." },
    { name: "MidlineClash", focus: "Competitive resource race", image: "webpage/assets/game-shots/game-009.png", desc: "Planning, navigation, and adversarial pressure while racing for shared midline resources, with each side at its own workbench and order counter." },
  ],
  coop: [
    { name: "SharedFloor", focus: "Symmetric cooperation", image: "webpage/assets/game-shots/game-011.png", desc: "Agents share objectives and capabilities, rewarding efficient division of labor." },
    { name: "HandoffRun", focus: "Asymmetric coordination", image: "webpage/assets/game-shots/game-012.png", desc: "Distinct roles must pass items and synchronize across separated areas." },
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
let REC = null, COLD = null, CURVES = null, IDC = null;

/* ---------- cold-start scores: the three tables of the paper, rows in the paper's order ---------- */
const coldEl = qs("[data-cold]"), coldSub = qs("[data-cold-sub]");
let coldReg = "solo";
const COLD_SUB = {
  solo: "Mean ± sample standard deviation over five episodes per cell. Random and Human expert are the two reference rows of the benchmark.",
  pvp: "Win rate of the row agent against the column agent over ten matches per pairing, five with each agent as Player 1.",
  coop: "Mean team score ± sample standard deviation over five episodes per cell. A team is two copies of the same model.",
};
const SHORT = {
  "Claude Opus 4.7": "Opus 4.7", "Claude Opus 4.6": "Opus 4.6", "Claude Sonnet 4.6": "Sonnet 4.6",
  "Gemini 3.1 Pro Preview": "Gemini 3.1 Pro", "Gemini 3 Flash Preview": "Gemini 3 Flash",
  "Qwen3.5-397B-A17B": "Qwen3.5-397B", "Qwen3.5-122B-A10B": "Qwen3.5-122B",
};
const shortName = (n) => SHORT[n] || n;
const logoImg = (logo) => (logo ? `<img class="ct-logo" src="webpage/assets/paper/icons/${logo}.png" alt="">` : "");
const REF_ROW = /^(Random|Human expert)$/;

function coldMeanTable(t) {
  let prev = null;
  const body = t.rows.map((r) => {
    const ref = REF_ROW.test(r.name);
    const label = ref ? "Reference" : (COLD.groups || {})[r.group];
    const head = r.group !== prev
      ? `<tr class="ct-group"><th colspan="${t.games.length + 1}" scope="rowgroup">${label}</th></tr>` : "";
    prev = r.group;
    const cells = r.cells.map(([m, s]) =>
      `<td><span class="ct-m">${m.toFixed(3)}</span><span class="ct-sd">±${s.toFixed(3)}</span></td>`).join("");
    return `${head}<tr${ref ? ' class="ct-ref"' : ""}><th scope="row">${logoImg(r.logo)}<span>${r.name}</span></th>${cells}</tr>`;
  }).join("");
  return `<div class="ct-scroll${t.games.length <= 3 ? " ct-narrow" : ""}"><table class="ct">
      <thead><tr><th scope="col">Agent</th>${t.games.map((g) => `<th scope="col">${g}</th>`).join("")}</tr></thead>
      <tbody>${body}</tbody>
    </table></div>`;
}

function coldPvp(p) {
  const cell = (v) => {
    if (v == null) return `<td class="mx-diag">–</td>`;
    return `<td class="mx${v >= 0.6 ? " mx-hi" : ""}" style="--a:${(0.05 + 0.8 * v).toFixed(2)}">${v.toFixed(2)}</td>`;
  };
  return `<div class="pvp-grid">${p.games.map((b) => `
      <figure class="pvp-mx">
        <figcaption>${b.game}</figcaption>
        <div class="ct-scroll"><table class="ct-mx">
          <thead><tr><th scope="col"><span class="mx-corner">row vs. column</span></th>${p.cols.map((c) => `<th scope="col" title="${c}">${shortName(c)}</th>`).join("")}</tr></thead>
          <tbody>${b.rows.map((r) => `<tr><th scope="row">${logoImg(r.logo)}<span>${shortName(r.name)}</span></th>${r.cells.map(cell).join("")}</tr>`).join("")}</tbody>
        </table></div>
      </figure>`).join("")}</div>`;
}

function renderCold() {
  if (!coldEl || !COLD) return;
  if (coldSub) coldSub.textContent = COLD_SUB[coldReg];
  coldEl.innerHTML = coldReg === "pvp" ? coldPvp(COLD.pvp) : coldMeanTable(COLD[coldReg]);
}

/* ---------- recordings (track + game; grid or matchup) ---------- */
const vidTabs = qs("[data-vid-tabs]"), vidContent = qs("[data-vid-content]");
let vidTrack = "lfm", vidRegime = "Solo", vidIdx = 0, muA = 0, muB = 1;
const curGames = () => ((REC && REC[vidTrack]) || []).filter((g) => g.regime === vidRegime);

function vCard(track, slug, mkey, name, logo, score, autoplay, fileScore) {
  const fs = fileScore != null ? fileScore : score;
  const src = FLAT_GAMES.has(`${track}/${slug}`) ? vsrcFlat(track, slug, mkey)
            : vsrc(`${track}_${slug}_${mkey}${vsuf(fs)}.mp4`);
  return `
      <figure class="vg-card">
        <video src="${src}" controls ${autoplay ? "autoplay " : ""}muted loop playsinline preload="metadata" aria-label="${name} playing ${slug}"></video>
        <figcaption class="vg-meta vg-meta-c">
          <span class="who"><img src="webpage/assets/paper/icons/${logo}.png" alt="">${name}</span>
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
  const src = (suf) => flat ? vsrcFlat(track, slug, m.key, suf) : vsrc(`${track}_${slug}_${m.key}${suf}${vsuf(fs)}.mp4`);
  return `
      <figure class="vg-card">
        ${m.p2 ? `<button class="cpv-sync" type="button" title="Play both perspectives from the start">⟲ Play both</button>` : ""}
        <div class="cpv-pair">${cpv(flat ? src("_p1") : src(""), "Player 1", true)}${m.p2 ? cpv(src("_p2"), "Player 2", true) : ""}</div>
        <figcaption class="vg-meta vg-meta-c">
          <span class="who"><img src="webpage/assets/paper/icons/${m.logo}.png" alt="">${m.name}</span>
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
  const cols2 = g.models.length === 4 ? " vgrid-2" : "";  // 4 models (LCM solo/coop): clean 2x2 instead of an orphaned 3+1 row
  vidContent.innerHTML = `<div class="vgrid${cols2}">${cards}</div>`;
  vidContent.querySelectorAll(".cpv-sync").forEach((btn) =>
    btn.addEventListener("click", () => playBothFromStart(btn.closest(".vg-card").querySelector(".cpv-pair"))));
}

function renderPairwiseMatchup(g) {
  const ms = g.pvpModels;
  if (muA >= ms.length) muA = 0;
  if (muB >= ms.length) muB = 1 % ms.length;
  if (muA === muB) muB = (muA + 1) % ms.length;
  const A = ms[muA], B = ms[muB];
  const picks = (sel) => ms.map((m, i) =>
    `<button class="mpick${i === sel ? " active" : ""}" type="button" data-i="${i}"><img src="webpage/assets/paper/icons/${m.logo}.png" alt="">${m.name}</button>`).join("");
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
        <figcaption class="vg-meta"><span class="who"><span class="mu-plab mu-plab-p${pnum}">Player&nbsp;${pnum}</span><img src="webpage/assets/paper/icons/${m.logo}.png" alt="">${m.name}</span>${res}</figcaption>
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

/* ---------- shared helpers: skill text, curve drawing ---------- */
const esc = (s) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const mdInline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
function renderSkill(md) {
  if (!md) return "";
  let html = "", inList = false;
  for (const raw of md.split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) { if (inList) { html += "</ul>"; inList = false; } continue; }  // skill-file headings are not shown
    if (line.startsWith("- ") || line.startsWith("* ")) { if (!inList) { html += "<ul>"; inList = true; } html += `<li>${mdInline(line.slice(2))}</li>`; }
    else { if (inList) { html += "</ul>"; inList = false; } html += `<p>${mdInline(line)}</p>`; }
  }
  if (inList) html += "</ul>";
  return html;
}
// six model colours, R0 baseline, circles and a star for the best round
const IDC_STYLE = {
  opus47: { color: "#f5a623" },
  opus46: { color: "#1f77b4" },
  gpt55: { color: "#2ca02c" },
  geminipro: { color: "#e377c2" },
  geminiflash: { color: "#9467bd" },
  qwen397: { color: "#8c564b" },
};
const cvNumber = (v) => Number(v.toFixed(3)).toString();
function curveStats(curve) {
  const points = (curve || []).slice(0, 11).map((v, i) => ({ i, v })).filter((p) => Number.isFinite(p.v));
  if (!points.length) return null;
  const peak = points.reduce((a, b) => b.v > a.v ? b : a);
  return { points, peak, first: points[0], last: points[points.length - 1] };
}
function cvMarker(cx, cy, color, peak, above) {
  cx = +cx.toFixed(1); cy = +cy.toFixed(1);
  if (peak) {
    const points = Array.from({ length: 10 }, (_, k) => {
      const a = -Math.PI / 2 + k * Math.PI / 5, r = k % 2 ? 2.4 : 6;
      return `${(cx + r * Math.cos(a)).toFixed(1)},${(cy + r * Math.sin(a)).toFixed(1)}`;
    }).join(" ");
    return `<polygon points="${points}" fill="${color}" stroke="${color}" stroke-width="1" stroke-linejoin="round"/>`;
  }
  return `<circle cx="${cx}" cy="${cy}" r="3" fill="${above ? color : "#fff"}" stroke="${color}" stroke-width="1.5"/>`;
}
function cvPaths(points, X, Y, value) {
  const segments = [];
  for (const p of points) {
    if (!segments.length || p.i !== segments[segments.length - 1].at(-1).i + 1) segments.push([]);
    segments.at(-1).push(p);
  }
  return segments.map((segment) => `<polyline points="${segment.map((p) => `${X(p.i).toFixed(1)},${Y(value(p)).toFixed(1)}`).join(" ")}" class="cv-line"/>`).join("");
}
// one score scale per game, shared by its six curves (as in the rows of the paper's table)
function gameRange(vals) {
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const span = Math.max(hi - lo, 0.05);
  const step = span > 0.45 ? 0.2 : span > 0.18 ? 0.1 : 0.05;
  lo = Math.max(0, Math.floor((lo - span * 0.06) / step) * step);
  hi = Math.min(1, Math.ceil((hi + span * 0.06) / step) * step);
  if (hi - lo < step) hi = lo + step;
  const ticks = [];
  for (let v = lo; v <= hi + 1e-9; v += step) ticks.push(+v.toFixed(2));
  return { lo, hi, ticks };
}
function miniCurve(curve, key, range) {
  const stats = curveStats(curve);
  if (!stats) return `<div class="mc-empty"></div>`;
  const { points, peak } = stats;
  const color = IDC_STYLE[key]?.color || "#2f6cad";
  const W = 320, H = 156, pl = 34, pr = 12, pt = 14, pb = 24;
  const X = (i) => pl + i / 10 * (W - pl - pr);
  const Y = (v) => H - pb - (v - range.lo) / (range.hi - range.lo) * (H - pt - pb);
  const base = points.find((p) => p.i === 0);
  const grid = range.ticks.map((t) =>
    `<line x1="${pl}" x2="${W - pr}" y1="${Y(t).toFixed(1)}" y2="${Y(t).toFixed(1)}" class="cv-grid"/>` +
    `<text x="${pl - 6}" y="${(Y(t) + 3.5).toFixed(1)}" class="cv-tick" text-anchor="end">${t}</text>`).join("");
  const baseline = base ? `<line x1="${pl}" x2="${W - pr}" y1="${Y(base.v).toFixed(1)}" y2="${Y(base.v).toFixed(1)}" stroke="${color}" stroke-width="1.2" stroke-dasharray="4 3" opacity="0.75"/>` : "";
  const marks = points.map((p) => `<g class="cv-point"><title>R${p.i}: ${cvNumber(p.v)}${p === peak ? " (best round)" : ""}</title>${cvMarker(X(p.i), Y(p.v), color, p === peak, !!base && p.v > base.v)}</g>`).join("");
  const xt = Array.from({ length: 11 }, (_, i) => `<text x="${X(i).toFixed(1)}" y="${H - 7}" class="cv-tick${i % 5 ? " cv-tick-minor" : ""}" text-anchor="middle">R${i}</text>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" class="idc-curve" role="img" aria-label="Score per round, R0 to R10; best round R${peak.i} at ${cvNumber(peak.v)}">
    ${grid}${baseline}<g style="--accent:${color}">${cvPaths(points, X, Y, (p) => p.v)}</g>${marks}${xt}</svg>`;
}

/* ---------- Improvement Dynamics Curves: the six agents of a game side by side ---------- */
const idcGameTabs = qs("[data-idc-game]"), idcContent = qs("[data-idc-content]");
let idcG = 0;
function renderIdcGameTabs() {
  if (!idcGameTabs || !CURVES) return;
  // one set per regime, so a wrapped row never splits a regime
  const sets = [];
  CURVES.forEach((g, i) => {
    if (!sets.length || sets.at(-1).regime !== g.regime) sets.push({ regime: g.regime, tabs: [] });
    sets.at(-1).tabs.push(`<button class="tab${i === idcG ? " active" : ""}" type="button" role="tab" data-i="${i}">${g.name}</button>`);
  });
  idcGameTabs.innerHTML = sets.map((s) => `<span class="tab-set"><span class="tab-group">${s.regime}</span>${s.tabs.join("")}</span>`).join("");
  qsa(".tab", idcGameTabs).forEach((b) => b.addEventListener("click", () => { idcG = Number(b.dataset.i); renderIdcContent(); }));
}
function renderIdcContent() {
  if (!idcContent || !CURVES) return;
  qsa(".tab", idcGameTabs).forEach((b) => b.classList.toggle("active", Number(b.dataset.i) === idcG));
  const g = CURVES[idcG];
  const vals = g.models.flatMap((m) => (m.curve || []).filter(Number.isFinite));
  const range = gameRange(vals.length ? vals : [0, 1]);
  idcContent.innerHTML = `<div class="mc-grid">${g.models.map((m) => {
    const st = curveStats(m.curve);
    const meta = st
      ? `<span>R0 <b>${st.first.v.toFixed(3)}</b></span><span>best R${st.peak.i} <b>${st.peak.v.toFixed(3)}</b></span>` +
        (st.last.i !== st.peak.i ? `<span>R${st.last.i} <b>${st.last.v.toFixed(3)}</b></span>` : "")
      : "";
    return `<figure class="mc-card">
        <figcaption class="mc-head"><img src="webpage/assets/paper/icons/${m.logo}.png" alt="">${m.name}</figcaption>
        ${miniCurve(m.curve, m.key, range)}
        <div class="mc-meta">${meta}</div>
      </figure>`;
  }).join("")}</div>`;
}

/* ---------- Transfer test: the learned skill, its variant cells and recordings (three games) ---------- */
const trGameTabs = qs("[data-tr-game]"), trModelBtns = qs("[data-tr-model]"), transferContent = qs("[data-transfer-content]");
let trG = 0, trM = 0;
const VAR_NAME = { VAR1: "VAR1", VAR2: "VAR2", VAR3: "VAR3", VAR4: "VAR4" };
const VAR_DESC = { VAR1: "var1", VAR2: "var2", VAR3: "var3", VAR4: "var4" };
function renderTrTabs() {
  if (!trGameTabs || !IDC) return;
  trGameTabs.innerHTML = IDC.map((g, i) => `<button class="tab${i === trG ? " active" : ""}" type="button" role="tab" data-i="${i}">${g.name}</button>`).join("");
  qsa(".tab", trGameTabs).forEach((b) => b.addEventListener("click", () => { trG = Number(b.dataset.i); trM = 0; renderTrModels(); renderTransfer(); }));
}
function renderTrModels() {
  if (!trModelBtns || !IDC) return;
  trModelBtns.innerHTML = IDC[trG].models.map((m, i) => `<button class="mpick${i === trM ? " active" : ""}" type="button" data-i="${i}"><img src="webpage/assets/paper/icons/${m.logo}.png" alt="">${m.name}</button>`).join("");
  qsa(".mpick", trModelBtns).forEach((b) => b.addEventListener("click", () => { trM = Number(b.dataset.i); renderTransfer(); }));
}
function renderTransfer() {
  if (!transferContent || !IDC) return;
  qsa(".tab", trGameTabs).forEach((b, j) => b.classList.toggle("active", j === trG));
  qsa(".mpick", trModelBtns).forEach((b, j) => b.classList.toggle("active", j === trM));
  const g = IDC[trG], m = g.models[trM];
  const coop = g.coop;
  const syncBtn = `<button class="cpv-sync" type="button" title="Play both perspectives from the start">⟲ Play both</button>`;
  // no #t= fragment here: these do not autoplay, so they should rest on frame 0 (see the seek below)
  const vsrc0 = (file) => vsrc(file).replace(/#t=[\d.]+$/, "");
  const cell = (base, p2) => `<figure class="ivg-cell${coop ? " coop" : ""}">${coop
      ? `${p2 ? syncBtn : ""}<div class="cpv-pair">${cpv(vsrc0(base + "_p1.mp4"), "Player 1", false)}${p2 ? cpv(vsrc0(base + "_p2.mp4"), "Player 2", false) : ""}</div>`
      : `<video src="${vsrc0(base + ".mp4")}" controls muted loop playsinline preload="metadata"></video>`}</figure>`;
  const empty = `<figure class="ivg-cell"><div class="ivg-blank"></div></figure>`;
  const delta = (v) => (v.withSkill != null && v.withoutSkill != null ? v.withSkill - v.withoutSkill : null);
  const badge = (d) => (d == null ? "" : `<span class="ivar-delta ${d >= 0 ? "up" : "down"}">${d >= 0 ? "+" : "−"}${Math.abs(d).toFixed(3)}</span>`);
  const num = (x) => (x != null ? x.toFixed(3) : "–");
  const trows = m.vars.map((v) => `<tr>
      <th scope="row">${VAR_NAME[v.var] || v.var}</th>
      <td class="tr-desc">${esc((g.varDescs || {})[VAR_DESC[v.var]] || "")}</td>
      <td class="tr-num">${num(v.withoutSkill)}</td><td class="tr-num">${num(v.withSkill)}</td>
      <td class="tr-d">${badge(delta(v)) || '<span class="tr-na">–</span>'}</td></tr>`).join("");
  const vrows = m.vars.map((v, i) => `${i > 0 ? `<div class="ivg-divider"></div>` : ""}<div class="ivg-lab">${VAR_NAME[v.var] || v.var}${badge(delta(v)) ? " " + badge(delta(v)) : ""}</div>
      ${v.ns ? cell(`idc_${g.slug}_${m.key}_${v.vk}_ns`, v.ns2) : empty}
      ${v.bs ? cell(`idc_${g.slug}_${m.key}_${v.vk}_bs`, v.bs2) : empty}`).join("");
  transferContent.innerHTML = `
    <div class="tr-top">
      <div class="idc-skill"><div class="idc-skill-inner"><h4>Learned skill <span class="idc-sub">${g.name} &middot; ${m.name}</span></h4>${renderSkill(m.skill)}</div></div>
      <div class="tr-box">
        <h4>Variants</h4>
        <div class="ct-scroll"><table class="ct tr-table">
          <thead><tr><th scope="col"></th><th scope="col">What the variant changes</th><th scope="col">Without</th><th scope="col">With</th><th scope="col">Difference</th></tr></thead>
          <tbody>${trows}</tbody>
        </table></div>
      </div>
    </div>
    <div class="idc-transfer">
      <h4>Recordings</h4>
      <div class="idc-vgrid">
        <div></div><div class="ivg-head">Without skill</div><div class="ivg-head">With the learned skill</div>
        ${vrows}
      </div>
    </div>`;
  transferContent.querySelectorAll(".cpv-sync").forEach((btn) =>
    btn.addEventListener("click", () => playBothFromStart(btn.closest(".ivg-cell").querySelector(".cpv-pair"))));
  // an explicit seek to 0 makes the browser decode and show frame 0 as the still
  transferContent.querySelectorAll("video").forEach((v) => {
    const toStart = () => { v.currentTime = 0; };
    if (v.readyState >= 1) toStart(); else v.addEventListener("loadedmetadata", toStart, { once: true });
  });
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
wire("[data-cold-tabs] .tab", "reg", (r) => { coldReg = r; renderCold(); });
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
// all page data is inlined in data.js
const loadData = (name) => Promise.resolve((window.OGA_DATA || {})[name]);
Promise.all([loadData("cold"), loadData("rec"), loadData("curves"), loadData("idc")]).then(([coldData, recData, curveData, idcData]) => {
  COLD = coldData; REC = recData; CURVES = curveData; IDC = idcData;
  // both result viewers open on LastStand, the running example of the paper
  idcG = Math.max(0, CURVES.findIndex((g) => g.slug === "last"));
  trG = Math.max(0, IDC.findIndex((g) => g.slug === "last"));
  renderCold();
  renderVidTabs();
  renderIdcGameTabs();
  renderTrTabs();
  renderTrModels();
  renderIdcContent();   // curves are light SVG, drawn at once; the two video blocks load on scroll
  lazy(vidContent, renderVidGame);
  lazy(transferContent, renderTransfer);
}).catch((err) => { console.error("data load failed", err); });
