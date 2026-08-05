/* 워크스페이스 패널 — 좌하단 칩의 목적지.
 *
 * 강좌 전환 · 강의 기본 정보 · LiteLLM 연결 설정 · 회사 PPT 양식이 전부 여기 있다.
 * 예전에 사이드바 확장기 세 개로 흩어져 있던 것을 한 창으로 모았다: 이 값들은
 * "작업"이 아니라 "설정"이라 바탕을 차지할 이유가 없고, 서로 붙어 있어야 한 번에
 * 점검된다(키가 없어서 생성이 안 되는데 폼만 붙들고 있는 상황이 실제로 잦았다).
 */
"use strict";

import { el, icon, api, toast } from "./util.js";
import { hydrateIcons } from "./icons.js";
import { setActions, actionBtn, closeActionBtn } from "./panel.js";
import { state, getProjects, getProject, getSettings, saveSettings, invalidate } from "./store.js";
import { pickCourse, refreshRail } from "./shell.js";

export const meta = {
  title: "워크스페이스",
  subtitle: "강좌 · 강의 기본 정보 · 연결 설정",
  actions: (ctx) => headActions(ctx),
};

const SECTIONS = [
  ["course", "강좌", "folder"],
  ["form", "강의 기본 정보", "clipboard"],
  ["conn", "연결 설정", "plug"],
  ["deck", "슬라이드 양식", "slide"],
];

let section = "course";

/** 고른 뒤 이어서 할 일 + 닫기. 강좌가 없으면 시작 버튼을 내지 않는다. */
function headActions(ctx) {
  const out = [];
  if (state.courseId) {
    out.push(actionBtn("작업 시작", {
      primary: true, iconName: "arrowRight",
      onClick: async () => {
        const p = await getProject();
        // 계획서가 없으면 그걸 먼저 만들어야 하고, 있으면 주차 작업으로 보낸다.
        ctx.navigate(p && p.syllabus_md ? "/slides/pick" : "/syllabus");
      },
    }));
  }
  out.push(closeActionBtn());
  return out;
}

export async function mount(root, ctx) {
  const cfg = await getSettings(true);
  await renderRail(ctx);
  await renderBody(root, ctx, cfg);
  // 강좌를 고르면 '작업 시작' 이 생겨야 하므로 다시 그릴 때마다 갱신한다.
  setActions(headActions(ctx));
}

async function renderRail(ctx) {
  const rail = ctx.panel.rail;
  rail.innerHTML = "";
  rail.appendChild(el("div", "panel-rail-title", "설정"));
  SECTIONS.forEach(([key, label, ic]) => {
    const b = el("button", key === section ? "active" : "");
    b.type = "button";
    b.append(icon(ic, 15), el("span", "pr-label", label));
    b.addEventListener("click", async () => {
      section = key;
      await mount(ctx.panel.body, ctx);
    });
    rail.appendChild(b);
  });
  hydrateIcons(rail);
}

async function renderBody(root, ctx, cfg) {
  root.innerHTML = "";
  const box = el("div");
  box.style.maxWidth = "620px";
  root.appendChild(box);
  if (section === "course") await secCourse(box, ctx);
  else if (section === "form") await secForm(box, ctx, cfg);
  else if (section === "conn") await secConn(box, ctx, cfg);
  else await secDeck(box, ctx, cfg);
  hydrateIcons(root);
}

function field(label, node, hint) {
  const f = el("div", "field");
  f.appendChild(el("label", null, label));
  f.appendChild(node);
  if (hint) f.appendChild(el("div", "field-hint", hint));
  return f;
}

function input(value, { type = "text", placeholder = "" } = {}) {
  const i = el("input");
  i.type = type;
  i.value = value ?? "";
  i.placeholder = placeholder;
  return i;
}

function select(options, value) {
  const s = el("select");
  options.forEach(([v, label]) => {
    const o = el("option", null, label);
    o.value = String(v);
    if (String(v) === String(value)) o.selected = true;
    s.appendChild(o);
  });
  return s;
}

/**
 * 강좌 삭제 — 목록에서만 뺄지, 작업 폴더까지 지울지 묻는다.
 * 폴더에는 몇 주 분량의 교재·덱이 들어 있어서, 한 번의 확인으로 지우면 안 된다.
 */
