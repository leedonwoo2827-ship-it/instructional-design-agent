/* 영상 — 슬라이드 뒤에 붙는 2단계.
 *
 *   1 나레이션 대본   슬라이드플랜 → 슬라이드당 약 2.5분(1,100자) 대본
 *   2 영상 렌더       슬라이드 PNG → 음성·자막 → mp4
 *
 * 왜 두 단계뿐인가: 대본은 이 서버가 만들고(LLM), 렌더는 **엔진 프로젝트의 별도
 * 프로세스**가 한다. onnxruntime·PowerPoint COM 을 이 서버에 끌어들이지 않고,
 * 서버를 닫아도 렌더가 이어지게 하려는 것이다. 100분 영상은 1시간이 넘는다.
 *
 * 대본 파일(06_영상/나레이션.json)이 유일한 진실이다. 여기서 고쳐도 되고
 * Claude Code 창에서 같은 파일을 고쳐도 된다 — 저장은 원자적이고 .bak 을 남긴다.
 *
 * 분량 기준: 영상 길이 = 학습 시간. 슬라이드 한 장에 2.5분, 실측 7.5자/초.
 * 프롬프트에만 맡기면 반드시 짧게 나온다(초기 버전이 목표의 1/8이었다) —
 * 그래서 목표 글자수를 주고, 생성 후 재서 부족분만 보강 재요청한다.
 */
"use strict";

import { el, icon, api, toast, fmtBytes, sse } from "./util.js";
import { hydrateIcons } from "./icons.js";
import { state, getProject, getWeek, getWeeks, invalidate } from "./store.js";
import { refreshRail } from "./shell.js";
import { statusLine } from "./docview.js";
import { weekBar, guard } from "./weekbar.js";

export const meta = {
  title: "영상",
  subtitle: "슬라이드플랜 → 나레이션 대본 → 음성·자막 → mp4. 대본 파일이 유일한 진실입니다.",
};

const STEPS = [
  {
    key: "script", no: 1, title: "나레이션 대본", iconName: "file",
    desc: "슬라이드플랜의 제목·핵심 메시지·불릿을 읽어 슬라이드당 약 2.5분(1,100자) " +
          "분량으로 씁니다. 목표에 못 미치는 슬라이드만 자동으로 보강합니다.",
    out: "06_영상/나레이션.json",
  },
  {
    key: "render", no: 2, title: "영상 렌더", iconName: "film",
    desc: "덱을 슬라이드 PNG 로 뽑고, 로컬 음성(Supertonic)으로 씬마다 읽어 자막을 맞춘 뒤 " +
          "ffmpeg 로 합칩니다. 창을 닫아도 계속됩니다.",
    out: "06_영상/슬라이드 · 번들 · 완성/영상_v1.mp4",
  },
];

const VOICES = ["F2", "F1", "F3", "F4", "F5", "M1", "M2", "M3", "M4", "M5"];
const POLL_MS = 2000;

