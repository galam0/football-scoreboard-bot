"""ESPN 비공식 API 데이터 소스.

API 키가 필요 없고 호출 한도가 없다. 응답을 API-Football 응답과 같은 모양으로 바꿔서
render.py / bot.py가 데이터 소스와 상관없이 그대로 동작하게 한다.

- 일정·스코어·경기 시계(초): /scoreboard
- 라인업(포메이션)·이벤트(골·카드): /summary
- 선수 국적·국기: /teams/{id}/roster  (선수 사진은 ESPN이 거의 주지 않아서 지원하지 않는다)

비공식·미문서 API라서 응답 형식이 예고 없이 바뀔 수 있다.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import aiohttp

from api import APIError

log = logging.getLogger("espn")

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
KST = ZoneInfo("Asia/Seoul")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; scoreboard-bot)"}

# ESPN 리그 슬러그 → (API-Football 리그 id, 이름). names_ko.py 가 API-Football id를 쓰기 때문에 맞춰 준다.
LEAGUES = {
    "esp.1": (140, "La Liga"),
    "uefa.champions": (2, "UEFA Champions League"),
}

# ESPN 상태 이름 → render.py 가 쓰는 API-Football 식 상태 코드
STATUS_MAP = {
    "STATUS_SCHEDULED": "NS",
    "STATUS_FIRST_HALF": "1H",
    "STATUS_HALFTIME": "HT",
    "STATUS_SECOND_HALF": "2H",
    "STATUS_FULL_TIME": "FT",
    "STATUS_FINAL": "FT",
    "STATUS_FINAL_AET": "AET",
    "STATUS_FINAL_PEN": "PEN",
    "STATUS_END_OF_REGULATION": "BT",
    "STATUS_END_OF_EXTRATIME": "BT",
    "STATUS_EXTRA_TIME": "ET",
    "STATUS_FIRST_HALF_EXTRA_TIME": "ET",
    "STATUS_SECOND_HALF_EXTRA_TIME": "ET",
    "STATUS_HALFTIME_ET": "BT",
    "STATUS_SHOOTOUT": "P",
    "STATUS_POSTPONED": "PST",
    "STATUS_CANCELED": "CANC",
    "STATUS_CANCELLED": "CANC",
    "STATUS_ABANDONED": "ABD",
    "STATUS_SUSPENDED": "SUSP",
    "STATUS_DELAYED": "INT",
    "STATUS_RAIN_DELAY": "INT",
}

# 포지션 약어 → 앞뒤 순서(0=골키퍼 … 4=공격수). 줄(수비/미드/공격)을 나누는 기준이다.
DEPTH = {
    "G": 0, "GK": 0,
    "CD": 1, "CB": 1, "SW": 1, "D": 1, "RB": 1, "LB": 1,
    "RWB": 1.5, "LWB": 1.5, "WB": 1.5,
    "DM": 1.8, "CDM": 1.8,
    "CM": 2, "M": 2, "LM": 2, "RM": 2,
    "AM": 3, "CAM": 3,
    "LW": 3.5, "RW": 3.5,
    "CF": 4, "F": 4, "ST": 4, "SS": 3.8,
}
WIDE = {"LB", "LWB", "LM", "LW", "RB", "RWB", "RM", "RW"}


def _minutes(display: str | None) -> tuple[int | None, int | None]:
    """"45'+2'" → (45, 2),  "37'" → (37, None)"""
    m = re.match(r"\s*(\d+)'?(?:\s*\+\s*(\d+)'?)?", display or "")
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2)) if m.group(2) else None


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_ts(text: str | None) -> int:
    if not text:
        return 0
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _x_score(abbr: str) -> float:
    """좌우 위치 점수 (왼쪽 < 가운데 < 오른쪽). 'CM-L' 같은 접미사와 'LB' 같은 약어를 모두 본다."""
    base, _, side = abbr.partition("-")
    wide = base in WIDE
    if side == "L" or (wide and base.startswith("L")):
        return -2 if wide else -1
    if side == "R" or (wide and base.startswith("R")):
        return 2 if wide else 1
    return 0


def _depth(pos: dict) -> float:
    abbr = (pos.get("abbreviation") or "").upper()
    base = abbr.split("-")[0]
    if base in DEPTH:
        return DEPTH[base]
    name = (pos.get("name") or "").lower()
    if "keeper" in name:
        return 0
    if "defen" in name or "back" in name:
        return 1
    if "forward" in name or "striker" in name:
        return 4
    return 2


def _grid(starters: list[dict], formation: str) -> dict[int, str]:
    """선발 선수를 포메이션 줄(4-2-3-1 → 1·4·2·3·1)에 나눠 'row:col' 을 만든다. 맞지 않으면 빈 dict."""
    counts = [int(n) for n in (formation or "").split("-") if n.isdigit()]
    if len(starters) != 11 or sum(counts) != 10:
        return {}
    gk = [p for p in starters if _depth(p["position"]) == 0]
    field = [p for p in starters if _depth(p["position"]) != 0]
    if len(gk) != 1 or len(field) != 10:
        return {}
    field.sort(key=lambda p: (_depth(p["position"]), _int(p.get("formationPlace")) or 99))
    grid = {gk[0]["_id"]: "1:1"}
    i = 0
    for row, n in enumerate(counts, start=2):
        line = sorted(field[i:i + n], key=lambda p: (_x_score((p["position"].get("abbreviation") or "").upper()),
                                                     _int(p.get("formationPlace")) or 99))
        for col, p in enumerate(line, start=1):
            grid[p["_id"]] = f"{row}:{col}"
        i += n
    return grid


class ESPNSource:
    """FootballAPI 와 같은 공개 메서드를 가진 ESPN 데이터 소스."""

    remaining_today = None  # ESPN은 호출 한도가 없다

    def __init__(self):
        self._session: aiohttp.ClientSession | None = None
        self._cache: dict[str, tuple[float, object]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._slug: dict[int, str] = {}  # 경기 id → 리그 슬러그
        self._date: dict[int, str] = {}  # 경기 id → 킥오프 날짜(UTC, YYYYMMDD)
        self._nat: dict[int, str] = {}  # 선수 id → 국적
        self._flag_url: dict[str, str] = {}  # 국가명 → 국기 이미지 주소
        self._team_loaded: dict[int, float] = {}
        self._images: dict[str, bytes] = {}
        self._image_miss: set[str] = set()

    async def start(self):
        self._session = aiohttp.ClientSession(headers=HEADERS)

    async def close(self):
        if self._session:
            await self._session.close()

    # ── HTTP + 캐시 ──
    async def _get(self, url: str, ttl: float) -> dict:
        hit = self._cache.get(url)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        lock = self._locks.setdefault(url, asyncio.Lock())
        async with lock:
            hit = self._cache.get(url)
            if hit and time.monotonic() - hit[0] < ttl:
                return hit[1]
            assert self._session is not None, "start()를 먼저 호출하세요"
            try:
                async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        log.warning("ESPN %s → HTTP %s", url, resp.status)
                        raise APIError(f"ESPN HTTP {resp.status}")
                    body = await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                raise APIError(f"네트워크 오류: {exc}") from exc
            self._cache[url] = (time.monotonic(), body)
            log.info("ESPN %s", url.replace(BASE, ""))
            return body

    # ── 변환 ──
    def _row(self, slug: str, ev: dict, fetched_at: float) -> dict:
        league_id, league_name = LEAGUES[slug]
        comp = ev["competitions"][0]
        st = ev.get("status") or comp.get("status") or {}
        stype = st.get("type") or {}
        short = STATUS_MAP.get(stype.get("name"))
        if short is None:
            short = {"pre": "NS", "in": "LIVE", "post": "FT"}.get(stype.get("state"), "NS")
        elapsed, extra = _minutes(st.get("displayClock")) if short not in ("NS", "HT", "FT") else (None, None)
        ts = _parse_ts(ev.get("date") or comp.get("date"))
        sides = {c["homeAway"]: c for c in comp["competitors"]}

        def team(side: str) -> dict:
            t = sides[side]["team"]
            logo = t.get("logo") or ((t.get("logos") or [{}])[0].get("href"))
            return {"id": int(t["id"]), "name": t.get("displayName") or t.get("name"), "logo": logo}

        score = {s: _int(sides[s].get("score")) for s in ("home", "away")}
        eid = int(ev["id"])
        self._slug[eid] = slug
        if ts:
            self._date[eid] = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y%m%d")
        fixture = {"id": eid, "timestamp": ts,
                   "status": {"short": short, "long": stype.get("description") or "", "elapsed": elapsed,
                              "extra": extra}}
        if short in ("1H", "2H", "ET") and st.get("clock") is not None:
            fixture["clock"] = float(st["clock"])  # 경기 시계(초)
            fixture["clock_at"] = fetched_at  # 위 값을 받은 시각 (이후 경과 시간은 렌더링 때 더한다)
        return {
            "fixture": fixture,
            "league": {"id": league_id, "name": league_name, "logo": self._league_logo(slug), "season": None},
            "teams": {"home": team("home"), "away": team("away")},
            "goals": score,
            "score": {"penalty": {"home": None, "away": None}},
            "events": [], "lineups": [],
        }

    @staticmethod
    def _league_logo(slug: str) -> str | None:
        return {"esp.1": "https://a.espncdn.com/i/leaguelogos/soccer/500/15.png",
                "uefa.champions": "https://a.espncdn.com/i/leaguelogos/soccer/500/2.png"}.get(slug)

    async def _scoreboard(self, slug: str, start: datetime, end: datetime, ttl: float) -> list[dict]:
        url = f"{BASE}/{slug}/scoreboard?dates={start:%Y%m%d}-{end:%Y%m%d}&limit=100"
        body = await self._get(url, ttl)
        now = time.time()
        return [self._row(slug, ev, now) for ev in body.get("events") or []]

    async def upcoming_fixtures(self, ttl: float = 1800) -> list[dict]:
        """한국 시간 기준 오늘·내일의 라리가/챔피언스리그 경기."""
        today = datetime.now(KST).date()
        lo = datetime.combine(today, datetime.min.time(), KST)
        hi = lo + timedelta(days=2)
        # ESPN 날짜 구분이 미국 시간 기준이라 앞뒤로 하루씩 넉넉히 받아서 한국 시간으로 거른다
        start, end = lo.astimezone(timezone.utc) - timedelta(days=1), hi.astimezone(timezone.utc) + timedelta(days=1)
        found: dict[int, dict] = {}
        for slug in LEAGUES:
            try:
                rows = await self._scoreboard(slug, start, end, ttl)
            except APIError:
                if slug == "esp.1":
                    raise
                continue  # 챔스 조회만 실패하면 라리가만이라도 보여준다
            for row in rows:
                if lo.timestamp() <= row["fixture"]["timestamp"] < hi.timestamp():
                    found[row["fixture"]["id"]] = row
        return sorted(found.values(), key=lambda r: r["fixture"]["timestamp"])

    async def fixture_detail(self, fixture_id: int, ttl: float = 20) -> dict:
        slug = self._slug.get(fixture_id)
        if slug is None:
            await self.upcoming_fixtures()
            slug = self._slug.get(fixture_id)
        if slug is None:
            raise APIError("경기를 찾을 수 없어요")

        day = datetime.strptime(self._date[fixture_id], "%Y%m%d")
        rows = await self._scoreboard(slug, day - timedelta(days=1), day + timedelta(days=1), ttl)
        row = next((r for r in rows if r["fixture"]["id"] == fixture_id), None)
        if row is None:
            raise APIError("경기를 찾을 수 없어요")

        summary = await self._get(f"{BASE}/{slug}/summary?event={fixture_id}", ttl)
        self._fill_lineups(row, summary)
        self._fill_events(row, summary)
        return row

    def _fill_lineups(self, row: dict, summary: dict):
        lineups = []
        for r in summary.get("rosters") or []:
            players = r.get("roster") or []
            starters = []
            for p in players:
                if not p.get("starter"):
                    continue
                ath = p.get("athlete") or {}
                pid = _int(ath.get("id"))
                if pid is None:
                    continue
                starters.append({**p, "_id": pid, "_name": ath.get("displayName") or ath.get("fullName") or "",
                                 "position": p.get("position") or {}})
            if not starters:
                continue
            formation = r.get("formation") or ""
            grid = _grid(starters, formation)
            team = r.get("team") or {}
            uni = r.get("uniform") or {}
            main = (uni.get("color") or "").lstrip("#")
            alt = (uni.get("alternateColor") or "").lstrip("#")
            colors = {"goalkeeper": {"primary": "f0c808", "number": "000000", "border": "f0c808"}}
            if main:
                colors["player"] = {"primary": main, "number": alt or "ffffff", "border": alt or main}
            lineups.append({
                "team": {"id": int(team["id"]), "name": team.get("displayName"), "colors": colors},
                "formation": formation,
                "startXI": [{"player": {
                    "id": p["_id"], "name": p["_name"], "number": _int(p.get("jersey")),
                    "pos": "G" if _depth(p["position"]) == 0 else (p["position"].get("abbreviation") or ""),
                    "grid": grid.get(p["_id"]),
                }} for p in starters],
            })
        row["lineups"] = lineups

    def _fill_events(self, row: dict, summary: dict):
        events = []
        for ev in summary.get("keyEvents") or []:
            etype = ((ev.get("type") or {}).get("type") or "").lower()
            text = ((ev.get("type") or {}).get("text") or "").lower()
            person = ((ev.get("participants") or [{}])[0].get("athlete")) or {}
            team_id = _int((ev.get("team") or {}).get("id"))
            elapsed, extra = _minutes((ev.get("clock") or {}).get("displayValue"))
            base = {"time": {"elapsed": elapsed, "extra": extra}, "team": {"id": team_id},
                    "player": {"id": _int(person.get("id")), "name": person.get("displayName")}}
            if ev.get("scoringPlay"):
                detail = "Own Goal" if "own" in etype or "own" in text else \
                    "Penalty" if "penalty" in etype or "penalty" in text else "Normal Goal"
                events.append({**base, "type": "Goal", "detail": detail,
                               "comments": "Penalty Shootout" if ev.get("shootout") else None})
            elif "card" in etype or "card" in text:
                red = "red" in etype or "red" in text
                events.append({**base, "type": "Card", "detail": "Red Card" if red else "Yellow Card",
                               "comments": None})
        row["events"] = events

    # ── 이미지: 로고, 국기 ──
    async def _image(self, url: str | None) -> bytes | None:
        if not url:
            return None
        if url in self._images:
            return self._images[url]
        if url in self._image_miss or self._session is None:
            return None
        try:
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    self._images[url] = await resp.read()
                    return self._images[url]
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass
        self._image_miss.add(url)
        log.warning("이미지를 받지 못했어요: %s", url)
        return None

    async def logos_for(self, data: dict) -> dict[str, bytes]:
        urls = {data["teams"]["home"].get("logo"), data["teams"]["away"].get("logo"), data["league"].get("logo")}
        got = await asyncio.gather(*(self._image(u) for u in urls))
        return {u: b for u, b in zip(urls, got) if u and b}

    async def photos_for(self, data: dict) -> dict[int, bytes]:
        return {}  # ESPN은 라리가 선수 사진을 거의 제공하지 않는다

    async def nationalities_for(self, data: dict) -> dict[int, str]:
        """선발 선수 국적. 팀 선수단(/teams/{id}/roster)을 팀당 한 번 받아 6시간 동안 쓴다."""
        slug = self._slug.get(data["fixture"]["id"], "esp.1")
        for lineup in data.get("lineups") or []:
            tid = lineup["team"]["id"]
            if time.monotonic() - self._team_loaded.get(tid, -1e9) < 6 * 3600:
                continue
            try:
                body = await self._get(f"{BASE}/{slug}/teams/{tid}/roster", 6 * 3600)
            except APIError as exc:
                log.warning("선수단을 불러오지 못했어요: %s", exc)
                continue
            self._team_loaded[tid] = time.monotonic()
            for a in body.get("athletes") or []:
                pid, country = _int(a.get("id")), a.get("citizenship")
                if pid is not None and country:
                    self._nat[pid] = country
                    href = (a.get("flag") or {}).get("href")
                    if href:
                        self._flag_url[country] = href
        wanted = {p["player"]["id"] for l in data.get("lineups") or [] for p in l["startXI"]}
        return {pid: self._nat[pid] for pid in wanted if pid in self._nat}

    async def flags_for(self, countries: set[str]) -> dict[str, bytes]:
        names = [c for c in countries if c in self._flag_url]
        got = await asyncio.gather(*(self._image(self._flag_url[c]) for c in names))
        return {c: b for c, b in zip(names, got) if b}