async function deleteCourse(ctx, p) {
  if (!confirm(`'${p.name}' 강좌를 삭제합니다.\n\n확인을 누르면 목록에서 제거합니다.\n(작업 폴더의 파일은 그대로 남습니다)`)) return;
  const purge = confirm(
    `작업 폴더의 파일도 함께 지울까요?\n\n` +
    `확인 = 교재·개요·슬라이드·이미지까지 전부 삭제 (되돌릴 수 없습니다)\n` +
    `취소 = 목록에서만 제거하고 파일은 보존`);
  try {
    await api(`/api/projects/${p.id}?purge=${purge ? "true" : "false"}`, { method: "DELETE" });
    if (state.courseId === p.id) state.courseId = null;
    invalidate();
    await refreshRail();
    toast(purge ? "강좌와 파일을 삭제했습니다." : "목록에서 제거했습니다. 파일은 남아 있습니다.", "ok");
    await mount(ctx.panel.body, ctx);
  } catch (e) {
    toast("삭제하지 못했습니다: " + e.message, "err");
  }
}

/* ── 강좌 ─────────────────────────────────────────── */
async function secCourse(box, ctx) {
  const list = await getProjects(true);

  const mk = el("div", "card");
  mk.appendChild(el("div", "card-title", "새 강좌"));
  const nameIn = input("", { placeholder: "예: 교육방법 및 교육공학" });
  mk.appendChild(field("강좌 이름", nameIn));
  const mkRow = el("div", "btn-row");
  const mkBtn = el("button", "btn primary");
  mkBtn.type = "button";
  mkBtn.append(icon("plus", 14), el("span", null, "만들기"));
  mkBtn.addEventListener("click", async () => {
    const name = nameIn.value.trim();
    if (!name) { toast("강좌 이름을 입력하세요."); return; }
    mkBtn.disabled = true;
    try {
      const d = await api("/api/projects", { method: "POST", body: { name } });
      await pickCourse(d.id);
      section = "form";
      toast(`'${name}' 강좌를 만들었습니다. 강의 기본 정보를 입력하세요.`, "ok");
      await mount(ctx.panel.body, ctx);
    } catch (e) { toast("만들지 못했습니다: " + e.message, "err"); }
    finally { mkBtn.disabled = false; }
  });
  mkRow.appendChild(mkBtn);
  mk.appendChild(mkRow);
  box.appendChild(mk);

  const c = el("div", "card");
  c.appendChild(el("div", "card-title", "강좌 전환"));
  if (!list.length) {
    c.appendChild(el("div", "field-hint", "아직 강좌가 없습니다."));
  } else {
    const wrap = el("div", "co-list");
    list.forEach((p) => {
      // 행 전체가 버튼이면 안쪽에 삭제 버튼을 넣을 수 없다(중첩 버튼 금지).
      // div 로 두고 이름 영역만 클릭 가능하게 한다.
      const row = el("div", "co-row" + (p.id === state.courseId ? " on" : ""));
      const left = el("button", "co-pick");
      left.type = "button";
      left.title = `${p.name} 로 전환`;
      left.appendChild(el("div", "co-name", p.name));
      left.appendChild(el("div", "co-sub",
        `${p.weeks}주차 · 교재 ${p.done_doc} · 개요 ${p.done_ppt} · 덱 ${p.done_deck}` +
        (p.has_syllabus ? "" : " · 계획서 없음")));
      left.addEventListener("click", async () => {
        await pickCourse(p.id);
        await mount(ctx.panel.body, ctx);
      });
      row.appendChild(left);

      const right = el("div", "co-right");
      right.appendChild(el("span", "badge" + (p.done_deck ? " brand" : ""),
                           `${p.done_deck}/${p.weeks}`));
      const del = el("button", "btn sm danger icon");
      del.type = "button";
      del.title = `${p.name} 삭제`;
      del.appendChild(icon("trash", 13));
      del.addEventListener("click", (e) => {
        e.stopPropagation();
        deleteCourse(ctx, p);
      });
      right.appendChild(del);
      row.appendChild(right);
      wrap.appendChild(row);
    });
    c.appendChild(wrap);
  }
  box.appendChild(c);

  // 폴더에만 남은 강좌 — '목록에서만 제거' 로 지운 것들. 되살리거나 완전히 지운다.
  try {
    const { orphans } = await api("/api/orphans");
    if (orphans?.length) {
      const o = el("div", "card");
      const ot = el("div", "card-title");
      ot.append(el("span", null, "폴더에만 남은 강좌"),
                el("span", "muted", "목록에서 제거했지만 파일은 남아 있는 것들"));
      o.appendChild(ot);
      const list = el("div", "co-list");
      orphans.forEach((x) => {
        const row = el("div", "co-row");
        const left = el("div");
        left.style.minWidth = "0";
        left.appendChild(el("div", "co-name", x.name));
        left.appendChild(el("div", "co-sub",
          `교재 ${x.done_doc} · 개요 ${x.done_ppt} · 덱 ${x.done_deck}` +
          (x.has_syllabus ? " · 계획서 있음" : "")));
        row.appendChild(left);
        const right = el("div", "co-right");
        const rb = el("button", "btn sm");
        rb.type = "button";
        rb.append(icon("check", 13), el("span", null, "복구"));
        rb.addEventListener("click", async () => {
          try {
            await api(`/api/orphans/${x.id}/restore`, { method: "POST" });
            invalidate();
            await refreshRail();
            toast(`'${x.name}' 을 목록에 되살렸습니다.`, "ok");
            await mount(ctx.panel.body, ctx);
          } catch (e) { toast("복구하지 못했습니다: " + e.message, "err"); }
        });
        const db_ = el("button", "btn sm danger");
        db_.type = "button";
        db_.append(icon("trash", 13), el("span", null, "폴더 삭제"));
        db_.addEventListener("click", async () => {
          if (!confirm(`'${x.name}' 폴더를 완전히 지웁니다.\n\n${x.dir}\n\n되돌릴 수 없습니다.`)) return;
          try {
            await api(`/api/orphans/${x.id}`, { method: "DELETE" });
            toast("폴더를 삭제했습니다.", "ok");
            await mount(ctx.panel.body, ctx);
          } catch (e) { toast("삭제하지 못했습니다: " + e.message, "err"); }
        });
        right.append(rb, db_);
        row.appendChild(right);
        list.appendChild(row);
      });
      o.appendChild(list);
      box.appendChild(o);
    }
  } catch { /* 고아 조회 실패는 조용히 넘어간다 — 본 기능이 아니다 */ }

  const cur = await getProject();

  // 고른 다음 어디로 가야 하는지를 목록 바로 아래에서 알려준다.
  // 머리의 '작업 시작' 만 두면 스크롤 위로 올라가야 보인다.
  if (cur) {
    const go = el("div", "card");
    const gt = el("div", "card-title");
    gt.append(el("span", null, "선택됨 — " + cur.name),
              el("span", "muted", cur.syllabus_md ? "" : "강의계획서가 아직 없습니다"));
    go.appendChild(gt);
    const grow = el("div", "btn-row");
    const mkGo = (label, ic, onClick, primary) => {
      const b = el("button", "btn" + (primary ? " primary" : ""));
      b.type = "button";
      b.append(icon(ic, 14), el("span", null, label));
      b.addEventListener("click", onClick);
      return b;
    };
    const goTo = (path) => () => ctx.navigate(path);
    if (cur.syllabus_md) {
      grow.append(mkGo("슬라이드 만들기", "slide", goTo("/slides/pick"), true),
                  mkGo("교재 만들기", "book", goTo("/textbook/pick")),
                  mkGo("강의계획서 보기", "clipboard", goTo("/syllabus")));
    } else {
      grow.append(mkGo("강의계획서 만들기", "clipboard", goTo("/syllabus"), true),
                  mkGo("강의 기본 정보 입력", "edit", async () => {
                    section = "form";
                    await mount(ctx.panel.body, ctx);
                  }));
    }
    go.appendChild(grow);
    box.appendChild(go);
  }
  if (cur) {
    const d = el("div", "card");
    d.appendChild(el("div", "card-title", "이름 변경 · 삭제"));
    const rn = input(cur.name);
    d.appendChild(field("강좌 이름", rn));
    d.appendChild(field("작업 폴더", (() => {
      const p = el("div", "field-hint");
      p.textContent = cur.dir;
      return p;
    })(), "주차별 산출물이 이 폴더에 남습니다. 강좌를 삭제해도 폴더는 기본적으로 지우지 않습니다."));
    const row = el("div", "btn-row");
    const save = el("button", "btn primary");
    save.type = "button";
    save.append(icon("check", 14), el("span", null, "이름 저장"));
    save.addEventListener("click", async () => {
      await api(`/api/projects/${cur.id}`, { method: "PATCH", body: { name: rn.value.trim() } });
      invalidate();
      await refreshRail();
      toast("이름을 저장했습니다.", "ok");
      await mount(ctx.panel.body, ctx);
    });
    const del = el("button", "btn danger");
    del.type = "button";
    del.append(icon("trash", 14), el("span", null, "강좌 삭제"));
    del.addEventListener("click", () => deleteCourse(ctx, { id: cur.id, name: cur.name }));
    row.append(save, del);
    d.appendChild(row);
    box.appendChild(d);
  }
}

