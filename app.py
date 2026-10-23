"""Securities-only Streamlit exploration, source-backed comparison, and chat."""
import json
from pathlib import Path

import streamlit as st

from config import PROJECT_ROOT, load_chat_config, load_index_config
from src.domain.recommendations import comparison_table, eligible_products
from src.domain.safety import NOTICE
from src.ingestion.product_index import load_product_records
from src.rag.conditions import parse_constraints
from src.ui.state import conversation


def choose_candidate(product_id):
    st.session_state.selected_products = [product_id]
    st.session_state.name_search = ""
    st.session_state.linked_only = False


def show_sources(evidence):
    for row in evidence:
        title = row.get("title") or row.get("product_name", "상품") + " — 상품 메타데이터"
        with st.expander(f"[근거 {row['evidence_id']}] {title}"):
            st.caption(row.get("product_name", row["product_id"]))
            if row.get("source_uri", "").startswith("https://www.bnkasset.co.kr/"):
                st.link_button("공식 원문 열기", row["source_uri"])
                st.caption(f"PDF 물리 페이지 {row['page_number']} · 문서 기준/시행일: {row.get('document_date') or '미확인'}")
                scope = row.get("link_scope", "")
                st.caption("상품 연결 범위: " + ({"fund_and_listed_class": "상품군 및 설명서에 기재된 클래스", "fund_contract": "상품군 공통 규약"}.get(scope, "상품군 공통 또는 클래스 조건 — 원문 확인 필요")))
            else:
                st.caption("AI Hub 상품 메타데이터 · 출처: " + row.get("source_file", "미확인"))
            # Quotes remain text, never HTML or executable instructions.
            st.text(row.get("content", ""))


