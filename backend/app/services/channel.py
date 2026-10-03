from __future__ import annotations

import json
import logging
import random
import re
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT
from app.database import safe_flush
from app.models import Episode, Member, Puzzle, PuzzleAttempt
from app.services.join import _normalize

log = logging.getLogger("puzmania.channel")

TIMER_START = 100
POINTS_FACTOR = 9
SERIES_PREFIX = {"JIG": "Jig Puzzle", "ALIEN": "Alien Monkeys", "BLUR": "Blur"}
MISS_MESSAGES = [
    "You missed this time",
    "Next time I hope",
    "Try another puzzle",
    "So close — keep going",
    "Not that one, friend",
    "Almost! Pick a new video",
    "Nice try — another puzzle awaits",
    "That was a tricky one",
    "Shake it off and try a new clip",
    "Oops! Your points stay with you",
    "Wrong guess, still a star",
    "Keep smiling and try the next one",
    "Not today — the next puzzle is waiting",
    "Good effort! Choose another video",
    "Missed it — no points lost",
    "Have another go on a new puzzle",
    "That answer slipped away",
    "Brave try! On to the next clip",
    "The picture fooled you this time",
    "One miss, many more videos to play",
    "Don't worry — try a different puzzle",
    "Next video, new chance",
    "You can do the next one",
    "Keep playing, champion",
]

JIG_BANK = [
    ("A lion", "A car", "A house", "A boat"),
    ("An elephant", "A clock", "A shoe", "A kite"),
    ("A giraffe", "A spoon", "A lamp", "A drum"),
    ("A panda", "A truck", "A hat", "A chair"),
    ("A dolphin", "A tree", "A bike", "A cake"),
    ("A butterfly", "A hammer", "A sock", "A train"),
    ("A rocket", "A flower", "A fish", "A cup"),
    ("A castle", "A banana", "A phone", "A frog"),
    ("A train", "A star", "A brush", "A cloud"),
    ("A bicycle", "A tiger", "A book", "A moon"),
    ("A rainbow", "A robot", "A door", "A map"),
    ("A sunflower", "A plane", "A bell", "A ring"),
    ("A penguin", "A wagon", "A leaf", "A coin"),
    ("A koala", "A ladder", "A drum", "A nest"),
    ("A tiger", "A bridge", "A pear", "A flag"),
    ("A turtle", "A guitar", "A key", "A tent"),
    ("A lighthouse", "A penguin", "A sled", "A bowl"),
    ("A sailboat", "A pizza", "A glove", "A fence"),
    ("A hot air balloon", "A camel", "A nail", "A jar"),
    ("An ice cream", "A whale", "A rope", "A desk"),
    ("A soccer ball", "A candle", "A crane", "A pot"),
    ("A guitar", "A zebra", "A towel", "A gate"),
    ("A piano", "A rocket", "A brush", "A well"),
    ("A robot", "A muffin", "A swing", "A dock"),
    ("A dinosaur", "A radio", "A peach", "A barn"),
    ("A unicorn", "A wagon", "A stamp", "A tray"),
    ("A volcano", "A kitten", "A scarf", "A bin"),
    ("A waterfall", "A helmet", "A grape", "A net"),
    ("A mushroom", "A trumpet", "A brick", "A pin"),
    ("A cupcake", "A tractor", "A shell", "A rod"),
    ("An owl", "A bucket", "A chili", "A tap"),
    ("A fox", "A kettle", "A stamp", "A lid"),
    ("A hedgehog", "A crayon", "A plank", "A peg"),
    ("A parrot", "A toaster", "A bead", "A cap"),
    ("A seahorse", "A magnet", "A cork", "A pad"),
    ("An octopus", "A helmet", "A lime", "A tag"),
    ("A camel", "A blender", "A twig", "A cub"),
    ("A kangaroo", "A suitcase", "A tile", "A mug"),
    ("A zebra", "A trumpet", "A vine", "A cub"),
    ("A hippo", "A flashlight", "A cone", "A bun"),
    ("A crocodile", "A backpack", "A cube", "A pit"),
    ("A flamingo", "A scooter", "A dart", "A log"),
    ("A peacock", "A lantern", "A bead", "A hut"),
    ("A squirrel", "A compass", "A rind", "A den"),
    ("A beaver", "A telescope", "A pod", "A sty"),
    ("A whale", "A bicycle", "A pip", "A inn"),
    ("A starfish", "A sandwich", "A nub", "A lab"),
    ("A snowman", "A guitar", "A wad", "A den"),
    ("An igloo", "A tractor", "A sap", "A loft"),
    ("A windmill", "A penguin", "A husk", "A coop"),
    ("A tractor", "A castle", "A sprig", "A shed"),
    ("A helicopter", "A dolphin", "A kernel", "A dock"),
]

