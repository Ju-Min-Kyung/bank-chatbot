"""F2003: structured official-name queries, not fabricated URLs."""
from src.rag.prompts import QUERY_PROMPT, QUERY_SCHEMA


def extract_queries(client, product, question):
    name = product["product_name"]
    result = client.structured(QUERY_PROMPT, {"official_name": name, "company": product.get("company"),
                               "product_type": product.get("product_type"), "question": question}, QUERY_SCHEMA, "search_queries")
    queries = [q.strip() for q in result["queries"] if q.strip() and len(q) <= 300]
    if not any(name in query for query in queries):
        queries.insert(0, name + " 공식 투자설명서 규약")
    return list(dict.fromkeys(queries))[:5]
