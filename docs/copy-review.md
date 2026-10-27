# 문구 검토: 영상 대본·자막, 서비스 화면, 첫 화면 (2026-10-28)

## 총평
- **틀린 문장 1개**: C4 대본 "at 1 kilometres a second" (1.12를 반올림하면서 숫자·복수형이 어긋남).
- **"AI가 쓴 티"가 나는 공통 패턴**
  1. 가운뎃점(·)과 화살표(→)로 사실 서너 개를 이어 붙인 자막. 스펙 시트처럼 읽히고, 8초 안에 못 읽음.
  2. 나열식 대본: "collision probability, verified burn options, a cited dossier, three votes, a validator".
  3. 셋씩 짝 맞춘 경구: "Code for what can be checked, Nemotron for what must be read, people for what must be decided."
  4. 수동태·추상어: "The fix comes from the simulator, flown again before it is shown", "a satellite that is looked after".
  5. 시스템이 자기 정직성을 계속 설명하는 메타 문장: "the fixes below come from the simulator, not the model", "code drops any rule whose quote is not in the notes…". 정직함은 좋지만 화면마다 반복되면 방어적으로 읽힘.
  6. 라벨을 그대로 읽는 대사: "On public data: monitor.", "decides again: monitor".
  7. 시각을 숫자로 읽게 함("02:49", "02:46"): 성우나 TTS가 어색하게 읽음.
- **전문용어**: 자막에 LTDN, LCOLA, SGP4, TLE, 3-DOF, ephemeris가 설명 없이 나옴. 심사위원 대부분은 우주 전문가가 아님.
- **표기 혼용**: manoeuvre/maneuver, kilometres/km, catalog/catalogued (영국식·미국식 혼용). 해커톤 기준 미국식으로 통일 권장.

## 1. 영상 대본 (현재 → 제안)
제안 대본은 샷 길이(초당 약 2.3단어)에 맞췄고, 시각은 말로 읽도록 바꿈. `{ }`는 녹화 결과에서 채우는 값.

| 샷 | 현재 | 제안 |
|---|---|---|
| J1 | Eighteen thousand objects are already in orbit. Jimin wants to add one. | Eighteen thousand objects already circle the Earth. Jimin wants to add one more. |
| J2 | This is CRIC Mission Studio. | (유지) |
| A1 | Jimin's student rocket, Garage One, stands next to real launchers, built from its own numbers. | Jimin built a small rocket, Garage One. Here it is, to scale, next to real launchers. |
| A2 | Nemotron Nano reads the mission from one sentence; code works out the orbit. Liftoff. | Jimin describes the mission in one sentence. Nemotron Nano turns it into an orbit. Liftoff. |
| A3 | Every motion comes from the flight simulation: max Q, staging, the fairing. But it is too slow. | Everything you see is the flight simulation. Max Q. Stage separation. Then the problem: it's too slow. |
| A4 | Its lowest point is too low, and it falls back. | It never gets high enough to stay up, and falls back. |
| A5 | Nemotron 3 Super explains where the speed went. The fix comes from the simulator, flown again before it is shown. | Nemotron 3 Super explains what went wrong. The simulator test-flies every fix before suggesting it. |
| A6 | A bigger first stage. | The answer: a bigger first stage. |
| A7 | This time it climbs all the way, | Second try. This time it keeps climbing, |
| A8 | and JIMIN-1 is in orbit. | and JIMIN-1 reaches orbit. |
| A9 | Five hundred scattered flights, with the forecast winds aloft, show where stage one can land: at sea. | Five hundred test flights, with the forecast winds, show where the spent stage lands. At sea. |
| B1 | When to launch? For an imaging satellite, the launch time sets the local time it passes over the ground. | Next: when to launch. For a camera satellite, the launch time decides what time of day it sees the ground. |
| B2 | Every minute of that hour is screened against the catalog: at 02:49, it would pass 2.7 kilometres from MUSAT-2. | Every minute is checked against the catalog. Launch at {two forty-nine}, and it passes {2.7} kilometres from another satellite. |
| B3 | The range notice and the customer's request, in Korean, become checked rules. Three roles vote, and Nemotron 3 Ultra calls go for 02:46. | The team's notes, even in Korean, become rules the code can check. Three officers vote. Nemotron 3 Ultra makes the call: go at {two forty-six}. |
| C1 | Launch. JIMIN-1 joins the catalog, and CRIC marks when it can photograph Seoul. | Launch. JIMIN-1 joins the catalog, and CRIC shows when it can photograph Seoul. |
| C2 | A close approach. The same CRIC pipeline takes over: collision probability, verified burn options, a cited dossier, three votes, a validator. On public data: monitor. | Days later, another satellite comes close. CRIC works out the risk, finds out who can move, and decides: {keep watching}. |
| C3 | The precise ephemeris arrives and CRIC decides again: monitor, with no propellant spent. | Better orbit data arrives. The risk drops to almost zero. No fuel spent. |
| C4 | Here is that pass: 2.5 kilometres, at 1 kilometres a second. ← **오류** | Here's that pass: {two and a half} kilometres apart. |
| C5 | And Jimin approves. | Jimin signs off. |
| J14 | From a garage rocket to a satellite that is looked after. Code for what can be checked, Nemotron for what must be read, people for what must be decided. | CRIC does the maths and the reading. The decisions stay with people. |