/* ── 강의 기본 정보 ───────────────────────────────── */
async function secForm(box, ctx, cfg) {
  const p = await getProject();
  if (!p) {
    box.appendChild(el("div", "empty", "먼저 강좌를 고르세요."));
    return;
  }
  const f = p.form || {};
  const c = el("div", "card");
  c.appendChild(el("div", "card-title", p.name));

  const title = input(f.title || "", { placeholder: "예: 교육방법 및 교육공학" });
  const fieldIn = input(f.field || "", { placeholder: "예: 교육학" });
  const target = input(f.target || "", { placeholder: "예: 교육대학원생" });
  const credit = input(f.credit || "", { placeholder: "예: 2학점, 주 2시간" });
  const weeks = select(cfg.week_choices.map((w) => [w, `${w}주`]), f.weeks ?? 15);
  const mode = select(cfg.mode_choices.map((m) => [m, m]), f.mode ?? "대면");
  const hours = input(f.hours ?? 2, { type: "number" });
  hours.min = 1; hours.max = 6;
  const topics = el("textarea");
  topics.value = f.topics || "";
  topics.placeholder = "예: 교수설계 이론, ADDIE 모형, 학습목표 설계, 매체 활용 등";
  const learner = input(f.learner || "", { placeholder: "예: 전공 기초 이수, 일부 현직 교사" });
  const policy = input(f.policy || "", { placeholder: "예: 과정 중심 40%, 토론 중심" });

  const two = (a, b) => { const g = el("div", "field-2"); g.append(a, b); return g; };
  c.append(
    two(field("과목명 *", title), field("학문 분야", fieldIn)),
    two(field("수강 대상 *", target), field("학점 / 시수", credit)),
    two(field("총 주차", weeks), field("강의 방식", mode)),
    field(`차시 수업 시간(시간) — 슬라이드는 시간당 약 ${cfg.slides_per_hour}장`, hours),
    field("주요 내용 · 주제 *", topics),
    two(field("수강생 특성", learner), field("평가 선호 · 수업 철학", policy)),
  );

  const row = el("div", "btn-row");
  const save = el("button", "btn primary");
  save.type = "button";
  save.append(icon("check", 14), el("span", null, "저장"));
  save.addEventListener("click", async () => {
    const form = {
      title: title.value.trim(), field: fieldIn.value.trim(), target: target.value.trim(),
      credit: credit.value.trim(), weeks: Number(weeks.value), mode: mode.value,
      hours: Number(hours.value) || 2, topics: topics.value.trim(),
      learner: learner.value.trim(), policy: policy.value.trim(),
    };
    if (!form.title || !form.topics) { toast("과목명과 주요 내용은 필수입니다."); return; }
    save.disabled = true;
    try {
      await api(`/api/projects/${p.id}`, { method: "PATCH", body: { form } });
      invalidate();
      await refreshRail();
      toast("강의 기본 정보를 저장했습니다.", "ok");
    } catch (e) { toast("저장하지 못했습니다: " + e.message, "err"); }
    finally { save.disabled = false; }
  });
  const go = el("button", "btn");
  go.type = "button";
  go.append(icon("arrowRight", 14), el("span", null, "강의계획서 만들기"));
  go.addEventListener("click", () => ctx.navigate("/syllabus"));
  row.append(save, go);
  c.appendChild(row);
  box.appendChild(c);
}

