"""Vector-based maximal marginal relevance and measured retrieval diversity."""
import re

import numpy as np


def validate_search(mode, k, fetch_k, lambda_mult):
    if mode not in {"similarity", "mmr"}:
        raise ValueError("search_mode must be similarity or mmr")
    if type(k) is not int or not 0 <= k <= 100:
        raise ValueError("document_top_k must be between 0 and 100")
    if type(fetch_k) is not int or not max(1, k) <= fetch_k <= 300:
        raise ValueError("fetch_k must be at least k and at most 300")
    if not isinstance(lambda_mult, (int, float)) or not np.isfinite(lambda_mult) or not 0 <= lambda_mult <= 1:
        raise ValueError("lambda_mult must be between 0 and 1")


def unit_vectors(vectors):
    values = np.asarray(vectors, dtype=np.float64)
    if values.ndim != 2 or not np.isfinite(values).all():
        raise ValueError("Invalid retrieval embeddings")
    norms = np.linalg.norm(values, axis=1)
    if (norms == 0).any():
        raise ValueError("Zero retrieval embedding")
    return values / norms[:, None]


def select_indices(query_vector, vectors, k, *, mode="mmr", lambda_mult=0.5):
    validate_search(mode, k, max(1, len(vectors), k), lambda_mult)
    if len(vectors) == 0 or k == 0:
        return []
    matrix = unit_vectors(vectors)
    query = unit_vectors([query_vector])[0]
    similarities = matrix @ query
    selected = [int(np.argmax(similarities))]
    if mode == "similarity":
        return sorted(range(len(vectors)), key=lambda i: (-float(similarities[i]), i))[:k]
    # Start with the most relevant hit; then penalize similarity to the
    # *most similar* already-selected chunk (not the average chunk).
    redundancy = matrix @ matrix[selected[0]]
    while len(selected) < min(k, len(vectors)):
        scores = lambda_mult * similarities - (1-lambda_mult) * redundancy
        scores[selected] = -np.inf
        index = int(np.argmax(scores))
        selected.append(index)
        redundancy = np.maximum(redundancy, matrix @ matrix[index])
    return selected


def compact(text):
    return re.sub(r"\s+", "", text)


def diversity_metrics(rows, vectors):
    """Descriptive diagnostics, not relevance/accuracy or a universal threshold."""
    count = len(rows)
    if count > 1:
        matrix = unit_vectors(vectors)
        pairs = (matrix @ matrix.T)[np.triu_indices(count, 1)]
        mean, highest = float(np.mean(pairs)), float(np.max(pairs))
        grams = [{compact(row["content"])[i:i+4] for i in range(max(0, len(compact(row["content"]))-3))} for row in rows]
        overlaps = [len(grams[i] & grams[j])/len(grams[i] | grams[j]) if grams[i] | grams[j] else 0
                    for i in range(count) for j in range(i+1, count)]
        overlap = float(np.mean(overlaps))
    else:
        mean = highest = overlap = None
    return {"selected_count": count, "unique_documents": len({row["document_id"] for row in rows}),
            "unique_pages": len({(row["document_id"], row["page_number"]) for row in rows}),
            "exact_text_duplicates": count-len({compact(row["content"]) for row in rows}),
            "mean_pairwise_cosine": mean, "max_pairwise_cosine": highest,
            "mean_character_4gram_jaccard": overlap}