판단 결과는 라벨을 그대로 읽지 말고 말로 바꿔 읽음: MONITOR → "keep watching", REQUEST_PRECISE_DATA → "get better data first", COORDINATE → "talk to the other operator", ESCALATE → "a person should decide", MANEUVER → "move".

## 2. 영상 자막 (한 자막에 한 가지, 8단어 이내)
| 샷 | 현재 | 제안 |
|---|---|---|
| J1 | 18,806 tracked objects · real orbits | 18,806 objects in orbit today |
| A1 | Rockets built from their numbers, at true scale · X-ray: where each number came from | Built from its numbers, to scale |
| A2 | Nemotron Nano reads the mission · orbit computed in code | One sentence in, an orbit out |
| A3 | Every motion from the 3-DOF simulation · plume and sky are illustration | Simulated flight |
| A4 | Perigee below the ~150 km needed: it falls back | Too low to stay in orbit |
| A5 | Every fix is flown again before it is shown · Nemotron 3 Super explains | Every fix is test-flown first |
| A6 | Stage 1 propellant and thrust +30% (4,300 → 5,590 kg) | First stage: 30% bigger |
| A7 | Second flight → 502 × 543 km, 97.4° | Second try: a 500 km orbit |
| A8 | Orbit · payload deployed | In orbit. Satellite released. |
| A9 | 500 scattered flights · forecast winds aloft · land check | Where the spent stage lands |
| B1 | Outlined hours: the orbit crosses the equator at 10:30 local (LTDN) · Open-Meteo · NOAA Kp | The hours that suit a camera satellite |
| B2 | Launch hour chosen for the film after screening the real forecast and catalog · LCOLA | {02:49} liftoff: {2.7} km from another satellite |
| B3 | Notes → rules with exact quotes (Nemotron 3 Super) · votes · director: Nemotron 3 Ultra | Notes become rules. The team votes. |
| C1 | TLE from the insertion state · photo passes over Seoul marked | Photo passes over Seoul |
| C2 | Close approach: AC1-001 · the CRIC pipeline, live (time-lapse) | Close approach (sped up) |
| C3 | Precise ephemeris: decided again · Monitor · no propellant spent | Better data: risk cleared |
| C4 | The pass in slow motion · positions from SGP4 | The pass, slowed down |
| C5 | Jimin approves · logged, hash-chained · nothing is sent to a spacecraft | The owner decides. Nothing is sent to the satellite. |

빠지는 정직성 문구는 마지막 카드 한 장에 작게 모음: "Simulated flights; visuals illustrative. Launch hour picked after checking the live forecast. Orbit data: public, about 1 km accurate."

