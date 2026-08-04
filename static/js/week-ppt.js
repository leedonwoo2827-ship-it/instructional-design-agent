/* 슬라이드 — 주차 단위 5단계 파이프라인.
 *
 *   1 개요          강의계획서 → 슬라이드 목록·본문 (md)
 *   2 초안 PPT      개요 → 레이아웃 배정 → 사진 없는 .pptx
 *   3 사진원고 서칭/추가  주제 사진 수집·배치 → 재빌드   ★ 지금은 꺼 둠 (off: true)
 *   4 씬 프롬프트 생성      슬라이드별 이미지 생성 프롬프트 (JSON)
 *   5 이미지 합치기 및 최종 PPTX  만들어 온 이미지 + 자동 사진 → 최종본
 *
 * 한 버튼이 전부 하던 것을 쪼갠 이유: 아트디렉터 LLM·사진 검색·빌드·프롬프트가
 * 한 덩어리라 하나만 틀려도 전부 다시 돌려야 했다. 지금은 사진만 다시 받거나
 * 프롬프트만 다시 뽑을 수 있고, 무엇이 끝났는지 한눈에 보인다.
 *
 * 덱은 단계마다 v1·v2·v3… 로 쌓인다. 지우지 않는다 — 어디서 틀어졌는지
 * 되돌아볼 수 있어야 한다.
 */
"use strict";

import { el, icon, api, toast, fmtBytes, sse } from "./util.js";
import { hydrateIcons } from "./icons.js";
import { state, getProject, getWeek, getWeeks, getSettings, invalidate } from "./store.js";
import { refreshRail } from "./shell.js";
import { statusLine, docBody, dlBtn, editControls, runStream } from "./docview.js";
import { weekBar, guard } from "./weekbar.js";

export const meta = {
  title: "슬라이드",
  subtitle: "개요 → 초안 → 씬 프롬프트 → 합치기. 단계마다 파일로 남습니다.",
};

/* 단계 정의 — 제목은 짧게, out 은 '어느 폴더에 무엇이 남는가'.
   폴더 구조(core/workspace.py STEPS)와 문구가 어긋나면 사람이 파일을 못 찾는다. */
const STEPS = [
  {
    key: "outline", no: 1, title: "개요", iconName: "file",
    desc: "강의계획서의 해당 주차 목표를 상속해 슬라이드 목록과 본문 개요를 만듭니다.",
    out: "01_개요/슬라이드개요.md",
  },
  {
    key: "draft", no: 2, title: "초안 PPT", iconName: "layers",
    desc: "개요를 읽어 슬라이드마다 레이아웃 13종(사진·프로세스·카드·비교·표·퀴즈·" +
          "학습목표·목차·마무리·수치…)을 배정하고, 사진 없이 먼저 빌드합니다.",
    out: "02_초안/슬라이드플랜.json · 슬라이드_v1.pptx",
  },
  {
    // ★ 이 단계만 꺼 둔다. 되살리려면 off 를 false 로 (서버 라우트는 그대로 살아 있다).
    //   끄면 4단계가 '자동 사진이 없는' 상태로 보므로 배치 대상 전부의 프롬프트가 나온다 —
    //   사진을 찾지 않고 전부 직접 그리는 흐름이 이렇게 된다.
    key: "visual", no: 3, title: "사진원고 서칭/추가", iconName: "image", off: true,
    desc: "photo 로 배정된 슬라이드에 주제 사진을 수집해 배치하고 다시 빌드합니다. " +
          "받은 사진은 assets/ 에 보관해 재빌드 때 다시 받지 않습니다.",
    out: "03_비주얼/assets/ · 슬라이드_v1.pptx · 이미지출처.txt",
  },
  {
    key: "prompts", no: 4, title: "씬 프롬프트 생성", iconName: "clipboard",
    desc: "슬라이드(씬)별 이미지 생성 프롬프트를 영어로 뽑습니다. 밖에서 이미지를 " +
          "만들어 올 때 쓰는 입력입니다.",
    out: "04_씬프롬프트/이미지프롬프트.json",
  },
  {
    key: "merge", no: 5, title: "이미지 합치기 및 최종 PPTX", iconName: "wand",
    desc: "05_합치기/assets/ 에 슬라이드 번호로 넣어 둔 이미지를 삽입합니다. " +
          "자동 사진 위에 내 이미지가 덮이고, 자리 없는 레이아웃은 건너뜁니다.",
    out: "05_합치기/assets/ · 슬라이드_v1.pptx",
  },
];

