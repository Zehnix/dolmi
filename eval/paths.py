"""Where the bench's audio lives. The manifest and index files name it 'data/<...>' whatever the folder.

Order: DOLMI_EVAL_DATA, else eval/data/ when it exists, else %LOCALAPPDATA%/dolmi-eval/data (a copy
that outlives a worktree), else eval/data/ (where a fresh build goes). None of it is in git.
"""
import os
from pathlib import Path

EVAL = Path(__file__).resolve().parent
PREFIX = "data/"


def data_dir():
    env = os.environ.get("DOLMI_EVAL_DATA")
    if env:
        return Path(env).expanduser()
    local = EVAL / "data"
    if local.exists():
        return local
    shared = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache") / "dolmi-eval" / "data"
    return shared if shared.exists() else local


def resolve(name):
    """A manifest name ('data/synthetic/s01.wav') -> the file on disk."""
    return data_dir() / name[len(PREFIX):] if name.startswith(PREFIX) else EVAL / name


def name_of(path):
    """A file under the data folder -> its manifest name ('data/...')."""
    return PREFIX + Path(path).resolve().relative_to(data_dir().resolve()).as_posix()
