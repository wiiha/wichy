import subprocess

from wichy.config import settings


def needs_user_attention():
    """
    Executes the configured user attention script if set.

    Reads script path from settings.needs_user_attention_script.
    If not set or empty, returns without action.
    If script execution fails, fails silently (no-op).
    """
    script = settings.needs_user_attention_script
    if not script:
        return

    try:
        subprocess.run([script], check=False)
    except Exception:
        # Silently ignore any errors - no-op behavior
        pass


if __name__ == "__main__":
    needs_user_attention()
