from uuid import UUID, uuid4


def new_uuid() -> UUID:
    return uuid4()


def new_id(prefix: str = "id") -> str:
    """Return a stable string id for the newer backend modules."""
    clean_prefix = prefix.strip().lower() or "id"
    return f"{clean_prefix}_{uuid4().hex}"
