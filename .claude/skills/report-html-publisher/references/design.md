# 리포트 페이지 디자인 규칙

출처: 저장소 루트 `DESIGN.md`(google-labs-code design.md 형식, 역할 토큰·스페이싱·반경·모션), 사용자 지정
Pretendard(github.com/orioncactus/pretendard, OFL), taste-skill(github.com/Leonxlnx/taste-skill, MIT)의 규칙 중
데이터 리포트에 맞는 것만 옮겼다. taste-skill의 회사 사이트용 장치(한 화면 한 섹션, 히어로 사진, 셰이더, 등장 연출)는
리포트에 맞지 않아 쓰지 않는다. taste-skill 자체는 프로젝트에 `design-taste-frontend`로 설치돼 있다(회사·제품 페이지를 만들 때 쓴다).

## 토큰 (report.css `:root`)
- 표면: basement(페이지 바탕) → default(카드) → fill(표 머리·타일·인용). 깊이는 색으로 먼저, 그림자는 카드에 s1만.
- 글자: fg / fg-muted / fg-subtle. 회색 위 회색 금지(대비 APCA Lc 75 이상을 목표).
- 브랜드 주황(#ff6600)은 머리 꼬리표·로고 점·목차 hover 선에만. 넓게 칠하지 않는다.
- 링크·포커스는 informative 파랑. 이익·상승은 빨강(--up), 손실·하락은 파랑(--down). 색만으로 알리지 않고 부호(+/−)를 남긴다.
- 다크 모드는 `prefers-color-scheme`로 팔레트를 뒤집는다(DESIGN.md의 dark-* 값).
- 간격 x 사다리(4px 단위), 반경 r2 8 · r3 12 · r4 16 · full. 카드 r4, 타일·표 r3, 입력 r2.

## 레이아웃 (반응형, 2026-11-18)
| 폭 | 페이지 | 목차 | 본문 글자 |
|---|---|---|---|
| ~479px | 화면 − 32px | 본문 위 펼침 목차, 테마 버튼은 아이콘만 | 16px |
| 480~1023px | 화면 − 여백 | 펼침 목차, 카드 여백 축소(~767px) | 16px |
| 1024~1599px | 화면 − 여백(1440에서 1310px) | 왼쪽 220px 고정 목차 | 16px~ |
| 1600~2199px | 최대 2040px | 260px, 카드 여백 확대, h2 23px | ~19px |
| 2200px~ | 2040px | 300px, h2 25px | 19px |

- 여백: `--gutter: clamp(16px, 4vw, 96px)`. 본문: `--read: clamp(16px, 0.42vw + 10px, 19px)`. 문단 `max-width: 56em`(약 56자). 표·차트·흐름도는 전체 폭을 쓴다.
- 머리: 꼬리표 → h1(clamp 28~54px) → 날짜·채널·원본 → 요약. h1은 페이지에 하나.
- 헤더는 고정이다(`scroll-padding-top`으로 목차 이동 시 제목이 가리지 않음).
- 표는 `.table-wrap` 안에서만 가로 스크롤하고, 휴대폰에서는 양옆 그림자로 더 있음을 알린다. 숫자·링크는 줄바꿈하지 않는다.
- 하이라이트 카드는 본문 위에 두고, 타일은 auto-fit 240px 격자(휴대폰 한 줄).
- 휴대폰(≤767px) 표는 `width: max-content; min-width: 100%`로 열 너비를 지키고 표 틀 안에서 스크롤한다. 열을 화면 폭에 눌러 담으면 행이 매우 길어진다.
- 휴대폰(≤479px) 막대는 이름을 막대 위 한 줄로 올린다(긴 ETF 이름이 잘리지 않게). 선 그래프 x축 글자는 하나 걸러 숨긴다(마지막은 남김).

## 차트·흐름도
- 막대(`bar_svg` → `.bars`): HTML 행이다(이름 | 막대 트랙 | 값). 값은 막대 끝에 붙고, 트랙 양옆 4.2em(휴대폰 3.9em)은 값 자리다. 최대 1280px.
- 선(`line_svg` → `.linechart`): SVG는 선만 그리고 박스에 맞춰 늘어난다(`non-scaling-stroke`). `.figure svg`의 크기 제한이 이 svg에 걸리면 선과 HTML 끝점이 어긋나므로 `.lc-plot svg`에서 풀어 둔다. 축 글자·끝점은 HTML이라 어느 폭에서도 같은 크기다. 높이는 clamp(200px, 26vw, 400px).
- 흐름도: archify가 옆 HTML로 그리고 iframe(clamp 420~640px, 휴대폰 70vh)에 담는다. `meta.animation`은 늘 none이다(taste loop-pause: 5초 넘게 움직이면 멈춤 버튼 필요).
- 차트 제목은 위에 둔다(그림 아래 설명 금지, taste 규칙).

## taste-skill에서 가져온 것
- 한 체계: 카드 한 벌, 반경 한 벌, 링크 모양 한 벌, 제목 단계는 한 단계씩.
- 평이한 문구: 제목은 무엇인지 말하고, 꾸밈말·자리표시 문구·"샘플" 같은 표시를 남기지 않는다.
- 좌우 가장자리를 맞춘다(모든 블록이 .wrap 선 위).
- 접근성: lang, title, description, 공유 메타, 본문 건너뛰기 링크(Tab 첫 번째), 보이는 포커스 링,
  컨트롤 44px 이상, reduced-motion에서는 움직임 없음.
- 움직임은 조용하게: 목록 카드 hover 2px 들림, 150ms. 스크롤에 따라 나타나는 연출·패럴랙스 없음.
- 보라·라벤더 계열을 페이지 색으로 쓰지 않는다.
- 확인은 실제 브라우저에서 1920·1440·768·390px(넓은 화면 작업이면 2560도), 라이트·다크.


## design-taste-frontend로 검수하기 (사용자 지시 2026-11-17)
HTML 템플릿·CSS를 바꾸는 디자인 작업은 `design-taste-frontend` 스킬을 불러 그 규칙(한 체계·평이한 문구·가장자리·접근성·
움직임)으로 하고, 그 스킬의 검사 스크립트로 확인한다. 리포트는 회사 사이트가 아니므로 finish gate(`gate.mjs --start`)는
켜지 않는다(켜면 모든 세션 종료 때 회사 사이트 규칙으로 막는다). 대신 미리보기 페이지에 직접 돌린다.

```bash
K=.claude/skills/design-taste-frontend
PYTHONPATH=agent:scripts .venv/bin/python scripts/report_pages.py --preview longterm-strategy <샘플.md> --out <임시>/site
node $K/scripts/consistency.mjs <임시>/site/reports/longterm-strategy/<키>.html --root <임시>/site \
  --widths 1440,1024,768,390 --profile <임시>/chrome --page service \
  --no-visuals --no-address --no-rhythm --no-tiles --no-gl-fallback --out <임시>/shots
node $K/scripts/perf.mjs <같은 html> --root <임시>/site --profile <임시>/chrome-p
```

리포트에 맞지 않아 면제하는 항목(2026-11-17 첫 검수 기준, 나머지 FAIL은 고친다):
| 검사 | 이유 |
|---|---|
| hero-fill · cta · fold-cut · no-visual · copy-headline | 회사 사이트용(첫 화면 히어로·구매 버튼·사진·카테고리 제목). 리포트는 제목·요약·본문 순서 |
| menu-toggle | 헤더의 테마 버튼을 메뉴 버튼으로 오인. 여닫는 것이 없어 aria-expanded가 틀린 값 |
| focus-visible (iframe) | 흐름도 iframe에 포커스가 오면 바깥 틀에 파란 고리가 나온다(`.diagram:focus-within`). 검사기는 iframe 자체만 본다 |
| font files | Pretendard 동적 서브셋이 한글을 작은 파일 여러 개로 나눈다(전체 약 0.3MB, font bytes는 WARN) |
| cut-text (넓은 표 칸) | 열이 많은 표는 표 틀 안에서 옆으로 스크롤하는 것이 설계다(휴대폰은 양옆 그림자). 표 틀 밖·페이지 가로 넘침이면 고친다 |
| off-grid (WARN, 본문 h2) | 넓은 화면에서 목차 칸 때문에 본문 제목이 페이지 여백과 다른 선에서 시작한다(두 단 배치) |
| section-gaps (WARN) | 하이라이트 카드와 본문 카드 사이 간격이 절 사이 간격과 다름(카드 두 개 구조) |
| share-meta (og:image) | Pages 주소가 정해진 뒤 대표 이미지를 붙인다 |

검수 결과(같은 날): 큰 화면 답답함은 1240px 폭·220px 목차·본문 78자 줄 길이로 풀었다. 테마 버튼 하나(시스템→라이트→다크)는
기기별로 기억하고 흐름도 iframe도 같은 테마로 바꾼다. 헤딩은 단계별 한 크기(h2 21px, 카드 h3 15px). 탭 대상은 44px.
글꼴은 2.3MB에서 0.3MB로 줄였다(자체 호스팅 전체 파일 제거). 막대 그래프는 HTML 행으로 그려 휴대폰에서도 잘리지 않는다.

11-18 보완(사용자 "더 넓은 화면에서 더 넓고 쾌적하게", "모바일 반응형도"):
- 폭: 고정 1240px을 화면을 따라가는 최대 2040px로 바꿨다.
- 글자: 본문 16→19px, 문단은 56em.
- 막대: 값을 막대 끝에 붙였다.
- 선 그래프: HTML 축·늘어나는 SVG 선으로 바꿨다(휴대폰 가로 스크롤 제거).
- 휴대폰: 펼침 목차, 표 스크롤 그림자를 넣었다.
- 흐름도: 애니메이션을 껐다.
- 검사: 1920·1440·768·390에서 면제 항목만 남았다.
