# -*- coding: utf-8 -*-
"""단색 라인 아이콘 + 의존성 0 인라인 SVG 차트.

- 아이콘: Lucide 스타일 단색 라인(currentColor 상속) — 이모지/다색 배제.
- 차트: 강의계획서의 Bloom 인지수준 태그([기억]…[창조])를 집계해 막대그래프로.
"""
from __future__ import annotations

import re
from typing import Dict


def _icon(path: str, size: int = 16) -> str:
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
            f'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
            f'stroke-linejoin="round" style="vertical-align:-3px;margin-right:7px">{path}</svg>')


ICON_INFO = _icon('<path d="M12 8h.01M11 12h1v4h1"/><circle cx="12" cy="12" r="9"/>')
ICON_DOC = _icon('<path d="M14 3v4a1 1 0 0 0 1 1h4"/>'
                 '<path d="M17 21H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h7l5 5v11a2 2 0 0 1-2 2z"/>')
ICON_SLIDE = _icon('<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M12 16v4M8 20h8"/>')
ICON_CHART = _icon('<path d="M3 3v18h18"/><rect x="7" y="10" width="3" height="7"/>'
                   '<rect x="12" y="6" width="3" height="11"/><rect x="17" y="13" width="3" height="4"/>')

# Bloom 인지수준 6단계 (저차 → 고차)
BLOOM = ["기억", "이해", "적용", "분석", "평가", "창조"]
# 순서값이므로 단일 색조 계조(스카이블루) — 범주형 색 섞지 않는다
BLOOM_SHADES = ["#cfe4f7", "#a9cfef", "#7db4e4", "#4a9be0", "#2a7fcd", "#1668c1"]


def bloom_counts(md: str) -> Dict[str, int]:
    return {b: len(re.findall(r"\[" + b + r"\]", md or "")) for b in BLOOM}


def bloom_chart_html(counts: Dict[str, int]) -> str:
    vals = [counts[b] for b in BLOOM]
    total = sum(vals)
    if total == 0:
        return ""
    maxv = max(vals)
    W, H, pad_l, pad_t, pad_b = 660, 190, 8, 22, 40
    plot_h = H - pad_t - pad_b
    bw = (W - pad_l * 2) / len(BLOOM)
    bars = ""
    for i, (lv, v) in enumerate(zip(BLOOM, vals)):
        bh = (v / maxv) * plot_h if maxv else 0
        x = pad_l + i * bw + bw * 0.16
        w = bw * 0.68
        y = pad_t + (plot_h - bh)
        bars += (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{max(bh, 2):.1f}" '
                 f'rx="4" fill="{BLOOM_SHADES[i]}"/>'
                 f'<text x="{x + w / 2:.1f}" y="{y - 6:.1f}" text-anchor="middle" '
                 f'font-size="12.5" font-weight="700" fill="#5b6472">{v}</text>'
                 f'<text x="{x + w / 2:.1f}" y="{H - 16:.1f}" text-anchor="middle" '
                 f'font-size="12.5" fill="#1a1d23">{lv}</text>')
    low = counts["기억"] + counts["이해"]
    high = counts["평가"] + counts["창조"]
    if total >= 3 and low / total >= 0.6:
        note = "하위 수준(기억·이해)에 집중 — 고차사고 목표 보강 검토"
    elif total >= 3 and high / total >= 0.6:
        note = "고차 수준(평가·창조)에 집중 — 토대 목표(기억·이해) 확인"
    else:
        note = "인지수준이 비교적 고르게 분포"
    return (
        '<div style="border:1px solid var(--line);border-radius:12px;background:#fff;'
        'padding:14px 16px;margin-bottom:12px;box-shadow:0 1px 3px rgba(20,24,40,.06)">'
        f'<div class="ida-panel-title" style="color:var(--brand2)">{ICON_CHART}'
        f'인지수준 분포 (Bloom · 목표 태그 {total}개)</div>'
        f'<svg viewBox="0 0 {W} {H}" width="100%" role="img">{bars}</svg>'
        f'<div style="font-size:12px;color:var(--ink2);margin-top:2px">{note}</div>'
        '</div>'
    )
