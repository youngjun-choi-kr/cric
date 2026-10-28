# 다음 작업 (Mission Studio 이후)

마감: 2026-10-31 02:00 KST (내부 목표 10/30 18:00). 최종 Devpost Submit은 팀이 직접 누른다.

## 0. 완료
- [x] 근접 3D 무대 MVP: 격납고(데이터로 만든 로켓·실제 비율·X-ray·수정 시 단이 늘어남), 발사 근접 뷰(발사대·화염·Max Q·단/페어링 분리·중계 HUD·카메라 4종), 궤도 사출, 글로벌 뷰 전환
- [x] 영상 v2 러프컷 재제작 (`data/film/studio_cut.mp4`, 2:57)
- [x] 3차 검토(docs/review-round3.md) 반영 (10/28): live data 워크플로 안정화, Mission control(분사 명령 → CRIC 검사 → 승인 → 해시 체인 기록), 미션 시계·지상 궤적, 촬영 예약, 수명·5년 규정·디오빗, 저장·공유 링크, 진행 막대, 리플레이에 분사 포함
- [x] 영상 v4 (10/29): 한 번에 녹음한 내레이션에 맞춘 편집, 2인칭("you"), 서비스의 한국어 예시 메모 → 영어, "Go / no-go" → "Launch poll", 위성 이름 CUBE-1. 공개 리플레이 재녹화 첫 시도는 미션 디렉터(Ultra)가 HOLD를 내려 녹화가 중단됨(같은 입력의 다음 실행은 GO); 한 번 더 녹화해 그 결과를 씀
- [x] 영상 v3 재녹화: Mission control 장면 추가, 3분 이내. 재촬영 1회(첫 테이크에서 미션 시계가 분사 시각을 지나 입력칸에 음수가 보임; 시계를 투입 시각으로 되돌려 기본값 3시간으로 다시 찍음)

## 1. 데모 영상 (가장 먼저)
- [ ] `data/film/studio_cut.mp4` (v2) 검수: 화면 숫자와 자막·내레이션 일치, 배속 표시, 자막이 HUD 토스트와 겹치는 샷 정리
- [ ] 근접 장면을 화면 녹화 대신 프레임 단위 오프라인 렌더(60fps)로 뽑아 화질·부드러움 개선 (Stretch)
- [ ] 내레이션 녹음: 대본은 `data/film/studio_vo.txt` (샷별 시작 시각 포함). 임시 TTS 버전으로 길이를 먼저 확인
- [ ] 배경 음악, 최종 색·자막 정리, 3분 이내 확인 (Devpost 규정 재확인)
- [ ] 기존 CRIC 관제 컷(`rough_cut.mp4`)과 합칠지, Mission Studio 단독으로 갈지 결정
- [ ] 업로드(YouTube 비공개 링크 등) 후 Devpost에 링크

## 2. Devpost 제출 문구
- [ ] 소개를 "로켓 → 발사 창 → 운영" 3단계 이야기로 갱신 (README의 Mission Studio 절 기반)
- [ ] 모델 역할 표: Nano(목표 궤도 해석), VL(사양서), Super(설명·역할 투표·관제 판단), Ultra(미션 디렉터·브리핑)
- [ ] 정직한 한계 문단 (LIMITATIONS.md의 Mission Studio 절 요약)
- [ ] 트랙 선택(Best Apps and Agents 권장)과 팀원 표기
- [ ] 스크린샷 3~4장 (1단계 실패→수정, 히트맵+LCOLA, Go/No-Go, 궤도 투입 후 관제)

## 3. 정적 데모(GitHub Pages)에서도 Studio 보이기
- [ ] 지금은 서버가 없으면 "로컬 서버 필요" 안내만 나온다
- [ ] 녹화된 한 번의 실행(JIMIN-1)을 JSON으로 저장해 재생하는 리플레이 모드 (콘솔의 Replay와 같은 방식)

## 4. Nebius에서 실제로 돌리기
- [ ] `cric launch-dispersion`을 Nebius Serverless Job으로 한 번 실행하고 결과·시간을 README에 기록 (지금은 로컬 8코어만)
- [ ] 저장소 Secrets 등록은 직접: `bash scripts/set_repo_secrets.sh` (일일 live 업데이트용)

## 5. Studio 다듬기
- [ ] API 엔드포인트 통합 테스트 (simulate / lcola / gonogo / deploy를 서버 경유로)
- [ ] API에서 과거 발사 시각 거부 (UI만 막고 있음)
- [ ] 다른 사이트 시간을 고르면 자동 재비행하는데, 그 사이트에서 실패할 때 안내 문구 개선
- [ ] 모바일 폭 레이아웃 점검, 거부(Reject) 시 사유 입력 UI를 prompt 대신 입력칸으로
- [ ] Kp 예보가 없는 3일 이후 시간대 표시(지금은 날씨만으로 판정) 범례에 명시

## 6. 문서
- [ ] ARCHITECTURE.md에 Mission Studio 절 (데이터 흐름, 모델 역할, 가드)
- [ ] `docs/how-it-works.svg`에 1~3단계 추가
- [ ] FEEDBACK.md에 Open-Meteo / Token Factory 사용 소감 추가
