"""
The Batcomputer's lines, kept as mp3s: what there is, what's still to render, and tidying up.

    venv/bin/python scripts/voice_lines.py            # how many lines, how many on disk, by moment
    venv/bin/python scripts/voice_lines.py --missing  # the lines not rendered yet
    venv/bin/python scripts/voice_lines.py --prime    # render them now (costs ElevenLabs characters, once)
    venv/bin/python scripts/voice_lines.py --prune    # remove clips nothing says any more

The console renders anything missing by itself at start; this is for seeing it,
and for doing it now. Clips live in data/_system_voice/<voice>/<moment>/.
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wayne import paths  # noqa: E402
from wayne.audio import system_voice  # noqa: E402
from wayne.contacts import directory  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--missing", action="store_true", help="list the lines not rendered yet")
    parser.add_argument("--prime", action="store_true", help="render every missing line now")
    parser.add_argument("--prune", action="store_true", help="remove clips no line uses any more")
    args = parser.parse_args()
    contacts = list(directory())
    if not system_voice.available():
        print("No system voice: set BATCOMPUTER_VOICE_ID and ELEVENLABS_API_KEY in .env.")
    total, have, missing = system_voice.status(contacts)
    print(f"{total} lines; {have} on disk; {len(missing)} to render (~{sum(len(t) for _, t in missing)} characters).")
    by_moment = Counter(e for e, _ in system_voice.every_line(contacts))
    gaps = Counter(e for e, _ in missing)
    for moment in system_voice.LINES:
        print(f"  {moment:14} {by_moment[moment] - gaps[moment]:3}/{by_moment[moment]}")
    if args.missing:
        for moment, text in missing:
            print(f"  [{moment}] {text}")
    if args.prime:
        from wayne.audio.tts import AlfredVoiceService
        made = system_voice.prime(AlfredVoiceService(), contacts, quiet=False)
        print(f"Rendered {made} line(s).")
    if args.prune:
        wanted = {system_voice._cache_path(t, e) for e, t in system_voice.every_line(contacts)}
        root = paths.DATA_DIR / "_system_voice"
        gone = [p for p in root.rglob("*.mp3") if p not in wanted]
        for p in gone:
            p.unlink()
        print(f"Removed {len(gone)} clip(s) nothing says any more.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
