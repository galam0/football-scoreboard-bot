"""API-Football(api-sports.io) 클라이언트.

- 응답을 메모리에 캐싱해서 무료 플랜의 하루 100회 한도를 아낀다.
- 같은 요청이 동시에 들어오면 한 번만 호출한다(락).
- 선수 국적처럼 잘 안 바뀌는 값은 .cache/ 폴더에 저장해 재시작 후에도 다시 받지 않는다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp

log = logging.getLogger("api")

BASE_URL = "https://v3.football.api-sports.io"
PHOTO_URL = "https://media.api-sports.io/football/players/{}.png"
FLAG_URL = "https://flagcdn.com/w80/{}.png"  # 국기 이미지 (국가 코드는 소문자, 예: es, gb-eng)
KST = ZoneInfo("Asia/Seoul")
CACHE_FILE = Path(__file__).parent / ".cache" / "football_cache.json"
MAX_PLAYER_PAGES = 5  # 팀 선수 목록(페이지당 20명)을 최대 몇 페이지까지 볼지

# 지원 리그: 라리가(140), 챔피언스리그(2)
LEAGUE_IDS = {140, 2}


class APIError(Exception):
    pass


def _key(name: str) -> str:
    """국가명 비교용 정규화 (대소문자·공백·기호 무시)."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


