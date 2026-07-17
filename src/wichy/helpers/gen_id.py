from uuid import uuid4


def gen_id() -> str:
    return uuid4().hex[:12]
