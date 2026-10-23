# 증권상품 근거 탐색 설계 및 14단계 추적

## 범위와 도메인 선택

도메인은 **증권 금융상품의 조건 탐색·비교**입니다. 사용자의 지시로 보험은 제외했습니다.
실제 주문·가입·법적 적합성 판정이나 실시간 수익률 조회는 제공하지 않습니다.

교육 요구사항 원문 PDF와 '도메인 선택 조건 세 가지'의 정확한 문구는 프로젝트에 없습니다.
아래는 현재 확보한 근거이며, 원문의 세 조건을 충족했다는 확정 판정으로 사용하지 않습니다.

| 확인 가능한 근거 | 실측/구현 | 증거 |
| --- | --- | --- |
| 충분한 항목 메타데이터 | 서로 다른 증권 상품 8,000건, 상품당 검색 텍스트 1개 | data_audit.md, data_preparation.md |
| 항목에 연결 가능한 외부 원문 | 공식 PDF 핵심 15개, 클래스별 23상품 연결 | external_documents.md, vector_index.md |
| 도메인 특화 탐색·비교 가능 | 위험등급·원금보장·만기·유동성·전략 비교 및 제약 검증 | recommendations.py, conditions.py, app.py |

원문 PDF를 확보하면 위 근거를 실제 세 조건에 다시 대응시켜야 합니다. 제출 시각/마감 준수 여부도 소급 주장하지 않습니다.

## 14개 실행 단계

| 단계 | 산출물/현재 상태 | 검증 또는 남은 범위 |
| --- | --- | --- |
| 1 필수·심화 검토 | requirements.md의 F2001~F2016 추적 | NF 상세 원문 미확보, 심화는 미착수 |
| 2 도메인 및 세 선택 조건 | 증권 전용; 위 표의 실측 근거 | 세 조건의 원문 대조는 대기 |
| 3 상품 1건의 텍스트 | 필드명+값; 공식명/유형/위험/원금/유동성/전략 포함 | 원문 attrs와 파싱 값을 별도 보존 |
| 4 컬렉션 설계 | financial_products, financial_documents | 아래 메타데이터 표 |
| 5 세 프롬프트 설계 | src/rag/prompts.py | JSON Schema + 로컬 출력 검증 |
| 6 상품 색인 | 8,000벡터 | 2회 실행 증가/추가 업서트 0 |
| 7 검색어 추출 | query_extractor.py | 공식명을 반드시 포함, 최대 5개 |
| 8 공식 문서 후보 검색·선별 | collect_external_docs.py, document_fetcher.py, document_selector.py | 외부 BNK 공시에서 수집한 캐시 카탈로그 한정. 임의 인터넷 전체 검색은 미구현 |
| 9 문서 별도 색인 | PDF 페이지 청크 → financial_documents | 3,389 고유 청크, 상품 관계 포함 15,287행 |
| 10 병합·중복 제거 | retriever.py, graph.py | 메타데이터 필수 포함; 상품/doc/chunk 관계별 중복 제거 |
| 11 답변/LangGraph | graph.py의 retrieve → generate | GMS Responses API 실제 호출 평가 |
| 12 실제 인용 출처 표시 | citations.py, app.py show_sources | 유효 인용·상품·원문 구절 검증 후 사용 출처만 표시 |
| 13 선택/대화/탐색/특화 화면 | app.py의 세 탭 | 상품 조합별 대화, 2~4후보, 조건 비교표 |
| 14 상태·위험 안내 | safety.py, app.py | 키 누락/색인 없음/후보 없음/API 오류/근거 부족 구분 |

## 상품 텍스트와 두 컬렉션

상품 텍스트는 정규화 단계에서 공식명, 상품유형, 원금보장, 만기, 원문 수익률,
상품 위험등급, 세금 표시, 납입 방식, 손실률, 유동성, 전략을 필드명과 함께 연결합니다.
평가 질문·추천 answer·cot1~3·소비자 레코드는 검색 근거에 넣지 않습니다.

| 컬렉션 | 단위 | 주요 메타데이터 |
| --- | --- | --- |
| financial_products | 상품 1건 = 1텍스트 = 1벡터 | product_id, product_name, category, company, product_type, source_type, source_file, source_record_hash, product_risk_grade, principal_protected |
| financial_documents | product_id:document_id:chunk_index | product_id, document_id, chunk_index, title, source_type, source_uri, source_file, source_sha256, page_number, char_start/end, document_date, collected_at, link_scope |

임베딩은 로컬 384차원 모델을 유지합니다. GMS는 세 작업 프롬프트에만 사용합니다.
PDF 페이지를 넘지 않고 1,000자/128토큰 상한을 지키며 실제 길이는 토크나이저에 따라 더 짧습니다.
공통 문서의 벡터 캐시는 공유하지만 상품별 연결을 별도 저장합니다.

## 프롬프트와 검증 경계

| 프롬프트 | 역할 | 출력 |
| --- | --- | --- |
| 1 검색어 | 공식명 포함, 질문 주제에 맞는 자료 검색어 | queries 문자열 배열, 1~5개 |
| 2 후보 선별 | 실제 후보의 제목·요약·출처로 선별 | selected_document_ids, reasons(document_id/reason) |
| 3 답변 | 제공 근거에 한정한 상품 주장/불확실성 | question_summary, claims(product_id/text/supporting_quote/citation_ids), unknowns |

