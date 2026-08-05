/* 셸 — 2층 해시 라우터, 좌측 레일(접기), 최근 강좌, 전역 단축키
 *
 * 라우터가 2층인 이유: 강좌·주차 목록은 작업 본문 위에 뜨는 부유 패널이다.
 * 패널을 열고 닫아도 베이스는 언마운트되지 않으므로, 몇 분 걸리는 생성 스트림과
 * 편집기의 미저장 텍스트가 살아 있다.
 *
 *   layer "base"  → #view 를 갈아치운다  (/syllabus /textbook /slides)
 *   layer "panel" → 패널을 띄운다        (/courses /textbook/pick /slides/pick /workspace)
 */
"use strict";

import { $, $$, el, toast, api } from "./util.js";
import { hydrateIcons } from "./icons.js";
import { openPanel, closePanel, isOpen as panelOpen, setCloseHandler, setActions } from "./panel.js";
import { state, getProject, getProjects, getWeeks, invalidate } from "./store.js";

const view = $("#view");

/* ── 라우트 테이블 ──────────────────────────────────
 * 일하는 화면은 전부 바탕(base)이다. 패널은 "고르고 닫는" 표면만 맡는다.
 */
const routes = [
  { re: /^\/home$/,      nav: "",    layer: "base", load: () => import("./home.js") },
  { re: /^\/syllabus$/,  nav: "syl", layer: "base", load: () => import("./syllabus.js") },
  { re: /^\/textbook$/,  nav: "doc", layer: "base", load: () => import("./week-doc.js") },
  { re: /^\/slides$/,    nav: "ppt", layer: "base", load: () => import("./week-ppt.js") },
  { re: /^\/video$/,     nav: "vid", layer: "base", load: () => import("./week-video.js") },

  // 부유 패널(위층) — 고르는 곳
  { re: /^\/courses$/,        nav: "",    layer: "panel", load: () => import("./pick.js") },
  { re: /^\/textbook\/pick$/, nav: "doc", layer: "panel", load: () => import("./pick.js") },
  { re: /^\/slides\/pick$/,   nav: "ppt", layer: "panel", load: () => import("./pick.js") },
  { re: /^\/video\/pick$/,    nav: "vid", layer: "panel", load: () => import("./pick.js") },
  { re: /^\/workspace$/,      nav: "",    layer: "panel", load: () => import("./workspace.js") },
];

const HOME = routes[0];

function parseHash() {
  const raw = (location.hash || "#/home").slice(1) || "/home";
  const [path, qs] = raw.split("?");
  const params = new URLSearchParams(qs || "");
  for (const rt of routes) {
    const m = path.match(rt.re);
    if (m) return { path, rt, args: m.slice(1).map(decodeURIComponent), params };
  }
  return { path: "/home", rt: HOME, args: [], params };
}

let baseToken = 0;      // 레이어별로 취소 토큰을 따로 둬야 경쟁 상태가 안 생긴다
let panelToken = 0;
let basePath = null;

export function navigate(path) {
  if (location.hash === "#" + path) render();
  else location.hash = path;
}
export const currentBase = () => basePath || "/home";

/* ── 렌더 ─────────────────────────────────────────── */
async function render() {
  const { path, rt, args, params } = parseHash();
  $$("#side-nav a").forEach((a) => a.classList.toggle("active", a.dataset.nav === rt.nav));
  // 레일은 매 렌더마다 맞춘다(캐시를 쓰므로 추가 요청이 거의 없다).
  // 이걸 부팅 시점에만 하면, 강좌가 이미 골라진 상태로 해시만 바뀔 때 레일이
  // 옛 상태(강좌 없음 → 주차 메뉴 잠김 · 주차 목록 대신 강좌 목록)로 굳어 버린다.
  refreshRail();

  if (rt.layer === "panel") {
    if (!basePath) await mountBase(HOME, "/home", [], new URLSearchParams());
    await mountPanel(rt, path, args, params);
    return;
  }

  if (panelOpen()) closePanel();

  // 이미 이 화면이 바탕에 떠 있으면 다시 마운트하지 않는다.
  // 이게 2층 구조의 존재 이유다 — 재마운트하면 스트림과 입력이 날아간다.
  if (path === basePath && view.firstElementChild) {
    view.focus({ preventScroll: true });
    return;
  }
  await mountBase(rt, path, args, params);
}

