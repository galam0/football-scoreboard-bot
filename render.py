"""스코어보드 이미지 렌더러 (Pillow).

API-Football `/fixtures?id=...` 응답 한 건(dict)을 받아
상단 스코어바(팀·스코어·경기 시간) + 홈/원정 포메이션 보드를 PNG로 그린다.
"""
from __future__ import annotations

import io
import time
import unicodedata
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from names_ko import ko_team

# 번들 폰트(Noto Sans KR 서브셋)에 없는 라틴 문자(Ł, ğ, š …)는 □로 깨지므로 가장 가까운 알파벳으로 바꿔서 그린다
_LATIN_OK = 'ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖ×ØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõö÷øùúûüýþÿĀāĂăĐđĒēĚěĨĩĪīŃńŇňŌōŎŏŒœŨũŪūŬŭƒƠơƯưǍǎǏǐǑǒǓǔǕǖǗǘǙǚǛǜǸǹ'
_LATIN_SPECIAL = {"Ł": "L", "ł": "l", "ø": "o", "Ø": "O", "đ": "d", "Đ": "D", "ı": "i", "ß": "ss", "æ": "ae",
                  "Æ": "AE", "œ": "oe", "Œ": "OE", "ð": "d", "Ð": "D", "þ": "th", "Þ": "Th", "ħ": "h", "ŧ": "t"}


def _safe(text: str | None) -> str:
    out = []
    for ch in text or "":
        o = ord(ch)
        if (0xC0 <= o < 0x250 and ch not in _LATIN_OK) or 0x1E00 <= o < 0x1F00:
            base = _LATIN_SPECIAL.get(ch) or "".join(
                x for x in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(x))
            out.append(base if base and all(ord(x) < 128 for x in base) else "?")
        else:
            out.append(ch)
    return "".join(out)


KST = ZoneInfo("Asia/Seoul")
BASE = Path(__file__).parent

# 한글 폰트 후보 (번들 서브셋 → 시스템 Noto → 나눔고딕). (경로, TTC 인덱스)
FONT_CANDIDATES = [
    (BASE / "fonts" / "NotoSansKR-Bold-subset.otf", 0),
    (Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"), 1),
    (Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"), 0),
]

S = 2  # 슈퍼샘플링 배율: 2배로 그린 뒤 줄여서 가장자리를 부드럽게
W, H = 820, 736  # 피치 아래 여백 20px (득점자는 아래 "득점" 칸에 따로 그린다)

BG = (15, 19, 30)
PURPLE = (40, 28, 92)
TITLE_BAR = (52, 38, 112)
HOME_DEFAULT = (190, 30, 45)
AWAY_DEFAULT = (30, 70, 160)

PITCH_Y, PITCH_W, PITCH_H = 196, 380, 520

LIVE = {"1H", "2H", "ET", "LIVE"}
PRE = {"NS", "TBD"}
TERMINAL = {"FT", "AET", "PEN", "CANC", "ABD", "AWD", "WO", "PST"}


# ───────────────────────── 상태 헬퍼 (bot.py에서도 사용) ─────────────────────────
def is_finished(data: dict) -> bool:
    return data["fixture"]["status"].get("short") in TERMINAL


def kickoff_dt(data: dict) -> datetime | None:
    ts = data["fixture"].get("timestamp")
    return datetime.fromtimestamp(ts, KST) if ts else None


def live_clock(data: dict, now: float | None = None) -> str | None:
    """경기 시계를 '31:20' 형태로 추정한다. API는 '분'만 주기 때문에,
    전·후반 시작 시각(fixture.periods)과 현재 시각의 차이로 초를 계산한다.
    값이 API의 분과 맞지 않거나 시작 시각이 없으면 None (분 단위 표시로 대체)."""
    fx = data["fixture"]
    st = fx["status"]
    if fx.get("clock") is not None and st.get("short") in ("1H", "2H", "ET"):
        # ESPN처럼 데이터 소스가 경기 시계(초)를 직접 주는 경우: 받은 뒤 지난 시간만 더한다
        now = time.time() if now is None else now
        sec = int(fx["clock"] + max(now - fx.get("clock_at", now), 0))
        return f"{sec // 60}:{sec % 60:02d}"
    elapsed = st.get("elapsed")
    if elapsed is None or st.get("short") not in ("1H", "2H"):
        return None
    periods = fx.get("periods") or {}
    start = periods.get("first" if st["short"] == "1H" else "second")
    if not start:
        return None
    now = time.time() if now is None else now
    sec = now - start + (0 if st["short"] == "1H" else 45 * 60)
    minute = elapsed + (st.get("extra") or 0)
    if sec < 0 or abs(sec / 60 - minute) > 1.5:
        return None
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}"


def status_text(data: dict, precise: bool = False) -> str:
    fx = data["fixture"]
    st = fx["status"]
    short = st.get("short")
    elapsed, extra = st.get("elapsed"), st.get("extra")
    pen = (data.get("score") or {}).get("penalty") or {}

    if short in LIVE:
        if elapsed is None:
            return "진행 중"
        if precise and (clock := live_clock(data)):
            return clock
        label = f"{elapsed}+{extra}'" if extra else f"{elapsed}'"
        return f"연장 {label}" if short == "ET" else label
    if short in PRE:
        ko = kickoff_dt(data)
        if short == "TBD" or ko is None:
            return "시간 미정"
        if ko.date() == datetime.now(KST).date():
            return ko.strftime("%H:%M 킥오프")
        return ko.strftime("%m/%d %H:%M 킥오프")
    if short in ("P", "PEN") and pen.get("home") is not None:
        prefix = "승부차기" if short == "P" else "승부차기 종료"
        return f"{prefix} {pen['home']}-{pen['away']}"
    return {
        "HT": "하프타임",
        "BT": "연장 휴식",
        "P": "승부차기",
        "FT": "경기 종료",
        "AET": "연장 종료",
        "PEN": "승부차기 종료",
        "SUSP": "경기 중단",
        "INT": "일시 중단",
        "PST": "경기 연기",
        "CANC": "경기 취소",
        "ABD": "경기 중단됨",
        "AWD": "서류상 결과",
        "WO": "부전승",
    }.get(short, st.get("long") or short or "")


def state_signature(data: dict) -> tuple:
    """화면에 영향을 주는 값만 묶어서 변화 여부를 판단한다."""
    st = data["fixture"]["status"]
    g = data.get("goals") or {}
    return (
        st.get("short"), st.get("elapsed"), st.get("extra"),
        g.get("home"), g.get("away"),
        len(data.get("events") or []),
        bool(data.get("lineups")),
    )


# ───────────────────────── 그리기 도구 ─────────────────────────
@lru_cache(maxsize=None)
def font(size: int) -> ImageFont.FreeTypeFont:
    for path, index in FONT_CANDIDATES:
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size * S, index=index)
            except OSError:
                continue
    return ImageFont.load_default(size * S)


