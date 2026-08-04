/* 홈 — "무엇을 할까요" 카드. 각 카드는 고르는 곳(패널)이나 작업면(바탕)으로 보낸다. */
"use strict";

import { el, icon } from "../js/util.js";
import { state, getProject, getWeeks } from "./store.js";

export const meta = {
  title: "무엇을 할까요",
  subtitle: "강의계획서 → 교재 → 슬라이드 순으로 만듭니다. 주차 단계는 목록 창에서 고릅니다.",
};

function card({ title, desc, hint, iconName, tone, disabled, onClick, metaNodes }) {
  const b = el("button", "hero-card");
  b.type = "button";
  b.disabled = !!disabled;
  const head = el("div", "hc-head");
  const box = el("span", "icon-box" + (tone ? " " + tone : ""));
  box.appendChild(icon(iconName, 17));
  head.append(box, el("span", "hc-title", title));
  b.appendChild(head);
  b.appendChild(el("div", "hc-desc", desc));
  if (metaNodes?.length) {
    const m = el("div", "hc-meta");
    metaNodes.forEach((n) => m.appendChild(n));
    b.appendChild(m);
  }
  b.appendChild(el("span", "hc-hint", hint));
  if (onClick) b.addEventListener("click", onClick);
  return b;
}

const badge = (text, cls) => el("span", "badge" + (cls ? " " + cls : ""), text);

export async function mount(root, ctx) {
  const page = el("div", "page");
  root.appendChild(page);

  const p = await getProject();
  const hasSyl = !!(p && p.syllabus_md);
  let weeks = { weeks: [], n_weeks: 15 };
  if (hasSyl) weeks = await getWeeks(true);
  const done = (k) => weeks.weeks.filter((w) => (k === "deck" ? w.deck : w[k])).length;

  if (!p) {
    const c = el("div", "card");
    c.appendChild(el("div", "card-title", "먼저 강좌를 만들거나 고르세요"));
    c.appendChild(el("div", "hc-desc",
      "좌하단 워크스페이스에서 강좌를 만들고, 강의 기본 정보와 LiteLLM 연결 설정을 입력합니다."));
    const row = el("div", "btn-row");
    row.style.marginTop = "14px";
    const b = el("button", "btn primary");
    b.type = "button";
    b.append(icon("plus", 14), el("span", null, "워크스페이스 열기"));
    b.addEventListener("click", () => ctx.navigate("/workspace"));
    row.appendChild(b);
    c.appendChild(row);
    page.appendChild(c);
    return;
  }

  const grid = el("div", "grid grid-2");
  page.appendChild(grid);

  grid.appendChild(card({
    title: "강의계획서",
    desc: "목표–평가–주차를 정렬한 강좌 전체 계획서를 만들고 정렬 점검합니다.",
    hint: hasSyl ? "바탕에서 바로 열립니다" : "여기서 시작하세요",
    iconName: "clipboard",
    tone: hasSyl ? "ok" : "",
    metaNodes: [hasSyl ? badge("작성됨", "ok") : badge("미작성")],
    onClick: () => ctx.navigate("/syllabus"),
  }));

  grid.appendChild(card({
    title: "교재",
    desc: "선택한 주차의 학생용 읽기 자료를 만듭니다. 주차마다 파일로 남습니다.",
    hint: "위에 목록 창이 뜹니다",
    iconName: "book",
    tone: hasSyl ? (done("doc") ? "ok" : "") : "idle",
    disabled: !hasSyl,
    metaNodes: hasSyl ? [badge(`${done("doc")} / ${weeks.n_weeks}주차`, done("doc") ? "brand" : "")] : [],
    onClick: () => ctx.navigate("/textbook/pick"),
  }));

  grid.appendChild(card({
    title: "슬라이드",
    desc: "개요를 만든 뒤 이미지·레이아웃을 정리해 디자인된 .pptx 로 빌드합니다.",
    hint: "위에 목록 창이 뜹니다",
    iconName: "slide",
    tone: hasSyl ? (done("deck") ? "ok" : "") : "idle",
    disabled: !hasSyl,
    metaNodes: hasSyl
      ? [badge(`개요 ${done("ppt")} / ${weeks.n_weeks}`), badge(`덱 ${done("deck")} / ${weeks.n_weeks}`,
          done("deck") ? "brand" : "")]
      : [],
    onClick: () => ctx.navigate("/slides/pick"),
  }));

  grid.appendChild(card({
    title: "워크스페이스",
    desc: "강좌 전환 · 강의 기본 정보 · LiteLLM 연결 설정 · 회사 PPT 양식.",
    hint: "좌하단 칩에서도 열립니다",
    iconName: "settings",
    onClick: () => ctx.navigate("/workspace"),
  }));

  if (hasSyl) {
    const c = el("div", "card");
    c.style.marginTop = "18px";
    const t = el("div", "card-title");
    t.append(el("span", null, "진행 상황"),
             el("span", "muted", `${p.name} · ${weeks.n_weeks}주차`));
    c.appendChild(t);
    const wrap = el("div", "wk-grid");
    weeks.weeks.forEach((w) => wrap.appendChild(weekCell(w, ctx)));
    c.appendChild(wrap);
    c.appendChild(legend());
    page.appendChild(c);
  }
}

/** 주차 칸 — 홈에서도 같은 모양을 쓴다(패널과 동일한 어법). */
export function weekCell(w, ctx, { onPick } = {}) {
  const b = el("button", "wk-cell" + (w.week === state.week ? " on" : ""));
  b.type = "button";
  b.title = `${w.week}주차 ${w.title || ""}`;
  b.appendChild(el("span", "wk-no tnum", String(w.week).padStart(2, "0")));
  b.appendChild(el("span", "wk-t", w.title || ""));
  const dots = el("div", "dots");
  const mk = (cls) => dots.appendChild(el("span", "dot" + (cls ? " " + cls : "")));
  mk(w.doc ? "on" : "");
  mk(w.ppt ? "on" : "");
  mk(w.deck ? "on" : w.plan ? "part" : "");
  mk(w.images ? "on" : "");
  if (w.deck) dots.appendChild(el("span", "badge brand", "v" + w.deck));
  b.appendChild(dots);
  b.addEventListener("click", () => {
    state.week = w.week;
    if (onPick) onPick(w);
    else { ctx.navigate("/slides"); window.dispatchEvent(new CustomEvent("ida:week-changed")); }
  });
  return b;
}

export function legend() {
  const l = el("div", "legend");
  const one = (cls, text) => {
    const s = el("span");
    s.append(el("i", "dot" + (cls ? " " + cls : "")), document.createTextNode(" " + text));
    return s;
  };
  l.append(one("on", "완료"), one("part", "진행"), one("", "미완"),
           el("span", null, "순서: 교재 · 개요 · 덱 · 이미지"));
  return l;
}
