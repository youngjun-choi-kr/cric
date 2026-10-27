# 다음 작업 (Mission Studio 이후)

마감: 2026-10-31 02:00 KST (내부 목표 10/30 18:00). 최종 Devpost Submit은 팀이 직접 누른다.

## 1. 데모 영상 (가장 먼저)
- [ ] `data/film/studio_cut.mp4` 러프컷 검수: 화면 숫자와 자막·내레이션이 일치하는지, 속도 표시(×배속)가 자연스러운지
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
