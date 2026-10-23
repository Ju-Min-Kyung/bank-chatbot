# bank-chatbot

AI Hub 금융상품 데이터에 근거한 상품 탐색·비교 챗봇 프로젝트입니다.
현재는 **증권 전용 데이터 감사·전처리·로컬 벡터 DB·GMS RAG·Streamlit 화면**을 구현했습니다. 사용자의 범위 변경에 따라 보험은 제외합니다.
상품 8,000건과 공식 PDF 15개의 상품별 문서 청크 15,287건을 두 Chroma 컬렉션에 저장했습니다.
세 프롬프트와 LangGraph, 조건 탐색, 상품별 대화, 근거가 연결된 비교표를 제공합니다. 설계/14단계 추적은 [docs/rag_design.md](docs/rag_design.md)를 확인하세요.

## MMR 검색 및 유사도 비교 (F2014)

문서 검색 기본값은 **후보 30개 → 최종 10개, MMR 가중치 0.5**입니다. 이미 고른 청크와 내용이 비슷한 후보를 감점해 겹침을 줄입니다.
사이드바 '검색 설정'에서 MMR/유사도 방식, 최종 개수, 후보 수, 관련성 가중치를 바꿀 수 있습니다.
'검색 방식 비교' 탭은 동일 질문·동일 후보 집합의 결과 순서·PDF/페이지·청크 간 유사도를 나란히 표시합니다. GMS를 호출하지 않습니다.

```dotenv
SEARCH_MODE=mmr
DOCUMENT_FETCH_K=30
DOCUMENT_TOP_K=10
MMR_LAMBDA_MULT=0.5
```

가중치가 1이면 관련성, 0이면 다양성을 강조합니다. 첫 청크는 가장 관련성 높은 것으로 고릅니다.
후보가 부족하거나 완전히 같은 본문을 제거하면 실제 선택 수가 줄어들 수 있습니다.
여러 상품의 대화에서는 상품마다 후보 수를 적용하고 최종 문서 상한을 나눕니다. 상품 메타데이터는 항상 별도로 포함합니다.
원본·색인 벡터를 변경하거나 다시 임베딩하지 않고, 질문 벡터와 저장된 벡터로 선택합니다.

```powershell
.\.venv\Scripts\python.exe scripts/compare_search.py --fetch-k 30 --top-k 10 --lambda-mult 0.5
.\.venv\Scripts\python.exe scripts/query_index.py "환매 조건" --product-id sec_53cdb52b41d92fdd3f592dc70c6e216b7a9bd61f458bd99c5d170b144cddaf7d --mode mmr --fetch-k 30 --top-k 10
```

실측 결과와 한계는 [docs/mmr_comparison.md](docs/mmr_comparison.md)에 있습니다. 겹침 감소가 답변 정확도 향상을 자동으로 보장하지는 않습니다.
`.env`를 바꾼 경우 실행 중인 Streamlit 서버를 재시작하세요.

## 챗봇 실행

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/check_gms.py
.\.venv\Scripts\python.exe -m streamlit run app.py
```

브라우저에서 [로컬 앱](http://127.0.0.1:8501)을 엽니다. 기존 색인과 로컬 임베딩 모델이 필요합니다.
`.env`에는 다음 GMS 설정을 입력합니다. 키를 소스/화면/채팅에 붙이지 마세요.

```dotenv
GMS_API_KEY=
GMS_BASE_URL=https://gms.ssafy.io/gmsapi/api.openai.com/v1
CHAT_MODEL=gpt-6-astra
GMS_ALLOW_SOURCE_TRANSMISSION=false
```

상품/PDF 발췌의 GMS 전송이 허용된 경우에만 마지막 값을 true로 설정합니다. 현재 작업 환경에는 사용자의 명시적 전송 허용을 반영했습니다.
벡터 DB 검색과 임베딩은 계속 로컬입니다. API 호출은 `store=false`를 요청하며 라벨·CoT·소비자 데이터를 보내지 않습니다.

```powershell
.\.venv\Scripts\python.exe scripts/ask.py "원금 손실 위험은?" --product-id sec_53cdb52b41d92fdd3f592dc70c6e216b7a9bd61f458bd99c5d170b144cddaf7d
.\.venv\Scripts\python.exe scripts/evaluate.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

