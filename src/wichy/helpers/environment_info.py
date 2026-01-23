import os
import platform
import subprocess
from datetime import date


def environment_information():
    """Gather and print environment information including working directory, git status, platform, and date."""

    def is_git_repo(path):
        """Check if the given path is inside a git repository."""
        if os.path.isdir(os.path.join(path, ".git")):
            return True

        try:
            result = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=path,
                capture_output=True,
                text=True,
                check=False,
            )
            return result.returncode == 0 and result.stdout.strip().lower() == "true"
        except FileNotFoundError:
            # git command not found
            return False

    cwd = os.getcwd()
    git_repo = "Yes" if is_git_repo(cwd) else "No"
    plat = platform.system().lower()
    os_ver = platform.release()
    today = date.today().isoformat()

    return f"""<env>
Working directory: {cwd}
Is directory a git repo: {git_repo}
Platform: {plat}
OS Version: {os_ver}
Today's date: {today}
</env>"""


def main():
    print(environment_information())


if __name__ == "__main__":
    main()
