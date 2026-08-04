/* 상태 저장소 — 현재 강좌·주차와 API 캐시.
 *
 * "지금 어느 강좌의 몇 주차인가" 는 앱 전체가 쓰는 값이라 URL 이 아니라 여기에
 * 둔다(라우트는 화면만 고른다). 새로 고쳐도 유지되도록 localStorage 에 남긴다.
 */
"use strict";

import { api } from "./util.js";

const K_COURSE = "ida.course";
const K_WEEK = "ida.week";

let _settings = null;
let _project = null;      // 현재 강좌 상세(form·syllabus·week_titles)
let _projects = null;     // 목록

export const state = {
  get courseId() {
    const v = Number(localStorage.getItem(K_COURSE) || 0);
    return v > 0 ? v : null;
  },
  set courseId(v) {
    if (v) localStorage.setItem(K_COURSE, String(v));
    else localStorage.removeItem(K_COURSE);
    _project = null;
  },
  get week() { return Number(localStorage.getItem(K_WEEK) || 1) || 1; },
  set week(v) { localStorage.setItem(K_WEEK, String(Math.max(1, Number(v) || 1))); },
};

export async function getSettings(force = false) {
  if (!_settings || force) _settings = await api("/api/settings");
  return _settings;
}

export async function saveSettings(patch) {
  await api("/api/settings", { method: "PUT", body: patch });
  _settings = null;
  return getSettings(true);
}

export async function getProjects(force = false) {
  if (!_projects || force) _projects = (await api("/api/projects")).projects;
  return _projects;
}

/** 현재 강좌 상세. 없으면 null. */
export async function getProject(force = false) {
  const id = state.courseId;
  if (!id) return null;
  if (!_project || force || _project.id !== id) {
    try {
      _project = await api(`/api/projects/${id}`);
    } catch (e) {
      // ★ 404(정말 지워진 강좌)일 때만 선택을 놓아준다.
      //   예전에는 모든 오류에서 놓아줬다 — 리로드로 요청이 취소되거나 서버가
      //   한 박자 늦기만 해도 고른 강좌가 사라져서, 레일이 강좌 목록으로
      //   되돌아가 버렸다.
      if (e && e.status === 404) state.courseId = null;
      _project = null;
      if (!e || e.status !== 404) console.warn("강좌를 불러오지 못했습니다(선택 유지):", e);
    }
  }
  return _project;
}

export async function getWeeks(force = false) {
  const id = state.courseId;
  if (!id) return { weeks: [], n_weeks: 15 };
  const key = "_weeks";
  if (!force && _project && _project[key] && _project.id === id) return _project[key];
  const d = await api(`/api/projects/${id}/weeks`);
  if (_project) _project[key] = d;
  return d;
}

export async function getWeek(week) {
  const id = state.courseId;
  if (!id) return null;
  return api(`/api/projects/${id}/weeks/${week}`);
}

export function invalidate() {
  _project = null;
  _projects = null;
}

/** 강좌·주차가 바뀌었음을 알린다 — 레일과 열려 있는 화면이 스스로 갱신한다. */
export function announce(name, detail) {
  window.dispatchEvent(new CustomEvent(name, { detail }));
}
