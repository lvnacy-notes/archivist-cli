# ---------------------------------------------------------------------------
# Output helpers (shared by all changelog and manifest subcommands)
# ---------------------------------------------------------------------------
#
# Thin facade over the "archivist" logger. The five public functions are the
# stable API — their signatures and call sites are unchanged. Internally,
# each one delegates to the logger; the ArchivistStreamHandler configured in
# cli.py handles formatting, routing (stdout vs stderr), and verbosity tiers.
#
# Do NOT import this module before _configure_logging() has run in cli.py.
# The logger will happily accept records before handlers are attached, but
# they'll vanish into the void and you'll spend twenty minutes wondering why
# nothing is printing. Don't be that person.

import logging
import sys
import threading
import time
from contextlib import contextmanager

from archivist.formatter import SUCCESS

ledger = logging.getLogger("archivist")


def error(msg: str) -> None:
    """
    Log an error message. Routes to stderr via ArchivistStreamHandler.
    ❌ prefix is applied by ArchivistTerminalFormatter.
    """
    ledger.error(msg)


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
    Log the dry-run header at INFO level so it respects --quiet.
    """
    ledger.info("=== This is a DRY RUN — no files written ===")
    

def progress(msg: str) -> None:
    """
    Log a progress/informational message at INFO level.
    In --verbose mode, callers that want truly noisy per-file output should
    call log.debug() directly instead of going through this function — that
    keeps the default tier clean without --quiet having to nuke everything.
    """
    ledger.info(msg)


def success(msg: str) -> None:
    """
    Log a success message at the custom SUCCESS level (25).
    ✅ prefix and GREEN styling applied by ArchivistTerminalFormatter.
    """
    ledger.log(SUCCESS, msg)


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
    Log a warning at WARNING level. Routes to stderr via ArchivistStreamHandler.
    ⚠️ prefix applied by ArchivistTerminalFormatter.
    """
    ledger.warning(msg)