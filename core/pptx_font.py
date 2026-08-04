# -*- coding: utf-8 -*-
"""PPTX 폰트 — 한글 렌더 폰트 지정(a:ea)과 폰트 임베드.

왜 필요한가
-----------
python-pptx 의 ``run.font.name`` 은 ``<a:latin>`` 만 세팅한다. PowerPoint 는 한글
글리프를 ``<a:ea>``(East Asian) 폰트로 그리므로, latin 만 지정하면 한글은 테마의
minor EA 폰트로 렌더된다 — 코드에서 지정한 폰트가 정작 한글에는 적용되지 않는다.
``apply_font`` 는 latin/ea/cs 를 함께 세팅해 이 구멍을 막는다.

``embed_fonts`` 는 assets/fonts 의 TTF 를 .pptx 안에 심어(embeddedFontLst),
폰트가 설치되지 않은 PC 에서 열어도 레이아웃이 무너지지 않게 한다. 실패해도
덱 생성 자체는 계속되도록 전부 그레이스풀.

폰트 파일(선택): assets/fonts/
    Pretendard-Regular.ttf · Pretendard-SemiBold.ttf · Pretendard-Bold.ttf
    (OFL — 상업 이용·임베드 허용). 없으면 자동으로 '맑은 고딕' 폴백.
"""
from __future__ import annotations

import os
import struct
from typing import Dict, List, NamedTuple, Optional

PREFERRED = "Pretendard"
FALLBACK = "맑은 고딕"

# assets/fonts 에서 찾는 파일 → (임베드 슬롯, 웨이트 키)
_WANTED = (
    ("Pretendard-Regular.ttf", "regular", "r"),
    ("Pretendard-SemiBold.ttf", "regular", "sb"),
    ("Pretendard-Bold.ttf", "bold", "b"),
)

_FNTDATA_CT = "application/x-fontdata"
_FONT_RT = ("http://schemas.openxmlformats.org/officeDocument/2006/"
            "relationships/font")
_R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


class FontSet(NamedTuple):
    """덱 전체가 쓰는 폰트 3종. ``embedded`` 가 False 면 시스템 폴백 상태."""
    regular: str
    semibold: str
    bold: str
    embedded: bool

    def face(self, weight: str) -> str:
        return {"r": self.regular, "sb": self.semibold, "b": self.bold}.get(
            weight, self.regular)

    def is_bold(self, weight: str) -> bool:
        """폰트 파일이 없으면 SemiBold 도 굵게 처리(웨이트 축이 없으므로)."""
        if weight == "b":
            return True
        if weight == "sb":
            return not self.embedded
        return False


SYSTEM_SET = FontSet(FALLBACK, FALLBACK, FALLBACK, embedded=False)


def fonts_dir(assets_dir) -> str:
    return os.path.join(str(assets_dir), "fonts")


def available(assets_dir) -> Dict[str, str]:
    """assets/fonts 에 실제로 있는 {파일명: 절대경로}."""
    d = fonts_dir(assets_dir)
    if not os.path.isdir(d):
        return {}
    return {f: os.path.join(d, f) for f, _s, _w in _WANTED
            if os.path.isfile(os.path.join(d, f))}


def font_set(assets_dir) -> FontSet:
    """이 덱에서 쓸 폰트 3종. Regular 가 없으면 통째로 시스템 폴백."""
    files = available(assets_dir)
    if "Pretendard-Regular.ttf" not in files:
        return SYSTEM_SET
    base = _ttf_family_name(files["Pretendard-Regular.ttf"]) or PREFERRED
    sb = base
    if "Pretendard-SemiBold.ttf" in files:
        sb = _ttf_family_name(files["Pretendard-SemiBold.ttf"]) or base
    return FontSet(regular=base, semibold=sb, bold=base, embedded=True)


# ── 런 단위 폰트 적용 ──────────────────────────────────────────────────────
def apply_face(run, name: str, *, spc_em: float = 0.0, size_pt=None) -> None:
    """run 의 latin/ea/cs 를 모두 name 으로. spc_em 은 자간(em) → rPr@spc(1/100pt)."""
    from pptx.oxml.ns import qn

    rPr = run._r.get_or_add_rPr()
    for tag in ("a:latin", "a:ea", "a:cs"):
        el = rPr.find(qn(tag))
        if el is None:
            el = rPr.makeelement(qn(tag), {})
            rPr.append(el)
        el.set("typeface", name)
    if spc_em and size_pt:
        rPr.set("spc", str(int(round(spc_em * float(size_pt) * 100))))