ALIEN_BANK = [
    ("A blue monkey dancing", "A red bus waiting", "A sleeping cat", "A flying kite"),
    ("An orange monkey jumping", "A parked bicycle", "A snowman melting", "A quiet library"),
    ("A green monkey waving", "A baking oven", "A closed shop", "A paper plane"),
    ("A yellow monkey spinning", "A rusty key", "A empty bowl", "A dusty book"),
    ("Two monkeys high-fiving", "A leaking tap", "A folded map", "A silent drum"),
    ("A monkey hiding a banana", "A wooden spoon", "A rainy bench", "A broken crayon"),
    ("A monkey on a spaceship", "A garden hose", "A wool hat", "A rusty nail"),
    ("A monkey with a helmet", "A picnic basket", "A wet sock", "A flat tire"),
    ("Monkeys playing drums", "A sleepy owl", "A cracked cup", "A torn page"),
    ("A monkey chasing stars", "A parked tram", "A cold soup", "A blunt pencil"),
    ("A purple alien waving", "A grocery cart", "A dry well", "A faded stamp"),
    ("Monkeys building a rocket", "A quiet pond", "A spare button", "A dusty shelf"),
    ("A monkey sliding down", "A locked gate", "A stale bread", "A missing sock"),
    ("A monkey wearing goggles", "A wilted rose", "A empty jar", "A bent fork"),
    ("Monkeys bouncing on a moon", "A slow turtle", "A torn flag", "A dull coin"),
    ("A monkey painting a planet", "A rusty bike", "A cold pizza", "A cracked tile"),
    ("A monkey catching a star", "A parked jeep", "A dry marker", "A loose screw"),
    ("Monkeys in a candy ship", "A silent piano", "A faded photo", "A spare tyre"),
    ("A monkey with a laser stick", "A laundry basket", "A soggy map", "A blunt saw"),
    ("A monkey riding a comet", "A stacked chair", "A dusty lamp", "A torn net"),
    ("Monkeys sharing a moon cookie", "A closed book", "A rusty hinge", "A spare lid"),
    ("A monkey floating in bubbles", "A garden rake", "A cold tea", "A bent pin"),
    ("A monkey fixing an antenna", "A wooden crate", "A dry sponge", "A faded rug"),
    ("Monkeys dancing in neon", "A parked scooter", "A stale cake", "A cracked jar"),
    ("A monkey peeking from a crater", "A metal bucket", "A limp balloon", "A spare knob"),
    ("A monkey hugging a planet", "A folded towel", "A rusty latch", "A dull blade"),
    ("Monkeys racing on beams", "A quiet hallway", "A dry well", "A torn strap"),
    ("A monkey wearing jet boots", "A picnic rug", "A cold grill", "A spare hook"),
    ("A monkey juggling moons", "A stacked crate", "A faded sign", "A bent hoop"),
    ("Monkeys waving from a porthole", "A garden gnome", "A dry pond", "A loose tile"),
    ("A monkey with a glow stick", "A wooden stool", "A stale chip", "A spare clip"),
    ("A monkey hopping on asteroids", "A parked van", "A dusty tray", "A torn lace"),
    ("Monkeys building a radio", "A quiet attic", "A cold bun", "A rusted tap"),
    ("A monkey wearing star shoes", "A metal bowl", "A limp leaf", "A spare tack"),
    ("A monkey hiding behind Saturn", "A folded sheet", "A dry sponge", "A bent rod"),
    ("Monkeys clapping for a launch", "A garden path", "A stale roll", "A faded tag"),
    ("A monkey steering a saucer", "A wooden bench", "A cold pie", "A spare bead"),
    ("A monkey holding a space map", "A stacked box", "A dusty pane", "A torn cuff"),
    ("Monkeys bouncing in zero-g", "A quiet cellar", "A dry crust", "A rusted pin"),
    ("A monkey with rainbow antenna", "A metal tray", "A limp vine", "A spare ring"),
    ("A monkey painting the sky", "A folded quilt", "A cold mash", "A bent wire"),
    ("Monkeys sliding on moon dust", "A garden shed", "A stale tart", "A faded tab"),
    ("A monkey wearing a visor", "A wooden chest", "A dry crumb", "A spare bolt"),
    ("A monkey catching a comet tail", "A stacked tin", "A dusty rail", "A torn hem"),
    ("Monkeys cheering a countdown", "A quiet loft", "A cold stew", "A rusted nut"),
    ("A monkey hugging a satellite", "A metal bin", "A limp stem", "A spare washer"),
    ("A monkey hopping through rings", "A folded mat", "A dry peel", "A bent clip"),
    ("Monkeys packing star snacks", "A garden gate", "A stale bun", "A faded mark"),
    ("A monkey waving a glow flag", "A wooden oar", "A cold mash", "A spare peg"),
    ("A monkey riding a moon buggy", "A stacked pail", "A dusty sash", "A torn rim"),
    ("Monkeys tapping a space drum", "A quiet alcove", "A dry rind", "A rusted cap"),
    ("A monkey planting a star flag", "A metal scoop", "A limp frond", "A spare stud"),
]

