# 로컬 임베딩 및 Chroma 구축 검증

검증 시각(UTC): 2026-10-23T05:13:54.978298+00:00

임베딩: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` / 384차원 / ONNX CPU / `apache-2.0`.
모델 revision: `faf4aa4225822f3bc6376869cb1164e8e3feedd0`

금융 본문을 외부 API에 전송하지 않고 로컬에서 처리했습니다. Python 네트워크를 차단하고 Chroma/Hugging Face/ONNX 텔레메트리를 비활성화했습니다.

## 두 컬렉션 및 재실행

| 컬렉션 | 첫 성공 스냅샷 | 두 번째 실행 | 증가 | 두 번째 upsert |
| --- | --- | --- | --- | --- |
| financial_products | 8,000 | 8,000 | 0 | 0 |
| financial_documents | 15,287 | 15,287 | 0 | 0 |

상품은 1상품=1검색 텍스트=1벡터입니다. 긴 상품 텍스트는 모든 토큰 구간을 임베딩한 후 평균·L2 정규화하며 일부를 조용히 잘라내지 않습니다.
문서는 실제 PDF 15개, 427페이지의 고유 청크 3,389개입니다.
클래스별 상품 ID 23개와의 관계를 보존해 Chroma에는 15,287개 상품-청크 행을 저장합니다. 서로 다른 PDF 15,287개라는 뜻이 아닙니다.
동일 청크의 임베딩은 캐시로 공유하고 product_id별 색인 ID는 분리합니다. 변경·정정대비표 10개는 근거 색인에서 제외했습니다.
청크 상한 설정은 1000자이며 모델 한도 128토큰을 우선합니다. 실측 최대 373자 / 128토큰입니다.
겹침은 min(설정값 200자, 실제 청크 길이의 20%)입니다. 페이지 경계를 넘지 않으며 물리 PDF 페이지(1부터), 문자 시작/끝, 원문 URI를 저장합니다.

## 검증 결과

- 모든 벡터의 차원·유한값·L2 길이와 저장 레코드 해시 확인.
- 모든 문서 청크가 추출된 해당 PDF 페이지의 정확한 문자 범위와 일치함을 확인.
- 선택 상품 메타데이터 필수 포함, 다른 상품 문서 혼입 없음, 문서 top-k 변경 및 두 상품 비교 메타데이터 포함 확인.
- 원본 ZIP 8개, 정규화 상품, 다운로드 PDF 25개의 해시 보존 확인.
- 순수 의미검색 예제: K200인덱스 상품군 top-5 포함 `False`. 상품명 검색에 한계가 있어 공식명 일치를 우선하고 나머지를 의미검색으로 보충합니다.
- 공식명 우선 검색 예제에서는 원본에 있는 K200인덱스 상품군 4개가 먼저 검색되고 남은 1개는 의미검색으로 보충되었습니다. 공백/NFC/대소문자만 정리하며 클래스 구두점은 유지합니다. 이름 일치 결과의 거리값은 null이며 의미검색 점수로 가장하지 않습니다.
- 두 번째 실행은 동일한 ID 집합·건수 유지, 추가 임베딩/업서트/삭제 없음.

## 저장 위치 및 재현

- `storage/chroma/`: 영속 Chroma DB 및 index_manifest.json/build_status.json.
- `storage/models/`: 공개 모델 가중치와 파일 해시·revision manifest.
- `storage/embedding_cache.sqlite3`: 모델·내용 해시별 벡터 캐시.
- `data/processed/document_pages.jsonl`, `document_chunks.jsonl`: 페이지와 상품별 청크 원문/출처.
- `docs/evidence/vector_index_first_run.json`, `vector_index_verification.json`: 두 실행 및 실제 검색 증거.

```powershell
.\.venv\Scripts\python.exe scripts/download_embedding_model.py
.\.venv\Scripts\python.exe scripts/build_indexes.py
.\.venv\Scripts\python.exe scripts/verify_vector_db.py
```

## 남은 범위

현재 전체 상품 메타데이터는 검색할 수 있지만 PDF 근거가 연결된 상품은 23개뿐입니다. 나머지 상품의 문서 조건을 확인했다고 표시하면 안 됩니다.
검색 유사도는 적격성 판단이 아닙니다. 구조화 제약 필터, LLM 검색어/문서 선별, LangGraph 답변 생성, 인용 UI는 이후 구현 대상입니다.
이번 검색 검증은 상품별 격리·원문 페이지 연결·건수 검증입니다. 질문 관련성/정답 품질을 보증하지 않으며 표·유사한 공통 문구의 상위 검색이 관찰되어 검색 품질 평가와 개선이 필요합니다.
문서의 외부 모델 전송/재배포 허용 범위는 미확인이며 이번 임베딩은 로컬에서만 수행했습니다.

참조: [모델 카드](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2), [FastEmbed 모델 목록](https://qdrant.github.io/fastembed/examples/Supported_Models/), [Chroma 영속 클라이언트](https://docs.trychroma.com/reference/python/client).

## 테스트 및 시각 검토

전체 테스트 56개 중 55개 통과, 1개는 이 환경에서 하드링크 생성 권한이 없어 건너뛰었습니다. `pip check`도 통과했습니다.

실제 검색에서 반환한 5개 상품군의 PDF 페이지를 렌더링해 상품군 머리말과 페이지 번호를 시각 확인했습니다. 이 검토는 출처 연결 검증이며 질문 관련성 평가를 대체하지 않습니다.

- 검증 기록: [vector_index_quality_checks.json](evidence/vector_index_quality_checks.json)
- 로컬 페이지 이미지: [retrieved_pages_contact_sheet.png](../data/processed/index_qa/retrieved_pages_contact_sheet.png)
