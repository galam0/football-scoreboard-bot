"""라리가·챔피언스리그 스코어보드 디스코드 봇.

/오늘경기      오늘·내일(한국 시간) 경기 일정과 스코어
/스코어보드    경기를 골라 명령어를 쓴 시점의 스코어·시간·포메이션 이미지를 올림
               (AUTO_UPDATE=1이면 경기가 끝날 때까지 같은 메시지를 자동으로 갱신)
/스코어중지    이 채널의 자동 갱신 중지 (AUTO_UPDATE=1일 때만 의미 있음)
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import sys
import time
from datetime import datetime

import discord
from aiohttp import web
from discord import app_commands
from dotenv import load_dotenv

from api import APIError, FootballAPI, KST
from names_ko import LEAGUE_SHORT_KO, ko_team
from render import (LIVE, PRE, is_finished, kickoff_dt, render_scoreboard,
                    state_signature, status_text)

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")

TOKEN = os.getenv("DISCORD_TOKEN")
API_KEY = os.getenv("API_FOOTBALL_KEY")
GUILD_ID = os.getenv("GUILD_ID")  # 선택: 테스트 서버 ID를 넣으면 명령어가 즉시 반영됨
POLL_SECONDS = max(int(os.getenv("POLL_SECONDS", "90")), 20)  # 경기 중 갱신 주기(초)
SHOW_PHOTOS = os.getenv("SHOW_PHOTOS", "1") != "0"  # 0이면 선수 사진 없이 번호 원으로만 표시
SHOW_FLAGS = os.getenv("SHOW_FLAGS", "1") != "0"  # 0이면 이름 옆 국기(국적)를 표시하지 않음
AUTO_UPDATE = os.getenv("AUTO_UPDATE", "0") == "1"  # 1이면 경기가 끝날 때까지 자동 갱신 (기본: 명령어를 쓸 때만)
MAX_ERRORS = 8


def fixture_line(row: dict) -> str:
    dt = datetime.fromtimestamp(row["fixture"]["timestamp"], KST)
    league = LEAGUE_SHORT_KO.get(row["league"]["id"], row["league"]["name"])
    home, away = ko_team(row["teams"]["home"]["name"]), ko_team(row["teams"]["away"]["name"])
    return f"{dt:%m/%d %H:%M} {league} · {home} vs {away}"


def next_delay(data: dict) -> float:
    """상태별 다음 갱신까지 대기 시간. 무료 플랜 호출 한도를 아끼려고 경기 전·휴식에는 길게 쉰다."""
    short = data["fixture"]["status"].get("short")
    if short in PRE:
        ko = kickoff_dt(data)
        secs = ko.timestamp() - time.time() if ko else 1800
        if secs > 3600:
            return min(max(secs - 3600, 300), 1800)
        if secs <= 120:
            return POLL_SECONDS
        if not data.get("lineups"):
            return 300  # 라인업은 보통 킥오프 1시간 전쯤 올라온다
        return min(max(secs, POLL_SECONDS), 900)
    if short in ("HT", "BT", "SUSP", "INT"):
        return max(POLL_SECONDS, 120)
    return POLL_SECONDS


def message_text(data: dict) -> str:
    dot = "🔴" if data["fixture"]["status"].get("short") in LIVE else "⚪"
    return f"{dot} <t:{int(time.time())}:T> 기준"


class ScoreBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
        self.api = FootballAPI(API_KEY)
        self.trackers: dict[tuple[int, int], asyncio.Task] = {}

    async def setup_hook(self):
        await self.api.start()
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        if os.getenv("PORT"):  # Render 웹 서비스처럼 포트 응답이 필요한 환경용
            await self._start_keepalive(int(os.environ["PORT"]))
        asyncio.create_task(self._warm_cache())

    async def _warm_cache(self):
        try:
            await self.api.upcoming_fixtures()
        except APIError as exc:
            log.warning("경기 목록 미리 불러오기 실패: %s", exc)

    async def _start_keepalive(self, port: int):
        app = web.Application()
        for path in ("/", "/health"):  # Render 헬스체크·핑 용도
            app.router.add_get(path, lambda _: web.Response(text="ok"))
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", port).start()

    async def close(self):
        for task in self.trackers.values():
            task.cancel()
        await self.api.close()
        await super().close()

    async def on_ready(self):
        log.info("로그인: %s", self.user)

    async def build_image(self, data: dict) -> bytes:
        logos = await self.api.logos_for(data)
        photos = await self.api.photos_for(data) if SHOW_PHOTOS else {}
        nats = await self.api.nationalities_for(data) if SHOW_FLAGS else {}
        flags = await self.api.flags_for(set(nats.values())) if nats else {}
        return await asyncio.to_thread(render_scoreboard, data, logos, photos, nats, flags)

    # ── 자동 갱신 ──
    def start_tracker(self, message: discord.Message, fixture_id: int, data: dict):
        key = (message.channel.id, fixture_id)
        task = asyncio.create_task(self._track(message, fixture_id, data))
        self.trackers[key] = task
        task.add_done_callback(lambda _t, k=key: self.trackers.pop(k, None))

    async def _track(self, message: discord.Message, fixture_id: int, data: dict):
        last_sig, errors, first = None, 0, True
        while True:
            try:
                if not first:
                    data = await self.api.fixture_detail(fixture_id, ttl=max(POLL_SECONDS - 5, 5))
                first = False
                errors = 0
                sig = state_signature(data)
                if sig != last_sig:
                    png = await self.build_image(data)
                    await message.edit(content=message_text(data),
                                       attachments=[discord.File(io.BytesIO(png), "scoreboard.png")])
                    last_sig = sig
                if is_finished(data):
                    return
                ko = kickoff_dt(data)
                if ko and time.time() > ko.timestamp() + 4 * 3600:
                    return  # 킥오프 4시간 뒤에도 안 끝났으면 중단
                delay = next_delay(data)
            except (discord.NotFound, discord.Forbidden):
                return  # 메시지가 지워졌거나 권한이 없어짐
            except APIError as exc:
                errors += 1
                log.warning("갱신 실패(%d/%d): %s", errors, MAX_ERRORS, exc)
                if errors >= MAX_ERRORS:
                    try:
                        await message.edit(content="⚠️ 데이터를 불러오지 못해 자동 갱신을 멈췄어요. `/스코어보드`로 다시 올려 주세요.")
                    except discord.HTTPException:
                        pass
                    return
                delay = 120
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("스코어보드 갱신 중 예외")
                errors += 1
                if errors >= MAX_ERRORS:
                    return
                delay = 120
            await asyncio.sleep(delay)


bot = ScoreBot()

_usage: dict[str, int] = {}
_usage_day = None


def log_use(interaction: discord.Interaction, command: str, detail: str = "") -> None:
    """명령어 사용을 로그에 남긴다. 횟수는 한국 시간 기준 하루 단위(재시작하면 0부터)."""
    global _usage_day
    today_kst = datetime.now(KST).date()
    if _usage_day != today_kst:
        _usage_day = today_kst
        _usage.clear()
    _usage[command] = _usage.get(command, 0) + 1
    where = interaction.guild.name if interaction.guild else "DM"
    channel = getattr(interaction.channel, "name", None) or "-"
    log.info("명령어 /%s%s | %s(%s) | %s #%s | 오늘 /%s %d회, 전체 %d회", command,
             f" [{detail}]" if detail else "", interaction.user, interaction.user.id, where, channel,
             command, _usage[command], sum(_usage.values()))


async def match_autocomplete(interaction: discord.Interaction, current: str):
    try:
        rows = await bot.api.upcoming_fixtures()
    except APIError:
        return []
    choices = []
    for row in rows:
        label = fixture_line(row)
        if row["fixture"]["status"].get("short") in LIVE:
            label = "🔴 " + label
        if current and current.lower() not in label.lower():
            continue
        choices.append(app_commands.Choice(name=label[:100], value=str(row["fixture"]["id"])))
    return choices[:25]


@bot.tree.command(name="오늘경기", description="라리가·챔피언스리그 오늘·내일(한국 시간) 경기 일정")
async def today(interaction: discord.Interaction):
    log_use(interaction, "오늘경기")
    await interaction.response.defer()
    try:
        rows = await bot.api.upcoming_fixtures()
    except APIError as exc:
        await interaction.followup.send(f"경기 일정을 불러오지 못했어요: {exc}")
        return
    if not rows:
        await interaction.followup.send("오늘·내일 예정된 라리가/챔피언스리그 경기가 없어요.")
        return

    lines, last_day = [], None
    for row in rows:
        dt = datetime.fromtimestamp(row["fixture"]["timestamp"], KST)
        if dt.date() != last_day:
            last_day = dt.date()
            lines.append(f"\n**{dt:%m월 %d일}**")
        league = LEAGUE_SHORT_KO.get(row["league"]["id"], row["league"]["name"])
        home, away = ko_team(row["teams"]["home"]["name"]), ko_team(row["teams"]["away"]["name"])
        short = row["fixture"]["status"].get("short")
        goals = row.get("goals") or {}
        if short in PRE:
            tail = ""
        elif short in LIVE:
            tail = f" 🔴 **{goals.get('home', 0)}-{goals.get('away', 0)}** ({status_text(row)})"
        else:
            tail = f" · {goals.get('home', '-')}-{goals.get('away', '-')} ({status_text(row)})"
        lines.append(f"`{dt:%H:%M}` {league} · {home} vs {away}{tail}")

    embed = discord.Embed(title="⚽ 라리가 · 챔피언스리그 경기", description="\n".join(lines).strip(),
                          color=0x3B2A80)
    embed.set_footer(text="/스코어보드 로 스코어·포메이션을 볼 수 있어요")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="스코어보드", description="지금 시점의 스코어·경기 시간·포메이션을 이미지로 보여줘요")
@app_commands.rename(match="경기")
@app_commands.describe(match="목록에서 경기를 골라 주세요")
@app_commands.autocomplete(match=match_autocomplete)
async def scoreboard(interaction: discord.Interaction, match: str):
    if not match.isdigit():
        await interaction.response.send_message("입력창에 나오는 목록에서 경기를 선택해 주세요.", ephemeral=True)
        return
    fixture_id = int(match)
    log_use(interaction, "스코어보드", f"경기 {fixture_id}")

    if not AUTO_UPDATE:
        # 명령어를 쓸 때마다 그 시점의 최신 데이터로 새로 그려서 올린다
        await interaction.response.defer()
        try:
            data = await bot.api.fixture_detail(fixture_id, ttl=15)
            log.info("스코어보드 조회: %s", fixture_line(data))
            png = await bot.build_image(data)
        except APIError as exc:
            await interaction.followup.send(f"데이터를 불러오지 못했어요: {exc}")
            return
        await interaction.followup.send(content=message_text(data),
                                        file=discord.File(io.BytesIO(png), "scoreboard.png"))
        return

    channel = interaction.channel
    if channel is None:
        await interaction.response.send_message("이 채널에서는 사용할 수 없어요.", ephemeral=True)
        return
    if (channel.id, fixture_id) in bot.trackers:
        await interaction.response.send_message("이 채널에서 이미 이 경기를 갱신 중이에요.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    try:
        data = await bot.api.fixture_detail(fixture_id, ttl=15)
        png = await bot.build_image(data)
        # 인터랙션 웹훅은 15분 뒤 만료되므로, 오래 수정할 수 있게 일반 메시지로 올린다
        message = await channel.send(content=message_text(data),
                                     file=discord.File(io.BytesIO(png), "scoreboard.png"))
    except APIError as exc:
        await interaction.followup.send(f"데이터를 불러오지 못했어요: {exc}", ephemeral=True)
        return
    except discord.Forbidden:
        await interaction.followup.send("이 채널에 이미지를 올릴 권한이 없어요. (메시지 보내기, 파일 첨부 권한 필요)",
                                        ephemeral=True)
        return

    if is_finished(data):
        await interaction.followup.send("끝난 경기라 한 번만 올렸어요.", ephemeral=True)
        return
    bot.start_tracker(message, fixture_id, data)
    await interaction.followup.send("올렸어요! 경기가 끝날 때까지 자동으로 갱신돼요.", ephemeral=True)


@bot.tree.command(name="스코어중지", description="이 채널의 스코어보드 자동 갱신을 멈춰요")
async def stop(interaction: discord.Interaction):
    log_use(interaction, "스코어중지")
    cid = interaction.channel_id
    keys = [k for k in bot.trackers if k[0] == cid]
    for k in keys:
        bot.trackers[k].cancel()
    await interaction.response.send_message(
        f"자동 갱신 {len(keys)}건을 멈췄어요." if keys else "이 채널에서 갱신 중인 스코어보드가 없어요.",
        ephemeral=True)


if __name__ == "__main__":
    if not TOKEN or not API_KEY:
        sys.exit("DISCORD_TOKEN 과 API_FOOTBALL_KEY 환경 변수를 설정해 주세요. (.env.example 참고)")
    bot.run(TOKEN, log_handler=None)