async function mountBase(rt, path, args, params) {
  const token = ++baseToken;
  view.innerHTML = '<div class="empty">불러오는 중…</div>';
  try {
    const mod = await rt.load();
    if (token !== baseToken) return;
    view.innerHTML = "";
    basePath = path;
    const ctx = { args, params, path, navigate };
    await mod.mount(view, ctx);
    if (mod.meta) applyBaseHead(mod, ctx);
  } catch (e) {
    console.error(e);
    if (token !== baseToken) return;
    view.innerHTML = "";
    basePath = path;
    view.appendChild(el("div", "empty", "화면을 불러오지 못했습니다: " + e.message));
  }
  hydrateIcons(view);
  view.focus({ preventScroll: true });
  window.scrollTo({ top: 0 });
}

async function mountPanel(rt, path, args, params) {
  const token = ++panelToken;
  const host = openPanel({ railed: rt.railed !== false });
  host.body.innerHTML = '<div class="empty">불러오는 중…</div>';
  try {
    const mod = await rt.load();
    if (token !== panelToken) return;
    host.body.innerHTML = "";
    const ctx = { args, params, path, navigate, panel: host };
    if (mod.meta) {
      host.setHead(resolve(mod.meta.title, ctx) || "", resolve(mod.meta.subtitle, ctx));
      setActions(mod.meta.actions ? mod.meta.actions(ctx) : []);
    }
    await mod.mount(host.body, ctx);
  } catch (e) {
    console.error(e);
    if (token !== panelToken) return;
    host.body.innerHTML = "";
    host.body.appendChild(el("div", "empty", "화면을 불러오지 못했습니다: " + e.message));
  }
  hydrateIcons(host.root);
  host.focusBody();
}

const resolve = (v, ctx) => (typeof v === "function" ? v(ctx) : v);

/** meta → 바탕 화면의 제목 줄. 모듈이 만든 .page 맨 앞에 끼워 넣어 폭이 정확히 맞는다. */
function applyBaseHead(mod, ctx) {
  const page = view.querySelector(".page");
  if (!page) return;
  const head = el("div", "page-head");
  const left = el("div");
  left.appendChild(el("h1", null, resolve(mod.meta.title, ctx) || ""));
  const sub = resolve(mod.meta.subtitle, ctx);
  if (sub) left.appendChild(el("p", null, sub));
  head.appendChild(left);
  const actions = (mod.meta.actions ? mod.meta.actions(ctx) : []).filter(Boolean);
  if (actions.length) {
    const box = el("div", "head-actions");
    actions.forEach((n) => box.appendChild(n));
    head.appendChild(box);
  }
  page.insertBefore(head, page.firstChild);
}

setCloseHandler(() => navigate(basePath || "/home"));

/* ── 좌측 레일 접기 ───────────────────────────────── */
const RAIL_KEY = "ida.rail";
function applyRail(s) {
  document.body.dataset.rail = s;
  const btn = $("#rail-toggle");
  if (btn) btn.setAttribute("aria-label", s === "collapsed" ? "메뉴 펼치기" : "메뉴 접기");
}
function toggleRail() {
  const next = document.body.dataset.rail === "collapsed" ? "expanded" : "collapsed";
  localStorage.setItem(RAIL_KEY, next);
  applyRail(next);
}
applyRail(localStorage.getItem(RAIL_KEY) === "collapsed" ? "collapsed" : "expanded");

/* ── 레일 머리·발 — 현재 강좌와 진행률 ────────────────
 * "지금 어느 강좌의 몇 주차를 하는 중인가" 가 이 앱에서 가장 중요한 값이라
 * 어느 화면에 있어도 보이도록 레일에 박아 둔다. */
