/* 고르는 패널 — 안쪽 레일 = 강좌 목록, 본문 = 그 강좌의 주차 목록.
 *
 * 경로에 따라 고르고 난 뒤 갈 바탕이 달라진다:
 *   /courses        → 강좌만 고른다(주차 목록은 참고용)  → 강의계획서
 *   /textbook/pick  → 강좌 → 주차                       → 교재
 *   /slides/pick    → 강좌 → 주차                       → 슬라이드
 *
 * 칸을 고르면 패널이 닫히고 **바탕이 그 주차로 바뀐다.** 패널은 고르는 곳,
 * 바탕은 하는 곳이다.
 */
"use strict";

import { $, el, icon, api, toast } from "./util.js";
import { hydrateIcons } from "./icons.js";
import { actionBtn, closeActionBtn } from "./panel.js";
import { state, getProjects, getProject, getWeeks, invalidate } from "./store.js";
import { pickCourse } from "./shell.js";
import { weekCell, legend } from "./home.js";

const TARGET = {
  "/courses": { base: "/syllabus", title: "강좌", sub: "강좌를 고르면 바탕이 그 강좌로 바뀝니다." },
  "/textbook/pick": { base: "/textbook", title: "교재 — 주차 고르기", sub: "칸을 고르면 패널이 닫히고 바탕이 그 주차로 바뀝니다." },
  "/video/pick": {
    base: "/video", title: "영상 — 주차 고르기",
    sub: "대본을 쓰고 영상을 렌더할 주차를 고릅니다.",
  },
  "/slides/pick": { base: "/slides", title: "슬라이드 — 주차 고르기", sub: "칸을 고르면 패널이 닫히고 바탕이 그 주차로 바뀝니다." },
};

export const meta = {
  title: (ctx) => (TARGET[ctx.path] || TARGET["/courses"]).title,
  subtitle: (ctx) => (TARGET[ctx.path] || TARGET["/courses"]).sub,
  actions: (ctx) => [
    actionBtn("워크스페이스", { iconName: "settings", onClick: () => ctx.navigate("/workspace") }),
    closeActionBtn(),
  ],
};

export async function mount(root, ctx) {
  const target = TARGET[ctx.path] || TARGET["/courses"];
  const weeksMode = ctx.path !== "/courses";
  await renderRail(ctx, target);
  await renderBody(root, ctx, target, weeksMode);
}

/* ── 안쪽 레일: 강좌 목록 ─────────────────────────── */
async function renderRail(ctx, target) {
  const rail = ctx.panel.rail;
  rail.innerHTML = "";
  rail.appendChild(el("div", "panel-rail-title", "강좌"));

  const list = await getProjects(true);
  if (!list.length) {
    rail.appendChild(el("div", "side-empty", "강좌가 없습니다."));
  }
  list.forEach((c) => {
    const b = el("button", c.id === state.courseId ? "active" : "");
    b.type = "button";
    b.title = c.name;
    b.append(icon("folder", 15), el("span", "pr-label", c.name),
             el("span", "pr-count tnum", `${c.done_deck}/${c.weeks}`));
    b.addEventListener("click", async () => {
      if (c.id === state.courseId) return;
      await pickCourse(c.id);
      // 강좌를 바꾸면 주차 목록도 통째로 바뀐다 — 패널 내용만 다시 그린다.
      ctx.navigate(ctx.path);
    });
    rail.appendChild(b);
  });

  const foot = el("div", "panel-rail-foot");
  const nb = el("button");
  nb.type = "button";
  nb.append(icon("plus", 15), el("span", "pr-label", "새 강좌"));
  nb.addEventListener("click", () => ctx.navigate("/workspace"));
  foot.appendChild(nb);
  rail.appendChild(foot);
  hydrateIcons(rail);
}

/* ── 본문: 주차 목록(또는 강좌 안내) ─────────────── */
async function renderBody(root, ctx, target, weeksMode) {
  root.innerHTML = "";
  const p = await getProject();

  if (!p) {
    root.appendChild(el("div", "empty", "왼쪽에서 강좌를 고르거나 '새 강좌'를 만드세요."));
    return;
  }
  if (!p.syllabus_md) {
    const box = el("div", "empty");
    box.appendChild(el("div", null, "이 강좌에는 강의계획서가 없습니다."));
    const row = el("div", "btn-row");
    row.style.justifyContent = "center";
    row.style.marginTop = "14px";
    const b = el("button", "btn primary");
    b.type = "button";
    b.append(icon("clipboard", 14), el("span", null, "강의계획서 만들기"));
    b.addEventListener("click", () => ctx.navigate("/syllabus"));
    row.appendChild(b);
    box.appendChild(row);
    root.appendChild(box);
    hydrateIcons(root);
    return;
  }

  if (!weeksMode) {
    // 강좌만 고르는 모드 — 고른 강좌를 확인하고 바탕으로 보낸다
    const c = el("div", "card");
    c.appendChild(el("div", "card-title", p.name));
    const w = await getWeeks(true);
    c.appendChild(el("div", "hc-desc",
      `${w.n_weeks}주차 · 교재 ${w.weeks.filter((x) => x.doc).length} · ` +
      `개요 ${w.weeks.filter((x) => x.ppt).length} · 덱 ${w.weeks.filter((x) => x.deck).length}`));
    const row = el("div", "btn-row");
    row.style.marginTop = "14px";
    [["강의계획서", "/syllabus", "clipboard"], ["교재", "/textbook/pick", "book"],
     ["슬라이드", "/slides/pick", "slide"]].forEach(([t, path, ic], i) => {
      const b = el("button", "btn" + (i === 0 ? " primary" : ""));
      b.type = "button";
      b.append(icon(ic, 14), el("span", null, t));
      b.addEventListener("click", () => ctx.navigate(path));
      row.appendChild(b);
    });
    c.appendChild(row);
    root.appendChild(c);
    hydrateIcons(root);
    return;
  }

  const { weeks } = await getWeeks(true);
  const grid = el("div", "wk-grid");
  weeks.forEach((w) => grid.appendChild(weekCell(w, ctx, {
    onPick: () => {
      ctx.navigate(target.base);
      window.dispatchEvent(new CustomEvent("ida:week-changed"));
    },
  })));
  root.appendChild(grid);
  root.appendChild(legend());
  hydrateIcons(root);
}
