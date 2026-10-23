# F2014 — 유사도/MMR 실제 검색 비교

검증 시각(UTC): 2026-10-23T06:05:00.246116+00:00

질문: 원금 손실 위험, 환매 방법, 보수와 수수료를 설명해 줘.
상품 ID: sec_53cdb52b41d92fdd3f592dc70c6e216b7a9bd61f458bd99c5d170b144cddaf7d

동일한 후보 집합에서 두 검색 방식을 비교했습니다. 후보 상한 30, 중복 제거 후 실제 후보 28, 최종 선택 상한 10, lambda=0.5입니다.

| 지표 | 유사도 | MMR |
| --- | --- | --- |
| 선택 청크 | 10 | 10 |
| 서로 다른 PDF | 2 | 2 |
| 서로 다른 페이지 | 6 | 10 |
| 완전히 같은 본문 중복 | 0 | 0 |
| 청크 간 평균 코사인 유사도 | 0.582960 | 0.559756 |
| 청크 간 최대 코사인 유사도 | 0.870825 | 0.715345 |
| 문자 4-gram 평균 Jaccard | 0.009921 | 0.003978 |

선택 집합의 공통 ID 3개, 대칭 차집합 14개입니다.

## 선택된 출처 순서

| 방식 | 선택 순서 | 원래 후보 순위 | 문서 | PDF 페이지 |
| --- | --- | --- | --- | --- |
| similarity | 1 | 1 | [[정정]투자설명서_BNK K200인덱스증권투자신탁(주식-파생형)_2026.08.07시행.pdf](https://www.bnkasset.co.kr/download.aspx?tb=Gongsi&seq=7057&fid=2) | 17 |
| similarity | 2 | 2 | [[정정]투자설명서_BNK K200인덱스증권투자신탁(주식-파생형)_2026.08.07시행.pdf](https://www.bnkasset.co.kr/download.aspx?tb=Gongsi&seq=7057&fid=2) | 34 |
| similarity | 3 | 3 | [[정정]투자설명서_BNK K200인덱스증권투자신탁(주식-파생형)_2026.08.07시행.pdf](https://www.bnkasset.co.kr/download.aspx?tb=Gongsi&seq=7057&fid=2) | 27 |
| similarity | 4 | 4 | [[정정]투자설명서_BNK K200인덱스증권투자신탁(주식-파생형)_2026.08.07시행.pdf](https://www.bnkasset.co.kr/download.aspx?tb=Gongsi&seq=7057&fid=2) | 26 |
| similarity | 5 | 5 | [[정정]간이투자설명서_BNK K200인덱스증권투자신탁(주식-파생형)_2026.08.07시행.pdf](https://www.bnkasset.co.kr/download.aspx?tb=Gongsi&seq=7057&fid=3) | 2 |
| similarity | 6 | 6 | [[정정]투자설명서_BNK K200인덱스증권투자신탁(주식-파생형)_2026.08.07시행.pdf](https://www.bnkasset.co.kr/download.aspx?tb=Gongsi&seq=7057&fid=2) | 34 |
| similarity | 7 | 7 | [[정정]투자설명서_BNK K200인덱스증권투자신탁(주식-파생형)_2026.08.07시행.pdf](https://www.bnkasset.co.kr/download.aspx?tb=Gongsi&seq=7057&fid=2) | 18 |
| similarity | 8 | 8 | [[정정]투자설명서_BNK K200인덱스증권투자신탁(주식-파생형)_2026.08.07시행.pdf](https://www.bnkasset.co.kr/download.aspx?tb=Gongsi&seq=7057&fid=2) | 18 |
| similarity | 9 | 9 | [[정정]간이투자설명서_BNK K200인덱스증권투자신탁(주식-파생형)_2026.08.07시행.pdf](https://www.bnkasset.co.kr/download.aspx?tb=Gongsi&seq=7057&fid=3) | 2 |
| similarity | 10 | 10 | [[정정]투자설명서_BNK K200인덱스증권투자신탁(주식-파생형)_2026.08.07시행.pdf](https://www.bnkasset.co.kr/download.aspx?tb=Gongsi&seq=7057&fid=2) | 26 |
| mmr | 1 | 1 | [[정정]투자설명서_BNK K200인덱스증권투자신탁(주식-파생형)_2026.08.07시행.pdf](https://www.bnkasset.co.kr/download.aspx?tb=Gongsi&seq=7057&fid=2) | 17 |
| mmr | 2 | 28 | [[정정]투자설명서_BNK K200인덱스증권투자신탁(주식-파생형)_2026.08.07시행.pdf](https://www.bnkasset.co.kr/download.aspx?tb=Gongsi&seq=7057&fid=2) | 27 |
| mmr | 3 | 12 | [[정정]투자설명서_BNK K200인덱스증권투자신탁(주식-파생형)_2026.08.07시행.pdf](https://www.bnkasset.co.kr/download.aspx?tb=Gongsi&seq=7057&fid=2) | 19 |
| mmr | 4 | 11 | [[정정]투자설명서_BNK K200인덱스증권투자신탁(주식-파생형)_2026.08.07시행.pdf](https://www.bnkasset.co.kr/download.aspx?tb=Gongsi&seq=7057&fid=2) | 29 |
| mmr | 5 | 19 | [[정정]투자설명서_BNK K200인덱스증권투자신탁(주식-파생형)_2026.08.07시행.pdf](https://www.bnkasset.co.kr/download.aspx?tb=Gongsi&seq=7057&fid=2) | 33 |
| mmr | 6 | 2 | [[정정]투자설명서_BNK K200인덱스증권투자신탁(주식-파생형)_2026.08.07시행.pdf](https://www.bnkasset.co.kr/download.aspx?tb=Gongsi&seq=7057&fid=2) | 34 |
| mmr | 7 | 13 | [[정정]투자설명서_BNK K200인덱스증권투자신탁(주식-파생형)_2026.08.07시행.pdf](https://www.bnkasset.co.kr/download.aspx?tb=Gongsi&seq=7057&fid=2) | 54 |
| mmr | 8 | 4 | [[정정]투자설명서_BNK K200인덱스증권투자신탁(주식-파생형)_2026.08.07시행.pdf](https://www.bnkasset.co.kr/download.aspx?tb=Gongsi&seq=7057&fid=2) | 26 |
| mmr | 9 | 27 | [[변경]집합투자규약_BNK K200인덱스증권투자신탁(주식-파생형)_2025.04.17시행.pdf](https://www.bnkasset.co.kr/download.aspx?tb=Gongsi&seq=6359&fid=1) | 23 |
| mmr | 10 | 15 | [[정정]투자설명서_BNK K200인덱스증권투자신탁(주식-파생형)_2026.08.07시행.pdf](https://www.bnkasset.co.kr/download.aspx?tb=Gongsi&seq=7057&fid=2) | 30 |

## 구현과 해석

MMR은 첫 청크를 질문 유사도가 가장 높은 것으로 고른 후, lambda×질문 코사인 유사도 − (1−lambda)×기선택 청크와의 최대 코사인 유사도를 반복 최대화합니다.
lambda=1은 관련성 순서, 0은 다양성을 강조하며 기본값은 0.5입니다. 거리(distance)는 원래 질문과의 거리로 유지하고 MMR 선택 순위와 구분합니다.
완전히 같은 정규화 본문은 상품 내부에서 먼저 중복 제거합니다. 서로 다른 상품의 문서 관계는 보존합니다.
채팅에서는 상품마다 후보를 fetch_k까지 가져와 배분된 k를 고른 뒤 상품별로 번갈아 병합합니다. 따라서 여러 상품을 비교하면 전체 후보 예산이 상품 수×fetch_k가 될 수 있습니다.
후보가 적으면 k보다 적게 반환하며 근거를 복제해 채우지 않습니다. 상품 메타데이터는 MMR과 별도로 항상 포함됩니다.
평균 청크 유사도/문자 중복이 낮으면 이 후보 집합에서 내용 겹침이 줄었다는 뜻이며, 질문 관련성·정답 품질 향상을 자동으로 보장하지 않습니다. 최대 유사도가 높을 수 있고 모든 유사 청크를 제거한다고 주장하지 않습니다.
이번 검증은 로컬 검색만 수행했으며 GMS를 호출하거나 기존 벡터를 다시 임베딩하지 않았습니다. 신규 질문 벡터만 로컬 생성/캐시합니다.

증거: [mmr_comparison.json](evidence/mmr_comparison.json). 화면의 '검색 방식 비교' 탭에서 같은 설정으로 재현할 수 있습니다.

[MMR 파라미터 공식 문서](https://reference.langchain.com/python/langchain-chroma/vectorstores/Chroma/max_marginal_relevance_search)

## 테스트 및 화면 검증

전체 회귀 테스트 77개 통과, 실패/건너뜀 0개. `pip check` 통과. 실제 브라우저의 검색 방식 비교 탭에서 후보/선택 수, 순서와 유사도 지표를 확인했습니다.

[검증 기록](evidence/mmr_validation.json) · [화면](evidence/screenshots/mmr_comparison.png)
