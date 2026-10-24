# API 키 설정 (팀원용)

키는 **각자 본인 계정으로** 발급합니다. 크레딧이 계정마다 따로 나옵니다.

## 1. Nebius Token Factory 키
1. https://tokenfactory.nebius.com 가입 및 로그인
2. 오른쪽 위 **Get API key** → 키 생성 → 바로 복사 (한 번만 보임)
3. 크레딧 받기 (Token Factory 가입과 같은 이메일로)
   - 해커톤 $25: https://nebius.com/promo-code 양식, 활성화 코드 `NEBIUS-DEVPOST-GLOBAL26`
   - Builder Program $25: https://dev.nebius.com/builders 가입

## 2. Tavily 키
1. https://app.tavily.com 가입 (Google/GitHub 로그인 가능)
2. 대시보드의 API Key 복사 (`tvly-`로 시작, 무료 월 1,000 크레딧)

## 3. `.env` 파일에 저장
프로젝트 폴더에서:
```bash
cp .env.example .env
```
`.env`를 열어 `=` 뒤에 붙여 넣고 저장 (따옴표 없이):
```
NEBIUS_API_KEY=여기에_Nebius_키
TAVILY_API_KEY=tvly-여기에_Tavily_키
```
Windows는 `copy .env.example .env`

## 4. 확인
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd spike && ../.venv/bin/python 00_list_models.py
```
`Configured models` 세 줄이 모두 `[OK]`이면 끝.

## 주의
- 키를 채팅, 코드, 스크린샷에 올리지 마세요. `.env`는 `.gitignore`에 있어 GitHub에 올라가지 않습니다.
- 키가 노출되면 발급 사이트에서 즉시 삭제하고 새로 만드세요.
