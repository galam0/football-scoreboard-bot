"""ESPN 비공식 API에 라인업·포메이션이 있는지 확인하는 점검 스크립트 (표준 라이브러리만 사용).

    python espn_probe.py              # 오늘(UTC)·어제 라리가(esp.1)·챔스(uefa.champions) 경기 확인
    python espn_probe.py 401882853    # 특정 경기 id(esp.1)만 자세히

결과를 그대로 복사해서 보내 주세요.
"""
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def probe(league, event_id):
    d = get(f"{BASE}/{league}/summary?event={event_id}")
    print(f"  summary 키: {sorted(d.keys())}")
    rosters = d.get("rosters") or []
    print(f"  rosters 팀 수: {len(rosters)}")
    for r in rosters:
        team = (r.get("team") or {}).get("displayName")
        players = r.get("roster") or []
        starters = [p for p in players if p.get("starter")]
        sample = starters[0] if starters else (players[0] if players else {})
        ath = sample.get("athlete") or {}
        print(f"   - {team}: formation={r.get('formation')!r}, 선수 {len(players)}명, 선발 {len(starters)}명")
        print(f"     샘플 키: {sorted(sample.keys())}")
        print(f"     formationPlace={sample.get('formationPlace')!r}, 이름={ath.get('displayName')!r}, "
              f"등번호={sample.get('jersey')!r}, 사진={'있음' if ath.get('headshot') else '없음'}, "
              f"국가/플래그={ath.get('flag') or ath.get('citizenship') or ath.get('birthPlace')}")
    key = d.get("keyEvents") or d.get("plays") or []
    print(f"  keyEvents/plays: {len(key)}건")
    comp = ((d.get("header") or {}).get("competitions") or [{}])[0]
    st = (comp.get("status") or {}).get("type") or {}
    print(f"  상태: {st.get('state')} / {st.get('detail')} / clock={(comp.get('status') or {}).get('displayClock')}")


def main():
    if len(sys.argv) > 1:
        probe("esp.1", sys.argv[1])
        return
    today = datetime.now(timezone.utc)
    for league in ("esp.1", "uefa.champions"):
        for day in (today - timedelta(days=1), today):
            ds = day.strftime("%Y%m%d")
            try:
                sb = get(f"{BASE}/{league}/scoreboard?dates={ds}")
            except Exception as exc:  # noqa: BLE001
                print(f"[{league} {ds}] 실패: {exc}")
                continue
            events = sb.get("events") or []
            print(f"[{league} {ds}] 경기 {len(events)}건")
            for ev in events:
                st = ev["status"]["type"]
                print(f" * {ev['id']} {ev['name']} | {st['state']} {st['detail']} | clock={ev['status'].get('displayClock')}")
                if st["state"] in ("in", "post"):
                    try:
                        probe(league, ev["id"])
                    except Exception as exc:  # noqa: BLE001
                        print(f"  summary 실패: {exc}")
                    break  # 리그·날짜당 한 경기만 자세히


if __name__ == "__main__":
    main()
