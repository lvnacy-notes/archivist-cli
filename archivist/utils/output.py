# ---------------------------------------------------------------------------
# Output helpers (shared by all changelog and manifest subcommands)
# ---------------------------------------------------------------------------

import sys
import threading
import time
from contextlib import contextmanager


def error(msg: str) -> None:
    """
    Print an error message to stderr with ❌ emoji prefix.
    """
    print(f"❌  {msg}", file=sys.stderr)


def get_action_verb(dry_run: bool, present: str, past: str) -> str:
    """
    Return the appropriate verb tense based on dry-run status.
    
    Args:
        dry_run: Whether this is a dry run
        present: Present tense verb (e.g., "will add")
        past: Past tense verb (e.g., "added")
    
    Returns:
        The appropriate verb form for the context.
    """
    return present if dry_run else past


def print_dry_run_header() -> None:
    """
    Print the dry-run header message to indicate no writes will occur.
    """
    print("=== This is a DRY RUN — no files written ===")
    

def progress(msg: str) -> None:
    """
    Print a progress message to stdout for informational output.
    """
    print(msg)


def success(msg: str) -> None:
    """
    Print a success message to stdout with ✅ emoji prefix.
    """
    print(f"✅ {msg}")


@contextmanager
def spinner(message: str = "Working"):
    """
    Context manager that displays a spinning cursor while work is being done.
    
    Usage:
        with spinner("Comparing files"):
            expensive_operation()
    
    The spinner runs in a background thread and is cleaned up when the context
    exits (successfully or via exception).
    """
    stop_event = threading.Event()
    frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    frame_index = [0]
    
    def spin():
        while not stop_event.is_set():
            frame = frames[frame_index[0] % len(frames)]
            sys.stdout.write(f"\r{frame}  {message}")
            sys.stdout.flush()
            frame_index[0] += 1
            time.sleep(0.08)
        # Clear the line when done
        sys.stdout.write(f"\r{' ' * (len(message) + 4)}\r")
        sys.stdout.flush()
    
    thread = threading.Thread(target=spin, daemon=True)
    thread.start()
    
    try:
        yield
    finally:
        stop_event.set()
        thread.join(timeout=0.5)



def warning(msg: str) -> None:
    """
    Print a warning message to stderr with ⚠️ emoji prefix.
    """
    print(f"⚠️  {msg}", file=sys.stderr)