class Canvas:
    def __init__(self, w: int, h: int, bg):
        self.w, self.h = w, h
        self.img = Image.new("RGB", (w * S, h * S), bg)
        self.d = ImageDraw.Draw(self.img, "RGBA")

    def rrect(self, box, r, fill=None, outline=None, width=1):
        x0, y0, x1, y1 = box
        self.d.rounded_rectangle((x0 * S, y0 * S, x1 * S, y1 * S), radius=r * S,
                                 fill=fill, outline=outline, width=int(width * S))

    def rect(self, box, fill=None, outline=None, width=1):
        x0, y0, x1, y1 = box
        self.d.rectangle((x0 * S, y0 * S, x1 * S, y1 * S),
                         fill=fill, outline=outline, width=int(width * S))

    def circle(self, cx, cy, r, fill=None, outline=None, width=1):
        self.d.ellipse(((cx - r) * S, (cy - r) * S, (cx + r) * S, (cy + r) * S),
                       fill=fill, outline=outline, width=int(width * S))

    def line(self, pts, fill, width=1):
        self.d.line([(x * S, y * S) for x, y in pts], fill=fill, width=int(width * S))

    def poly(self, pts, fill):
        self.d.polygon([(x * S, y * S) for x, y in pts], fill=fill)

    def text(self, xy, s, size, fill=(255, 255, 255), anchor="la", stroke=0, stroke_fill=(0, 0, 0)):
        self.d.text((xy[0] * S, xy[1] * S), _safe(s), font=font(size), fill=fill, anchor=anchor,
                    stroke_width=int(stroke * S), stroke_fill=stroke_fill)

    def text_width(self, s, size) -> float:
        return self.d.textlength(_safe(s), font=font(size)) / S

    def logo(self, raw: bytes | None, cx, cy, size, fallback=""):
        if raw:
            try:
                im = Image.open(io.BytesIO(raw)).convert("RGBA")
                im.thumbnail((size * S, size * S), Image.LANCZOS)
                self.img.paste(im, (int(cx * S - im.width / 2), int(cy * S - im.height / 2)), im)
                return
            except Exception:
                pass
        self.circle(cx, cy, size / 2, fill=(255, 255, 255, 40), outline=(255, 255, 255, 120), width=2)
        if fallback:
            self.text((cx, cy), fallback[:1], int(size * 0.5), anchor="mm")

    def result(self) -> Image.Image:
        return self.img.resize((self.w, self.h), Image.LANCZOS)


def _hex(c: str | None, default):
    if not c:
        return default
    c = c.lstrip("#")
    try:
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return default


def _lum(rgb) -> float:
    r, g, b = rgb[:3]
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


def _text_on(rgb):
    return (255, 255, 255) if _lum(rgb) < 0.6 else (20, 20, 28)


def _dist(a, b) -> float:
    return sum((x - y) ** 2 for x, y in zip(a[:3], b[:3])) ** 0.5


def _fit(c: Canvas, s: str, max_w: float, start: int, minimum: int = 14) -> int:
    size = start
    while size > minimum and c.text_width(s, size) > max_w:
        size -= 1
    return size


def _surname(name: str, max_len: int = 9) -> str:
    parts = (name or "").split()
    if not parts:
        return ""
    s = " ".join(parts[1:]) if len(parts) >= 2 and parts[0].endswith(".") else parts[-1]
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


