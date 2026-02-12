import json
import os
import time
from datetime import datetime
from os.path import isfile, join

from rich import print

from wichy.helpers.file import drop_last_n_lines

CONTEXT_DIR = ".wichy/contexts/"
CONTEXT_FILE_EXT = ".json"


class ContextHandler:
    """
    A class to handle context management for conversations.

    This class manages a list of conversation messages and provides methods to
    add, save, and delete context data. It also ensures the necessary directory
    structure exists for storing context files.
    """

    def __init__(self, custom_suffix="", sub_dir=""):
        """
        Initialize a new ContextHandler instance.

        Args:
            custom_suffix (str, optional): Custom suffix to add to the context file name. Defaults to empty string.
            sub_dir (str, optional): Subdirectory to store context files in. Defaults to empty string.

        Attributes:
            context (list): List of conversation messages.
            id (str): Unique identifier for this context instance (based on current time).
            start_date (str): Date when this context was created (YYYY-MM-DD format).
            custom_suffix (str): Custom suffix for the context file name.
            sub_dir (str): Subdirectory for storing context files.
            context_dir (str): Full path to the context directory.
        """
        self.context = []
        # generating a time based id is fine under the assumption that this will
        # be running on a local machine only and not in a multi user setup.
        self.id = str(time.time()).split(".")[0]
        self.start_date = datetime.now().strftime("%Y-%m-%d")
        self.custom_suffix = custom_suffix
        self.sub_dir = sub_dir
        self._ensure_context_dir()

    def _ensure_context_dir(self):
        """
        Ensure the .wichy/contexts directory exists.

        This method creates the context directory if it doesn't exist. If a subdirectory
        is specified, it will create that as well.
        """
        self.context_dir = CONTEXT_DIR
        if self.sub_dir != "":
            self.context_dir += self.sub_dir + "/"
        os.makedirs(self.context_dir, exist_ok=True)

    def __len__(self):
        """
        Return the number of messages in the context.

        Returns:
            int: The number of messages in the context.
        """
        return len(self.context)

    def __call__(self):
        """
        Return the context as a list.

        Returns:
            list: The current context messages.
        """
        return self.context

    def append(self, new_object):
        """
        Append a new object to context and log it to file.

        Args:
            new_object (dict): The object to add to the context. Should be a dictionary
                with at least 'role' and 'content' keys.

        Side effects:
            - Adds the object to the context list
            - Saves the object to a JSON file
        """
        self.context.append(new_object)
        self._save_to_file(new_object)

    def add(self, role, content):
        """
        Add a new message to context and log it to file. Helper method.

        Args:
            role (str): The role of the message (e.g., 'user', 'assistant')
            content (str): The content of the message

        Side effects:
            - Creates a new message dictionary
            - Adds it to the context list
            - Saves it to a JSON file
        """
        x = {"role": role, "content": content}
        self.append(x)

    def drop(self, n: int = 1):
        """
        drops the n last items from the context,
        this will also modify the corresponding file
        on disk.

        :param n: Number of items to drop. n < 1 is a no-op.
        :type n: int
        """
        if n < 1:
            return

        try:
            drop_last_n_lines(filename=self._gen_save_path(), n=n)
        except Exception as e:
            print(f"[red]Error dropping lines from file:[/red] {e}")
            return

        # all good, remove from context
        self.context = self.context[:-n]

    def _save_to_file(self, new_object):
        """
        Save the new object as a JSON object on a new line in the log file.

        Args:
            new_object (dict): The object to save to file

        Side effects:
            - Creates or appends to a context file with a name based on start date,
              ID, and custom suffix
            - Writes the object as a JSON string followed by a newline
        """
        log_file_path = self._gen_save_path()
        try:
            with open(log_file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(new_object) + "\n")
        except Exception as e:
            print(f"[red]Error saving context to file:[/red] {e}")

    def _gen_save_path(self) -> str:
        save_path = self.context_dir + self.start_date + "_" + self.id
        if self.custom_suffix != "":
            save_path += "_" + self.custom_suffix
        save_path += CONTEXT_FILE_EXT
        return save_path

    def delete(self):
        """
        Delete the context file.

        This method deletes the context file associated with this instance.

        Side effects:
            - Removes the context file from disk
        """
        save_path = self._gen_save_path()
        os.remove(save_path)


def new_context():
    """
    Create a new ContextHandler instance.

    Returns:
        ContextHandler: A new context handler instance
    """
    return ContextHandler()


def context_from_file(path):
    """
    Load a context from a file.

    Args:
        path (str): Path to the context file

    Returns:
        ContextHandler: A new context handler instance loaded with data from the file

    Raises:
        ValueError: If the file is empty
    """
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
    if path_parts.index("contexts") != (len(path_parts) - 2):
        # we have a sub dir
        ctx_sub_dir = path_parts[-2]

    ch = ContextHandler(custom_suffix=ctx_suffix, sub_dir=ctx_sub_dir)
    ch.start_date = ctx_date
    ch.id = ctx_id
    ch.context = ctx

    return ch


def previous_conversations():
    """
    Get a list of previous conversation files.

    Returns:
        list: List of context file names in the contexts directory
    """
    cs = [f for f in os.listdir(CONTEXT_DIR) if isfile(join(CONTEXT_DIR, f))]
    return cs


if __name__ == "__main__":
    for c in previous_conversations():
        print(c)
