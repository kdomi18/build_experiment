from pathlib import Path


def ensure_directory(path: Path):
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
    return path


def find_project_root() -> Path:
    current = Path.cwd()
    for parent in [current, *current.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("Could not find project root (pyproject.toml)")


PROJECT_ROOT = find_project_root()
DATA_DIR = PROJECT_ROOT / "data"