export async function renderChip() {
  const nameEl = $("#su-name"), teamEl = $("#su-team");
  const p = await getProject();
  if (!p) {
    nameEl.textContent = "강좌를 고르세요";
    teamEl.textContent = "클릭 → 강좌 · 연결 설정";
    teamEl.classList.add("bad");
    return;
  }
  teamEl.classList.remove("bad");
  nameEl.textContent = p.name;
  const list = await getProjects();
  const row = list.find((x) => x.id === p.id);
  teamEl.textContent = p.syllabus_md
    ? `${p.weeks}주차 · 교재 ${row?.done_doc ?? 0} · 덱 ${row?.done_deck ?? 0}`
    : "강의계획서가 아직 없습니다";
  $("#su-avatar").textContent = (p.name || "교수").slice(0, 2);
}

/** 강의계획서가 없으면 주차 화면은 열 수 없다 — 레일에서 눌러도 되게 막는다.
 *  겸해서 주차 단계 메뉴에 **지금 몇 주차인지**를 붙인다(슬라이드를 여러 번
 *  만들다 보면 바탕의 작업 바가 스크롤에 묻혀 어느 주차인지 놓친다). */
export async function renderNavGuards() {
  const p = await getProject();
  const on = !!(p && p.syllabus_md);
  ["doc", "ppt", "vid"].forEach((k) => {
    const a = $(`#side-nav a[data-nav="${k}"]`);
    if (!a) return;
    a.setAttribute("aria-disabled", on ? "false" : "true");
    const dest = a.querySelector(".nav-dest");
    if (dest) dest.textContent = on ? `${String(state.week).padStart(2, "0")}주차` : "주차별";
  });
}

/* ── 레일 목록 ─────────────────────────────────────
 * 강좌를 고르기 전에는 강좌 목록, 고른 뒤에는 **주차 목록**을 보여 준다.
 * 슬라이드는 주차마다 여러 번 빌드하므로, 어느 주차를 어디까지 만들었는지가
 * 어느 화면에 있어도 보여야 한다. */
/* 목록 그리기는 await 를 두 번 지나므로, 동시에 두 번 불리면 두 번 다 append 해서
 * 행이 겹친다(15줄이 30줄이 됐다). 라우터와 같은 토큰으로 최신 호출만 그린다. */
let railToken = 0;

export async function renderRecent() {
  const token = ++railToken;
  const box = $("#side-recent");
  const titleEl = $("#side-section-title");
  const moreEl = $("#side-section-more");

  const p = await getProject();
  if (token !== railToken) return;

  if (p && p.syllabus_md) {
    const { weeks } = await getWeeks();
    if (token !== railToken) return;
    titleEl.textContent = "주차";
    moreEl.textContent = "목록 열기";
    moreEl.dataset.go = "/slides/pick";
    box.innerHTML = "";
    weeks.forEach((w) => box.appendChild(weekRow(w, p)));
    // 현재 주차가 스크롤 밖이면 보이도록 끌어온다
    box.querySelector(".recent-item.on")?.scrollIntoView({ block: "nearest" });
    return;
  }

  // 매 렌더에서 호출되므로 강제 재조회하지 않는다(목록이 바뀌면 invalidate 가 부른다).
  const list = await getProjects();
  if (token !== railToken) return;
  titleEl.textContent = "최근 강좌";
  moreEl.textContent = "전체";
  moreEl.dataset.go = "/courses";
  box.innerHTML = "";
  if (!list.length) {
    box.appendChild(el("div", "side-empty", "아직 강좌가 없습니다."));
    return;
  }
  list.slice(0, 10).forEach((c) => {
    const a = el("button", "recent-item" + (c.id === state.courseId ? " on" : ""));
    a.type = "button";
    a.title = `${c.name} · 교재 ${c.done_doc}/${c.weeks} · 덱 ${c.done_deck}/${c.weeks}`;
    const ratio = c.weeks ? c.done_deck / c.weeks : 0;
    a.appendChild(el("span", "ri-dot " + (ratio >= 1 ? "done" : ratio > 0 ? "part" : "")));
    a.appendChild(el("span", "ri-name", c.name));
    a.appendChild(el("span", "ri-count tnum", `${c.done_deck}/${c.weeks}`));
    a.addEventListener("click", async () => {
      await pickCourse(c.id);
      navigate(basePath && basePath !== "/home" ? basePath : "/syllabus");
    });
    box.appendChild(a);
  });
}