BLUR_BANK = [
    ("A cat", "A truck", "A cloud", "A spoon"),
    ("A dog", "A piano", "A brick", "A kite"),
    ("A bicycle", "A tiger", "A bowl", "A nail"),
    ("A car", "A penguin", "A drum", "A leaf"),
    ("A house", "A dolphin", "A sock", "A coin"),
    ("An apple", "A rocket", "A glove", "A rope"),
    ("A banana", "A castle", "A bell", "A pin"),
    ("A flower", "A robot", "A tent", "A lid"),
    ("A bird", "A tractor", "A mug", "A peg"),
    ("A fish", "A helicopter", "A jar", "A cub"),
    ("A tree", "A guitar", "A cap", "A den"),
    ("A ball", "A lighthouse", "A tray", "A hut"),
    ("A shoe", "A volcano", "A bin", "A sty"),
    ("A hat", "A waterfall", "A pot", "A inn"),
    ("A clock", "A mushroom", "A net", "A lab"),
    ("A book", "A cupcake", "A rod", "A loft"),
    ("A chair", "An owl", "A tap", "A coop"),
    ("A table", "A fox", "A lid", "A shed"),
    ("A phone", "A hedgehog", "A pad", "A dock"),
    ("A key", "A parrot", "A tag", "A barn"),
    ("A cup", "A seahorse", "A cub", "A well"),
    ("A plate", "An octopus", "A bun", "A gate"),
    ("A spoon", "A camel", "A pit", "A fence"),
    ("A fork", "A kangaroo", "A log", "A bridge"),
    ("A bottle", "A zebra", "A hut", "A tunnel"),
    ("A bag", "A hippo", "A den", "A tower"),
    ("A backpack", "A crocodile", "A sty", "A palace"),
    ("A camera", "A flamingo", "A inn", "A cabin"),
    ("A lamp", "A peacock", "A lab", "A hut"),
    ("A candle", "A squirrel", "A loft", "A shed"),
    ("A teddy bear", "A beaver", "A coop", "A dock"),
    ("A doll", "A whale", "A shed", "A barn"),
    ("A crayon", "A starfish", "A dock", "A well"),
    ("A paintbrush", "A snowman", "A barn", "A gate"),
    ("A kite", "An igloo", "A well", "A fence"),
    ("A balloon", "A windmill", "A gate", "A bridge"),
    ("A drum", "A tractor", "A fence", "A tunnel"),
    ("A whistle", "A helicopter", "A bridge", "A tower"),
    ("A bell", "A lion", "A tunnel", "A palace"),
    ("A star", "An elephant", "A tower", "A cabin"),
    ("A moon", "A giraffe", "A palace", "A hut"),
    ("A sun", "A panda", "A cabin", "A loft"),
    ("A cloud", "A dolphin", "A hut", "A coop"),
    ("A raindrop", "A butterfly", "A loft", "A sty"),
    ("A snowflake", "A rocket", "A coop", "A den"),
    ("A leaf", "A castle", "A sty", "A pit"),
    ("A feather", "A train", "A den", "A log"),
    ("A shell", "A bicycle", "A pit", "A rod"),
    ("A pebble", "A rainbow", "A log", "A tap"),
    ("A button", "A sunflower", "A rod", "A peg"),
    ("A ribbon", "A penguin", "A tap", "A lid"),
    ("A cookie", "A koala", "A peg", "A pad"),
]