/* ── 연결 설정 ────────────────────────────────────── */
async function secConn(box, ctx, cfg) {
  const s = cfg.settings;
  const isCli = (s.provider || "cli") !== "litellm";
  const c = el("div", "card");
  const t = el("div", "card-title");
  // CLI 는 키가 필요 없다 — 배지는 '무엇이 준비됐는가' 를 말해야 한다.
  const ok = isCli ? !!cfg.cli_available : !!s.api_key;
  t.append(el("span", null, isCli ? "Claude Code CLI" : "Ubion LiteLLM"),
           el("span", "badge" + (ok ? " ok" : " warn"),
              isCli ? (ok ? "CLI 확인됨" : "CLI 없음") : (ok ? "키 있음" : "키 없음")));
  c.appendChild(t);

  const prov = select(Object.entries(cfg.providers || {}), s.provider || "cli");
  const url = input(s.base_url, { placeholder: "http://192.168.50.119:4000" });
  const key = input(s.api_key, { type: "password", placeholder: "sk-..." });
  const model = select(Object.entries(cfg.models), s.model);
  const uns = input(s.unsplash_key, { type: "password" });

  c.append(field("LLM 연결 방식", prov,
    "CLI 는 이 PC 의 Claude Code 로그인(구독)을 그대로 씁니다 — API 키 발급·과금이 없습니다."));

  const proxyRows = el("div");
  proxyRows.append(
    field("LiteLLM URL", url, "사내망에서만 접속됩니다."),
    field("API 키", key, "사내 대시보드(/ui/)에서 발급한 sk- 키."),
  );
  proxyRows.style.display = isCli ? "none" : "";
  c.appendChild(proxyRows);

  c.append(
    field("모델", model,
      isCli ? "CLI 기본 모델을 권장합니다. 구조화(JSON) 작업은 형식 안정성을 위해 비추론 모델을 자동으로 씁니다."
            : "디자인 슬라이드의 구조화(JSON) 작업은 형식 안정성을 위해 비추론 모델을 자동으로 씁니다."),
    field("Unsplash Access Key (선택)", uns,
      "넣으면 슬라이드 사진을 Unsplash(고품질)에서 가져옵니다. 비우면 Openverse(무료 CC)로 동작."),
  );

  // 방식을 바꾸면 모델 목록이 달라진다 → 저장 후 다시 그린다.
  prov.addEventListener("change", async () => {
    try {
      await saveSettings({ provider: prov.value });
      ctx.navigate("/workspace");
      toast(prov.value === "litellm" ? "LiteLLM 프록시로 바꿨습니다."
                                     : "Claude Code CLI 로 바꿨습니다.", "ok");
    } catch (e) { toast("저장하지 못했습니다: " + e.message, "err"); }
  });

  const row = el("div", "btn-row");
  const save = el("button", "btn primary");
  save.type = "button";
  save.append(icon("check", 14), el("span", null, "저장"));
  const ping = el("button", "btn");
  ping.type = "button";
  ping.append(icon("plug", 14), el("span", null, "연결 테스트"));
  const out = el("div", "field-hint");
  out.style.marginTop = "10px";

  save.addEventListener("click", async () => {
    save.disabled = true;
    try {
      await saveSettings({
        provider: prov.value,
        base_url: url.value.trim(), api_key: key.value.trim(),
        model: model.value, unsplash_key: uns.value.trim(),
      });
      toast("연결 설정을 저장했습니다.", "ok");
    } catch (e) { toast("저장하지 못했습니다: " + e.message, "err"); }
    finally { save.disabled = false; }
  });
  ping.addEventListener("click", async () => {
    ping.disabled = true;
    out.textContent = "확인 중…";
    try {
      const d = await api("/api/settings/ping", { method: "POST" });
      out.textContent = d.message;
      out.style.color = d.ok ? "var(--ok)" : "var(--err)";
    } catch (e) { out.textContent = e.message; out.style.color = "var(--err)"; }
    finally { ping.disabled = false; }
  });
  row.append(save, ping);
  c.append(row, out);
  box.appendChild(c);
}