/** 레일의 주차 한 줄 — 번호 · 주제 · 진행 상태 뱃지. 누르면 그 주차로 전환. */
function weekRow(w, p) {
  const on = w.week === state.week;
  const a = el("button", "recent-item ri-wk" + (on ? " on" : ""));
  a.type = "button";
  const stage = w.deck ? `덱 v${w.deck}` : w.plan ? "플랜" : w.ppt ? "개요" : w.doc ? "교재" : "";
  a.title = `${w.week}주차 ${w.title || ""}${stage ? " · " + stage : " · 미작성"}`;
  a.appendChild(el("span", "ri-dot " + (w.deck ? "done" : (w.ppt || w.doc) ? "part" : "")));
  a.appendChild(el("span", "ri-wk-no tnum", String(w.week).padStart(2, "0")));
  a.appendChild(el("span", "ri-name", w.title || `${w.week}주차`));
  if (stage) a.appendChild(el("span", "ri-stage", stage));
  a.addEventListener("click", () => {
    state.week = w.week;
    refreshRail();
    // 주차 단계 화면에 있으면 그 자리에서 바꾸고, 아니면 슬라이드로 보낸다.
    const stay = basePath === "/slides" || basePath === "/textbook" || basePath === "/video";
    if (!stay) navigate("/slides");
    window.dispatchEvent(new CustomEvent("ida:week-changed"));
  });
  return a;
}

/** 강좌 전환 — 캐시를 비우고 레일을 갱신한다. */
export async function pickCourse(id) {
  state.courseId = id;
  invalidate();
  await refreshRail();
  window.dispatchEvent(new CustomEvent("ida:course-changed"));
}

export async function refreshRail() {
  await renderRecent();
  await renderChip();
  await renderNavGuards();
}

/* ── 전역 액션: 다음 미작성 주차 ──────────────────────
 * 15주차를 도는 것이 이 앱의 핵심 루프다. 어느 화면에서든 한 번에 큐로 돌아온다. */
async function gotoNextWeek() {
  const p = await getProject();
  if (!p) { navigate("/workspace"); return; }
  if (!p.syllabus_md) { toast("먼저 강의계획서를 만드세요."); navigate("/syllabus"); return; }
  const { weeks } = await getWeeks(true);
  const next = weeks.find((w) => !w.deck) || weeks.find((w) => !w.ppt) || weeks[0];
  if (!next) { toast("주차 정보를 읽지 못했습니다.", "err"); return; }
  state.week = next.week;
  toast(`${next.week}주차로 이동했습니다.`);
  navigate("/slides");
  window.dispatchEvent(new CustomEvent("ida:week-changed"));
}

/* ── 부팅 ─────────────────────────────────────────── */
hydrateIcons(document);

$("#btn-next-week").addEventListener("click", gotoNextWeek);
$("#rail-toggle").addEventListener("click", toggleRail);
$("#brand-mark").addEventListener("click", () => {
  if (document.body.dataset.rail === "collapsed") toggleRail();
});
// 좌하단 칩 = 워크스페이스 진입점. 강좌 전환 · 강의 정보 · 연결 설정이 거기 있다.
$("#side-user").addEventListener("click", () => navigate("/workspace"));
// 목록 섹션의 '전체 / 목록 열기' — 문맥에 따라 목적지가 바뀐다.
$("#side-section-more").addEventListener("click", (e) =>
  navigate(e.currentTarget.dataset.go || "/courses"));

// 레일의 단계 메뉴 — 강좌/주차를 먼저 고르게 패널로 보낸다.
$$("#side-nav a").forEach((a) => {
  a.addEventListener("click", (e) => {
    if (a.getAttribute("aria-disabled") === "true") { e.preventDefault(); return; }
    const nav = a.dataset.nav;
    if (nav === "syl") return;                    // 강좌 전체 — 바탕으로 직행
    if (!state.courseId) { e.preventDefault(); navigate("/courses"); return; }
    // 주차 단계는 "고르는 곳"을 먼저 띄운다 — 목록은 패널, 작업은 바탕.
    e.preventDefault();
    navigate(nav === "doc" ? "/textbook/pick"
             : nav === "vid" ? "/video/pick" : "/slides/pick");
  });
});

