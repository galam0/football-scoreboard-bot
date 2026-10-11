"""API 키 없이 스코어보드 이미지를 미리 보는 스크립트.

    python preview.py            # 경기 중(48분) 예시(가짜 사진·국기 포함) → preview.png
    python preview.py nophoto    # 사진 없이 → preview_nophoto.png
    python preview.py noflag     # 국기 없이 → preview_noflag.png
    python preview.py pre        # 라인업 발표 전 예시 → preview_pre.png

선수 이름·이벤트는 모두 가짜 데이터입니다.
"""
from __future__ import annotations

import io
import sys
import time

from PIL import Image, ImageDraw, ImageFont

from render import FONT_CANDIDATES, render_scoreboard


def fake_logo(letter: str, color: tuple[int, int, int]) -> bytes:
    im = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((6, 6, 194, 194), fill=color, outline=(255, 255, 255), width=8)
    for path, idx in FONT_CANDIDATES:
        if path.exists():
            f = ImageFont.truetype(str(path), 110, index=idx)
            d.text((100, 100), letter, font=f, fill=(255, 255, 255), anchor="mm")
            break
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def xi(names: list[str], grids: list[str], first_id: int, start_no: int = 1):
    nums = [1, 2, 4, 5, 3, 8, 6, 10, 7, 9, 11]
    pos = ["G", "D", "D", "D", "D", "M", "M", "M", "F", "F", "F"]
    return [
        {"player": {"id": first_id + i, "name": n, "number": nums[i], "pos": pos[i], "grid": g}}
        for i, (n, g) in enumerate(zip(names, grids))
    ]


HOME_GRIDS = ["1:1", "2:1", "2:2", "2:3", "2:4", "3:1", "3:2", "3:3", "4:1", "4:2", "4:3"]
AWAY_GRIDS = ["1:1", "2:1", "2:2", "2:3", "2:4", "3:1", "3:2", "4:1", "4:2", "4:3", "5:1"]


def mock(pre: bool = False) -> dict:
    home = {"id": 1, "name": "Barcelona", "logo": "home.png"}
    away = {"id": 2, "name": "Real Madrid", "logo": "away.png"}
    data = {
        "fixture": {
            "id": 1, "timestamp": int(time.time()) + (3 * 3600 if pre else -48 * 60),
            "status": {"short": "NS" if pre else "2H", "long": "", "elapsed": None if pre else 48, "extra": None},
            "periods": {"first": None if pre else int(time.time()) - 68 * 60, "second": None if pre else int(time.time()) - 200},
        },
        "league": {"id": 140, "name": "La Liga", "logo": "league.png"},
        "teams": {"home": home, "away": away},
        "goals": {"home": None if pre else 1, "away": None if pre else 0},
        "score": {"penalty": {"home": None, "away": None}},
        "events": [], "lineups": [],
    }
    if pre:
        return data
    data["lineups"] = [
        {
            "team": {"id": 1, "name": "Barcelona",
                     "colors": {"player": {"primary": "a50044", "number": "ffffff", "border": "004d98"},
                                "goalkeeper": {"primary": "f0c808", "number": "000000", "border": "f0c808"}}},
            "formation": "4-3-3",
            "startXI": xi(["M. Keller", "J. Ortiz", "R. Navarro", "P. Duarte", "A. Vidal",
                           "L. Castillo", "D. Romero", "F. Herrera", "S. Mendes", "T. Lindgren", "K. Okafor"],
                          HOME_GRIDS, 100),
        },
        {
            "team": {"id": 2, "name": "Real Madrid",
                     "colors": {"player": {"primary": "ffffff", "number": "1a1a1a", "border": "ffffff"},
                                "goalkeeper": {"primary": "2d9cdb", "number": "ffffff", "border": "2d9cdb"}}},
            "formation": "4-2-3-1",
            "startXI": xi(["B. Costa", "N. Fuentes", "E. Soler", "H. Marin", "C. Vega",
                           "I. Ramos", "G. Blanco", "V. Ibanez", "O. Santana", "Z. Moreau", "W. Delgado"],
                          AWAY_GRIDS, 200),
        },
    ]
    data["events"] = [
        {"time": {"elapsed": 23, "extra": None}, "team": {"id": 1}, "player": {"id": 110, "name": "K. Okafor"},
         "type": "Goal", "detail": "Normal Goal", "comments": None},
        {"time": {"elapsed": 31, "extra": None}, "team": {"id": 2}, "player": {"id": 203, "name": "H. Marin"},
         "type": "Card", "detail": "Yellow Card", "comments": "Foul"},
        {"time": {"elapsed": 40, "extra": None}, "team": {"id": 1}, "player": {"id": 105, "name": "L. Castillo"},
         "type": "Card", "detail": "Red Card", "comments": "Foul"},
    ]
    return data


def fake_photo(pid: int) -> bytes:
    """사진 자리 표시용 가짜 증명사진 (머리+어깨 실루엣)."""
    tones = [(224, 172, 140), (198, 134, 100), (141, 85, 60), (240, 200, 170)]
    shirts = [(60, 90, 160), (170, 60, 60), (70, 130, 90), (90, 90, 100)]
    bgs = [(210, 220, 235), (235, 225, 210), (215, 235, 220), (225, 215, 235)]
    im = Image.new("RGBA", (300, 300), bgs[pid % 4] + (255,))
    d = ImageDraw.Draw(im)
    d.ellipse((40, 205, 260, 400), fill=shirts[pid % 4])
    d.ellipse((95, 55, 205, 190), fill=tones[pid % 4])
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


FAKE_COUNTRIES = {  # 가짜 국기: 색 띠 3개 (실제 국기가 아님)
    "Spain": ((170, 21, 27), (241, 191, 0), (170, 21, 27)),
    "Brazil": ((0, 156, 59), (255, 223, 0), (0, 39, 118)),
    "France": ((0, 35, 149), (255, 255, 255), (237, 41, 57)),
    "Argentina": ((116, 172, 223), (255, 255, 255), (116, 172, 223)),
}


def fake_flag(colors) -> bytes:
    im = Image.new("RGB", (80, 53))
    d = ImageDraw.Draw(im)
    for i, col in enumerate(colors):
        d.rectangle((0, i * 18, 80, i * 18 + 18), fill=col)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


if __name__ == "__main__":
    args = set(sys.argv[1:])
    pre = "pre" in args
    logos = {
        "home.png": fake_logo("B", (165, 0, 68)),
        "away.png": fake_logo("R", (120, 120, 140)),
        "league.png": fake_logo("L", (230, 80, 40)),
    }
    data = mock(pre)
    photos = {} if "nophoto" in args else {
        p["player"]["id"]: fake_photo(p["player"]["id"])
        for lineup in data["lineups"] for p in lineup["startXI"]
    }
    names = list(FAKE_COUNTRIES)
    nats = {} if "noflag" in args else {
        p["player"]["id"]: names[p["player"]["id"] % len(names)]
        for lineup in data["lineups"] for p in lineup["startXI"]
    }
    flags = {n: fake_flag(c) for n, c in FAKE_COUNTRIES.items()}
    out = ("preview_pre.png" if pre else "preview_nophoto.png" if "nophoto" in args
           else "preview_noflag.png" if "noflag" in args else "preview.png")
    with open(out, "wb") as fh:
        fh.write(render_scoreboard(data, logos, photos, nats, flags))
    print("saved", out)