/** 로고 한 자리. 미리보기 + 고르기 + 지우기.
 *  ★ multipart 대신 data URL 로 올린다 — python-multipart 를 안 늘리려는 것이다.
 *  ★ 미리보기 주소에 시각을 붙인다. 안 붙이면 바꿔도 브라우저가 옛 그림을 보여 준다. */
function logoSlot(n, title, where, cfg) {
  const has = n === 1 ? cfg.logo : cfg.logo2;
  const d = el("div", "logo-slot");
  const head = el("div", "logo-slot-head");
  head.append(el("b", null, title), el("span", "field-hint", where));
  const prev = el("div", "logo-prev");
  const draw = (on) => {
    prev.innerHTML = "";
    if (on) {
      const img = el("img");
      img.src = `/api/logo/${n}?t=${Date.now()}`;
      img.alt = title;
      prev.appendChild(img);
    } else {
      prev.appendChild(el("span", "logo-empty", "없음"));
    }
    prev.classList.toggle("on", !!on);
  };
  draw(has);

  const file = el("input");
  file.type = "file";
  file.accept = "image/png,image/jpeg,image/webp";
  file.hidden = true;
  const pick = el("button", "btn sm");
  pick.type = "button";
  pick.textContent = has ? "바꾸기" : "고르기";
  pick.addEventListener("click", () => file.click());
  const del = el("button", "btn sm");
  del.type = "button";
  del.textContent = "지우기";
  del.hidden = !has;

  file.addEventListener("change", async () => {
    const f = file.files?.[0];
    if (!f) return;
    if (f.size > 4 * 1024 * 1024) { toast("4MB 를 넘습니다.", "err"); return; }
    pick.disabled = true;
    try {
      const data_url = await new Promise((res, rej) => {
        const r = new FileReader();
        r.onload = () => res(r.result);
        r.onerror = () => rej(new Error("파일을 읽지 못했습니다."));
        r.readAsDataURL(f);
      });
      const r = await api(`/api/logo/${n}`, { method: "PUT", body: { data_url } });
      draw(true); del.hidden = false; pick.textContent = "바꾸기";
      toast(r.message, "ok");
    } catch (e) { toast(e.message, "err"); }
    finally { pick.disabled = false; file.value = ""; }
  });
  del.addEventListener("click", async () => {
    try {
      const r = await api(`/api/logo/${n}`, { method: "DELETE" });
      draw(false); del.hidden = true; pick.textContent = "고르기";
      toast(r.message, "ok");
    } catch (e) { toast(e.message, "err"); }
  });

  const row = el("div", "btn-row");
  row.append(pick, del, file);
  d.append(head, prev, row);
  return d;
}