export async function mount(root, ctx) {
  const page = el("div", "page");
  root.appendChild(page);

  const p = await getProject();
  if (guard(page, ctx, p)) return;

  const status = statusLine();
  let busy = false;
  let cur = null;
  let timer = null;

  // ── 작업 바: 주차 · 목표 분 · 보이스 ──
  const bar = weekBar(ctx, { pickPath: "/video/pick" });
  const mins = el("input");
  mins.type = "number";
  mins.min = "5"; mins.max = "300"; mins.step = "5";
  mins.style.width = "84px";
  mins.title = "목표 영상 길이(분). 슬라이드 장수 × 2.5분이 기준입니다.";
  const voice = el("select");
  VOICES.forEach((v) => voice.appendChild(el("option", null, v)));
  voice.title = "로컬 음성(Supertonic). F 는 여성, M 은 남성.";
  const mlb = el("label", "check");
  mlb.append(el("span", null, "목표"), mins, el("span", null, "분"));
  const vlb = el("label", "check");
  vlb.append(el("span", null, "보이스"), voice);
  // 자막 — 번인은 되돌릴 수 없으니 '끌 수 있는' 것을 기본으로 둔다.
  const subs = el("select");
  [["soft", "끌 수 있는 자막"], ["burn", "화면에 굽기(끌 수 없음)"], ["none", "자막 없음"]]
    .forEach(([v, t]) => {
      const o = el("option", null, t);
      o.value = v;
      subs.appendChild(o);
    });
  subs.title = "끌 수 있는 자막: mp4 안에 자막 트랙을 넣어 플레이어에서 켜고 끕니다."
             + "\n굽기: 어디서든 보이지만 한 번 구우면 되돌릴 수 없습니다.";
  const slb = el("label", "check");
  slb.append(el("span", null, "자막"), subs);
  bar.node.append(mlb, vlb, slb);
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
    box.append(head, el("div", "step-desc", s.desc));
    const extra = el("div", "step-extra");
    extra.hidden = true;
    box.appendChild(extra);
    stepWrap.appendChild(box);
    nodes[s.key] = { box, no, stateBadge, act, extra };
  });

  const vers = el("div", "card");          // 완성 영상 목록
  const editor = el("div", "card");        // 대본 검수
  page.append(vers, editor);

  /* ── 실행 ─────────────────────────────────────── */
  function setBusy(on) {
    busy = on;
    mins.disabled = on;
    voice.disabled = on;
    subs.disabled = on;
    bar.setBusy(on);
    STEPS.forEach((s) => nodes[s.key].act.querySelectorAll("button")
      .forEach((b) => { b.disabled = on || b.dataset.lock === "1"; }));
    editor.querySelectorAll("textarea,button").forEach((n) => { n.disabled = on; });
  }

  function runScript(regen) {
    if (busy) return;
    setBusy(true);
    status.show("슬라이드플랜 읽는 중…");
    let failed = false;
    sse("/api/video/script", {
      project_id: p.id, week: state.week,
      minutes: Number(mins.value) || undefined, regen: !!regen,
    }, {
      status: (d) => { status.show(d.message); status.progress(d.progress ?? null); },
      error: (d) => { failed = true; toast(d.message || "대본 생성 실패", "err"); },
      done: async (d) => {
        const warn = d.short ? ` · 분량 부족 ${d.short}장` : "";
        await after((d.message || "대본 완료") + warn);
      },
      close: () => { status.hide(); setBusy(false); if (failed) refresh(); },
    });
  }

  async function runRender() {
    if (busy) return;
    setBusy(true);
    try {
      await api("/api/video/render", {
        method: "POST",
        body: { project_id: p.id, week: state.week,
                voice: voice.value, speed: 1.02,
                burn_subs: subs.value === "burn",
                soft_subs: subs.value === "soft" },
      });
      toast("렌더를 시작했습니다. 창을 닫아도 계속됩니다.", "ok");
      await refresh();
    } catch (e) {
      toast(e.message, "err");
      setBusy(false);
    }
  }

  async function cancelRender() {
    try {
      await api("/api/video/cancel",
                { method: "POST", body: { project_id: p.id, week: state.week } });
      toast("렌더를 중단했습니다.", "ok");
    } catch (e) { toast(e.message, "err"); }
    await refresh();
  }

  async function after(msg) {
    status.hide();
    invalidate();
    await getWeeks(true);
    await refreshRail();
    await refresh();
    toast(msg, "ok");
  }

  /* ── 그리기 ───────────────────────────────────── */
  const btn = (label, { primary, iconName, onClick, lock, title, danger } = {}) => {
    const b = el("button", "btn sm" + (primary ? " primary" : "") + (danger ? " danger" : ""));
    b.type = "button";
    if (title) b.title = title;
    if (lock) { b.disabled = true; b.dataset.lock = "1"; }
    if (iconName) b.appendChild(icon(iconName, 13));
    b.appendChild(el("span", null, label));
    if (onClick) b.addEventListener("click", onClick);
    return b;
  };

  function markState(key, kind, text) {
    const n = nodes[key];
    n.box.classList.toggle("done", kind === "done");
    n.box.classList.toggle("locked", kind === "locked");
    n.stateBadge.className = "badge" + (kind === "done" ? " ok" : kind === "ready" ? " brand" : "");
    n.stateBadge.textContent = text;
  }

  function renderVersions(w) {
    vers.textContent = "";
    const t = el("div", "card-title");
    t.append(el("span", null, "완성 영상"),
             el("span", "badge" + (w.videos?.length ? " ok" : ""),
                w.videos?.length ? `v${w.videos.length}` : "없음"));
    vers.appendChild(t);
    if (!w.videos?.length) {
      vers.appendChild(el("div", "empty", "아직 없습니다. 대본을 만든 뒤 2단계를 실행하세요."));
      return;
    }
    const list = el("div", "co-list");
    w.videos.forEach((v) => {
      const row = el("div", "co-row");
      const nm = el("div", "co-name", v.name);
      const sub = el("div", "co-sub", fmtBytes(v.size));
      const left = el("div", "grow");
      left.append(nm, sub);
      const right = el("div", "co-right");
      const a = el("a", "btn sm");
      a.href = `/api/dl/video/${p.id}/${state.week}/${encodeURIComponent(v.name)}`;
      a.append(icon("download", 13), el("span", null, "받기"));
      right.appendChild(a);
      row.append(left, right);
      list.appendChild(row);
    });
    vers.appendChild(list);
  }

  /** 대본 검수 — 씬별 textarea. 목표 미달은 배지로 알린다.
   *  ★ 패널이 아니라 베이스 레이어에 둔다(패널은 Esc·스크림으로 닫혀 편집 중 내용을 잃는다). */
  async function renderEditor(w) {
    editor.textContent = "";
    if (!w.has_script) {
      editor.hidden = true;
      return;
    }
    editor.hidden = false;
    const t = el("div", "card-title");
    t.append(el("span", null, "대본 검수"),
             el("span", "badge",
                `${w.script_slides}씬 · ${w.script_chars.toLocaleString()}자 · 예상 ${w.script_est_min}분`));
    if (w.script_short) t.appendChild(el("span", "badge warn", `분량 부족 ${w.script_short}장`));
    editor.appendChild(t);

    const hint = el("div", "field-hint");
    hint.textContent = `이 파일이 유일한 진실입니다 — ${w.video_dir}\\나레이션.json  ·  ` +
      "Claude Code 창에서 같은 파일을 고쳐도 되고, 아래에서 고쳐도 됩니다.";
    editor.appendChild(hint);

    let doc;
    try {
      doc = (await api(`/api/video/script/${p.id}/${state.week}`)).script;
    } catch (e) {
      editor.appendChild(el("div", "empty", "대본을 읽지 못했습니다: " + e.message));
      return;
    }
    const rep = {};
    (doc.report || []).forEach((r) => { rep[r.index] = r; });
    const areas = {};
    (doc.slides || []).forEach((row) => {
      const m = rep[row.index] || {};
      const f = el("div", "field");
      const lb = el("label");
      lb.append(el("span", null, `씬 ${String(row.index).padStart(2, "0")} — ${row.title || ""}`));
      const cnt = el("span", "badge" +
        (m.ratio != null && m.ratio < 0.85 ? " warn" : ""));
      cnt.textContent = `${(row.narration || "").length.toLocaleString()}자` +
        (m.target ? ` / 목표 ${m.target.toLocaleString()}` : "");
      lb.appendChild(cnt);
      const ta = el("textarea");
      ta.value = row.narration || "";
      ta.rows = 6;
      ta.addEventListener("input", () => {
        cnt.textContent = `${ta.value.length.toLocaleString()}자` +
          (m.target ? ` / 목표 ${m.target.toLocaleString()}` : "");
        cnt.className = "badge" + (m.target && ta.value.length < m.target * 0.85 ? " warn" : "");
      });
      areas[row.index] = ta;
      f.append(lb, ta);
      editor.appendChild(f);
    });

    const row = el("div", "btn-row");
    row.appendChild(btn("편집 내용 저장", {
      primary: true, iconName: "check",
      onClick: async () => {
        const slides = {};
        Object.entries(areas).forEach(([i, ta]) => { slides[i] = ta.value; });
        try {
          const d = await api(`/api/video/script/${p.id}/${state.week}`,
                              { method: "PUT", body: { slides } });
          if (d.changed) await after(`${d.changed}씬 저장 — 다시 렌더하면 반영됩니다`);
          else toast("변경된 씬이 없습니다.", "ok");
        } catch (e) { toast(e.message, "err"); }
      },
    }));
    row.appendChild(btn("인쇄 (강사 확정용)", {
      iconName: "file",
      title: "새 창에 슬라이드+대본+시간대를 띄웁니다. 그 창에서 인쇄하면 PDF 로 받을 수 있습니다.",
      onClick: () => openPrint(w, areas),
    }));
    row.appendChild(btn("폴더 열기", {
      iconName: "folder",
      onClick: () => api("/api/open-folder", {
        method: "POST", body: { project_id: p.id, week: state.week, what: "video" },
      }).catch((e) => toast(e.message, "err")),
    }));
    editor.appendChild(row);
  }

  /** 인쇄용 화면을 새 창에 띄운다. 현재 화면은 그대로 둔다.
   *
   *  ★ 창은 클릭 즉시 연다 — await 뒤에 window.open 하면 브라우저가 팝업으로 막는다.
   *  ★ 화면에서 고친 내용은 아직 파일에 없다. 먼저 저장하지 않으면 저장본이 인쇄돼
   *    강사가 옛 대본을 확정하는 사고가 난다.
   *  ★ 슬라이드 PNG 가 없으면 render 단계만 먼저 돌린다(약 10초). 그래야 옆에 화면이 보인다.
   */
  async function openPrint(w, areas) {
    const win = window.open("", "_blank");
    if (!win) { toast("팝업이 차단되었습니다. 이 사이트의 팝업을 허용하세요.", "err"); return; }
    const say = (m) => {
      win.document.title = "대본 인쇄 준비";
      win.document.body.innerHTML =
        `<p style="font:14px/1.7 'Malgun Gothic',sans-serif;padding:32px;color:#333">${m}</p>`;
    };
    say("준비 중…");

    try {
      const slides = {};
      Object.entries(areas || {}).forEach(([i, ta]) => { slides[i] = ta.value; });
      if (Object.keys(slides).length) {
        const d = await api(`/api/video/script/${p.id}/${state.week}`,
                            { method: "PUT", body: { slides } });
        if (d.changed) toast(`${d.changed}씬 저장 후 인쇄합니다.`, "ok");
      }

      if (!w.slide_pngs && !w.render_running) {
        say("슬라이드 이미지를 먼저 뽑습니다 (PowerPoint, 약 10초)…");
        await api("/api/video/render", {
          method: "POST",
          body: { project_id: p.id, week: state.week, stages: ["render"] },
        });
        for (let i = 0; i < 90; i++) {                 // 최대 3분
          await new Promise((r) => setTimeout(r, 2000));
          const x = await getWeek(state.week);
          if (x.slide_pngs) { w = x; break; }
          if (!x.render_running) break;                // 실패했으면 슬라이드 없이 인쇄
        }
      }
      say("문서를 만들고 있습니다…");
      win.location = `/api/video/script/${p.id}/${state.week}/print`;
    } catch (e) {
      say("실패: " + e.message);
      toast(e.message, "err");
    }
  }

  async function refresh() {
    const w = await getWeek(state.week);
    cur = w;
    if (!mins.value) mins.value = String(w.video_minutes || 100);

    // 1 대본
    const a1 = nodes.script.act;
    a1.textContent = "";
    if (!w.has_plan) {
      markState("script", "locked", "슬라이드플랜 필요");
      a1.appendChild(btn("슬라이드로 이동", {
        iconName: "layers", onClick: () => ctx.navigate("/slides"),
      }));
      nodes.script.extra.hidden = false;
      nodes.script.extra.textContent =
        "먼저 슬라이드 단계의 '2 초안 PPT' 까지 끝내면 슬라이드플랜이 생깁니다.";
    } else {
      nodes.script.extra.hidden = !w.has_script;
      if (w.has_script) {
        nodes.script.extra.textContent =
          `${w.script_slides}씬 · ${w.script_chars.toLocaleString()}자 · ` +
          `예상 ${w.script_est_min}분 (목표 ${w.video_minutes}분)` +
          (w.script_updated ? ` · 수정 ${w.script_updated.replace("T", " ")}` : "");
      }
      markState("script", w.has_script ? "done" : "ready",
                w.has_script ? `${w.script_slides}씬` : "준비됨");
      a1.appendChild(btn(w.has_script ? "빈 씬만 채우기" : "대본 생성", {
        primary: !w.has_script, iconName: "wand", onClick: () => runScript(false),
        title: "이미 있는 씬은 그대로 두고 비어 있는 씬만 생성합니다.",
      }));
      if (w.has_script) {
        a1.appendChild(btn("전체 다시 쓰기", {
          iconName: "refresh", onClick: () => {
            if (confirm("현재 대본을 .bak 으로 남기고 전부 새로 씁니다. 계속할까요?")) runScript(true);
          },
        }));
      }
    }

    // 2 렌더
    const a2 = nodes.render.act;
    a2.textContent = "";
    const pr = w.render_progress;
    const running = !!w.render_running;
    if (!w.engine_ok) {
      markState("render", "locked", "엔진 준비 안 됨");
      nodes.render.extra.hidden = false;
      nodes.render.extra.textContent = "영상 엔진: " + w.engine_why;
    } else if (!w.decks?.length) {
      markState("render", "locked", "덱 필요");
      nodes.render.extra.hidden = false;
      nodes.render.extra.textContent = "완성된 .pptx 가 없습니다. 슬라이드 단계를 끝내세요.";
    } else if (!w.has_script) {
      markState("render", "locked", "대본 필요");
      nodes.render.extra.hidden = true;
    } else if (running) {
      markState("render", "ready", "진행 중");
      a2.appendChild(btn("중단", { danger: true, iconName: "x", onClick: cancelRender }));
      nodes.render.extra.hidden = false;
      nodes.render.extra.textContent =
        `${w.render_summary} · ${Math.round((w.render_ratio || 0) * 100)}% — 창을 닫아도 계속됩니다.`;
      status.show(w.render_summary);
      status.progress(w.render_ratio ?? null);
    } else {
      markState("render", w.videos?.length ? "done" : "ready",
                w.videos?.length ? `v${w.videos.length}` : "준비됨");
      a2.appendChild(btn(w.videos?.length ? "다시 렌더" : "영상 만들기", {
        primary: !w.videos?.length, iconName: "film", onClick: runRender,
      }));
      if (pr?.error) {
        nodes.render.extra.hidden = false;
        nodes.render.extra.textContent = "지난 실행 실패 — " + pr.error;
      } else if (w.render_died) {
        // 하드 킬(창 종료·재부팅)은 error 를 남기지 못한다 — 완료로 오인하면 안 된다
        nodes.render.extra.hidden = false;
        nodes.render.extra.textContent = w.render_summary + " 다시 렌더하세요.";
      } else {
        nodes.render.extra.hidden = !pr;
        if (pr) nodes.render.extra.textContent = "지난 실행: 완료";
      }
      status.hide();
    }

    renderVersions(w);
    await renderEditor(w);

    // 진행 중일 때만 폴링한다 — 끝나면 멈춘다.
    if (timer) { clearTimeout(timer); timer = null; }
    if (running) timer = setTimeout(refresh, POLL_MS);

    bar.refresh();
    if (busy && !running) setBusy(false);
    else if (running) setBusy(true);
    hydrateIcons(page);
  }

  window.addEventListener("ida:week-changed", () => refresh());
  await refresh();
}