`evaluate.py`는 실제 GMS 호출을 사용하며 사용량이 발생합니다. 평가 질문은 코드에 고정된 비개인 질문입니다.
단위/UI 테스트는 모델 대역을 사용해 GMS를 호출하지 않습니다. Windows의 제한된 샌드박스에서는 AppTest 내부 로컬 소켓 때문에 UI 테스트를 별도 허용 환경에서 실행해야 할 수 있습니다.

공식 상품명으로 최대 4개를 선택하고 비교/대화 탭에서 질문합니다. 대화는 선택 상품 조합별로 분리됩니다.
조건 탐색은 원금보장·위험등급·유형·환매·만기를 코드로 검증하며, 미확인 속성은 충족으로 처리하지 않습니다.
답변 근거 패널에는 실제 사용한 출처만 표시합니다. 없는 인용 번호/다른 상품/없는 인용구는 제외합니다.
현재 수익률이나 판매 상태를 보장하지 않으며 전문 자문을 대체하지 않습니다.

## 로컬 임베딩·벡터 DB

`requirements-index.txt`의 고정 버전으로 Windows / Python 3.12.14에서 검증합니다.
현재 개발 환경은 Codex 번들 Python으로 만든 `.venv`이며 NumPy/pypdf 등 번들 패키지도 사용합니다.
모델 최초 다운로드만 네트워크가 필요하고, 색인·검색은 로컬에서 실행하며 API 키가 필요하지 않습니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-index.txt
.\.venv\Scripts\python.exe scripts/download_embedding_model.py
.\.venv\Scripts\python.exe scripts/build_indexes.py
```

상품/문서 중 하나만 다시 색인하려면 각각 `scripts/build_product_index.py`, `scripts/build_document_index.py`를 실행합니다.
전체 최초 구축은 두 컬렉션을 만드는 `build_indexes.py`를 사용하세요. 로컬 모델 다운로드와 실제 색인은 분리했습니다.
`.env.example`에 기본 모델/경로/청킹/CPU 설정이 있습니다. `.env`가 있으면 색인·검색 설정을 읽으며 API 키는 사용하지 않습니다.

| 컬렉션 | 저장 단위 |
| --- | --- |
| `financial_products` | 상품별 검색 텍스트 1개와 벡터 1개 |
| `financial_documents` | 검증된 PDF 페이지 청크 × 연결된 상품 ID |

임베딩은 Apache-2.0 모델 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`의 FastEmbed ONNX 양자화 버전(384차원)입니다.
모델 가중치와 토크나이저의 SHA-256/revision을 기록하고, 색인·검색 모델 구성이 다르면 기존 컬렉션을 덮어쓰지 않고 실패합니다.
문서/상품 본문을 외부 임베딩 API에 전송하지 않으며 Python 소켓과 텔레메트리를 차단합니다.

모델의 128토큰 제한을 지킵니다. 긴 상품 텍스트는 모든 구간 벡터를 평균·정규화해 하나로 저장합니다.
문서 청크는 설정한 1,000자 이하이면서 128토큰 이하로 만들고 페이지를 넘지 않습니다.
겹침은 설정값 200자와 실제 청크 길이의 20% 중 작은 값입니다. 따라서 실제 청크 길이는 1,000자보다 짧을 수 있습니다.
본문의 위험등급·원금보장 등 정형 조건을 유사도 점수로 충족했다고 판단하지 않습니다.
상품명 입력은 공식명 일치를 우선하고 남은 결과를 의미검색으로 채웁니다. 순수 의미검색은 상품명 검색에서 누락이 관찰됐으며,
이름 일치 결과는 `match_type=official_name`, `distance=null`로 구분합니다. PDF가 연결된 상품은 현재 23개입니다.