/* ── 슬라이드 양식 · 폰트 ─────────────────────────── */
async function secDeck(box, ctx, cfg) {
  // 로고 — 파일을 직접 고른다. 표지를 뺀 모든 슬라이드 상단에 들어간다.
  const c = el("div", "card");
  c.appendChild(el("div", "card-title", "로고"));
  c.appendChild(el("div", "hc-desc",
    "로고1은 우상단, 로고2는 좌상단에 들어갑니다. 안 넣으면 그 자리는 비워 둡니다."));
  const slots = el("div", "logo-slots");
  [[1, "로고 1", "우상단 — 기본"], [2, "로고 2", "좌상단 — 있을 때만"]].forEach(
    ([n, title, where]) => slots.appendChild(logoSlot(n, title, where, cfg)));
  c.appendChild(slots);
  c.appendChild(el("div", "field-hint",
    "PNG · JPG · WEBP, 4MB 까지. 높이 0.42인치로 맞춰 넣으므로 **가로로 긴 로고**가 " +
    "잘 맞습니다. 파일은 이 PC 의 assets\ 에만 저장됩니다."));
  box.appendChild(c);

  const f = el("div", "card");
  const t = el("div", "card-title");
  t.append(el("span", null, "슬라이드 폰트"),
           el("span", "badge" + (cfg.font.embedded ? " ok" : " warn"),
              cfg.font.embedded ? "임베드 가능" : "시스템 폰트"));
  f.appendChild(t);
  f.appendChild(el("div", "hc-desc", `현재 폰트: ${cfg.font.family}`));
  f.appendChild(el("div", "field-hint", cfg.font.embedded
    ? "Pretendard 를 .pptx 에 심어서 내보냅니다. 폰트가 없는 PC 에서도 레이아웃이 그대로 열립니다(파일이 약 3MB 커집니다). 슬라이드 화면에서 끌 수 있습니다."
    : "assets/fonts 에 Pretendard TTF 가 없어 '맑은 고딕'으로 만듭니다. `python tools/get_fonts.py` 로 설치하세요."));
  box.appendChild(f);

  const w = el("div", "card");
  w.appendChild(el("div", "card-title", "작업 폴더"));
  const path = el("div", "field-hint");
  path.textContent = cfg.workspace;
  w.appendChild(path);
  w.appendChild(el("div", "field-hint",
    "강좌별로 01/ 02/ … 15/ 주차 폴더가 생기고, 슬라이드는 빌드할 때마다 v1·v2·v3… 로 전부 보존됩니다."));
  box.appendChild(w);
}
