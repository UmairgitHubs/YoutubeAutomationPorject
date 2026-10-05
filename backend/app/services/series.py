from __future__ import annotations

import re

SERIES_PREFIX = {"JIG": "Jig Puzzle", "ALIEN": "Alien Monkeys", "BLUR": "Blur"}
SERIES_ROTATION = ("JIG", "ALIEN", "BLUR")
SERIES_DIR_ALIASES = {
    "alien_finals": "ALIEN",
    "alien": "ALIEN",
    "aliens": "ALIEN",
    "alien_monkeys": "ALIEN",
    "alienmonkeys": "ALIEN",
    "blur_finals": "BLUR",
    "blur": "BLUR",
    "blurs": "BLUR",
    "jig_finals": "JIG",
    "jig": "JIG",
    "jigsaw": "JIG",
    "jig_puzzle": "JIG",
    "jigpuzzle": "JIG",
}


def folder_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def series_of(episode_id: str) -> str | None:
    name = (episode_id or "").upper()
    for prefix in SERIES_PREFIX:
        if name.startswith(prefix + "_"):
            return prefix
    return SERIES_DIR_ALIASES.get(folder_key(episode_id or ""))


def series_from_folder(name: str) -> str | None:
    return SERIES_DIR_ALIASES.get(folder_key(name))


def episode_number(episode_id: str) -> int:
    match = re.search(r"(\d+)\s*$", (episode_id or "").rsplit(".", 1)[0])
    return int(match.group(1)) if match else 1


def parse_cycle(raw: str, fallback: str) -> list[str]:
    text = re.sub(r"[^A-Za-z0-9]", "", (raw or "").strip().upper()) or fallback
    if text.isdigit():
        return list(text)
    if text.isalpha():
        return list(text)
    return list(fallback)


def answer_key(series: str, number: int, cycle: list[str], offset: int = 0) -> str:
    if not cycle:
        return "A"
    index = (max(1, int(number)) - 1 + max(0, int(offset))) % len(cycle)
    return cycle[index]


def detect_offset(cycle: list[str], samples: list[tuple[int, str]]) -> int:
    """Find the cycle offset so sample episode numbers map to the given keys."""
    if not cycle or not samples:
        return 0
    keys = [k.upper() for k in cycle]
    for offset in range(len(keys)):
        if all(answer_key("X", number, keys, offset) == str(mark).strip().upper() for number, mark in samples):
            return offset
    return 0