# ───────────────────────── 피치 / 선수 배치 ─────────────────────────
@lru_cache(maxsize=1)
def _pitch(pw: int, ph: int):
    img = Image.new("RGB", (pw * S, ph * S), (34, 115, 62))
    d = ImageDraw.Draw(img, "RGBA")
    bands = 10
    for i in range(1, bands, 2):
        d.rectangle((0, round(ph * S * i / bands), pw * S, round(ph * S * (i + 1) / bands)),
                    fill=(255, 255, 255, 12))

    m = 14
    line, lw = (255, 255, 255, 170), 2 * S
    x0, y0, x1, y1 = m, m, pw - m, ph - m
    sx, sy = (x1 - x0) / 68, (y1 - y0) / 105
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2

    def rect(a, b, c_, d_):
        d.rectangle((min(a, c_) * S, min(b, d_) * S, max(a, c_) * S, max(b, d_) * S),
                    outline=line, width=lw)

    rect(x0, y0, x1, y1)
    d.line((x0 * S, cy * S, x1 * S, cy * S), fill=line, width=lw)
    r = 9.15 * sx
    d.ellipse(((cx - r) * S, (cy - r) * S, (cx + r) * S, (cy + r) * S), outline=line, width=lw)
    d.ellipse(((cx - 3) * S, (cy - 3) * S, (cx + 3) * S, (cy + 3) * S), fill=line)
    pbw, pbd, gbw, gbd = 40.3 * sx, 16.5 * sy, 18.3 * sx, 5.5 * sy
    for top in (True, False):
        yb = y0 if top else y1
        sign = 1 if top else -1
        rect(cx - pbw / 2, yb, cx + pbw / 2, yb + sign * pbd)
        rect(cx - gbw / 2, yb, cx + gbw / 2, yb + sign * gbd)

    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=12 * S, fill=255)
    return img, mask


# 한 줄에 선수가 n명일 때 가운데에서 양 끝까지의 거리(피치 너비 비율). 5명이면 76px 간격이 나온다
HALF_SPAN = {1: 0.0, 2: 0.18, 3: 0.30, 4: 0.38, 5: 0.40}


def _half_span(n: int) -> float:
    return HALF_SPAN.get(n, 0.42)


def _spread(i: int, n: int) -> float:
    """n명 중 i번째 선수의 가로 위치(0~1)."""
    if n == 1:
        return 0.5
    return 0.5 + _half_span(n) * (2 * i / (n - 1) - 1)


def _fit_name(c: Canvas, name: str, max_w: float, start: int = 17, minimum: int = 11):
    """폭에 맞게 글자 크기를 줄이고, 그래도 넘치면 …로 줄인다. (이름, 글자 크기)를 돌려준다."""
    size = start
    while size > minimum and c.text_width(name, size) > max_w:
        size -= 1
    while len(name) > 3 and c.text_width(name, size) > max_w:
        name = name[:-2] + "…"
    return name, size


def _flag_image(raw: bytes | None, w: int) -> Image.Image | None:
    """국기를 가로 w(논리 좌표)에 맞춰 모서리를 둥글게 만든다. 열 수 없으면 None."""
    if not raw:
        return None
    try:
        im = Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception:
        return None
    h = max(1, round(w * im.height / im.width))
    im = im.resize((w * S, h * S), Image.LANCZOS)
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, im.width - 1, im.height - 1), radius=2 * S, fill=255)
    im.putalpha(mask)
    ImageDraw.Draw(im).rounded_rectangle((0, 0, im.width - 1, im.height - 1), radius=2 * S,
                                         outline=(0, 0, 0, 110), width=S)
    return im


def _circle_photo(raw: bytes | None, r: int) -> Image.Image | None:
    """선수 사진을 반지름 r(논리 좌표)의 원형으로 잘라 돌려준다. 열 수 없으면 None."""
    if not raw:
        return None
    try:
        im = Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception:
        return None
    size = 2 * r * S
    side = min(im.size)
    left = (im.width - side) // 2
    im = im.crop((left, 0, left + side, side)).resize((size, size), Image.LANCZOS)  # 얼굴이 위쪽이라 위 기준으로 자름
    bg = Image.new("RGBA", (size, size), (225, 228, 235, 255))  # 투명 배경 사진용 바탕
    bg.alpha_composite(im)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    bg.putalpha(mask)
    return bg


def _layout(lineup: dict):
    """[(선수, x비율 0~1(좌→우), y비율 0~1(GK=0 → 공격수=1))]"""
    xi = [p["player"] for p in lineup.get("startXI", [])]
    rows: dict[int, list] = {}
    if xi and all(p.get("grid") for p in xi):
        for p in xi:
            r, col = p["grid"].split(":")
            rows.setdefault(int(r), []).append((int(col), p))
    else:  # grid가 없으면 포메이션 문자열(4-2-3-1)로 줄을 나눈다
        counts = [1] + [int(n) for n in (lineup.get("formation") or "").split("-") if n.isdigit()]
        i = 0
        for r, n in enumerate(counts, start=1):
            for col, p in enumerate(xi[i:i + n], start=1):
                rows.setdefault(r, []).append((col, p))
            i += n
        for p in xi[i:]:  # 포메이션 합계가 모자라면 마지막 줄에 붙인다
            last = max(rows) if rows else 1
            rows.setdefault(last, []).append((len(rows.get(last, [])) + 1, p))
    if not rows:
        return []
    top = max(rows)
    out = []
    for r, items in rows.items():
        items.sort(key=lambda t: t[0])
        n = len(items)
        for i, (_, p) in enumerate(items):
            out.append((p, _spread(i, n), (r - 1) / (top - 1) if top > 1 else 0))
    return out


