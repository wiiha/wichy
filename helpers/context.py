import secrets
import hashlib
import json
import os
from os.path import isfile, join
from datetime import datetime
from rich import print

CONTEXT_DIR = ".wichy/contexts/"
CONTEXT_FILE_EXT = ".json"


class ContextHandler:
    def __init__(self, custom_suffix="", sub_dir=""):
        self.context = []
        h = hashlib.sha256()
        h.update(secrets.token_urlsafe(10).encode())
        self.id = h.hexdigest()
        self.start_date = datetime.now().strftime("%Y-%m-%d")
        self.custom_suffix = custom_suffix
        self.sub_dir = sub_dir
        self._ensure_context_dir()

    def _ensure_context_dir(self):
        """Ensure the .wichy/contexts directory exists."""
        self.context_dir = CONTEXT_DIR
        if self.sub_dir != "":
            self.context_dir += self.sub_dir + "/"
        os.makedirs(self.context_dir, exist_ok=True)

    def __len__(self):
        return len(self.context)

    def __call__(self):
        return self.context

    def append(self, new_object):
        """Append a new object to context and log it to file."""
        self.context.append(new_object)
        self._save_to_file(new_object)

    def add(self, role, content):
        """Add a new message to context and log it to file. Helper method"""
        x = {"role": role, "content": content}
        self.append(x)

    def _save_to_file(self, new_object):
        """Save the new object as a JSON object on a new line in the log file."""
        save_path = self.context_dir + self.start_date + "_" + self.id
        if self.custom_suffix != "":
            save_path += "_" + self.custom_suffix
        save_path += CONTEXT_FILE_EXT
        log_file_path = save_path
        try:
            with open(log_file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(new_object) + "\n")
        except Exception as e:
            print(f"[red]Error saving context to file:[/red] {e}")


def new_context():
    return ContextHandler()


def context_from_file(path):
    lines = []
    with open(path, "r") as f:
        lines = f.readlines()

    if len(lines) == 0:
        raise ValueError("no lines in context, check content of " + path)

    ctx = []
    for l in lines:
        l = l.strip()
        if l == "":
            continue
        c = json.loads(l)
        ctx.append(c)

    # id and date can be inferred from path
    filename = str(path).split("/")[-1]
    filename = filename[: -len(CONTEXT_FILE_EXT)]
    parts = filename.split("_")
    x = []
    for p in parts:
        if p.strip() == "":
            continue
        x.append(p)
    parts = x
    ctx_date = parts[0]
    ctx_id = parts[1]
    ctx_suffix = ""
    if len(parts) > 2:
        # we have some suffix
        s = "_".join(parts[2:])
        ctx_suffix = s

    path_parts = str(path).split("/")
    ctx_sub_dir = ""
    if path_parts.index(CONTEXT_DIR) != (len(path_parts) - 2):
        # we have a sub dir
        ctx_sub_dir = path_parts[-2]

    ch = ContextHandler(custom_suffix=ctx_suffix, sub_dir=ctx_sub_dir)
    ch.start_date = ctx_date
    ch.id = ctx_id
    ch.context = ctx

    return ch


def previous_conversations():
    cs = [f for f in os.listdir(CONTEXT_DIR) if isfile(join(CONTEXT_DIR, f))]
    return cs


if __name__ == "__main__":
    for c in previous_conversations():
        print(c)