class FootballAPI:
    def __init__(self, key: str):
        self._season_cap: int | None = None
        self._key = key
        self._session: aiohttp.ClientSession | None = None
        self._cache: dict[str, tuple[float, object, tuple[int, int]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._logos: dict[str, bytes] = {}
        self._photos: dict[int, bytes] = {}
        self._photo_miss: dict[int, float] = {}  # 받기 실패한 선수 → 다시 시도해도 되는 시각
        self._nat: dict[int, str] = {}  # 선수 id → 국적(국가명)
        self._codes: dict[str, str] = {}  # 정규화한 국가명 → 국가 코드
        self._flags: dict[str, bytes] = {}  # 국가 코드 → 국기 PNG
        self._flag_miss: set[str] = set()
        self.remaining_today: int | None = None
        self._load_disk()

    # ── 디스크 캐시 ──
    def _load_disk(self):
        try:
            raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            self._nat = {int(k): v for k, v in raw.get("nat", {}).items()}
            self._codes = raw.get("codes", {})
        except (OSError, ValueError):
            pass

    def _save_disk(self):
        try:
            CACHE_FILE.parent.mkdir(exist_ok=True)
            CACHE_FILE.write_text(json.dumps({"nat": self._nat, "codes": self._codes}, ensure_ascii=False),
                                  encoding="utf-8")
        except OSError as exc:
            log.warning("캐시 파일을 저장하지 못했어요: %s", exc)

    async def start(self):
        self._session = aiohttp.ClientSession()

    async def close(self):
        if self._session:
            await self._session.close()

    async def _get(self, path: str, params: dict, ttl: float, with_paging: bool = False):
        cache_key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))

        def pack(entry):
            return (entry[1], entry[2]) if with_paging else entry[1]

        hit = self._cache.get(cache_key)
        if hit and hit[0] > time.monotonic():
            return pack(hit)

        lock = self._locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            hit = self._cache.get(cache_key)
            if hit and hit[0] > time.monotonic():
                return pack(hit)
            assert self._session is not None, "start()를 먼저 호출하세요"
            try:
                async with self._session.get(
                    BASE_URL + path,
                    params=params,
                    headers={"x-apisports-key": self._key},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        raise APIError(f"HTTP {resp.status}")
                    remaining = resp.headers.get("x-ratelimit-requests-remaining")
                    if remaining and remaining.isdigit():
                        self.remaining_today = int(remaining)
                    body = await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                raise APIError(f"네트워크 오류: {exc}") from exc

            # 한도 초과·키 오류·플랜 제한 등은 HTTP 200 + errors 필드로 온다
            if body.get("errors"):
                text = str(body["errors"])
                if "plan" in text.lower():
                    text += " (무료 플랜은 볼 수 있는 시즌이 제한될 수 있어요)"
                raise APIError(text)
            data = body.get("response", [])
            paging = body.get("paging") or {}
            entry = (time.monotonic() + ttl, data, (paging.get("current", 1), paging.get("total", 1)))
            self._cache[cache_key] = entry
            log.info("API %s %s → %d건 (오늘 남은 호출: %s)", path, params, len(data), self.remaining_today)
            return pack(entry)

    async def upcoming_fixtures(self, ttl: float = 1800) -> list[dict]:
        """한국 시간 기준 오늘·내일의 라리가/챔피언스리그 경기 (2회 호출, 캐시됨).

        유럽 저녁 경기는 한국 시간으로 다음 날 새벽이라 내일 날짜까지 함께 가져온다.
        """
        today = datetime.now(KST).date()
        found: dict[int, dict] = {}
        for day in (today, today + timedelta(days=1)):
            rows = await self._get("/fixtures", {"date": day.isoformat(), "timezone": "Asia/Seoul"}, ttl)
            for row in rows:
                if row["league"]["id"] in LEAGUE_IDS:
                    found[row["fixture"]["id"]] = row
        return sorted(found.values(), key=lambda r: r["fixture"]["timestamp"])

    async def fixture_detail(self, fixture_id: int, ttl: float = 20) -> dict:
        """경기 1건의 스코어·이벤트·라인업(포메이션)을 한 번에 가져온다."""
        rows = await self._get("/fixtures", {"id": fixture_id, "timezone": "Asia/Seoul"}, ttl)
        if not rows:
            raise APIError("경기를 찾을 수 없어요")
        return rows[0]

    async def logos_for(self, data: dict) -> dict[str, bytes]:
        """팀·리그 로고 이미지를 받아 캐싱한다 (실패하면 건너뜀)."""
        urls = {data["teams"]["home"].get("logo"), data["teams"]["away"].get("logo"),
                data["league"].get("logo")}
        for url in urls:
            if not url or url in self._logos or self._session is None:
                continue
            try:
                async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        self._logos[url] = await resp.read()
            except (aiohttp.ClientError, asyncio.TimeoutError):
                log.warning("로고를 받지 못했어요: %s", url)
        return {u: self._logos[u] for u in urls if u in self._logos}

    async def photos_for(self, data: dict) -> dict[int, bytes]:
        """선발 선수 사진을 받아 캐싱한다. 받지 못한 선수는 빠지고, 렌더러가 번호 원으로 대신 그린다."""
        ids = [p["player"]["id"] for lineup in (data.get("lineups") or [])
               for p in lineup.get("startXI", []) if p["player"].get("id")]
        sem = asyncio.Semaphore(6)

        async def fetch(pid: int):
            if pid in self._photos or self._session is None:
                return
            if self._photo_miss.get(pid, 0) > time.monotonic():
                return
            async with sem:
                try:
                    async with self._session.get(PHOTO_URL.format(pid),
                                                 timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status == 200:
                            self._photos[pid] = await resp.read()
                            return
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    pass
            self._photo_miss[pid] = time.monotonic() + 1800  # 30분 동안은 다시 시도하지 않음

        await asyncio.gather(*(fetch(pid) for pid in ids))
        return {pid: self._photos[pid] for pid in ids if pid in self._photos}

    async def nationalities_for(self, data: dict) -> dict[int, str]:
        """선발 선수의 국적(국가명).

        라인업 응답에는 국적이 없어서 팀 선수 목록(/players?team=&season=)을 페이지별로 받는다.
        국적은 안 바뀌니 한 번 받으면 디스크에 저장해 다음부터는 호출하지 않는다.
        """
        season = data["league"].get("season")
        wanted: dict[int, int] = {}  # 선수 id → 팀 id
        for lineup in data.get("lineups") or []:
            for p in lineup.get("startXI", []):
                if p["player"].get("id"):
                    wanted[p["player"]["id"]] = lineup["team"]["id"]

        if self._season_cap and season and season > self._season_cap:
            season = self._season_cap  # 무료 플랜이 허용하는 마지막 시즌의 선수 목록을 쓴다
        changed = False
        for team_id in {t for pid, t in wanted.items() if pid not in self._nat}:
            need = {pid for pid, t in wanted.items() if t == team_id and pid not in self._nat}
            page = 1
            while need and season and page <= MAX_PLAYER_PAGES:
                try:
                    rows, (_, total) = await self._get(
                        "/players", {"team": team_id, "season": season, "page": page}, 3600, with_paging=True)
                except APIError as exc:
                    # "try from 2022 to 2024" 같은 안내가 오면 그 시즌으로 한 번만 다시 시도하고, 이후엔 처음부터 그 시즌을 쓴다
                    m = re.search(r"to (\d{4})", str(exc))
                    if "access to this season" in str(exc) and m and int(m.group(1)) < season:
                        self._season_cap = int(m.group(1))
                        season = self._season_cap
                        continue
                    log.warning("선수 국적을 불러오지 못했어요: %s", exc)
                    break
                for row in rows:
                    pl = row.get("player") or {}
                    if pl.get("id") and pl.get("nationality"):
                        self._nat[pl["id"]] = pl["nationality"]
                        need.discard(pl["id"])
                        changed = True
                if page >= total:
                    break
                page += 1
        if changed:
            self._save_disk()
        return {pid: self._nat[pid] for pid in wanted if pid in self._nat}

    async def flags_for(self, countries: set[str]) -> dict[str, bytes]:
        """국가명 → 국기 PNG. 국가 코드는 /countries를 한 번만 받아 저장해 둔다."""
        if not countries:
            return {}
        if not self._codes:
            try:
                rows = await self._get("/countries", {}, 86400)
            except APIError as exc:
                log.warning("국가 목록을 불러오지 못했어요: %s", exc)
                return {}
            self._codes = {_key(r["name"]): r["code"] for r in rows if r.get("code")}
            self._save_disk()

        async def fetch(code: str):
            if code in self._flags or code in self._flag_miss or self._session is None:
                return
            try:
                async with self._session.get(FLAG_URL.format(code),
                                             timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        self._flags[code] = await resp.read()
                        return
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass
            self._flag_miss.add(code)
            log.warning("국기를 받지 못했어요: %s", code)

        codes = {name: self._codes[_key(name)].lower() for name in countries if _key(name) in self._codes}
        await asyncio.gather(*(fetch(c) for c in set(codes.values())))
        return {name: self._flags[code] for name, code in codes.items() if code in self._flags}