공유 문서는 `product_id:document_id:chunk_index`로 상품별 관계를 분리하되 동일 텍스트 벡터는 캐시로 재사용합니다.
동일 원문·설정의 재실행은 업서트/임베딩을 건너뜁니다. 변경된 원문은 갱신하고 전체 입력 처리가 성공한 뒤 관리 대상 컬렉션의 오래된 ID를 제거합니다.
상품/라벨 원문, CoT, 평가 질문은 문서 컬렉션에 넣지 않으며 변경·정정대비표도 제외합니다.

```powershell
# 상품명 우선·의미검색: 검색 후보이며 적격성 추천 결과가 아닙니다.
.\.venv\Scripts\python.exe scripts/query_index.py "BNK K200인덱스증권투자신탁"
# 선택 상품의 메타데이터를 필수 포함하고 해당 상품 문서만 검색
.\.venv\Scripts\python.exe scripts/query_index.py "환매 조건" --product-id sec_53cdb52b41d92fdd3f592dc70c6e216b7a9bd61f458bd99c5d170b144cddaf7d --top-k 5
```

`--top-k`는 선택 상품 검색에서는 문서 청크 상한이며 상품 메타데이터는 별도로 포함됩니다.
선택한 상품에 PDF가 없으면 메타데이터만 반환합니다. 두 상품 비교에는 `--product-id`를 두 번 지정합니다.

저장 위치는 `storage/chroma/`, 모델 캐시는 `storage/models/`, 벡터 캐시는 `storage/embedding_cache.sqlite3`입니다.
추출한 페이지와 출처는 `data/processed/document_pages.jsonl`, 상품별 청크는 `document_chunks.jsonl`에 저장합니다.
색인 중 중단되면 build_status가 ready가 아니므로 검색을 거부하며 같은 명령으로 재실행하면 저장된 캐시/레코드부터 이어갑니다.

검증은 첫 성공 실행의 `storage/chroma/index_manifest.json`을 `docs/evidence/vector_index_first_run.json`으로 보존하고
`build_indexes.py`를 다시 실행한 뒤 `verify_vector_db.py`를 실행합니다. 실제 측정 결과와 한계는 `docs/vector_index.md`에 기록합니다.
모델이나 청킹 설정을 바꾸려면 새 `CHROMA_DIR`을 사용해야 합니다. 보관된 원본이나 기존 인덱스를 자동 초기화하지 않습니다.

## 확보한 공식 설명서·규약

초기 문서 묶음으로 BNK 5개 상품군의 투자설명서·간이투자설명서·집합투자규약 15개와 변경/정정대비표 10개를 확보했습니다.
목록과 PDF 열기 링크는 `docs/external_documents.md`에 있습니다. 전체 8,000개 상품에 대한 문서 확보를 뜻하지 않습니다.

- `data/external/bnk/`: 공식 공시에서 받은 원본 PDF 25개.
- `data/external/manifest.json`: 문서 제목·공식 URL·시행일·수집일·페이지 수·SHA-256·상품 연결 범위.
- `data/external/cache/`: 공식 HTML/PDF 응답 캐시와 해시. Git 제외.
- `data/processed/document_links.jsonl`: 클래스별 상품 ID와 문서 ID 연결표. 기존 products.jsonl은 재작성하지 않습니다.
- `data/processed/document_qa/`: 핵심 PDF 표지와 클래스 표 렌더링 검토 이미지. Git 제외.
- `docs/external_collection_plan.json`: 실제 확인한 공식 상품·상세 페이지의 수집 계획.

공식 BNK 도메인만 허용하는 수집 스크립트를 추가했습니다. 데이터 감사/전처리는 계속 표준 라이브러리만 사용하며,
PDF 수집·본문 검증에는 별도로 `pypdf==6.10.0`이 필요합니다(현재 번들 Python에서 검증).

```powershell
python -m pip install -r requirements-docs.txt
python scripts/collect_external_docs.py --plan docs/external_collection_plan.json
# 검증된 캐시만 사용해 재검증: 추가 HTTP 요청 없음, 캐시 누락 시 실패
python scripts/collect_external_docs.py --plan docs/external_collection_plan.json --offline
```

