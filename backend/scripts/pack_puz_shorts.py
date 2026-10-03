"""Split Puz_shorts batches into one episode folder per video."""

from __future__ import annotations

import json
import re
import shutil
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "episodes" / "Puz_shorts"
EPISODES = ROOT / "episodes"

SERIES = {
    "alien_finals": {
        "id_prefix": "ALIEN",
        "title": "Alien Monkeys",
        "tags": ["puzmania", "shorts", "alien", "puzzle"],
        "blurb": "A Puzmania short from the Alien Monkeys series.",
    },
    "blur_finals": {
        "id_prefix": "BLUR",
        "title": "Blur",
        "tags": ["puzmania", "shorts", "blur", "puzzle"],
        "blurb": "A Puzmania short from the Blur series.",
    },
    "jig_finals": {
        "id_prefix": "JIG",
        "title": "Jigsaw",
        "tags": ["puzmania", "shorts", "jigsaw", "puzzle"],
        "blurb": "A Puzmania short from the Jigsaw series.",
    },
}

NUM = re.compile(r"(\d+)\s*$")


def episode_number(path: Path) -> int:
    match = NUM.search(path.stem)
    if not match:
        raise ValueError(f"No episode number in {path.name}")
    return int(match.group(1))


def main() -> None:
    if not SOURCE.is_dir():
        raise SystemExit(f"Source folder not found: {SOURCE}")

    jobs: list[tuple[Path, dict, int]] = []
    for folder_name, series in SERIES.items():
        batch = SOURCE / folder_name
        if not batch.is_dir():
            print(f"Skip missing batch {batch}")
            continue
        videos = sorted(
            (p for p in batch.iterdir() if p.suffix.lower() == ".mp4"),
            key=episode_number,
        )
        for video in videos:
            jobs.append((video, series, episode_number(video)))

    if not jobs:
        raise SystemExit("No mp4 files found to pack.")

    start = date.today()
    packed = 0
    for index, (video, series, number) in enumerate(jobs):
        episode_id = f"{series['id_prefix']}_{number:02d}"
        dest = EPISODES / episode_id
        dest.mkdir(parents=True, exist_ok=True)
        dest_video = dest / video.name
        if video.resolve() != dest_video.resolve():
            if dest_video.exists():
                dest_video.unlink()
            shutil.move(str(video), str(dest_video))
        meta = {
            "id": episode_id,
            "title": f"{series['title']} {number:02d}",
            "description": f"{series['blurb']} Episode {number:02d}.",
            "tags": series["tags"],
            "filename": dest_video.name,
            "scheduled": (start + timedelta(days=index)).isoformat(),
        }
        (dest / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        packed += 1
        print(f"Packed {episode_id} <- {video.name}")

    for leftover in SOURCE.rglob("*"):
        if leftover.is_dir():
            continue
        print(f"Left behind: {leftover}")

    shutil.rmtree(SOURCE, ignore_errors=True)
    print(f"Done. Packed {packed} episodes into {EPISODES}")


if __name__ == "__main__":
    main()
