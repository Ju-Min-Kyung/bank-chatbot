# GMS RAG 및 화면 평가

평가 시각(UTC): 2026-10-23T05:48:01.125032+00:00

실제 `gpt-6-astra` GMS 호출로 7개 대표 질문을 평가했습니다. 본문 전체 결과는 Git 제외 경로인 `storage/evaluation/gms_results.json`에 보관하고, 이 보고서에는 검증 상태와 출처 연결을 기록합니다.

| 질문 | 상태 | 검색 근거 | 실제 인용 출처 | 지연시간(ms) |
| --- | --- | --- | --- | --- |
| risk | answered | 6 | 5 | 27940.23 |
| liquidity | answered | 6 | 3 | 39630.98 |
| comparison | answered | 7 | 2 | 51733.94 |
| current_return | insufficient_evidence | 6 | 0 | 18672.62 |
| principal_required | no_candidates | 0 | 0 | 24.0 |
| unknown_redemption | no_candidates | 0 | 0 | 18.31 |
| no_pdf | answered | 1 | 1 | 27499.63 |

실제 API 호출 15회. 선택 상품별 검색어/후보 선별 및 답변 생성 요청을 포함합니다. 이 7건은 전체 품질 통계나 SLA가 아닙니다.

## 확인한 사항

- 원금보장 필수: 8,000개 모두 비보장형이므로 후보 0개. 비보장형을 대체 추천하지 않음.
- 중도환매 필요: 원문 중도해지와 구분하여 확인된 적격 후보 0개.
- 오늘 수익률: 검증된 현행 자료가 없어 확인 불가.
- 선택 상품 메타데이터 필수 포함 및 두 상품 비교에서 상품 ID별 근거 격리.
- 공식 PDF 없는 상품: 메타데이터만 사용하고 PDF 미확보 안내.
- 수집 카탈로그 반복 검색 캐시 재사용. PDF 재수집 없음.
- 인용 번호/상품 연결/원문 인용구 검증, 실제 사용한 출처만 화면 표시.
- 에이전트가 수락된 20개 주장을 제공 근거와 대조. 독립 금융 전문가 검수와는 구분.
- 원본 ZIP 해시 유지, Chroma 컬렉션 8,000/15,287건 유지.
- 단위·UI 테스트 71개 모두 통과(Windows 내부 로컬 소켓을 허용한 환경). pip check 통과.

## 화면 검증

실제 브라우저에서 상품 두 개 선택→비교표, 한 상품 선택→GMS 답변→인용 PDF 제목·공식 URL·물리 페이지를 확인했습니다.

- [상품 비교](evidence/screenshots/comparison.png)
- [실제 GMS 답변](evidence/screenshots/chat_answer.png)
- [원문 출처 패널](evidence/screenshots/chat_sources.png)

## 검증 한계

새 외부 출처의 범용 인터넷 검색은 구현하지 않았으며 기존에 수집·검증한 BNK 공식 카탈로그에서 후보를 검색합니다. PDF 연결은 23상품뿐입니다.
조건 파서는 명시적으로 지원한 조건을 대상으로 하며 모든 자연어/부정문 해석을 보증하지 않습니다. 의미검색 관련성의 넓은 질문 평가도 필요합니다.
최초 실제 평가 시 API 오류가 한 번 발생했고 재실행에서 7건을 통과했습니다. API 실패는 검색 결과 없음으로 숨기지 않습니다.
원문 요구사항 PDF의 세 도메인 선택 조건/NF 상세 대조와 F2013~F2016 심화는 남아 있습니다.
사용자가 GMS 전송을 허용했지만 원본 데이터/PDF의 이용 조건 자체를 독립 검증한 것으로 처리하지 않습니다.

증거: [실제 호출 평가](evidence/gms_rag_evaluation.json), [검토 및 테스트](evidence/rag_review.json), [합성 연결 테스트](evidence/gms_connection.json).
