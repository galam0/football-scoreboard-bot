"""팀·리그 한글 이름 매핑.

API-Football은 영문 이름을 돌려주므로 여기서 한글 약칭으로 바꿉니다.
매핑에 없는 팀은 영문 이름을 그대로 보여주니, 필요하면 아래 TEAMS에 한 줄씩 추가하세요.
키는 소문자 + 악센트 제거 + 'fc/cf' 같은 군더더기 제거 후의 이름입니다.
"""
from __future__ import annotations

import re
import unicodedata

LEAGUE_KO = {
    140: "라리가",
    2: "챔피언스리그",
}
LEAGUE_SHORT_KO = {
    140: "라리가",
    2: "챔스",
}


def _norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().replace("&", "and")
    s = re.sub(r"[.\-_/]", " ", s)
    s = re.sub(r"\b(fc|cf|afc|sc|ud|cd|rcd|sd|ca|ssc|as|sk|fk|bk|if)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_RAW_TEAMS = {
    # 라리가
    "Real Madrid": "레알 마드리드",
    "Barcelona": "바르셀로나",
    "Atletico Madrid": "아틀레티코",
    "Athletic Club": "빌바오",
    "Villarreal": "비야레알",
    "Real Sociedad": "소시에다드",
    "Real Betis": "베티스",
    "Sevilla": "세비야",
    "Valencia": "발렌시아",
    "Girona": "지로나",
    "Celta Vigo": "셀타 비고",
    "Osasuna": "오사수나",
    "Getafe": "헤타페",
    "Rayo Vallecano": "라요",
    "Mallorca": "마요르카",
    "Alaves": "알라베스",
    "Espanyol": "에스파뇰",
    "Las Palmas": "라스팔마스",
    "Leganes": "레가네스",
    "Valladolid": "바야돌리드",
    "Elche": "엘체",
    "Levante": "레반테",
    "Real Oviedo": "오비에도",
    "Deportivo La Coruna": "데포르티보",
    "Racing Santander": "라싱 산탄데르",
    "Almeria": "알메리아",
    "Cadiz": "카디스",
    "Granada": "그라나다",
    "Malaga": "말라가",
    "Sporting Gijon": "스포르팅 히혼",
    "Real Zaragoza": "사라고사",
    "Eibar": "에이바르",
    # 잉글랜드
    "Manchester United": "맨유",
    "Manchester City": "맨시티",
    "Liverpool": "리버풀",
    "Arsenal": "아스널",
    "Chelsea": "첼시",
    "Tottenham": "토트넘",
    "Newcastle": "뉴캐슬",
    "Aston Villa": "아스톤 빌라",
    # 독일
    "Bayern Munich": "뮌헨",
    "Borussia Dortmund": "도르트문트",
    "Bayer Leverkusen": "레버쿠젠",
    "RB Leipzig": "라이프치히",
    "Eintracht Frankfurt": "프랑크푸르트",
    "VfB Stuttgart": "슈투트가르트",
    # 프랑스
    "Paris Saint Germain": "PSG",
    "Marseille": "마르세유",
    "Monaco": "모나코",
    "Lille": "릴",
    "Lyon": "리옹",
    # 이탈리아
    "Inter": "인터 밀란",
    "AC Milan": "밀란",
    "Juventus": "유벤투스",
    "Napoli": "나폴리",
    "Atalanta": "아탈란타",
    "AS Roma": "로마",
    "Lazio": "라치오",
    "Fiorentina": "피오렌티나",
    # 그 외 유럽
    "Benfica": "벤피카",
    "FC Porto": "포르투",
    "Sporting CP": "스포르팅 CP",
    "Ajax": "아약스",
    "PSV Eindhoven": "PSV",
    "Feyenoord": "페예노르트",
    "Celtic": "셀틱",
    "Rangers": "레인저스",
    "Club Brugge KV": "브뤼헤",
    "Galatasaray": "갈라타사라이",
    "Fenerbahce": "페네르바체",
    "Besiktas": "베식타시",
    "Olympiakos Piraeus": "올림피아코스",
    "Shakhtar Donetsk": "샤흐타르",
    "Dinamo Zagreb": "디나모 자그레브",
    "Red Bull Salzburg": "잘츠부르크",
    "Young Boys": "영보이스",
    "Slavia Praha": "슬라비아 프라하",
    "Sparta Praha": "스파르타 프라하",
    "Union St. Gilloise": "위니옹 SG",
    "Qarabag": "카라바흐",
    "Pafos": "파포스",
    "Kairat Almaty": "카이라트",
    "Bodo/Glimt": "보되/글림트",
    "FC Copenhagen": "코펜하겐",
    "Sturm Graz": "슈투름 그라츠",
}

TEAMS = {_norm(k): v for k, v in _RAW_TEAMS.items()}


def ko_team(name: str) -> str:
    """영문 팀명을 한글 약칭으로. 매핑이 없으면 원문 그대로."""
    return TEAMS.get(_norm(name), name)
