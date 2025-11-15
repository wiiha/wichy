import secrets

class ContextHandler:
    def __init__(self):
        self.context = []
        self.id = secrets.token_urlsafe(7)

    def __len__(self):
        return len(self.context)

    def __call__(self):
        return self.context

    def append(self, new_object):
        self.context.append(new_object)

    def add(self, role, content):
        x = {"role": role, "content": content}
        self.append(x)


def new_context():
    return ContextHandler()