def _events(data: dict):
    """(선수별 카드·골 표시, 홈/원정 득점 텍스트 목록)"""
    home_id, away_id = data["teams"]["home"]["id"], data["teams"]["away"]["id"]
    marks: dict[int, dict] = {}
    normal = {"home": [], "away": []}
    own = []  # (이벤트 팀 쪽, 텍스트)

    def minute(ev):
        t = ev.get("time") or {}
        el, ex = t.get("elapsed"), t.get("extra")
        return f"{el}+{ex}'" if ex else f"{el}'"

    for ev in data.get("events") or []:
        pid = (ev.get("player") or {}).get("id")
        kind, detail = ev.get("type"), (ev.get("detail") or "").lower()
        if kind == "Card" and pid:
            m = marks.setdefault(pid, {"y": 0, "r": 0, "g": 0})
            if "red" in detail or "second yellow" in detail:
                m["r"] = 1
            elif "yellow" in detail:
                m["y"] = 1
        elif kind == "Goal" and "missed" not in detail and ev.get("comments") != "Penalty Shootout":
            side = "home" if (ev.get("team") or {}).get("id") == home_id else "away"
            t = ev.get("time") or {}
            item = {"min": minute(ev), "order": (t.get("elapsed") or 0) * 100 + (t.get("extra") or 0),
                    "name": (ev.get("player") or {}).get("name") or "",
                    "assist": (ev.get("assist") or {}).get("name")}
            if "own goal" in detail:
                own.append((side, {**item, "tag": "자책골", "assist": None}))
            else:
                if pid:
                    marks.setdefault(pid, {"y": 0, "r": 0, "g": 0})["g"] += 1
                item["tag"] = "PK" if "penalty" in detail else ""
                normal[side].append(item)

    # 자책골은 상대 팀 득점으로 센다. 최종 스코어와 안 맞으면 반대로 뒤집어 본다.
    g = data.get("goals") or {}
    flip = False
    if own and g.get("home") is not None:
        want = (g["home"], g["away"])
        opp = (len(normal["home"]) + sum(1 for s, _ in own if s == "away"),
               len(normal["away"]) + sum(1 for s, _ in own if s == "home"))
        same = (len(normal["home"]) + sum(1 for s, _ in own if s == "home"),
                len(normal["away"]) + sum(1 for s, _ in own if s == "away"))
        flip = opp != want and same == want
    scorers = {"home": list(normal["home"]), "away": list(normal["away"])}
    for side, item in own:
        credit = side if flip else ("away" if side == "home" else "home")
        scorers[credit].append(item)
    for side in scorers:
        scorers[side].sort(key=lambda x: x["order"])
    return marks, scorers


def _wrap(c: Canvas, prefix: str, items: list[str], size: int, max_w: float, max_lines: int = 2):
    lines, cur = [], prefix
    for it in items:
        cand = cur + it if cur == prefix else cur + "  " + it
        if cur != prefix and c.text_width(cand, size) > max_w:
            lines.append(cur)
            cur = it
        else:
            cur = cand
    lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip() + " …"
    return lines


