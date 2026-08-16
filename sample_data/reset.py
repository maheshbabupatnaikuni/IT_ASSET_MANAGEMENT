"""Safely replace only the ignored application runtime directory."""

from pathlib import Path
import shutil

from config import RUNTIME_DIR
from sample_data.seed import seed


def reset() -> dict[str, int]:
    project_root = Path(__file__).resolve().parents[1]
    runtime = RUNTIME_DIR.resolve()
    if runtime.parent != project_root or runtime.name != ".runtime":
        raise RuntimeError(f"Refusing to reset unexpected path: {runtime}")
    if runtime.exists():
        shutil.rmtree(runtime)
    runtime.mkdir(parents=True, exist_ok=True)
    return seed()


if __name__ == "__main__":
    counts = reset()
    print(f"Reset complete: {counts['assets']} assets, {counts['infrastructure']} infrastructure items.")