def seed_channel_puzzles(db: Session, episodes_dir: Path | None = None) -> int:
    """Add one channel puzzle per JIG/ALIEN/BLUR video. Never overwrites saved answers."""
    root = Path(episodes_dir or PROJECT_ROOT / "episodes")
    added = 0
    if not root.is_dir():
        return 0
    existing = set(db.scalars(select(Puzzle.episode_id).where(Puzzle.episode_id.is_not(None))))
    for folder in sorted(root.iterdir(), key=lambda path: path.name):
        if not folder.is_dir():
            continue
        episode_id = folder.name.upper()
        series = _series_of(episode_id)
        if not series:
            continue
        if episode_id in existing:
            continue
        has_package = (folder / "metadata.json").is_file() or any(folder.glob("*.mp4"))
        if not has_package:
            continue
        spec = _spec_for(episode_id)
        db.add(
            Puzzle(
                prompt=spec["prompt"],
                answer=spec["answer"],
                choices=json.dumps(spec["choices"]),
                active=True,
                episode_id=episode_id,
                series=series,
            )
        )
        added += 1
    if added:
        safe_flush(db)
        log.info("Seeded %s channel video puzzles", added)
    return added


def _series_of(episode_id: str) -> str | None:
    name = (episode_id or "").upper()
    for prefix in SERIES_PREFIX:
        if name.startswith(prefix + "_"):
            return prefix
    return None


def _episode_number(episode_id: str) -> int:
    match = re.search(r"_(\d+)$", episode_id or "")
    return int(match.group(1)) if match else 1


def _spec_for(episode_id: str) -> dict:
    series = _series_of(episode_id) or "JIG"
    number = _episode_number(episode_id)
    bank = {"JIG": JIG_BANK, "ALIEN": ALIEN_BANK, "BLUR": BLUR_BANK}[series]
    answer, w1, w2, w3 = bank[(number - 1) % len(bank)]
    prompt = {
        "JIG": f"What picture is this jigsaw making? ({SERIES_PREFIX[series]} #{number:02d})",
        "ALIEN": f"What is happening in this clip? ({SERIES_PREFIX[series]} #{number:02d})",
        "BLUR": f"What is the blurred picture? ({SERIES_PREFIX[series]} #{number:02d})",
    }[series]
    choices = [answer, w1, w2, w3]
    random.Random(episode_id).shuffle(choices)
    return {"prompt": prompt, "answer": answer, "choices": choices}


def video_file(episode_id: str, db: Session | None = None) -> Path | None:
    name = (episode_id or "").strip().upper()
    if not name or not _series_of(name):
        return None
    folders: list[Path] = []
    filename = ""
    if db is not None:
        episode = db.get(Episode, name)
        if episode:
            filename = episode.filename or ""
            if episode.folder_path:
                folders.append(Path(episode.folder_path))
    folders.append(PROJECT_ROOT / "episodes" / name)
    seen: set[Path] = set()
    for folder in folders:
        if folder in seen or not folder.is_dir():
            continue
        seen.add(folder)
        if filename:
            candidate = folder / filename
            if candidate.is_file():
                return candidate
        found = next((p for p in folder.iterdir() if p.suffix.lower() in {".mp4", ".mov", ".m4v", ".webm"}), None)
        if found:
            return found
    return None