def _draw_team_board(c: Canvas, x: int, team: dict, lineup: dict | None, default_color,
                     marks: dict, scorers: list[str], kickoff_note: bool,
                     photos: dict[int, bytes] | None = None,
                     nats: dict[int, str] | None = None,
                     flags: dict[str, bytes] | None = None):
    # 제목 막대
    c.rrect((x, 152, x + PITCH_W, 186), 10, fill=TITLE_BAR)
    formation = f"  {lineup['formation']}" if lineup and lineup.get("formation") else ""
    title = f"{ko_team(team['name'])} 포메이션{formation}"
    c.text((x + PITCH_W / 2, 169), title, _fit(c, title, PITCH_W - 24, 19), anchor="mm")

    # 피치
    img, mask = _pitch(PITCH_W, PITCH_H)
    c.img.paste(img, (x * S, PITCH_Y * S), mask)

    if not lineup or not lineup.get("startXI"):
        c.text((x + PITCH_W / 2, PITCH_Y + PITCH_H / 2 - 12), "라인업 발표 전", 26, anchor="mm", stroke=2)
        if kickoff_note:
            c.text((x + PITCH_W / 2, PITCH_Y + PITCH_H / 2 + 24), "보통 킥오프 1시간 전쯤 공개돼요", 15,
                   fill=(235, 245, 235), anchor="mm", stroke=2)
        return

    colors = lineup["team"].get("colors") or {}
    layout = _layout(lineup)
    row_sizes: dict[float, int] = {}
    for _, _, fy in layout:
        row_sizes[round(fy, 3)] = row_sizes.get(round(fy, 3), 0) + 1
    top, bottom = PITCH_Y + 62, PITCH_Y + PITCH_H - 68

    for p, fx, fy in layout:
        px, py = x + PITCH_W * fx, bottom - (bottom - top) * fy
        n_row = row_sizes[round(fy, 3)]
        gap = PITCH_W if n_row == 1 else PITCH_W * 2 * _half_span(n_row) / (n_row - 1)  # 옆 선수와의 간격(px)
        col = colors.get("goalkeeper" if p.get("pos") == "G" else "player") or colors.get("player") or {}
        fill = _hex(col.get("primary"), default_color)
        num = _hex(col.get("number"), _text_on(fill))
        if abs(_lum(fill) - _lum(num)) < 0.35:
            num = _text_on(fill)
        edge = (30, 30, 30) if _lum(fill) > 0.75 else (255, 255, 255)

        photo = _circle_photo(photos.get(p.get("id")), 25) if photos else None
        number = str(p.get("number", ""))
        if photo:
            rr = 28  # 팀 색 링을 포함한 바깥 반지름
            c.circle(px, py + 2, rr, fill=(0, 0, 0, 60))  # 그림자
            c.circle(px, py, rr, fill=fill, outline=edge, width=1.5)
            c.img.paste(photo, (int(px * S - photo.width / 2), int(py * S - photo.height / 2)), photo)
            bx, by = px - rr * 0.7, py + rr * 0.62  # 왼쪽 아래 번호 배지
            c.circle(bx, by, 11, fill=fill, outline=edge, width=2)
            c.text((bx, by), number, 14, fill=num, anchor="mm")
        else:
            rr = 22
            c.circle(px, py + 2, rr, fill=(0, 0, 0, 60))  # 그림자
            c.circle(px, py, rr, fill=fill, outline=edge, width=2.5)
            c.text((px, py), number, 21, fill=num, anchor="mm")
        # 이름(+국기): 옆 선수와 안 겹치게 글자 크기를 맞춘다
        country = (nats or {}).get(p.get("id"))
        flag = _flag_image((flags or {}).get(country), 22 if gap >= 90 else 17) if country else None
        flag_w = flag.width / S if flag else 0
        name, nsize = _fit_name(c, _surname(p.get("name", ""), 24),
                                gap - 6 - (flag_w + 4 if flag else 0), 16 if flag else 17)
        ny = py + rr + 5 + nsize * 0.62
        if flag:
            x0 = px - (flag_w + 4 + c.text_width(name, nsize)) / 2
            c.img.paste(flag, (int(x0 * S), int((ny - flag.height / S / 2) * S)), flag)
            c.text((x0 + flag_w + 4, ny), name, nsize, anchor="lm", stroke=2)
        else:
            c.text((px, ny), name, nsize, anchor="mm", stroke=2)

        if p.get("subbed_out"):
            tx0, ty0 = px - rr * 0.8, py - rr * 0.75
            c.circle(tx0, ty0, 9, fill=(255, 255, 255), outline=(20, 20, 20), width=1.2)
            c.poly([(tx0 - 5, ty0 - 3), (tx0 + 5, ty0 - 3), (tx0, ty0 + 5)], fill=(220, 40, 40))

        m = marks.get(p.get("id"))
        if m:
            cx0, cy0 = px + rr * 0.64, py - rr - 9
            if m["r"]:
                c.rect((cx0, cy0, cx0 + 10, cy0 + 16), fill=(220, 30, 30), outline=(255, 255, 255), width=1)
            elif m["y"]:
                c.rect((cx0, cy0, cx0 + 10, cy0 + 16), fill=(250, 204, 21), outline=(255, 255, 255), width=1)
            if m["g"]:
                bx, by = px + rr * 0.9, py + rr * 0.73
                c.circle(bx, by, 9, fill=(255, 255, 255), outline=(20, 20, 20), width=1.5)
                c.circle(bx, by, 3.2, fill=(20, 20, 20))
                if m["g"] > 1:
                    c.text((bx + 12, by), f"x{m['g']}", 13, anchor="lm", stroke=2)




# ───────────────────────── 경기 통계 · 교체 ─────────────────────────
STAT_ROWS = [  # (표시 이름, ESPN 통계 이름, 형식)
    ("점유율", "possessionPct", "pct"),
    ("슈팅", "totalShots", "n"),
    ("유효 슈팅", "shotsOnTarget", "n"),
    ("코너킥", "wonCorners", "n"),
    ("파울", "foulsCommitted", "n"),
    ("경고", "yellowCards", "n"),
    ("퇴장", "redCards", "n"),
    ("오프사이드", "offsides", "n"),
    ("패스 성공률", "passPct", "ratio"),
]
STAT_ROW_H, SUB_ROW_H, GOAL_ROW_H = 30, 26, 30


def _stat_value(stats: dict, key: str):
    v = (stats or {}).get(key)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _stat_rows(data: dict):
    """[(이름, 홈값, 원정값, 형식)] — 값이 없거나(퇴장은 0이면) 둘 다 0인 줄은 뺀다."""
    stats = data.get("stats") or {}
    rows = []
    for label, key, kind in STAT_ROWS:
        h, a = _stat_value(stats.get("home"), key), _stat_value(stats.get("away"), key)
        if h is None or a is None:
            continue
        if key == "redCards" and h == 0 and a == 0:
            continue
        rows.append((label, h, a, kind))
    return rows


def _fmt_stat(v: float, kind: str) -> str:
    if kind == "pct":
        return f"{v:.0f}%"
    if kind == "ratio":
        return f"{v * 100:.0f}%"
    return f"{v:.0f}"


def _goal_count(data: dict) -> int:
    _, sc = _events(data)
    return max(len(sc["home"]), len(sc["away"]))


def _extras_height(data: dict, pre: bool) -> int:
    h = 0
    if not pre and _goal_count(data):
        h += 14 + 34 + _goal_count(data) * GOAL_ROW_H + 6
    if not pre and _stat_rows(data):
        h += 14 + 34 + len(_stat_rows(data)) * STAT_ROW_H + 6
    n = max(_subs_by_side(data).values(), key=len, default=[])
    if not pre and n:
        h += 14 + 34 + min(len(n), 6) * SUB_ROW_H + 6
    return h


