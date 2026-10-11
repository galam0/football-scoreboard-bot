"""ESPN 응답 원본을 파일로 저장한다 (봇 연동 구현에 필요한 구조 확인용, 표준 라이브러리만 사용).

    python espn_dump.py

같은 폴더에 espn_dump.json 이 생겨요. 그 파일을 채팅에 첨부해 주세요.
(진행 중이거나 막 끝난 라리가 경기를 골라요. 없으면 어제 경기를 써요.)
"""
import json
import traceback
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
DROP = ("news", "videos", "odds", "pickcenter", "standings", "seasonseries", "lastFiveGames", "broadcasts", "leaders")


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def main():
    out = {}
    now = datetime.now(timezone.utc)
    chosen = None
    for day in (now, now - timedelta(days=1)):
        sb = get(f"{BASE}/esp.1/scoreboard?dates={day:%Y%m%d}")
        out[f"scoreboard_{day:%Y%m%d}"] = sb
        events = sb.get("events") or []
        for ev in events:
            if ev["status"]["type"]["state"] in ("in", "post") and ev["status"]["type"].get("name") != "STATUS_ABANDONED":
                chosen = chosen or ev
        if chosen:
            break
    if not chosen:
        print("진행 중이거나 끝난 경기를 찾지 못했어요.")
        return
    print("선택한 경기:", chosen["name"], chosen["status"]["type"]["detail"])
    summ = get(f"{BASE}/esp.1/summary?event={chosen['id']}")
    for k in DROP:
        summ.pop(k, None)
    if isinstance(summ.get("commentary"), list):
        summ["commentary"] = summ["commentary"][:5]
    out["summary"] = summ
    teams = {}
    for comp in chosen["competitions"][0]["competitors"]:
        tid = comp["team"]["id"]
        try:
            teams[comp["team"]["displayName"]] = get(f"{BASE}/esp.1/teams/{tid}/roster")
        except Exception:  # noqa: BLE001
            teams[comp["team"]["displayName"]] = traceback.format_exc()
    out["team_rosters"] = teams
    with open("espn_dump.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("저장했어요: espn_dump.json  → 이 파일을 채팅에 첨부해 주세요.")


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        print(traceback.format_exc())
    input("Enter 를 누르면 닫혀요...")