상품군 명칭과 현재 공식 클래스 목록을 대조하고 설명서의 클래스 표기 페이지를 기록합니다.
클래스 별칭/변경 명칭은 임의로 맞추지 않습니다. 규약 연결은 상품군 공통 범위이며, 대비표는 완전한 계약 문서로 취급하지 않습니다.
일부 간이설명서의 클래스 표기는 텍스트만으로 확인하지 못해 연결을 보류했습니다.
공시 시행일은 PDF 작성기준일이나 AI Hub 메타데이터 기준일을 대체하지 않습니다.

공식 사이트의 재배포·외부 모델 전송 허용 범위는 확인되지 않았습니다. 자료는 Git 제외 경로에 로컬 보관하며 임베딩도 로컬에서만 수행합니다.
이 단계는 공식 링크 기반 초기 수집이며 F2003/F2004의 LLM 검색어·선별 및 F2005 색인은 별도 구현 대상입니다.

## 증권 데이터 준비

```powershell
python scripts/prepare_data.py
python -m unittest discover -s tests -v
```

Python 3.10 이상과 표준 라이브러리만 사용합니다. 새 입력을 먼저 전체 감사한 후 변환하며 ZIP을 해제하지 않습니다.
정규화/검색 텍스트 생성은 로컬 변환만 수행하고 모델/API 호출이나 Chroma 색인은 하지 않습니다.

```powershell
python scripts/prepare_data.py --raw-dir data/raw --output-dir data/processed --report docs/data_preparation.md
```

주요 산출물은 다음과 같습니다. 모든 경로는 기본 설정 기준입니다.

| 경로 | 용도 |
| --- | --- |
| `data/processed/products.jsonl` | 상품 8,000건, 원문 속성·정리본·구조화 값·출처·상품당 1검색 텍스트 |
| `data/processed/evaluation/training.jsonl` | 연결 검증된 평가 질문 1,902건 |
| `data/processed/evaluation/validation.jsonl` | 연결 검증된 평가 질문 238건 |
| `data/processed/evaluation/test.jsonl` | 미확보 분할이므로 빈 파일 |
| `data/processed/review/labels.jsonl` | 불완전/미연결 라벨 7건, 평가에서 제외 |
| `data/processed/review/product_warnings.jsonl` | 상품별 미확인 기준일·조건·파싱/텍스트 이슈 |
| `data/processed/review/products.jsonl` | 상품 ID 충돌/형식 오류, 현재 0건 |
| `data/processed/review/input_issues.jsonl` | 입력 파싱/분류 오류, 현재 0건 |
| `data/processed/manifest.json` | 변환 버전, 입력/출력 해시, 건수, 품질 집계 |
| `docs/data_preparation.md` | 전처리 규칙과 실제 집계 결과 |
| `docs/evidence/preparation_verification.json` | 실제 데이터 원문 보존·연결·재실행 검증 |

`attributes`와 `normalized_attributes.*.raw_value`는 원문을 유지합니다. `cleaned_attributes`에는 정리된 문자열을,
`normalized_attributes.*.normalized_value`에는 검증된 파싱 결과를 저장하며 실패하면 null/unknown으로 남깁니다.
상품 ID는 명시 원본 ID를 우선하고 없으면 회사(미제공 시 null)와 공식명 해시를 사용합니다. 클래스 괄호/구두점을 유지하며,
동일 ID의 다른 콘텐츠는 임의 병합하지 않습니다.

평가용 파일은 질문과 상품 ID 연결을 위한 자료입니다. `answer`, `cot1~3`, 연령/성별은 내보내지 않으며 임베딩 대상으로 쓰지 않습니다.
불완전 상품명은 자동 복구하지 않고, 미연결/다중연결 및 분할 간 동일 ID·질문은 정상 평가에서 제외합니다.

현재 수익률 89건·만기 7건은 정형 파싱을 확정하지 않았습니다. 모든 수익률 기준일과 상세 환매 조건은 미확인입니다.
특히 `중도해지 가능`을 `중도환매 가능`으로 확장하지 않습니다. 공식 상품 문서와 이용 조건은 다음 단계에서 확보해야 합니다.