def _subs_by_side(data: dict) -> dict[str, list]:
    home_id = data["teams"]["home"]["id"]
    out = {"home": [], "away": []}
    for sub in data.get("subs") or []:
        out["home" if sub.get("team_id") == home_id else "away"].append(sub)
    return out


def _draw_stats(c: Canvas, y: int, data: dict, hcol, acol) -> int:
    rows = _stat_rows(data)
    c.rrect((20, y + 14, 800, y + 48), 10, fill=TITLE_BAR)
    c.text((410, y + 31), "경기 통계", 19, anchor="mm")
    y += 14 + 34
    for i, (label, h, a, kind) in enumerate(rows):
        cy = y + i * STAT_ROW_H + STAT_ROW_H / 2 + 3
        c.text((410, cy), label, 15, fill=(200, 208, 225), anchor="mm")
        c.text((34, cy), _fmt_stat(h, kind), 17, anchor="lm")
        c.text((786, cy), _fmt_stat(a, kind), 17, anchor="rm")
        total = (h + a) or 1
        # 막대: 홈은 가운데 라벨 왼쪽에서 왼쪽으로, 원정은 오른쪽으로. 두 값의 비율만큼 채운다
        span = 250
        hw = span * h / max(h, a, 1) if kind not in ("pct",) else span * h / total * 1.0
        aw = span * a / max(h, a, 1) if kind not in ("pct",) else span * a / total * 1.0
        c.rrect((330 - span, cy - 4, 330, cy + 4), 4, fill=(255, 255, 255, 28))
        c.rrect((490, cy - 4, 490 + span, cy + 4), 4, fill=(255, 255, 255, 28))
        if hw > 1:
            c.rrect((330 - hw, cy - 4, 330, cy + 4), 4, fill=hcol)
        if aw > 1:
            c.rrect((490, cy - 4, 490 + aw, cy + 4), 4, fill=acol)
    return y + len(rows) * STAT_ROW_H + 6


def _ball(c: Canvas, cx, cy, r=8):
    c.circle(cx, cy, r, fill=(255, 255, 255), outline=(20, 20, 20), width=1.5)
    c.circle(cx, cy, r * 0.38, fill=(20, 20, 20))


def _draw_goals(c: Canvas, y: int, scorers: dict) -> int:
    n = max(len(scorers["home"]), len(scorers["away"]))
    c.rrect((20, y + 14, 800, y + 48), 10, fill=TITLE_BAR)
    c.text((410, y + 31), "득점", 19, anchor="mm")
    y += 14 + 34
    c.line([(410, y + 4), (410, y + n * GOAL_ROW_H - 2)], fill=(255, 255, 255, 40), width=1)
    for side in ("home", "away"):
        for i, g in enumerate(scorers[side]):
            cy = y + i * GOAL_ROW_H + GOAL_ROW_H / 2 + 2
            name = _surname(g["name"], 14) or "?"
            extra = f" ({g['tag']})" if g["tag"] else ""
            assist = f"  도움 {_surname(g['assist'], 11)}" if g.get("assist") else ""
            if side == "home":  # 홈: 가운데에서 왼쪽으로  [이름 (PK)  도움 X  ⚽ 23']
                c.text((396, cy), g["min"], 15, fill=(170, 180, 200), anchor="rm")
                bx = 396 - c.text_width(g["min"], 15) - 14
                _ball(c, bx, cy)
                x = bx - 14
                if assist:
                    c.text((x, cy), assist, 13, fill=(160, 170, 190), anchor="rm")
                    x -= c.text_width(assist, 13) + 4
                c.text((x, cy), name + extra, 17, anchor="rm")
            else:  # 원정: 가운데에서 오른쪽으로  [23' ⚽  이름 (PK)  도움 X]
                c.text((424, cy), g["min"], 15, fill=(170, 180, 200), anchor="lm")
                bx = 424 + c.text_width(g["min"], 15) + 14
                _ball(c, bx, cy)
                x = bx + 14
                c.text((x, cy), name + extra, 17, anchor="lm")
                if assist:
                    c.text((x + c.text_width(name + extra, 17) + 4, cy), assist, 13,
                           fill=(160, 170, 190), anchor="lm")
    return y + n * GOAL_ROW_H + 6


def _draw_subs(c: Canvas, y: int, data: dict) -> int:
    sides = _subs_by_side(data)
    n = min(max(len(sides["home"]), len(sides["away"])), 6)
    c.rrect((20, y + 14, 800, y + 48), 10, fill=TITLE_BAR)
    c.text((410, y + 31), "교체", 19, anchor="mm")
    y += 14 + 34
    for side, x0 in (("home", 24), ("away", 424)):
        for i, sub in enumerate(sides[side][:6]):
            cy = y + i * SUB_ROW_H + SUB_ROW_H / 2 + 3
            c.text((x0, cy), sub.get("minute") or "", 14, fill=(170, 180, 200), anchor="lm")
            x = x0 + 46
            c.poly([(x, cy + 4), (x + 10, cy + 4), (x + 5, cy - 5)], fill=(60, 200, 110))
            name_in = _surname(sub.get("in") or "", 11)
            c.text((x + 15, cy), name_in, 15, anchor="lm")
            x2 = x + 15 + c.text_width(name_in, 15) + 12
            c.poly([(x2, cy - 4), (x2 + 10, cy - 4), (x2 + 5, cy + 5)], fill=(235, 70, 70))
            c.text((x2 + 15, cy), _surname(sub.get("out") or "", 11), 15, fill=(200, 208, 225), anchor="lm")
    return y + n * SUB_ROW_H + 6


