# 증권 데이터 전처리 결과

생성 시각(UTC): 2026-10-23T03:07:22.642997+00:00

사용자 지시에 따라 보험은 제외합니다. API/임베딩/색인/외부 문서 수집은 수행하지 않았습니다.

- 상태: `ready_with_review` (검토 대상은 정상 평가 데이터와 분리)
- 원본 SHA-256 보존: `{'sha256_unchanged': True, 'files_checked': 8}`
- 입력 레코드: `{'labels': 2147, 'products': 8000}`; 제외한 분야/역할: `{}`
- 정규화 상품: 8,000건; 중복 병합: 0건; 상품 검토: 0건
- 정상 평가용 라벨: 2,140건; 분할: `{'training': 1902, 'validation': 238}`; 검토 라벨: 7건
- 라벨 검토 코드: `{'unmatched_product_name': 5, 'invalid_product_names': 2}`
- 상품명 연결(모든 파싱 가능 라벨 참조): `{'exact': 5010, 'unmatched': 6}`
- 입력 읽기/분류 이슈: 0건

## 산출물

| data/processed/ 아래 기본 경로 | 레코드 | bytes |
| --- | --- | --- |
| evaluation/test.jsonl | 0 | 0 |
| evaluation/training.jsonl | 1902 | 4831617 |
| evaluation/validation.jsonl | 238 | 672946 |
| products.jsonl | 8000 | 76914165 |
| review/input_issues.jsonl | 0 | 0 |
| review/labels.jsonl | 7 | 15174 |
| review/product_warnings.jsonl | 8000 | 3197102 |
| review/products.jsonl | 0 | 0 |

`manifest.json`에는 입력/출력 해시와 변환 버전이 있습니다. 재실행 시 전체를 다시 만들고 각 파일을 원자적으로 교체하며 manifest를 마지막에 씁니다.
여러 파일 전체를 한 번에 교체하는 트랜잭션은 아니므로 후속 로더는 manifest의 출력 해시를 확인해야 합니다.

## 원문과 정리본

`attributes`는 원본 상품 객체를 유지하며 `cleaned_attributes`는 NFC/공백/Office 제어 escape/사설영역 글리프를 정리합니다.
`normalized_attributes`는 raw_value, normalized_value, parse_status를 제공합니다. 미확인값은 null/unknown으로 유지합니다.
대체문자(�)는 임의 복원하지 않습니다. 인식하지 못한 Office escape는 남기고 품질 표시합니다.
변경된 텍스트 필드 수: `{'investment_strategy': 5395, 'income_rate': 15, 'product_name': 1}`

## 정형 파싱

| 내부 필드 | 파싱 상태/건수 |
| --- | --- |
| product_risk_grade | {'parsed': 8000} |
| principal_protected | {'parsed': 8000} |
| income_rate | {'unknown': 89, 'parsed': 7911} |
| maturity | {'parsed': 7993, 'unknown': 7} |
| liquidity | {'parsed': 8000} |
| loss_rate | {'parsed': 8000} |
| tax_benefit | {'parsed': 8000} |
| payment | {'parsed': 8000} |

- 위험등급은 상품의 1~6등급 숫자/원문 설명만 파싱합니다. 투자자 성향으로 자동 매핑하지 않습니다.
- 수익률은 %와 명시된 기간이 완전한 형식일 때만 파싱하며 기준일과 현재성은 미확인입니다. 오타/누락 %는 보정하지 않습니다.
- `중도해지 가능`은 early_termination_allowed만 true로 변환합니다. 중도환매·환매일·비용은 미확인입니다.
- 만기는 명시 날짜 또는 단순 기간만 파싱합니다. 날짜가 과거라고 현재 판매/만기 상태를 추정하지 않습니다.
- 세제혜택 `유`는 원문 표기만 뜻하며 사용자 적용 자격이 확인되었다는 뜻이 아닙니다.
- `최대`가 없는 손실률은 상한으로 사용하지 않습니다. 일시납을 추가납입 불가로 추정하지 않습니다.

## 상품 ID·중복·라벨 격리

상품 ID는 명시 원본 ID를 우선하고, 없으면 분야+회사(미제공 시 null)+NFC/공백 정리한 상품명 해시입니다. 클래스 괄호·구두점은 유지합니다.
동일 ID/동일 콘텐츠만 중복 제거하고 출처를 합칩니다. 동일 ID에 다른 콘텐츠가 있으면 모두 상품 검토로 분리합니다.
라벨은 질문과 상품 링크만 평가용으로 내보냅니다. 인구통계/answer/cot1~3은 제외하며 원문은 원본 경로로 추적합니다.
불완전 product_names는 자동 복구하지 않습니다. 미연결/다중연결/분할 간 ID·질문 중복/ID 충돌이 있으면 라벨 전체를 정상 평가에서 제외합니다.
파일 오류는 원본을 옮기지 않고 review/input_issues.jsonl에 기록합니다. partial 상태 산출물을 완전한 데이터로 사용하지 마세요.

## 품질 표시

| 필드:코드 | 상품 수 |
| --- | --- |
| income_rate:unparsed_value | 89 |
| income_rate:basis_date_unverified | 8000 |
| liquidity_conditions:detailed_conditions_unverified | 8000 |
| investment_strategy:damaged_or_unresolved_text | 29 |
| loss_rate:maximum_loss_not_stated | 10 |
| maturity:unparsed_value | 7 |

## 남은 작업

공식 상품 설명서·약관과 상품 ID를 연결하고 이용 조건을 확인해야 합니다. 외부 문서 RAG와 Chroma 색인은 아직 수행하지 않았습니다.
원금보장/환매 등 추천 조건 필터는 후속 단계에서 구조화 값과 미확인 상태를 함께 검증해야 합니다.