def main():
    st.set_page_config(page_title="증권상품 근거 탐색", page_icon="📚", layout="wide")
    st.title("증권상품 근거 탐색")
    st.caption("AI Hub 상품 자료 · 설명서상 구축연도 2025 · 상품별 수익률 기준일 미확인")
    st.info(NOTICE)
    st.caption("보험은 제외합니다. 현재 공식 PDF가 연결된 상품은 23개이며 나머지는 메타데이터만 제공합니다.")
    try:
        config, chat_config = load_index_config(), load_chat_config()
        # Offline catalog and comparison stay usable without a GMS key or vector DB.
        products, _ = load_product_records(config)
    except (ValueError, OSError, KeyError):
        st.error("정규화 상품 데이터 또는 설정을 읽을 수 없습니다. 데이터 준비 결과와 .env 설정을 확인하세요.")
        return
    by_id = {p["product_id"]: p for p in products}
    if not chat_config.api_key:
        st.warning("GMS 키가 없습니다. .env의 GMS_API_KEY를 입력하면 답변 생성을 사용할 수 있습니다.")
    if not chat_config.source_transmission_allowed:
        st.warning("자료의 GMS 전송 허용 설정이 꺼져 있습니다. 로컬 탐색·비교만 사용할 수 있습니다.")
    status_file = config.chroma_dir / "build_status.json"
    try:
        status = json.loads(status_file.read_text(encoding="utf-8"))
        index_manifest = json.loads((config.chroma_dir / "index_manifest.json").read_text(encoding="utf-8"))
        index_ready = status["status"] == "ready" and all(v["after"] > 0 for v in index_manifest["collections"].values())
    except (OSError, ValueError, KeyError):
        index_ready, index_manifest = False, {}
    if not index_ready:
        st.warning("벡터 색인이 없거나 구축이 끝나지 않았습니다. scripts/build_indexes.py를 실행하세요.")
    with st.sidebar:
        st.header("상품 선택")
        st.caption("증권 전용 · 1~4개 선택 시 해당 상품 근거만 검색합니다.")
        search = st.text_input("공식 상품명 검색", placeholder="BNK K200", key="name_search")
        linked_only = st.checkbox("공식 PDF 연결 상품만", value=True, key="linked_only")
        try:
            with config.document_links.open(encoding="utf-8") as source:
                linked = {json.loads(line)["product_id"] for line in source}
        except OSError:
            linked = set()
        def name_key(text):
            return "".join(text.casefold().split())
        visible = [p for p in products if (not linked_only or p["product_id"] in linked) and name_key(search) in name_key(p["product_name"])]
        if not visible:
            st.caption("일치하는 상품이 없습니다. 검색어나 PDF 연결 필터를 바꿔 보세요.")
        selected = st.multiselect("질문·비교할 상품", [p["product_id"] for p in visible],
                                  format_func=lambda pid: by_id[pid]["product_name"], max_selections=4, key="selected_products")
        st.caption(f"전체 상품 {len(products):,}건 · PDF 연결 {len(linked)}건")
        for name, row in index_manifest.get("collections", {}).items():
            st.caption(f"{name}: {row['after']:,}")
        with st.expander("검색 설정"):
            search_mode = st.selectbox("문서 검색 방식", ["mmr", "similarity"], index=["mmr", "similarity"].index(chat_config.search_mode),
                format_func=lambda mode: "MMR · 관련성과 다양성" if mode == "mmr" else "유사도 · 관련성 순", key="search_mode")
            top_k = st.slider("최종 문서 청크 수", 1, 30, chat_config.document_top_k, key="document_k")
            fetch_k = st.slider("먼저 가져올 후보 수", top_k, 100, max(top_k, min(100, chat_config.document_fetch_k)), key="document_fetch_k")
            lambda_mult = st.slider("MMR 관련성 가중치", 0.0, 1.0, chat_config.mmr_lambda_mult, 0.05, key="mmr_lambda")
            st.caption("가중치가 낮을수록 다양성을 강조합니다. 후보가 부족하면 최종 수보다 적게 반환합니다.")
            st.caption("대화에서 여러 상품을 비교하면 상품별로 후보를 검색하고, 최종 문서 상한을 나눠 적용합니다.")
            st.caption("상품 메타데이터는 상한과 별도로 필수 포함됩니다.")
    browse_tab, compare_tab, chat_tab, search_tab = st.tabs(["조건 기반 탐색", "상품 조건 비교", "근거 기반 대화", "검색 방식 비교"])
    with browse_tab:
        st.subheader("원하는 조건으로 후보 찾기")
        query = st.text_input("상품 조건", placeholder="고위험은 피하고 싶어. 채권형 상품을 찾아줘", key="condition_query")
        a, b, c = st.columns(3)
        principal = a.checkbox("원금보장 필수", key="principal_required")
        minimum = b.selectbox("상품 위험등급 범위", [None, 4, 5, 6], format_func=lambda v: "지정 안 함" if v is None else f"{v}~6등급 (숫자가 클수록 낮은 위험)")
        kind = c.selectbox("상품유형", [None, "주식형", "채권형", "재간접", "단기금융", "파생형", "퇴직연금"], format_func=lambda v: v or "전체")
        filter_signature = [query, principal, minimum, kind]
        if st.button("조건 검증 후 후보 탐색", key="browse"):
            constraints, warnings = parse_constraints(query)
            constraints.principal_required |= principal
            if minimum:
                constraints.minimum_risk_grade = max(minimum, constraints.minimum_risk_grade or 1)
            if kind:
                if constraints.product_type and constraints.product_type != kind:
                    constraints.ambiguous = True
                else:
                    constraints.product_type = kind
            matches = eligible_products(products, constraints)
            for warning in warnings:
                st.warning(warning)
            filters = []
            if constraints.principal_required:
                filters.append("원금보장 필수")
            if constraints.minimum_risk_grade:
                filters.append(f"위험등급 {constraints.minimum_risk_grade}~6")
            if constraints.product_type:
                filters.append(constraints.product_type)
            if constraints.early_redemption_required:
                filters.append("중도환매 가능 확인 필요")
            if constraints.maximum_maturity_months:
                filters.append(f"만기 {constraints.maximum_maturity_months}개월 이내")
            st.caption("적용 조건: " + (" · ".join(filters) or "정형 조건 지정 없음"))
            if not matches:
                st.warning("확인 가능한 자료에서 조건을 충족한 상품을 찾지 못했습니다. 미확인 정보는 충족으로 간주하지 않습니다.")
            else:
                # Semantic ranking is optional; hard eligibility already checked.
                if query.strip() and index_ready:
                    try:
                        runtime = get_runtime()
                        hits = runtime.retriever.search_products(query, top_k=4, allowed_ids=[p["product_id"] for p in matches])
                        matches = [by_id[row["product_id"]] for row in hits]
                    except Exception:
                        st.warning("의미검색을 사용할 수 없어 정형 조건을 통과한 이름순 후보를 표시합니다.")
                        matches = sorted(matches, key=lambda p: p["product_name"])[:4]
                else:
                    matches = sorted(matches, key=lambda p: p["product_name"])[:4]
                st.session_state.browse_results = [p["product_id"] for p in matches]
            if not matches:
                st.session_state.browse_results = []
            st.session_state.browse_signature = filter_signature
        shown = st.session_state.get("browse_results", []) if st.session_state.get("browse_signature") == filter_signature else []
        for pid in shown:
            product = by_id[pid]
            with st.container(border=True):
                st.markdown("**" + product["product_name"] + "**")
                attrs = product["cleaned_attributes"]
                st.write(f"{product['product_type']} · {attrs.get('risk_grade')} · {attrs.get('protected_type')}")
                st.caption("확인 필요: 현재 수익률·상세 환매 비용·가입/세제 적용요건. 법적 적합성 판정이 아닙니다.")
                st.caption("PDF 근거 연결됨" if product["product_id"] in linked else "PDF 미확보 · 메타데이터만 있음")
                st.button("이 상품 선택", key="choose_"+pid, on_click=choose_candidate, args=(pid,))
    with compare_tab:
        st.subheader("근거가 연결된 상품 조건 비교")
        if selected:
            table, sources = comparison_table([by_id[pid] for pid in selected])
            st.dataframe(table, hide_index=True, width="stretch")
            st.caption("수익률은 원문에 기재된 과거 값이며 측정 기준일 미확인입니다. 세제혜택 표시로 실제 적용 자격을 확정하지 않습니다.")
            show_sources(sources)
        else:
            st.info("사이드바에서 비교할 상품을 선택하세요.")
    with chat_tab:
        st.subheader("상품별 근거 기반 대화")
        st.caption("선택한 상품 조합마다 대화가 분리됩니다. 미선택 시 조건을 검증해 후보를 탐색합니다. 민감정보는 입력하지 마세요.")
        histories = st.session_state.setdefault("conversations", {})
        history = conversation(histories, selected)
        if st.button("현재 대화 지우기"):
            history.clear()
        for item in history:
            with st.chat_message(item["role"]):
                if item["role"] == "user":
                    st.text(item["content"])
                else:
                    st.markdown(item["content"])
                    show_sources(item.get("evidence", []))
        question = st.chat_input("예: 이 상품의 원금 손실 위험과 환매 조건을 알려줘", disabled=not(index_ready and chat_config.api_key and chat_config.source_transmission_allowed))
        if question:
            with st.chat_message("user"):
                st.text(question)
            with st.chat_message("assistant"):
                with st.status("상품 조건·공식 문서 근거를 검색하고 답변을 생성합니다.", expanded=False) as progress:
                    try:
                        runtime = get_runtime()
                        runtime.service.document_top_k = top_k
                        runtime.service.search_mode = search_mode
                        runtime.service.fetch_k = fetch_k
                        runtime.service.lambda_mult = lambda_mult
                        result = runtime.service.ask(question, selected, history=[{"role": h["role"], "content": h["content"]} for h in history])
                    except Exception:
                        progress.update(label="처리 실패", state="error")
                        st.error("처리 중 문제가 발생했습니다. 로컬 색인·문서 파일·GMS 연결 설정을 확인하세요.")
                        return
                    progress.update(label="답변 검증 완료" if result["status"] == "answered" else "확인 필요", state="complete" if result["status"] != "api_error" else "error")
                for warning in result.get("warnings", []):
                    st.warning(warning)
                st.markdown(result["answer"])
                show_sources(result.get("cited_evidence", []))
                labels = {"answered": "근거 확인 답변", "api_error": "GMS 연결 오류", "no_candidates": "조건 충족 후보 없음", "needs_input": "입력 확인 필요", "insufficient_evidence": "근거 부족"}
                st.caption(labels.get(result["status"], "확인 필요"))
            history.extend([{"role": "user", "content": question}, {"role": "assistant", "content": result["answer"], "evidence": result.get("cited_evidence", [])}])
    with search_tab:
        st.subheader("유사도와 MMR의 청크 선택 비교")
        st.caption("동일 질문·동일 후보 집합에서 두 방식을 비교합니다. GMS 호출 없이 로컬 벡터만 사용하며 답변 정확도 평가는 별도입니다.")
        comparison_options = selected or [p["product_id"] for p in products if p["product_id"] in linked]
        target = st.selectbox("검색 비교 대상 상품", comparison_options, format_func=lambda pid: by_id[pid]["product_name"], key="retrieval_target")
        comparison_query = st.text_input("검색 비교 질문", value="원금 손실 위험, 환매 방법, 보수와 수수료를 설명해 줘.", key="retrieval_query")
        if st.button("두 검색 방식 비교", disabled=not(index_ready and target and comparison_query.strip()), key="compare_retrieval"):
            with st.spinner("동일한 문서 후보에서 검색 결과를 비교합니다."):
                try:
                    runtime = get_runtime()
                    report = runtime.retriever.compare_documents(comparison_query, [target], document_top_k=top_k, fetch_k=fetch_k, lambda_mult=lambda_mult)
                    st.session_state.retrieval_comparison = {"signature": [target, comparison_query, top_k, fetch_k, lambda_mult], "report": report}
                except Exception:
                    st.error("검색 비교를 수행하지 못했습니다. 색인과 후보 수 설정을 확인하세요.")
        saved = st.session_state.get("retrieval_comparison", {})
        if saved.get("signature") == [target, comparison_query, top_k, fetch_k, lambda_mult]:
            report = saved["report"]
            st.caption(f"후보 상한 {report['fetch_k']} → 중복 정리 후 후보 {report['unique_candidates']} → 최종 최대 {report['k']}개 · 공통 선택 {report['shared_selected_ids']}개")
            if not report["unique_candidates"]:
                st.info("해당 상품에 연결된 문서 검색 결과가 없습니다.")
            for column, mode in zip(st.columns(2), ("similarity", "mmr")):
                with column:
                    data = report["results"][mode]
                    st.markdown("**" + ("유사도 검색" if mode == "similarity" else "MMR 검색") + "**")
                    metrics = data["metrics"]
                    mean = metrics["mean_pairwise_cosine"]
                    st.write(f"선택 {metrics['selected_count']}개 · 문서 {metrics['unique_documents']}개 · 페이지 {metrics['unique_pages']}개")
                    st.caption("청크 간 평균 코사인 유사도: " + (f"{mean:.4f}" if mean is not None else "비교할 쌍 없음"))
                    st.dataframe([{"선택 순서": row["selection_rank"], "후보 순위": row["candidate_rank"], "문서": row["title"],
                        "페이지": row["page_number"], "질문과 거리": round(row["distance"], 4), "내용": row["content"][:200],
                        "공식 원문": row["source_uri"]} for row in data["rows"]], hide_index=True, width="stretch")
            st.caption("청크 간 유사도가 낮으면 선택 내용이 더 다양하다는 뜻입니다. 질문 관련성이나 사실 정확도가 더 높다는 뜻은 아닙니다.")


def get_runtime():
    # Session-local SQLite connection and ONNX resources; no cross-user history.
    if "runtime" not in st.session_state:
        from src.rag.runtime import ChatRuntime
        st.session_state.runtime = ChatRuntime()
    return st.session_state.runtime


if __name__ == "__main__":
    main()