def render_scoreboard(data: dict, logos: dict[str, bytes] | None = None,
                      photos: dict[int, bytes] | None = None,
                      nats: dict[int, str] | None = None,
                      flags: dict[str, bytes] | None = None) -> bytes:
    logos = logos or {}
    home, away = data["teams"]["home"], data["teams"]["away"]
    goals = data.get("goals") or {}
    lineups = {l["team"]["id"]: l for l in (data.get("lineups") or [])}
    hl, al = lineups.get(home["id"]), lineups.get(away["id"])

    def team_color(lineup, default):
        return _hex(((lineup or {}).get("team", {}).get("colors") or {}).get("player", {}).get("primary"), default)

    hcol, acol = team_color(hl, HOME_DEFAULT), team_color(al, AWAY_DEFAULT)
    if _dist(hcol, acol) < 90:  # 두 팀 색이 비슷하면 원정 색을 바꾼다
        acol = (30, 70, 160) if _dist(hcol, (30, 70, 160)) > 90 else (235, 235, 235)

    short = data["fixture"]["status"].get("short")
    pre_match = short in PRE
    c = Canvas(W, H + _extras_height(data, pre_match), BG)

    # ── 상단 스코어바 ──
    c.rrect((20, 24, 420, 104), 14, fill=hcol)
    c.rrect((400, 24, 800, 104), 14, fill=acol)
    hname, aname = ko_team(home["name"]), ko_team(away["name"])
    c.logo(logos.get(home.get("logo")), 62, 64, 56, hname)
    c.logo(logos.get(away.get("logo")), 758, 64, 56, aname)
    c.text((100, 64), hname, _fit(c, hname, 190, 30, 16), fill=_text_on(hcol), anchor="lm")
    c.text((720, 64), aname, _fit(c, aname, 190, 30, 16), fill=_text_on(acol), anchor="rm")

    c.rrect((300, 14, 520, 136), 18, fill=PURPLE, outline=(255, 255, 255, 50), width=2)
    hs = "-" if goals.get("home") is None else str(goals["home"])
    as_ = "-" if goals.get("away") is None else str(goals["away"])
    c.text((352, 56), hs, 58, anchor="mm")
    c.text((468, 56), as_, 58, anchor="mm")
    c.logo(logos.get(data["league"].get("logo")), 410, 56, 40)
    c.line([(330, 97), (490, 97)], fill=(255, 255, 255, 40), width=1)

    stext = status_text(data, precise=True)
    tsize = _fit(c, stext, 190, 22, 14)
    tw = c.text_width(stext, tsize)
    tx = 410
    if short in LIVE:
        c.circle(tx - tw / 2 - 10, 117, 5, fill=(255, 70, 70))
        tx += 8
    c.text((tx, 117), stext, tsize, anchor="mm")

    # ── 포메이션 보드 ──
    marks, scorers = _events(data)
    pre = short in PRE
    _draw_team_board(c, 20, home, hl, hcol, marks, scorers["home"], pre, photos, nats, flags)
    _draw_team_board(c, 420, away, al, acol, marks, scorers["away"], pre, photos, nats, flags)

    y = H
    if not pre and (scorers["home"] or scorers["away"]):
        y = _draw_goals(c, y, scorers)
    if not pre and _stat_rows(data):
        y = _draw_stats(c, y, data, hcol, acol)
    if not pre and any(_subs_by_side(data).values()):
        y = _draw_subs(c, y, data)

    buf = io.BytesIO()
    c.result().save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ───────────────────────── 순위표 ─────────────────────────
def _note_ko(desc: str | None) -> str:
    d = (desc or "").lower()
    for key, ko in (("round of 16", "16강 직행"), ("playoff", "플레이오프 진출"), ("eliminat", "탈락"),
                    ("champions league", "챔피언스리그"), ("europa", "유로파리그"),
                    ("conference", "컨퍼런스리그"), ("relegat", "강등")):
        if key in d:
            return ko
    return desc or ""