선택 상품 자체의 검증된 문서는 선별 결과에서 강제로 유지합니다. 후보에 없는 ID는 오류입니다.
캐시 카탈로그에는 기존에 확인한 공식 공시만 들어가며 새 URL을 모델이 생성하거나 본문을 임의로 다운로드하지 않습니다.
캐시 키는 원문 manifest 해시·연결표 해시·상품 ID·검색어를 포함하고 재사용 때 PDF 해시도 확인합니다.

인용 검증은 실제 번호, 해당 상품 ID, 원문에 존재하는 연속 인용구를 확인합니다.
없는 번호/다른 상품/존재하지 않는 인용구가 있으면 그 문장을 제외합니다. 인용되지 않은 근거는 답변 패널에 표시하지 않습니다.
이 검증만으로 모든 문장의 사실적 함의가 입증되지는 않으므로 실제 답변 내용 검토를 병행합니다.

## 조건 탐색·흐름·대화

지원하는 정형 조건은 원금보장 필수, 상품 위험등급 범위(1이 최고위험, 6이 최저위험), 상품유형,
중도환매 가능 확인, 만기 없음/최대 기간입니다. 조건에 필요한 속성이 없으면 적격으로 취급하지 않습니다.
'중도해지 가능'을 '중도환매 가능'으로 바꾸지 않습니다. 안정적 성향만으로 등급을 단정하지 않습니다.
복수·부정형 유형이 모호하면 후보를 확정하지 않습니다. 이는 법적 적합성/적정성 판정이 아닙니다.

retrieve는 정형 조건을 먼저 검증하고 적격 ID로 검색을 제한한 뒤 필수 상품 메타데이터를 가져옵니다.
상품별 검색어 추출→카탈로그 후보→LLM 선별→상품/문서 ID 필터 검색을 수행합니다.
문서 top-k는 메타데이터와 별개이며 비교에서는 상품별 문서를 번갈아 배분합니다.
generate는 번호가 붙은 근거로 구조화 응답을 만들고 인용 검증을 거쳐 화면에 표시합니다.
오늘/현재 수익률·판매 상태·가격은 검증된 현행 데이터가 없으므로 답변하지 않습니다.

대화 기록은 정렬한 상품 ID 조합을 키로 세션 메모리에만 보관합니다.
실사용자의 질문을 파일·추적 서비스에 저장하지 않습니다. 연락처·주민번호·이메일 패턴이 있으면 전송 전에 제거를 요청합니다.

## GMS 설정과 데이터 경계

사용자가 제공한 주소 `https://gms.ssafy.io/gmsapi/api.openai.com/v1/responses`, 모델 `gpt-6-astra`를 사용합니다.
Bearer 키는 `.env`의 GMS_API_KEY 또는 환경변수 GMS_KEY에서 읽으며 로그·화면에 출력하지 않습니다.
`store=false`를 요청하고 프롬프트/오류 본문/추론 출력은 기록하지 않습니다. 이는 공급자 전체 보관 정책을 보증하는 뜻이 아닙니다.
인증 헤더가 리다이렉트로 전달되지 않도록 차단하고 지정 GMS HTTPS 주소만 허용합니다.

사용자가 이 채팅에서 GMS 전송을 명시적으로 허용했으므로 현재 로컬 `.env`의 GMS_ALLOW_SOURCE_TRANSMISSION=true를 반영했습니다.
이용 조건 자체는 독립 검증되지 않았으며 사용자 전송 허용과 구분합니다. 공개 재배포 허용으로 해석하지 않습니다.
선택 상품 정보와 필요한 PDF 발췌만 보내며 전체 ZIP·라벨·CoT·소비자 데이터를 전송하지 않습니다.

## 검증 자료와 남은 사항

- vector_index.md: 실제 벡터/원문/2회 색인 증거.
- evidence/gms_connection.json: 합성 입력을 통한 실제 GMS 구조화 응답.
- evidence/gms_rag_evaluation.json: 대표 실제 질의, 출처 연결, 필터, 지연시간.
- evidence/screenshots/: 탐색/비교/대화/근거 패널 화면.

F2014는 후속 요청으로 구현했습니다. 문서 후보를 넉넉히 가져와 MMR을 적용하고 유사도 방식과 동일 후보 집합에서 비교합니다.
기본값은 fetch_k=30, k=10, lambda=0.5이며 실제 결과는 mmr_comparison.md에 기록합니다.
선택 상품 메타데이터·문서 필터와 기존 근거 인용 절차는 유지합니다. 검색 방식 비교는 로컬에서만 수행합니다.

원문 요구사항 PDF의 세 조건/NF 대조, 23개 외 상품의 추가 문서 확보, 의미검색 관련성의 넓은 평가,
F2013/F2015/F2016 심화는 별도로 남습니다. 완료 범위와 미확인 범위를 섞어 제출하지 않습니다.

구현 참조: [Responses 구조화 출력](https://developers.openai.com/api/docs/guides/structured-outputs),
[LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/quickstart).
