"""START -> retrieve -> generate -> END, injected dependencies for offline tests."""
import math
import re
import time
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from src.domain.recommendations import eligible_products
from src.domain.safety import NO_EVIDENCE, NOTICE, sensitive_question
from src.ingestion.query_extractor import extract_queries
from src.ingestion.document_selector import select_documents
from src.rag.conditions import QueryConstraints, check_product, parse_constraints
from src.rag.citations import validated_answer
from src.rag.gms import ModelError
from src.rag.prompts import ANSWER_PROMPT, ANSWER_SCHEMA


class RAGState(TypedDict, total=False):
    question: str
    selected_product_ids: list[str]
    chat_history: list[dict]
    constraint_overrides: dict
    constraints: dict
    candidate_products: list[dict]
    evidence: list[dict]
    cited_evidence: list[dict]
    cited_evidence_ids: list[int]
    answer: str
    claims: list[dict]
    warnings: list[str]
    status: str
    retrieval_audit: list[dict]
    timings: dict
    rejected_claims: int


class RAGService:
    def __init__(self, products, retriever, catalog, client, document_top_k=5, *, search_mode="similarity", fetch_k=30, lambda_mult=0.5):
        self.products = {p["product_id"]: p for p in products}
        self.retriever, self.catalog, self.client = retriever, catalog, client
        self.document_top_k = document_top_k
        self.search_mode, self.fetch_k, self.lambda_mult = search_mode, fetch_k, lambda_mult
        builder = StateGraph(RAGState)
        builder.add_node("retrieve", self.retrieve)
        builder.add_node("generate", self.generate)
        builder.add_edge(START, "retrieve")
        builder.add_edge("retrieve", "generate")
        builder.add_edge("generate", END)
        self.graph = builder.compile()

    def retrieve(self, state):
        started = time.perf_counter()
        question = state["question"].strip()
        constraints, warnings = parse_constraints(question)
        overrides = state.get("constraint_overrides", {})
        # UI can strengthen parsed constraints; cannot silently relax a hard request.
        if overrides.get("principal_required"):
            constraints.principal_required = True
        if overrides.get("minimum_risk_grade"):
            constraints.minimum_risk_grade = max(constraints.minimum_risk_grade or 1, int(overrides["minimum_risk_grade"]))
        if overrides.get("product_type"):
            if constraints.product_type and constraints.product_type != overrides["product_type"]:
                constraints.ambiguous = True
            else:
                constraints.product_type = overrides["product_type"]
        result = {"constraints": constraints.to_dict(), "warnings": warnings, "evidence": [], "candidate_products": [],
                  "retrieval_audit": [], "status": "ready"}
        if not question or len(question) > 1500 or sensitive_question(question):
            result.update(status="needs_input", warnings=warnings + ["질문은 1~1,500자로 입력하고 연락처·주민번호·이메일을 제거하세요."])
            return result
        selected = list(dict.fromkeys(state.get("selected_product_ids", [])))
        if len(selected) > 4 or any(pid not in self.products for pid in selected):
            result.update(status="needs_input", warnings=warnings + ["존재하는 상품을 최대 4개 선택하세요."])
            return result
        eligible = eligible_products(self.products.values(), constraints)
        if selected:
            failures = [(self.products[pid]["product_name"], check_product(self.products[pid], constraints)) for pid in selected]
            failures = [(name, issues) for name, issues in failures if issues]
            if failures:
                result.update(status="no_candidates", warnings=warnings + [name + ": " + ", ".join(issues) for name, issues in failures])
                return result
        else:
            found = self.retriever.search_products(question, top_k=4, allowed_ids=[p["product_id"] for p in eligible]) if eligible else []
            selected = [row["product_id"] for row in found]
        if not selected:
            result.update(status="no_candidates", warnings=warnings + ["확인 가능한 자료에서 조건을 모두 충족한 상품을 찾지 못했습니다."])
            return result
        result["candidate_products"] = [{key: self.products[pid].get(key) for key in ("product_id", "product_name", "product_type")} for pid in selected]
        # Required metadata is always obtained, even if GMS search/selection fails.
        result["evidence"] = self.retriever.retrieve(question, selected, document_top_k=0)
        document_groups = []
        try:
            for pid in selected:
                product = self.products[pid]
                queries = extract_queries(self.client, product, question)
                candidates = self.catalog.search(pid, queries)
                chosen = select_documents(self.client, product, question, candidates)
                result["retrieval_audit"].append({"product_id": pid, "queries": queries,
                    "candidate_ids": [c["id"] for c in candidates], "selection": chosen})
                if not chosen["selected_document_ids"]:
                    warnings.append(product["product_name"] + ": 연결된 공식 PDF가 없습니다. 상품 메타데이터만 사용합니다.")
                    document_groups.append([])
                    continue
                docs = self.retriever.retrieve(question, [pid], document_top_k=max(1, math.ceil(self.document_top_k/len(selected))),
                                               document_ids=chosen["selected_document_ids"], search_mode=self.search_mode,
                                               fetch_k=self.fetch_k, lambda_mult=self.lambda_mult)
                result["retrieval_audit"][-1]["search"] = {"mode": self.search_mode, "fetch_k": self.fetch_k, "lambda_mult": self.lambda_mult,
                    "selected_chunks": len(docs)-1, "unique_candidates": max((row.get("candidate_count", 0) for row in docs), default=0)}
                document_groups.append([row for row in docs if row["source_type"] == "official_pdf"])
            # Round-robin preserves source opportunities for every compared product.
            seen, merged = set(), []
            for i in range(max((len(group) for group in document_groups), default=0)):
                for group in document_groups:
                    if i < len(group):
                        row = group[i]
                        key = (row["product_id"], row["document_id"], row["chunk_index"])
                        if key not in seen:
                            seen.add(key)
                            merged.append(row)
            result["evidence"].extend(merged[:self.document_top_k])
            for i, row in enumerate(result["evidence"], 1):
                row["evidence_id"] = i
        except ModelError as exc:
            result.update(status="api_error", warnings=warnings + [str(exc)])
        result["warnings"] = warnings if result["status"] != "api_error" else result["warnings"]
        result["timings"] = {"retrieve_ms": round((time.perf_counter()-started)*1000, 2)}
        return result

    def generate(self, state):
        started = time.perf_counter()
        empty = {"cited_evidence": [], "cited_evidence_ids": [], "claims": [], "rejected_claims": 0}
        if state["status"] != "ready" or not state.get("evidence"):
            text = "GMS 호출에 실패했습니다. 검색 결과 없음과는 다른 상태입니다." if state["status"] == "api_error" else NO_EVIDENCE
            return {**empty, "answer": text + "\n\n" + NOTICE}
        # Dataset has no verified basis date/live quote feed; do not invite invention.
        if re.search(r"(?:현재|오늘|실시간).{0,12}(?:수익률|판매|가격|세율)", state["question"]):
            return {**empty, "status": "insufficient_evidence", "answer": NO_EVIDENCE + " 현재 시점이 검증된 데이터가 연결되어 있지 않습니다.\n\n" + NOTICE}
        evidence = [{k: row[k] for k in ("evidence_id", "product_id", "product_name", "source_type", "title", "page_number", "link_scope", "content") if k in row} for row in state["evidence"]]
        history = [{"role": item["role"], "content": item["content"][:1500]} for item in state.get("chat_history", [])[-6:]]
        history = [item for item in history if item["role"] in ("user", "assistant") and not sensitive_question(item["content"])]
        try:
            response = self.client.structured(ANSWER_PROMPT, {"question": state["question"], "constraints": state["constraints"],
                "candidate_products": state["candidate_products"], "evidence": evidence, "history": history}, ANSWER_SCHEMA, "grounded_answer")
            result = validated_answer(response, state["evidence"], {p["product_id"]: p["product_name"] for p in state["candidate_products"]})
            result["status"] = "answered" if result["claims"] else "insufficient_evidence"
        except ModelError as exc:
            result = {**empty, "status": "api_error", "answer": str(exc) + "\n\n" + NOTICE}
        result["timings"] = {**state.get("timings", {}), "generate_ms": round((time.perf_counter()-started)*1000, 2)}
        return result

    def ask(self, question, selected_product_ids=(), history=(), constraint_overrides=None):
        return self.graph.invoke({"question": question, "selected_product_ids": list(selected_product_ids),
                                  "chat_history": list(history), "constraint_overrides": constraint_overrides or {}})
