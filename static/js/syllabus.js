/* 강의계획서 — 강좌 전체 단계. 주차 개념이 없다. */
"use strict";

import { el, icon, api, toast } from "./util.js";
import { hydrateIcons } from "./icons.js";
import { getProject, invalidate } from "./store.js";
import { refreshRail } from "./shell.js";
import { statusLine, docBody, dlBtn, editControls, runStream } from "./docview.js";

const BLOOM = ["기억", "이해", "적용", "분석", "평가", "창조"];

export const meta = {
  title: "강의계획서",
  subtitle: "목표–평가–주차를 정렬합니다. 여기서 정해진 주차 목표를 교재·슬라이드가 상속합니다.",
};

export async function mount(root, ctx) {
  const page = el("div", "page");
  root.appendChild(page);

  const p = await getProject();
  if (!p) {
    page.appendChild(emptyCard(ctx, "먼저 강좌를 고르세요.",
      "좌하단 워크스페이스에서 강좌를 만들거나 고릅니다.", "/workspace", "워크스페이스 열기"));
    return;
  }
  const f = p.form || {};
  if (!f.title || !f.topics) {
    page.appendChild(emptyCard(ctx, "강의 기본 정보가 필요합니다.",
      "과목명과 주요 내용·주제를 입력하면 강의계획서를 만들 수 있습니다.",
      "/workspace", "강의 기본 정보 입력"));
    return;
  }

  const status = statusLine();
  const doc = docBody("아래 '강의계획서 생성'을 누르면 여기에 실시간으로 나타납니다.");
  let busy = false;

  // ── 작업 바 ──
  const bar = el("div", "work-bar");
  const info = el("div", "grow");
  info.appendChild(el("div", "co-name", f.title));
  info.appendChild(el("div", "co-sub",
    `${f.target || "대상 미입력"} · ${f.weeks || 15}주 · ${f.mode || "대면"} · 차시 ${f.hours || 2}시간`));
  bar.appendChild(info);

  const genBtn = el("button", "btn primary");
  genBtn.type = "button";
  genBtn.append(icon("wand", 14),
    el("span", null, p.syllabus_md ? "다시 생성" : "강의계획서 생성"));
  genBtn.addEventListener("click", () => {
    if (p.syllabus_md && !confirm("현재 강의계획서를 새로 만들면 기존 내용이 대체됩니다. 계속할까요?")) return;
    run({ kind: "gen", form: f });
  });
  bar.appendChild(genBtn);

  const dl = el("div", "btn-row");
  bar.appendChild(dl);
  page.append(bar, status.node, doc.node);

  const controls = editControls({
    getText: () => doc.text,
    onRefine: (req) => run({ kind: "refine", request: req }),
    onCheck: () => run({ kind: "check" }),
    onSave: async (text) => {
      await api(`/api/projects/${p.id}`, { method: "PATCH", body: { syllabus_md: text } });
      invalidate();
      await refresh();
      toast("편집 내용을 저장했습니다.", "ok");
    },
  });
  page.appendChild(controls.node);

  function setBusy(on) {
    busy = on;
    genBtn.disabled = on;
    controls.setBusy(on);
  }

  function run(body) {
    if (busy) return;
    runStream("/api/gen/syllabus", { project_id: p.id, ...body }, {
      status, body: doc, setBusy,
      onDone: async () => {
        invalidate();
        await refreshRail();
        await refresh();
        toast("완료했습니다.", "ok");
      },
    });
  }

  async function refresh() {
    const fresh = await getProject(true);
    doc.render(fresh?.syllabus_md || "");
    dl.innerHTML = "";
    controls.node.hidden = !fresh?.syllabus_md;
    if (fresh?.syllabus_md) {
      dl.append(dlBtn("MD", `/api/dl/syllabus/${p.id}.md`),
                dlBtn("DOC 저장", `/api/dl/syllabus/${p.id}.doc`));
      const n = el("span", "chip", `${fresh.syllabus_md.length.toLocaleString()}자`);
      dl.appendChild(n);
      renderBloom(page, fresh.bloom);
    }
    genBtn.querySelector("span:last-child").textContent =
      fresh?.syllabus_md ? "다시 생성" : "강의계획서 생성";
    hydrateIcons(page);
  }

  await refresh();
}

function renderBloom(page, counts) {
  let card = page.querySelector(".bloom-card");
  const total = BLOOM.reduce((a, b) => a + (counts?.[b] || 0), 0);
  if (!total) { card?.remove(); return; }
  if (!card) {
    card = el("div", "card bloom-card");
    const t = el("div", "card-title");
    t.append(el("span", null, "Bloom 인지수준 분포"),
             el("span", "muted", "저차 → 고차"));
    card.appendChild(t);
    card.appendChild(el("div", "bloom"));
    page.insertBefore(card, page.querySelector(".doc"));
  }
  const wrap = card.querySelector(".bloom");
  wrap.innerHTML = "";
  const max = Math.max(...BLOOM.map((b) => counts[b] || 0), 1);
  BLOOM.forEach((b, i) => {
    const col = el("div", "bloom-col");
    const bar = el("div", "bloom-bar");
    bar.style.height = Math.round(((counts[b] || 0) / max) * 34) + "px";
    // 순서값 → 단일 색조 계조. 범주형 색을 섞지 않는다.
    bar.style.background = ["#cfe4f7", "#a9cfef", "#7db4e4", "#4a9be0", "#2a7fcd", "#1668c1"][i];
    col.append(bar, el("div", "bloom-lb", `${b} ${counts[b] || 0}`));
    wrap.appendChild(col);
  });
}

function emptyCard(ctx, title, desc, path, label) {
  const c = el("div", "card");
  c.appendChild(el("div", "card-title", title));
  c.appendChild(el("div", "hc-desc", desc));
  const row = el("div", "btn-row");
  row.style.marginTop = "14px";
  const b = el("button", "btn primary");
  b.type = "button";
  b.append(icon("arrowRight", 14), el("span", null, label));
  b.addEventListener("click", () => ctx.navigate(path));
  row.appendChild(b);
  c.appendChild(row);
  return c;
}
