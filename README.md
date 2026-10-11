# 라리가 · 챔피언스리그 스코어보드 디스코드 봇

경기를 고르면 **스코어 · 경기 시간 · 홈/원정 포메이션**이 한 장의 이미지로 올라와요.
기본은 **명령어를 쓴 그 시점의 데이터**로 그려서 올리는 방식이고, 원하면 경기가 끝날 때까지 자동 갱신으로 바꿀 수 있어요.
(라리가, UEFA 챔피언스리그만 지원)

- 상단: 팀 로고·이름, 스코어, 경기 시간(`48'`, `하프타임`, `경기 종료`, 킥오프 전엔 `23:55 킥오프`)
  - 팀 로고는 API가 주는 공식 로고를 받아서 넣어요. (`preview.py`의 로고는 가짜 자리 표시용이에요)
- 하단: 팀별 포메이션 보드(선수 사진·등번호·국기+성(姓)·옐로/레드카드·득점 표시), 득점자 목록
- 선수 사진은 API-Football 이미지 서버(`media.api-sports.io`)에서 받아 원형으로 넣어요. 사진이 없거나 못 받은 선수는 번호 원으로 대신 표시하고,
  `.env`의 `SHOW_PHOTOS=0`으로 끌 수 있어요. 사진 요청이 API 호출 한도에 포함되는지는 직접 확인하지 못했으니, 첫 경기 후 대시보드의 사용량을 한번 확인해 보세요.
