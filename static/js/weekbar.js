/* 주차 작업 바 — 바탕에 늘 보이는 "지금 몇 주차인가" + 주차 전환(패널 열기).
 *
 * 전환 버튼이 패널을 여는 것이 이 앱의 기본 어법이다: 목록은 위층, 작업은 아래층.
 */
"use strict";

import { el, icon } from "./util.js";
import { hydrateIcons } from "./icons.js";
import { state, getProject, getWeeks } from "./store.js";

export function weekBar(ctx, { pickPath }) {
  const bar = el("div", "work-bar");
  const btn = el("button", "wb-week");
  btn.type = "button";
  btn.title = "주차 목록을 패널로 엽니다. 작업 중인 화면은 그대로 유지됩니다.";
  const no = el("span", "wk-no tnum", "01");
  const t = el("span", "wk-t", "");
  btn.append(no, t, icon("layers", 14));
  btn.addEventListener("click", () => ctx.navigate(pickPath));
  bar.appendChild(btn);

  return {
    node: bar,
    setBusy(on) { btn.disabled = on; },   // 스트리밍 중 주차 전환 방지
    async refresh() {
      const p = await getProject();
      const nw = p?.weeks || 15;
      if (state.week > nw) state.week = nw;
      no.textContent = String(state.week).padStart(2, "0");
      const titles = p?.week_titles || {};
      t.textContent = titles[String(state.week)] || `${state.week}주차`;
      hydrateIcons(bar);
    },
  };
}

/** 강좌·강의계획서가 없으면 주차 화면은 열 수 없다. 안내 카드를 그리고 true 를 돌려준다. */
export function guard(page, ctx, project) {
  const card = (title, desc, path, label, ic = "arrowRight") => {
    const c = el("div", "card");
    c.appendChild(el("div", "card-title", title));
    c.appendChild(el("div", "hc-desc", desc));
    const row = el("div", "btn-row");
    row.style.marginTop = "14px";
    const b = el("button", "btn primary");
    b.type = "button";
    b.append(icon(ic, 14), el("span", null, label));
    b.addEventListener("click", () => ctx.navigate(path));
    row.appendChild(b);
    c.appendChild(row);
    page.appendChild(c);
    hydrateIcons(page);
    return true;
  };
  if (!project) {
    return card("먼저 강좌를 고르세요.",
      "좌하단 워크스페이스에서 강좌를 만들거나 고릅니다.", "/workspace", "워크스페이스 열기", "settings");
  }
  if (!project.syllabus_md) {
    return card("강의계획서가 먼저 필요합니다.",
      "교재와 슬라이드는 강의계획서의 주차 목표를 상속합니다.", "/syllabus", "강의계획서 만들기", "clipboard");
  }
  return false;
}