def catalog(db: Session, member: Member | None) -> list[dict]:
    rows = list(
        db.scalars(
            select(Puzzle)
            .where(Puzzle.active.is_(True), Puzzle.episode_id.is_not(None))
            .order_by(Puzzle.series, Puzzle.episode_id)
        )
    )
    attempts = {}
    if member:
        attempts = {
            row.puzzle_id: row
            for row in db.scalars(select(PuzzleAttempt).where(PuzzleAttempt.member_id == member.id))
        }
    out = []
    for row in rows:
        attempt = attempts.get(row.id)
        try:
            choices = json.loads(row.choices or "[]")
        except json.JSONDecodeError:
            choices = []
        out.append(
            {
                "id": row.id,
                "episodeId": row.episode_id,
                "series": row.series,
                "title": f"{SERIES_PREFIX.get(row.series or '', row.series)} {row.episode_id}",
                "prompt": row.prompt,
                "choices": choices,
                "hasVideo": video_file(row.episode_id or "", db) is not None,
                "solved": bool(attempt and attempt.finished_at),
                "correct": bool(attempt and attempt.correct) if attempt and attempt.finished_at else None,
                "pointsAwarded": int(attempt.points_awarded) if attempt and attempt.finished_at else 0,
            }
        )
    return out


def start_attempt(db: Session, member: Member, puzzle_id: int) -> dict:
    puzzle = db.get(Puzzle, puzzle_id)
    if not puzzle or not puzzle.active or not puzzle.episode_id:
        raise ValueError("That puzzle is not on the channel.")
    attempt = db.scalar(
        select(PuzzleAttempt).where(
            PuzzleAttempt.member_id == member.id,
            PuzzleAttempt.puzzle_id == puzzle.id,
        )
    )
    if attempt and attempt.finished_at is not None:
        raise ValueError("You already solved this puzzle. Pick a new video.")
    if attempt is None:
        attempt = PuzzleAttempt(member_id=member.id, puzzle_id=puzzle.id, started_at=datetime.now(UTC))
        db.add(attempt)
        safe_flush(db)
    return {
        "puzzleId": puzzle.id,
        "episodeId": puzzle.episode_id,
        "timerStart": TIMER_START,
        "factor": POINTS_FACTOR,
        "startedAt": attempt.started_at.isoformat() if attempt.started_at else datetime.now(UTC).isoformat(),
    }


def submit_answer(db: Session, member: Member, puzzle_id: int, choice: str) -> dict:
    puzzle = db.get(Puzzle, puzzle_id)
    if not puzzle or not puzzle.active or not puzzle.episode_id:
        raise ValueError("That puzzle is not on the channel.")
    attempt = db.scalar(
        select(PuzzleAttempt).where(
            PuzzleAttempt.member_id == member.id,
            PuzzleAttempt.puzzle_id == puzzle.id,
        )
    )
    if attempt and attempt.finished_at is not None:
        raise ValueError("You already solved this puzzle. Pick a new video.")
    now = datetime.now(UTC)
    if attempt is None:
        attempt = PuzzleAttempt(member_id=member.id, puzzle_id=puzzle.id, started_at=now)
        db.add(attempt)
        safe_flush(db)
    started = attempt.started_at or now
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    elapsed = max(0, int((now - started).total_seconds()))
    timer_left = max(1, TIMER_START - elapsed)
    given = (choice or "").strip()
    correct = _normalize(given) == _normalize(puzzle.answer)
    points = timer_left * POINTS_FACTOR if correct else 0
    if correct:
        member.channel_points = int(member.channel_points or 0) + points
        message = f"Yes! +{points} points"
    else:
        message = random.choice(MISS_MESSAGES)
    attempt.finished_at = now
    attempt.choice = given[:128]
    attempt.correct = correct
    attempt.timer_left = timer_left
    attempt.points_awarded = points
    attempt.message = message
    member.last_seen_at = now
    safe_flush(db)
    return {
        "correct": correct,
        "pointsAwarded": points,
        "timerLeft": timer_left,
        "channelPoints": int(member.channel_points or 0),
        "message": message,
        "factor": POINTS_FACTOR,
    }
