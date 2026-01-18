from uuid import uuid4


def gen_id():
    return f"{uuid4().hex[:12]}"
