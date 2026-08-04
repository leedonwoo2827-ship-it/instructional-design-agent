/* 교재 — 주차 단위. 주차는 패널에서 고르고, 여기서 만든다. */
"use strict";

import { el, icon, api, toast } from "./util.js";
import { hydrateIcons } from "./icons.js";
import { state, getProject, getWeek, getWeeks, invalidate } from "./store.js";
import { refreshRail } from "./shell.js";
import { statusLine, docBody, dlBtn, editControls, runStream } from "./docview.js";
import { weekBar, guard } from "./weekbar.js";

export const meta = {
  title: "교재",
  subtitle: "선택한 주차의 학생용 읽기 자료. 강의계획서의 해당 주차 목표를 상속합니다.",
};

export async function mount(root, ctx) {
  const page = el("div", "page");
  root.appendChild(page);

  const p = await getProject();
  const stop = guard(page, ctx, p);
  if (stop) return;

  const status = statusLine();
  const doc = docBody("위 '교재 생성'을 누르면 여기에 실시간으로 나타납니다.");
  let busy = false;

  const bar = weekBar(ctx, { pickPath: "/textbook/pick" });
  const note = el("input");
  note.type = "text";
  note.className = "grow";
  note.placeholder = "이 차시 요청사항 (선택) — 예: 사례 중심으로, 표·그림 제안 포함";
  const genBtn = el("button", "btn primary");
  genBtn.type = "button";
  genBtn.append(icon("wand", 14), el("span", null, "교재 생성"));
  const dl = el("div", "btn-row");
  bar.node.append(note, genBtn);

  page.append(bar.node, dl, status.node, doc.node);

  const controls = editControls({
    getText: () => doc.text,
    onRefine: (req) => run({ kind: "refine", request: req }),
    onCheck: () => run({ kind: "check" }),
    onSave: async (text) => {
      await api(`/api/projects/${p.id}/weeks/${state.week}`,
                { method: "PUT", body: { doc_md: text } });
      await refresh();
      toast("편집 내용을 저장했습니다.", "ok");
    },
  });
  page.appendChild(controls.node);

  genBtn.addEventListener("click", () => {
    if (doc.text.trim() && !confirm(`${state.week}주차 교재를 새로 만들면 기존 내용이 대체됩니다. 계속할까요?`)) return;
    run({ kind: "gen", note: note.value.trim() });
  });

  function setBusy(on) {
    busy = on;
    genBtn.disabled = on;
    note.disabled = on;
    bar.setBusy(on);
    controls.setBusy(on);
  }

  function run(body) {
    if (busy) return;
    runStream("/api/gen/week",
      { project_id: p.id, week: state.week, format: "doc", ...body }, {
        status, body: doc, setBusy,
        onDone: async () => {
          invalidate();
          await getWeeks(true);
          await refreshRail();
          await refresh();
          toast("완료했습니다.", "ok");
        },
      });
  }

  async function refresh() {
    const w = await getWeek(state.week);
    doc.render(w?.doc_md || "");
    controls.node.hidden = !(w?.doc_md || "").trim();
    dl.innerHTML = "";
    if ((w?.doc_md || "").trim()) {
      dl.append(dlBtn("MD", `/api/dl/week/${p.id}/${state.week}/doc.md`),
                dlBtn("DOC 저장", `/api/dl/week/${p.id}/${state.week}/doc.doc`));
      const chars = w.doc_md.length;
      dl.appendChild(el("span", "chip",
        `${chars.toLocaleString()}자 · 약 ${(chars / 1800).toFixed(1)}쪽(A4)`));
    }
    genBtn.querySelector("span:last-child").textContent =
      (w?.doc_md || "").trim() ? "다시 생성" : "교재 생성";
    await bar.refresh();
    hydrateIcons(page);
  }

  const onWeek = () => refresh();
  window.addEventListener("ida:week-changed", onWeek);
  await refresh();
}