준비 스크립트의 종료 코드는 `0` 정상 산출(라벨 검토 대상은 분리), `1` 입력/상품 오류로 부분 산출, `2` 실행 실패입니다.
`manifest.status=partial`이면 완전한 데이터로 사용하지 마세요. 재실행은 생성 파일 전체를 교체하며 manifest는 마지막에 씁니다.
다중 파일 트랜잭션은 아니므로 후속 로더는 manifest의 출력 SHA-256을 확인해야 합니다.

## 데이터 감사 실행

Python 3.10 이상이 필요합니다. 표준 라이브러리만 사용하며 패키지 설치, API 키, 네트워크가 필요하지 않습니다.

```powershell
python scripts/audit_data.py
python -m unittest discover -s tests -v
```

현재 개발 PC의 기본 `python`/`py`에는 사용 가능한 Python이 연결되어 있지 않아, 검증 시 Codex에 포함된 Python을 사용했습니다.

```powershell
& 'C:\Users\SSAFY\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts/audit_data.py
& 'C:\Users\SSAFY\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts/prepare_data.py
```

프로젝트 외부 디렉터리에서 실행해도 기본 입력/출력은 스크립트가 속한 프로젝트 기준입니다.
직접 지정한 상대 경로는 실행 디렉터리 기준입니다.

```powershell
python scripts/audit_data.py --raw-dir data/raw --external-dir data/external --report docs/data_audit.md --output-dir data/processed/audit
```

`.env.example`은 이후 단계용 예시이며 감사 스크립트는 `.env`를 읽지 않습니다. 감사 경로는 CLI 옵션으로 지정합니다.

## 산출물과 동작

- `docs/data_audit.md`: 실제 파일 구성, 스키마·결측·중복, 라벨 연결, 전처리 이슈.
- `docs/requirements.md`: AGENTS.md 해석, 프로젝트 구조 분석, 요구사항 상태.
- `data/processed/audit/summary.json`: 전체 감사 통계와 원본 ZIP SHA-256.
- `data/processed/audit/inventory.jsonl`: 파일별 원래 멤버명·크기·SHA-256·형식·인코딩·상태.
- `docs/evidence/audit_verification.json`: 실제 데이터 재실행 일관성과 원본 무결성 검증 기록.

ZIP은 **해제하지 않고 읽습니다**. `.json `은 판독 시에만 확장자를 인식하며 원본 이름을 바꾸지 않습니다.
JSON 객체/객체 배열/JSONL, 쉼표 CSV를 검사하고 중첩 경로를 집계합니다. 래퍼 객체는 임의로 상품 목록으로 해석하지 않습니다.
형식 오류는 원본을 이동하지 않고 오류 경로로 기록하며 나머지 파일을 계속 조사합니다.
파일당 판독 한도는 64 MiB이며 초과 파일은 오류로 기록합니다. 미지원 확장자는 `not_parsed`로 표시합니다.

빈값률은 관측값 대비 null/공백/빈 컨테이너 비율이며 누락 필드는 별도로 집계합니다.
상품명 연결은 원문 일치와 NFC/공백 정리 후보를 구분하며 상품 식별을 확정하거나 자동 병합하지 않습니다.
질문·답변·CoT·소비자 원문은 보고서에 복사하지 않습니다. 라이선스/URL 공식성·최신성은 오프라인 감사만으로 검증할 수 없습니다.

실행 시 기존 감사 산출물을 갱신합니다. 원본 내부·상위 디렉터리로의 출력이나 원본 파일 덮어쓰기는 거부합니다.
종료 코드는 `0` 정상 감사(필드 이슈는 보고서 확인), `1` 파일 오류/원본 변경 감지, `2` 경로/설정/실행 실패입니다.
`invalid_product_names` 같은 필드 품질 이슈는 JSON 파싱 오류와 구분합니다.

원본·외부 문서·로컬 manifest는 `.gitignore`로 제외합니다. 이용 조건 확인 없이 원본을 저장소에 추가하지 마세요.