- 이름 옆 국기는 선수 국적(`/players`)을 받아 [flagcdn.com](https://flagcdn.com) 국기 이미지로 표시해요. 국적은 한 번 받으면 `.cache/`에 저장해서
  다시 받지 않아요. 처음 보는 팀은 국적 조회에 호출이 몇 번(팀당 1~3회) 더 들고, `SHOW_FLAGS=0`으로 끌 수 있어요.
- 라인업이 아직 안 나온 경기는 "라인업 발표 전"으로 표시되고, 발표되면 다음 조회부터 채워져요.

API 키 없이 이미지만 미리 보려면:

```bash
pip install -r requirements.txt
python preview.py             # 경기 중 예시(가짜 사진·국기 포함) → preview.png
python preview.py nophoto     # 사진 없이                         → preview_nophoto.png
python preview.py noflag      # 국기 없이                         → preview_noflag.png
python preview.py pre         # 경기 전 예시                      → preview_pre.png
```

## 명령어

| 명령어 | 설명 |
| --- | --- |
| `/오늘경기` | 한국 시간 기준 오늘·내일의 라리가/챔스 경기 일정과 스코어 |
| `/스코어보드 경기:` | 입력창에 뜨는 목록에서 경기를 골라, 지금 시점의 스코어·포메이션 이미지를 올림 (최신으로 보려면 다시 실행) |
| `/스코어중지` | 이 채널의 자동 갱신 중지 (`AUTO_UPDATE=1`일 때만 의미 있음) |

유럽 저녁 경기는 한국 시간으로 다음 날 새벽이라서, 목록은 오늘과 내일 날짜를 함께 보여줘요.

## 준비물

1. **디스코드 봇 토큰** — [Developer Portal](https://discord.com/developers/applications) → Bot → Token
   - 초대 링크 권한: Scope `bot`, `applications.commands` / 권한 `View Channels`, `Send Messages`, `Attach Files`
2. **API-Football 키** — [dashboard.api-football.com](https://dashboard.api-football.com) 가입 후 발급 (무료 플랜: 하루 100회)

## 로컬 실행

```bash
pip install -r requirements.txt
cp .env.example .env     # DISCORD_TOKEN, API_FOOTBALL_KEY 채우기
python bot.py
```

`GUILD_ID`에 테스트 서버 ID를 넣으면 슬래시 명령어가 바로 반영돼요. 비워 두면 전체 서버에 등록되고 반영까지 시간이 걸릴 수 있어요.

## API 호출량 · 플랜 (중요)

**기본(명령어 전용) 모드**는 호출이 거의 안 들어요.

- `/스코어보드` 1회 = 경기 상세 1회 호출 (`SNAPSHOT_TTL`초, 기본 60초 안에 같은 경기를 다시 부르면 캐시 사용)
- 자동완성 목록 = 오늘·내일 일정 2회 호출 (30분 캐시, 봇 시작 때 미리 불러오지 않음)
- 처음 보는 팀의 선수 국적 조회 = 팀당 1~3회, `/countries` 1회 (모두 `.cache/`에 저장)

`AUTO_UPDATE=1`(자동 갱신)을 켜면 경기 중 `POLL_SECONDS`(기본 90초)마다 1회씩 쓰고, 경기 전·하프타임에는 간격을 길게 둬요.
이 경우 하루 100회 기준으로 경기 1건 정도가 한계예요.

**무료 플랜은 선수 목록(`/players`)을 최근 시즌(예: 2022~2024)만 줘요.** 국기용 국적은 그 마지막 시즌 명단에서 찾아요.

**무료 플랜의 시즌 제한도 확인하세요.** API-Football 요금 페이지에는 무료 플랜이 "볼 수 있는 시즌이 제한된다"고만 적혀 있고
어느 시즌까지인지는 나와 있지 않아요. 무료 플랜으로는 이번 시즌 경기가 조회되지 않을 수 있고, 그러면 봇이
"Free plans do not have access to this season"처럼 플랜 제한 메시지를 보여줘요. 이 경우 유료 플랜(Pro, 월 $19, 하루 7,500회)이 필요해요.
키를 발급받으면 먼저 `/오늘경기`로 확인해 보세요.

## Render 배포

- Build Command: `pip install -r requirements.txt`
- Start Command: `python bot.py`
- Environment: `DISCORD_TOKEN`, `API_FOOTBALL_KEY` (+ 선택 `GUILD_ID`, `SHOW_PHOTOS`, `SHOW_FLAGS`, `AUTO_UPDATE`, `POLL_SECONDS`)
- `.cache/`(선수 국적)는 Render 디스크가 초기화되면 사라져서 재배포 후 첫 조회에 국적 호출이 다시 들어가요.
- **Background Worker**로 올리는 게 가장 간단해요. Web Service로 올리면 `PORT`를 감지해서 `/`에 `ok`를 응답하는
  미니 서버를 자동으로 띄워요. 무료 Web Service는 일정 시간 요청이 없으면 잠들 수 있으니, 경기 시간에 계속 돌려야 하면 유료 인스턴스를 권장해요.

## 알아둘 점

- **경기 시간은 분 단위**예요. API가 초 단위를 주지 않아서 `48:45` 대신 `48'`로 보여줘요.
- 데이터는 실시간이 아니라 API 갱신 주기(보통 수십 초~1분 정도)만큼 지연돼요.
- 이미 올린 이미지는 자동으로 바뀌지 않아요. 최신 상태가 보고 싶으면 `/스코어보드`를 다시 쓰면 돼요. (`AUTO_UPDATE=1`이면 봇 재시작 시 자동 갱신이 풀려요)
- 국적은 API의 선수 목록에서 찾아서, 이번 시즌 목록에 없는 선수는 국기가 안 나올 수 있어요.
- 팀 이름은 `names_ko.py`의 매핑으로 한글 약칭을 써요. 매핑에 없는 팀은 영문 이름으로 나오니 필요하면 한 줄씩 추가하세요.
- 선수 이름은 API가 주는 영문 표기의 성(姓)만 보여줘요.
- 이미지 크기는 820×790이라 디스코드에서 클릭하면 크게 볼 수 있어요.

## 구성

```
bot.py        디스코드 명령어 (선택: 자동 갱신 루프)
api.py        API-Football 클라이언트 (캐싱, 로고·선수 사진·국적·국기)
render.py     스코어보드 이미지 그리기 (Pillow)
names_ko.py   팀·리그 한글 이름 매핑
preview.py    API 키 없이 이미지 미리 보기 (가짜 데이터)
fonts/        한글 폰트 서브셋
```

## 라이선스 참고

`fonts/NotoSansKR-Bold-subset.otf`는 Noto Sans CJK KR Bold(SIL Open Font License 1.1)에서 필요한 글자만 추려낸 서브셋이에요.
