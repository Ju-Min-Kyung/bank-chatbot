"""In-memory conversations isolated by the complete selected product set."""
def conversation_key(selected_ids):
    return "|".join(sorted(set(selected_ids))) if selected_ids else "exploration"


def conversation(store, selected_ids):
    return store.setdefault(conversation_key(selected_ids), [])