def render_standings(title: str, rows: list[dict], logos: dict[str, bytes] | None = None) -> bytes:
    """순위표 이미지. rows 는 espn.ESPNSource.standings() 결과."""
    logos = logos or {}
    n = len(rows)
    row_h = 34 if n <= 24 else 30
    legend = []
    for r in rows:  # 같은 설명은 한 번만 (순서 유지)
        note = r.get("note") or {}
        if note.get("description") and all(note["description"] != d for _, d in legend):
            legend.append((_hex(note.get("color"), (160, 160, 160)), note["description"]))
    top = 78 + 36
    h = top + n * row_h + 14 + (-(-len(legend) // 3)) * 26 + 16
    c = Canvas(W, h, BG)

    c.rrect((20, 16, 800, 66), 12, fill=PURPLE, outline=(255, 255, 255, 50), width=2)
    c.text((410, 41), title, 24, anchor="mm")

    c.rrect((20, 78, 800, 112), 8, fill=TITLE_BAR)
    cols = {"gp": 520, "w": 574, "d": 628, "l": 682, "gd": 748, "pts": 796}
    heads = {"gp": "경기", "w": "승", "d": "무", "l": "패", "gd": "득실", "pts": "승점"}
    c.text((52, 95), "#", 15, fill=(200, 208, 225), anchor="mm")
    c.text((118, 95), "팀", 15, fill=(200, 208, 225), anchor="lm")
    for k, x in cols.items():
        c.text((x, 95), heads[k], 15, fill=(200, 208, 225), anchor="rm")

    for i, r in enumerate(rows):
        y = top + i * row_h
        if i % 2 == 0:
            c.rect((20, y, 800, y + row_h), fill=(255, 255, 255, 10))
        note = r.get("note") or {}
        if note.get("color"):
            c.rect((20, y + 2, 26, y + row_h - 2), fill=_hex(note["color"], (160, 160, 160)))
        cy = y + row_h / 2
        c.text((52, cy), str(r["rank"]), 17, anchor="mm")
        c.logo(logos.get(r["team"].get("logo")), 92, cy, row_h - 10, ko_team(r["team"]["name"] or ""))
        name = ko_team(r["team"]["name"] or "")
        c.text((118, cy), name, _fit(c, name, 330, 18, 13), anchor="lm")
        gd = r["gd"]
        vals = {"gp": str(r["gp"]), "w": str(r["w"]), "d": str(r["d"]), "l": str(r["l"]),
                "gd": f"+{gd}" if gd > 0 else str(gd), "pts": str(r["pts"])}
        for k, x in cols.items():
            c.text((x, cy), vals[k], 17 if k != "pts" else 19, fill=(255, 255, 255) if k == "pts" else (225, 230, 240),
                   anchor="rm")

    ly = top + n * row_h + 14
    for i, (color, desc) in enumerate(legend):
        lx, lyy = 26 + (i % 3) * 262, ly + (i // 3) * 26 + 12
        c.circle(lx + 6, lyy, 6, fill=color)
        c.text((lx + 18, lyy), _note_ko(desc), 14, fill=(200, 208, 225), anchor="lm")

    buf = io.BytesIO()
    c.result().save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ───────────────────────── 경기 일정 목록 ─────────────────────────
WEEKDAY_KO = "월화수목금토일"


def render_matchlist(title: str, rows: list[dict], logos: dict[str, bytes] | None = None) -> bytes:
    """/오늘경기 이미지. rows 는 upcoming_fixtures() 결과(킥오프 시각순)."""
    from names_ko import LEAGUE_SHORT_KO

    logos = logos or {}
    row_h, head_h = 54, 38
    groups: list[tuple] = []  # (날짜, [row, ...])
    for r in rows:
        day = datetime.fromtimestamp(r["fixture"]["timestamp"], KST).date()
        if not groups or groups[-1][0] != day:
            groups.append((day, []))
        groups[-1][1].append(r)
    h = 16 + 50 + 14 + sum(head_h + 6 + len(g) * row_h + 10 for _, g in groups) + 10
    c = Canvas(W, h, BG)
    c.rrect((20, 16, 800, 66), 12, fill=PURPLE, outline=(255, 255, 255, 50), width=2)
    c.text((410, 41), title, 24, anchor="mm")

    y = 80
    for day, items in groups:
        c.rrect((20, y, 800, y + head_h), 10, fill=TITLE_BAR)
        c.text((36, y + head_h / 2), f"{day.month}월 {day.day}일 ({WEEKDAY_KO[day.weekday()]})", 18, anchor="lm")
        y += head_h + 6
        for i, r in enumerate(items):
            cy = y + row_h / 2
            short = r["fixture"]["status"].get("short")
            live = short in LIVE
            if live:
                c.rrect((20, y, 800, y + row_h - 4), 10, fill=(220, 50, 50, 28))
            elif i % 2 == 0:
                c.rrect((20, y, 800, y + row_h - 4), 10, fill=(255, 255, 255, 10))
            ts = datetime.fromtimestamp(r["fixture"]["timestamp"], KST)
            c.text((70, cy - 8), f"{ts:%H:%M}", 19, anchor="mm")
            c.text((70, cy + 13), LEAGUE_SHORT_KO.get(r["league"]["id"], r["league"]["name"]), 13,
                   fill=(160, 170, 195), anchor="mm")

            home, away = r["teams"]["home"], r["teams"]["away"]
            hn, an = ko_team(home["name"]), ko_team(away["name"])
            c.text((330, cy), hn, _fit(c, hn, 200, 20, 13), anchor="rm")
            c.logo(logos.get(home.get("logo")), 358, cy, 34, hn)
            c.logo(logos.get(away.get("logo")), 462, cy, 34, an)
            c.text((490, cy), an, _fit(c, an, 200, 20, 13), anchor="lm")

            goals = r.get("goals") or {}
            if short in PRE or goals.get("home") is None:
                c.text((410, cy), "vs", 18, fill=(150, 160, 188), anchor="mm")
            else:
                c.text((410, cy), f"{goals['home']} - {goals['away']}", 24, anchor="mm")

            if short not in PRE:
                label = status_text(r)
                if short in ("FT", "AET", "PEN"):
                    label = "종료"
                if live:
                    tw = c.text_width(label, 16)
                    c.circle(796 - tw - 12, cy, 5, fill=(255, 70, 70))
                    c.text((796, cy), label, 16, anchor="rm")
                else:
                    c.text((796, cy), label, 15, fill=(180, 190, 210), anchor="rm")
            y += row_h
        y += 10

    buf = io.BytesIO()
    c.result().save(buf, "PNG", optimize=True)
    return buf.getvalue()