document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && (e.key === "b" || e.key === "B")) {
    e.preventDefault(); toggleRail();
  }
});

/* ── 렌더 진행 표시 ─────────────────────────────────
 * 영상 렌더는 1시간이 넘는다. 사람이 그 화면을 지키고 있을 수 없고, 다른 주차나
 * 교재를 만들다가 돌아온다. 그래서 **레일에 늘 붙여 둔다** — 돌고 있지 않으면 숨는다.
 *
 * 폴링 간격을 나눈다: 도는 중 5초, 없으면 25초. 진행 중일 때만 자주 물으면 되고,
 * 노는 동안 5초마다 15개 파일을 읽을 이유가 없다.
 */
const RENDER_POLL_RUN = 5000;
const RENDER_POLL_IDLE = 25000;
let renderTimer = null;

async function pollRenders() {
  const bar = $("#render-bar");
  if (!bar) return RENDER_POLL_IDLE;
  let jobs = [];
  try {
    const p = await getProject();
    if (p) jobs = (await api(`/api/video/running?project_id=${p.id}`)).jobs || [];
  } catch {
    // 서버가 잠깐 없을 수 있다(재시작). 표시만 숨기고 계속 물어본다.
    bar.hidden = true;
    return RENDER_POLL_IDLE;
  }
  if (!jobs.length) {
    bar.hidden = true;
    return RENDER_POLL_IDLE;
  }
  // 여러 주차가 동시에 돌 일은 없지만(단일 실행 락), 있으면 첫 번째를 보여 준다.
  const j = jobs[0];
  const wk = String(j.week).padStart(2, "0");
  const pct = Math.round((j.ratio || 0) * 100);
  bar.hidden = false;
  bar.classList.toggle("dead", !!j.died);
  bar.href = `#/video?week=${j.week}`;
  $("#render-bar-title").textContent = j.died
    ? `${wk}주차 렌더 중단됨` : `${wk}주차 영상 렌더`;
  $("#render-bar-pct").textContent = j.died ? "!" : `${pct}%`;
  $("#render-bar-fill").style.width = `${j.died ? 100 : pct}%`;
  $("#render-bar-msg").textContent =
    j.died ? "다시 눌러 이어서 만드세요" : (j.summary || j.message || j.stage || "");
  bar.title = j.died
    ? `${wk}주차 렌더가 끝나지 않고 멈췄습니다. 눌러서 확인하세요.`
    : `${wk}주차 · ${j.summary || j.stage} · ${j.message || ""}`;
  return j.died ? RENDER_POLL_IDLE : RENDER_POLL_RUN;
}

function scheduleRenderPoll(ms) {
  clearTimeout(renderTimer);
  renderTimer = setTimeout(async () => {
    scheduleRenderPoll(await pollRenders());
  }, ms);
}
// 탭이 숨으면 멈춘다 — 배경 탭에서 5초마다 두드릴 이유가 없다.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) clearTimeout(renderTimer);
  else scheduleRenderPoll(0);
});
scheduleRenderPoll(0);
window.addEventListener("ida:course-changed", () => scheduleRenderPoll(0));

window.addEventListener("hashchange", render);
window.addEventListener("ida:course-changed", () => { invalidate(); refreshRail(); });
window.addEventListener("ida:project-list-changed", () => refreshRail());
// 주차가 바뀌거나 빌드가 끝나면 레일의 주차 목록·진행 상태를 다시 그린다.
window.addEventListener("ida:week-changed", () => refreshRail());

/* ★ 첫 화면은 **바탕**이다. 부유 패널을 열어 두고 시작하지 않는다.
 *   패널은 "고르는 곳"이므로 사람이 열어야 뜬다. */
if (!location.hash) location.hash = "#/home";
render();   // render() 안에서 refreshRail() 을 부른다 — 여기서 또 부르면 겹친다

// 전역 오류를 조용히 삼키지 않는다(로컬 앱이라 콘솔을 잘 안 본다)
window.addEventListener("unhandledrejection", (e) => {
  console.error(e.reason);
  toast("처리 중 오류: " + (e.reason?.message || e.reason), "err");
});