export async function mount(root, ctx) {
  const page = el("div", "page");
  root.appendChild(page);

  const p = await getProject();
  if (guard(page, ctx, p)) return;
  const cfg = await getSettings();

  const status = statusLine();
  const doc = docBody("'1 개요' 를 실행하면 개요가 여기에 실시간으로 나타납니다.");
  let busy = false;
  let cur = null;

  // ── 작업 바: 주차 · 요청사항 · 폰트 임베드 ──
  const bar = weekBar(ctx, { pickPath: "/slides/pick" });
  const note = el("input");
  note.type = "text";
  note.className = "grow";
  note.placeholder = "이 차시 요청사항 (선택) — 예: 오리엔테이션 최소화, 개념마다 예시";
  const embed = el("input");
  embed.type = "checkbox";
  embed.checked = true;
  embed.disabled = !cfg.font.embedded;
  const embedLb = el("label", "check");
  embedLb.title = cfg.font.embedded
    ? "폰트가 없는 PC 에서도 레이아웃이 그대로 열립니다(파일 약 3MB 증가)."
    : "assets/fonts 에 Pretendard 가 없어 시스템 폰트로 만듭니다.";
  embedLb.append(embed, el("span", null, "폰트 임베드"));
  bar.node.append(note, embedLb);
  page.append(bar.node, status.node);

  // ── 단계 카드 ──
  const stepWrap = el("div", "steps");
  page.appendChild(stepWrap);
  const nodes = {};
  STEPS.forEach((s) => {
    const box = el("div", "step");
    const head = el("div", "step-head");
    const no = el("span", "step-no", String(s.no));
    const tt = el("div", "step-tt");
    tt.append(el("div", "step-title", s.title), el("div", "step-out", s.out));
    const stateBadge = el("span", "badge");
    const act = el("div", "btn-row step-act");
    head.append(no, tt, stateBadge, act);
    box.appendChild(head);
    box.appendChild(el("div", "step-desc", s.desc));
    const extra = el("div", "step-extra");
    extra.hidden = true;
    box.appendChild(extra);
    stepWrap.appendChild(box);
    nodes[s.key] = { box, no, stateBadge, act, extra };
  });

  const dl = el("div", "btn-row");
  dl.style.margin = "18px 0 0";
  const vers = el("div", "card");
  page.append(vers, dl, doc.node);

  const controls = editControls({
    getText: () => doc.text,
    onRefine: (req) => runGen({ kind: "refine", request: req }),
    onCheck: () => runGen({ kind: "check" }),
    onSave: async (text) => {
      await api(`/api/projects/${p.id}/weeks/${state.week}`,
                { method: "PUT", body: { ppt_md: text } });
      await refresh();
      toast("편집 내용을 저장했습니다.", "ok");
    },
  });
  page.appendChild(controls.node);

  /* ── 실행 ─────────────────────────────────────── */
  function setBusy(on) {
    busy = on;
    note.disabled = on;
    bar.setBusy(on);
    controls.setBusy(on);
    STEPS.forEach((s) => nodes[s.key].act.querySelectorAll("button")
      .forEach((b) => { b.disabled = on || b.dataset.lock === "1"; }));
  }

  function runGen(body) {
    if (busy) return;
    runStream("/api/gen/week",
      { project_id: p.id, week: state.week, format: "ppt", ...body }, {
        status, body: doc, setBusy,
        onDone: async () => { await after("개요 완료"); },
      });
  }

  /** 2~4단계 공통 — SSE 로 상태만 흘리고 결과는 파일로 남는다. */
  function runStep(path, extraBody, label, describe) {
    if (busy) return;
    setBusy(true);
    status.show("시작하는 중…");
    let failed = false;
    sse(path, { project_id: p.id, week: state.week,
                embed_font: embed.checked, ...extraBody }, {
      status: (d) => { status.show(d.message); status.progress(d.progress ?? null); },
      error: (d) => { failed = true; toast(d.message || `${label} 실패`, "err"); },
      done: async (d) => { await after(describe ? describe(d) : `${label} 완료`); },
      close: () => { status.hide(); setBusy(false); if (failed) refresh(); },
    });
  }

  async function after(msg) {
    status.hide();
    invalidate();
    await getWeeks(true);
    await refreshRail();
    await refresh();
    toast(msg, "ok");
  }

  async function runMerge() {
    if (busy) return;
    setBusy(true);
    status.show("이미지 폴더 확인 → 재빌드 중…");
    try {
      const d = await api("/api/slides/merge",
        { method: "POST", body: { project_id: p.id, week: state.week,
                                  embed_font: embed.checked } });
      if (!d.ok) {
        toast("합칠 이미지를 찾지 못했습니다.", "err");
        showMergeReport(d);
        return;
      }
      showMergeReport(d);
      await after(`${d.placed}장 배치 (내 이미지 ${d.mine} · 자동 사진 ${d.auto})`);
    } catch (e) { toast(e.message, "err"); }
    finally { status.hide(); setBusy(false); }
  }

  function showMergeReport(d) {
    const ex = nodes.merge.extra;
    const lines = [d.report];
    if (d.skipped?.length) {
      lines.push("자리 없어 건너뜀(도형 레이아웃): " +
        d.skipped.slice(0, 10).map((n) => n + "번").join(", "));
    }
    ex.textContent = lines.join("  ·  ");
    ex.hidden = false;
  }

  /* ── 그리기 ───────────────────────────────────── */
  const btn = (label, { primary, iconName, onClick, lock, title } = {}) => {
    const b = el("button", "btn sm" + (primary ? " primary" : ""));
    b.type = "button";
    if (title) b.title = title;
    if (lock) { b.disabled = true; b.dataset.lock = "1"; }
    if (iconName) b.appendChild(icon(iconName, 13));
    b.appendChild(el("span", null, label));
    if (onClick) b.addEventListener("click", onClick);
    return b;
  };

  function markState(key, kind, text) {
    const { box, stateBadge } = nodes[key];
    box.classList.toggle("done", kind === "done");
    box.classList.toggle("locked", kind === "locked");
    stateBadge.className = "badge" + (kind === "done" ? " ok" : kind === "ready" ? " brand" : "");
    stateBadge.textContent = text;
  }

  async function refresh() {
    cur = await getWeek(state.week);
    const hasOutline = !!(cur?.ppt_md || "").trim();
    const hasPlan = !!cur?.has_plan;
    const nPhoto = cur?.photos || 0;
    const slots = cur?.photo_slots || 0;
    const hasPrompt = !!(cur?.img_prompt || "").trim();
    const nMine = cur?.images_matched || 0;

    doc.render(cur?.ppt_md || "");
    controls.node.hidden = !hasOutline;

    // 1 개요
    markState("outline", hasOutline ? "done" : "ready",
              hasOutline ? `${cur.n_slides}장` : "시작하세요");
    nodes.outline.act.innerHTML = "";
    nodes.outline.act.appendChild(btn(hasOutline ? "다시 만들기" : "개요 만들기", {
      primary: !hasOutline, iconName: "wand",
      onClick: () => {
        if (hasOutline && !confirm(`${state.week}주차 개요를 새로 만들면 기존 내용이 대체됩니다. 계속할까요?`)) return;
        runGen({ kind: "gen", note: note.value.trim() });
      },
    }));
    nodes.outline.extra.hidden = !hasOutline;
    if (hasOutline) {
      nodes.outline.extra.textContent =
        `슬라이드 ${cur.n_slides}장 · 목표 약 ${cur.target_slides}장 · ${cur.ppt_md.length.toLocaleString()}자`;
    }

    // 2 초안 PPT
    markState("draft", !hasOutline ? "locked" : hasPlan ? "done" : "ready",
              !hasOutline ? "개요 먼저" : hasPlan ? `${cur.plan_slides}장 배정` : "대기");
    nodes.draft.act.innerHTML = "";
    nodes.draft.act.appendChild(btn(hasPlan ? "다시 설계" : "초안 만들기", {
      primary: hasOutline && !hasPlan, iconName: "layers", lock: !hasOutline,
      onClick: () => runStep("/api/slides/draft", {}, "초안",
        (d) => `초안 ${d.slides}장 · 사진 자리 ${d.photo_slots}곳 · ${d.file}`),
    }));
    nodes.draft.extra.hidden = !hasPlan;
    if (hasPlan) {
      const t = cur.plan_types || {};
      const lv = cur.plan_levels || {};
      const lines = ["레이아웃 — " + Object.entries(t).sort((a, b) => b[1] - a[1])
        .map(([k, v]) => `${k} ${v}`).join(" · ")];
      // 인지수준 분포는 목표–자료 정렬 점검용이다. 슬라이드에는 인쇄되지 않는다.
      if (Object.keys(lv).length) {
        lines.push("인지수준 — " + Object.entries(lv).map(([k, v]) => `${k} ${v}`).join(" · "));
      }
      nodes.draft.extra.textContent = lines.join("      ");
    }

    // 3 사진원고 서칭/추가 — 꺼 두었다(STEPS 의 off). 자리는 2단계에서 이미 잡혀 있고,
    //   사진은 찾지 않고 4단계에서 전부 직접 그린다.
    const visualOff = !!STEPS.find((s) => s.key === "visual").off;
    nodes.visual.act.innerHTML = "";
    if (visualOff) {
      markState("visual", "locked", "사용 안 함");
      nodes.visual.act.appendChild(btn("사진 수집·배치", {
        iconName: "image", lock: true,
        title: "지금은 쓰지 않습니다. 사진 자리는 2단계에서 잡히고, 그림은 4·5단계에서 넣습니다.",
      }));
      nodes.visual.extra.hidden = false;
      nodes.visual.extra.textContent = nPhoto
        ? `꺼 둔 단계입니다. 예전에 받아 둔 사진 ${nPhoto}장은 ${cur.photos_dir} 에 그대로 있습니다.`
        : "꺼 둔 단계입니다. 사진을 자동으로 찾지 않고, 배치 대상 전부를 4단계 프롬프트로 뽑아 직접 그립니다.";
    } else {
      markState("visual", !hasPlan ? "locked" : nPhoto ? "done" : "ready",
                !hasPlan ? "초안 먼저" : nPhoto ? `사진 ${nPhoto}/${slots}` : `자리 ${slots}곳`);
      nodes.visual.act.appendChild(btn(nPhoto ? "다시 빌드" : "사진 수집·배치", {
        primary: hasPlan && !nPhoto, iconName: "image", lock: !hasPlan,
        title: "보관된 사진을 재사용합니다.",
        onClick: () => runStep("/api/slides/visual", {}, "사진원고 서칭/추가",
          (d) => `사진 ${d.photos}/${d.slots}장 배치 (재사용 ${d.reused}) · ${d.file}`),
      }));
      if (nPhoto) {
        nodes.visual.act.appendChild(btn("사진 새로 받기", {
          iconName: "image", lock: !hasPlan,
          title: "보관된 사진을 버리고 다시 검색합니다.",
          onClick: () => {
            if (!confirm("보관된 사진을 버리고 다시 검색합니다. 계속할까요?")) return;
            runStep("/api/slides/visual", { refetch: true }, "사진원고 서칭/추가",
              (d) => `사진 ${d.photos}/${d.slots}장 새로 받음 · ${d.file}`);
          },
        }));
      }
      nodes.visual.extra.hidden = !nPhoto;
      if (nPhoto) nodes.visual.extra.textContent = `보관 위치: ${cur.photos_dir}`;
    }

    // 4 씬 프롬프트 생성
    markState("prompts", !hasPlan ? "locked" : hasPrompt ? "done" : "ready",
              !hasPlan ? "초안 먼저" : hasPrompt ? "추출됨" : "대기");
    nodes.prompts.act.innerHTML = "";
    nodes.prompts.act.appendChild(btn(hasPrompt ? "다시 추출" : "프롬프트 추출", {
      primary: hasPlan && !hasPrompt, iconName: "clipboard", lock: !hasPlan,
      // 3단계를 껐으면 사진 자리 전부를 뽑는다. 예전에 받아 둔 사진 때문에 자리를
      // 빼 버리면 사진도 그림도 없는 슬라이드가 조용히 남는다.
      onClick: () => runStep("/api/slides/prompts", { all_slots: visualOff }, "씬 프롬프트",
        (d) => `프롬프트 ${d.count}개 (배치 대상 ${d.placeable}개)`),
    }));
    if (hasPrompt) {
      nodes.prompts.act.appendChild(
        dlBtn("JSON 내려받기", `/api/dl/week/${p.id}/${state.week}/imgprompt.json`, "download"));
    }
    nodes.prompts.extra.hidden = !hasPrompt;
    if (hasPrompt) {
      let n = 0, placed = 0;
      try {
        const b = JSON.parse(cur.img_prompt);
        n = b.count || 0;
        placed = (b.prompts || []).filter((x) => x.place).length;
      } catch { /* 손으로 고쳐 깨졌을 수 있다 — 숫자 없이 넘어간다 */ }
      nodes.prompts.extra.textContent = n
        ? `${n}개 · 이미지를 실제로 올릴 슬라이드 ${placed}개 (place=true)`
        : "JSON 을 읽을 수 없습니다.";
    }

    // 5 이미지 합치기 및 최종 PPTX
    markState("merge", !hasPlan ? "locked" : nMine ? "ready" : "locked",
              !hasPlan ? "초안 먼저" : nMine ? `내 이미지 ${nMine}장` : "폴더 비어 있음");
    nodes.merge.act.innerHTML = "";
    nodes.merge.act.appendChild(btn("이미지 폴더 열기", {
      iconName: "folder", lock: !hasPlan,
      onClick: async () => {
        try {
          const d = await api("/api/open-folder",
            { method: "POST", body: { project_id: p.id, week: state.week } });
          if (!d.ok) toast(d.message || "탐색기를 열지 못했습니다. 아래 경로를 직접 여세요.", "err");
        } catch (e) { toast(e.message, "err"); }
      },
    }));
    nodes.merge.act.appendChild(btn(`합쳐서 재빌드${nMine ? ` (${nMine})` : ""}`, {
      primary: !!nMine, iconName: "wand", lock: !hasPlan || !nMine,
      onClick: runMerge,
    }));
    if (hasPlan) {
      const ex = nodes.merge.extra;
      if (ex.hidden || !ex.textContent) {
        ex.textContent = `${cur.images_dir}  —  003.png → 3번 슬라이드 ` +
          `(확장자·뒤 설명 자유: 003_뇌구조.png)`;
        ex.hidden = false;
      }
    }

    // 개요 다운로드
    dl.innerHTML = "";
    if (hasOutline) {
      dl.append(dlBtn("개요 MD", `/api/dl/week/${p.id}/${state.week}/ppt.md`),
                dlBtn("개요 DOC", `/api/dl/week/${p.id}/${state.week}/ppt.doc`),
                dlBtn("개요 PPTX", `/api/dl/week/${p.id}/${state.week}/ppt.pptx`, "slide"));
    }

    // 덱 버전 기록
    vers.innerHTML = "";
    if (cur?.decks?.length) {
      const t = el("div", "card-title");
      t.append(el("span", null, "이 주차 슬라이드 기록"),
               el("span", "muted",
                  `${cur.decks.length}개 · 단계별로 버전이 따로 쌓이고 지워지지 않습니다`));
      vers.appendChild(t);
      const list = el("div", "co-list");
      cur.decks.forEach((f, i) => {
        const row = el("div", "co-row");
        const left = el("div");
        left.style.minWidth = "0";
        left.appendChild(el("div", "co-name", f.name));
        left.appendChild(el("div", "co-sub",
          `${f.step} · ${fmtBytes(f.size)}` + (i === 0 ? " · 최신" : "")));
        row.appendChild(left);
        const right = el("div", "co-right");
        // 어느 단계 산출물인지가 파일명만으로는 안 보인다(단계마다 v1 부터 시작).
        right.appendChild(el("span", "badge" + (i === 0 ? " brand" : ""), f.step));
        right.appendChild(dlBtn("내려받기",
          `/api/dl/deck/${p.id}/${state.week}/${encodeURIComponent(f.name)}`));
        row.appendChild(right);
        list.appendChild(row);
      });
      vers.appendChild(list);
      const cr = el("div", "btn-row");
      cr.style.marginTop = "12px";
      cr.appendChild(dlBtn("이미지 출처 (.txt)", `/api/dl/credits/${p.id}/${state.week}`, "file"));
      const wb = el("button", "btn sm");
      wb.type = "button";
      wb.append(icon("folder", 13), el("span", null, "주차 폴더 열기"));
      wb.addEventListener("click", async () => {
        try {
          await api("/api/open-folder",
            { method: "POST", body: { project_id: p.id, week: state.week, what: "week" } });
        } catch (e) { toast(e.message, "err"); }
      });
      cr.appendChild(wb);
      vers.appendChild(cr);
      vers.hidden = false;
    } else {
      vers.hidden = true;
    }

    await bar.refresh();
    if (busy) setBusy(true);      // 잠금 상태 유지
    hydrateIcons(page);
  }

  window.addEventListener("ida:week-changed", () => refresh());
  await refresh();
}