def apply_face_to_cell(cell, name: str) -> None:
    """표 셀 안 모든 run 에 폰트 적용(크기·굵기는 건드리지 않음)."""
    for para in cell.text_frame.paragraphs:
        for run in para.runs:
            apply_face(run, name)


# ── TTF name 테이블에서 패밀리명 읽기 ──────────────────────────────────────
def _ttf_family_name(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as f:
            data = f.read()
        num_tables = struct.unpack(">H", data[4:6])[0]
        name_off = None
        for i in range(num_tables):
            rec = 12 + i * 16
            if data[rec:rec + 4] == b"name":
                name_off = struct.unpack(">I", data[rec + 8:rec + 12])[0]
                break
        if name_off is None:
            return None
        cnt, str_off = struct.unpack(">HH", data[name_off + 2:name_off + 6])
        best = None
        for i in range(cnt):
            r = name_off + 6 + i * 12
            pid, eid, lid, nid, ln, off = struct.unpack(">HHHHHH", data[r:r + 12])
            if nid != 1:
                continue
            raw = data[name_off + str_off + off: name_off + str_off + off + ln]
            try:
                txt = raw.decode("utf-16-be") if pid == 3 else raw.decode("latin-1")
            except Exception:  # noqa: BLE001
                continue
            if not txt:
                continue
            if pid == 3 and eid == 1 and lid == 0x409:   # Windows / en-US 우선
                return txt
            best = best or txt
        return best
    except Exception:  # noqa: BLE001
        return None


# ── 폰트 임베드 ────────────────────────────────────────────────────────────
def embed_fonts(prs, assets_dir) -> List[str]:
    """assets/fonts 의 TTF 를 .pptx 에 임베드. 임베드된 패밀리명 목록 반환.

    presentation.xml 에 embedTrueTypeFonts="1" 와 <p:embeddedFontLst> 를 넣고,
    폰트 바이트를 /ppt/fonts/fontN.fntdata 파트로 추가한다. 어느 단계든 실패하면
    [] 를 돌려주고 덱 생성은 그대로 진행한다(그레이스풀).
    """
    files = available(assets_dir)
    if not files:
        return []
    try:
        from pptx.opc.package import Part
        from pptx.opc.packuri import PackURI
        from pptx.oxml.ns import qn
    except Exception:  # noqa: BLE001
        return []

    # 패밀리별 슬롯 모으기 — Pretendard{regular,bold} / "Pretendard SemiBold"{regular}
    fams: Dict[str, Dict[str, str]] = {}
    for fname, slot, _w in _WANTED:
        path = files.get(fname)
        if not path:
            continue
        fam = _ttf_family_name(path) or PREFERRED
        fams.setdefault(fam, {}).setdefault(slot, path)
    if not fams:
        return []

    try:
        pres_part, pres_el = prs.part, prs.element
        pres_el.set("embedTrueTypeFonts", "1")
        pres_el.set("saveSubsetFonts", "0")

        old = pres_el.find(qn("p:embeddedFontLst"))
        if old is not None:
            pres_el.remove(old)
        lst = pres_el.makeelement(qn("p:embeddedFontLst"), {})

        n, embedded = 0, []
        for fam, slots in fams.items():
            ef = lst.makeelement(qn("p:embeddedFont"), {})
            fel = ef.makeelement(qn("p:font"), {})
            fel.set("typeface", fam)
            fel.set("pitchFamily", "34")
            fel.set("charset", "-127")           # Hangul
            ef.append(fel)
            for slot in ("regular", "bold"):
                path = slots.get(slot)
                if not path:
                    continue
                n += 1
                with open(path, "rb") as fp:
                    blob = fp.read()
                part = Part(PackURI(f"/ppt/fonts/font{n}.fntdata"), _FNTDATA_CT,
                            pres_part.package, blob)
                sel = ef.makeelement(qn(f"p:{slot}"), {})
                sel.set(_R_ID, pres_part.relate_to(part, _FONT_RT))
                ef.append(sel)
            if len(ef) > 1:                      # p:font 외 최소 1슬롯
                lst.append(ef)
                embedded.append(fam)
        if len(lst) == 0:
            return []

        # embeddedFontLst 는 sldSz/notesSz 뒤에 온다
        anchor = None
        for tag in ("p:sldIdLst", "p:sldSz", "p:notesSz"):
            found = pres_el.find(qn(tag))
            if found is not None:
                anchor = found
        if anchor is not None:
            anchor.addnext(lst)
        else:
            pres_el.append(lst)
        return embedded
    except Exception as e:  # noqa: BLE001
        print(f"[font] 임베드 실패(무시하고 진행): {e}", flush=True)
        return []