## 3. 서비스 화면 문구 (주요 항목)
| 위치 | 현재 | 제안 |
|---|---|---|
| 상단 부제 | Your rocket → your launch window → your satellite in orbit · 3-DOF ascent · Open-Meteo · NOAA SWPC · CelesTrak · NVIDIA Nemotron on Nebius | Your rocket, your launch, your satellite in orbit. (데이터 출처는 하단/정보 버튼으로) |
| 비행 중 표시 | Optimising the ascent guidance and flying it (differential evolution, parallel)… | Flying your rocket… |
| 수정안 탐색 | Searching for fixes, each one verified by re-flying the rocket (parallel)… | Looking for fixes and test-flying each one… |
| 분산 | Monte Carlo: 500 flights with thrust, Isp, mass, pitch and azimuth scatter, and the forecast winds aloft… | Flying 500 slightly different rockets… |
| 날씨 | Weather (Open-Meteo) and space weather (NOAA SWPC) for every site that can reach your orbit… | Checking the weather at every launch site… |
| 팀 투표 | Launch team voting (weather, range safety, orbital safety · Nemotron 3 Super) and mission director brief (Nemotron 3 Ultra)… | The launch team is voting… |
| 투입 | Inserting into orbit, writing its TLE, and screening it against the catalog for 72 h… | Putting JIMIN-1 in orbit and checking the next 3 days… |
| 수정안 설명 아래 | Nemotron 3 Super explains; the fixes below come from the simulator, not the model. | (ⓘ 툴팁으로) Fixes are test-flown by the simulator. |
| 메모 안내 | Nemotron 3 Super turns the notes into rules with an exact quote each; code drops any rule whose quote is not in the notes and applies the rest to every window. | Each rule must quote your notes word for word, or it's dropped. |
| Go/No-Go 안내 | Pick an hour on the map, then minutes on the collision strip (up to 4). Three launch-team roles vote … cannot be chosen. | Pick an hour, then up to 4 minutes. The team votes; a vetoed minute can't win. |
| 투입 카드 | Tracked with 18,806 catalogued objects. The TLE reproduces the simulated insertion state at epoch. | Now tracked with 18,806 other objects. |
| 버튼 | Convene the launch team | Ask the launch team |
| 단계 제목 | YOUR ROCKET / WHEN AND WHERE · next 7 days, hourly | Your rocket / When and where (next 7 days) |
| 판단 카드 | DECISION · 20 s · $0.009 / MONITOR | Decision: keep watching (20 s · $0.009) |
| 체크박스 | it has propulsion (can manoeuvre) | It can maneuver (has a thruster) |
| 투어 4단계 | Then choose when to launch (weather, orbit timing, collision screening, the launch team’s votes) and run the satellite with CRIC: passes over home, close approaches, your approval. | Next, pick when to launch. Then CRIC looks after the satellite, and you make the final calls. |

## 4. 첫 화면 (index.html)·README 첫 단락
| 현재 | 제안 |
|---|---|
| From your rocket to a satellite that is looked after. | (A) From a garage rocket to a safe orbit. (B) Launch your own satellite, then keep it out of trouble. |
| … with an AI that does the work and knows when to ask a person. | CRIC does the heavy lifting, and asks you when it matters. |
| WHAT IS REAL, WHAT IS NOT / Stated plainly | Real data / What's simulated |
| What stops it going wrong | Safety check |

## 5. 일관성 규칙 (적용 시)
- 미국식 표기: maneuver, kilometers, catalog. 숫자는 화면에선 숫자, 대본에선 말로.
- 화면 제목은 문장형 대소문자(“Your rocket”), 전부 대문자는 쓰지 않음.
- 모델 이름은 그 모델이 일하는 순간에만 한 번씩(배지로), 문장 속 반복은 빼기.
- 한 문장은 15단어 이내, 한 자막은 8단어 이내, 메타 설명은 ⓘ 툴팁으로.
