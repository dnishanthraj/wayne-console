"""
The room sounds: what the catalogue holds, what's been made, and making the rest.

    venv/bin/python scripts/soundscape.py            # how much is made, and this month's allowance
    venv/bin/python scripts/soundscape.py --missing  # what isn't made yet
    venv/bin/python scripts/soundscape.py --render street_day car rooftop   # make these places, and their sounds
    venv/bin/python scripts/soundscape.py --doc      # rewrite docs/soundscape.md from the catalogue

Everything is made by itself the first time a call needs it (see wayne/audio/ambience.py), within the month's
allowance (WAYNE_SFX_CREDITS); this is for seeing it, and for making places ahead of time.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wayne import config  # noqa: E402
from wayne.audio import ambience  # noqa: E402

_DISTANCE = {"near": "beside them", "mid": "close by", "far": "far off", "pass": "going past"}


def _doc():
    scenes, sounds = ambience.CATALOGUE_SCENES, ambience.CATALOGUE_SOUNDS
    lines = ["# The soundscape", "",
             "Every kind of place someone on the line can be in, and every sound that happens there. Generated "
             "from [`wayne/audio/soundscape.json`](../wayne/audio/soundscape.json) by `scripts/soundscape.py "
             "--doc`; how it plays is in [`wayne/audio/ambience.py`](../wayne/audio/ambience.py).", "",
             "Each place is a loop (made once, kept). Some lay another loop under theirs (a crowd under a busy "
             "street), and the weather goes over anywhere outdoors, or on the windows of anywhere with them. Sounds "
             "happen at random at about the rate shown, from the distances shown, and whoever's there hears them "
             "as they happen. A place with bursts and rests (a training hall) goes quiet between them; a place "
             "that can cut into someone's sentences (a fight, a heavy bag) says so.", "",
             f"{len(scenes)} places, {len(sounds)} sounds.", "", "## Places", "",
             "| Place | What it is | What they'd say they can hear | Sounds there (every ~seconds) |",
             "|---|---|---|---|"]
    for name, spec in scenes.items():
        if spec.get("layer_only"):
            continue
        heard = ", ".join(f"{s} ({every}s)" for s, every, _ in spec.get("sounds", []))
        extras = []
        if spec.get("layers"):
            extras.append("over " + ", ".join(n for n, _ in spec["layers"]))
        if spec.get("rhythm"):
            extras.append("bursts and rests")
        if spec.get("cuts"):
            extras.append("cuts in: " + ", ".join(c for c, _ in spec["cuts"]))
        if spec.get("outdoor"):
            extras.append("out in the weather")
        elif spec.get("windows"):
            extras.append("rain on the windows")
        what = spec["prompt"] + (f" *({'; '.join(extras)})*" if extras else "")
        lines.append(f"| `{name}` | {what} | {spec['say']} | {heard or '—'} |")
    lines += ["", "## Layers and weather", "", "| Loop | What it is |", "|---|---|"]
    for name, spec in scenes.items():
        if spec.get("layer_only"):
            lines.append(f"| `{name}` | {spec['prompt']} |")
    lines += ["", "## Sounds", "", "| Sound | Heard as | From |", "|---|---|---|"]
    for name, spec in sounds.items():
        lines.append(f"| `{name}` | {spec['say']} | "
                     + ", ".join(_DISTANCE.get(d, d) for d in spec["variants"]) + " |")
    out = ROOT / "docs" / "soundscape.md"
    out.write_text("\n".join(lines) + "\n")
    print(f"Wrote {out.relative_to(ROOT)}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--missing", action="store_true", help="list what isn't made yet")
    parser.add_argument("--render", nargs="*", metavar="PLACE", help="make these places and their sounds now")
    parser.add_argument("--doc", action="store_true", help="rewrite docs/soundscape.md")
    args = parser.parse_args()
    if args.doc:
        return _doc()
    clips = ambience.every_clip()
    made = [(n, s) for n, s in clips if ambience.path(n).exists()]
    missing = [(n, s) for n, s in clips if not ambience.path(n).exists()]
    print(f"{len(clips)} clips in the catalogue; {len(made)} made; {len(missing)} to make "
          f"(~{sum(s for _, s in missing)}s of audio, at most ~{sum(s for _, s in missing) * ambience.CREDITS_PER_SECOND:,} credits).")
    print(f"This month: {ambience.spent():,} of {config.SFX_CREDITS:,} credits counted.")
    if args.missing:
        for name, seconds in missing:
            print(f"  {name} ({seconds}s)")
    if args.render is not None:
        if not ambience.available():
            print("No ElevenLabs key, or WAYNE_AMBIENCE=0: nothing can be made.")
            return 1
        wanted = []
        for place in args.render:
            spec = ambience.CATALOGUE_SCENES.get(place)
            if not spec:
                print(f"  no such place: {place}")
                continue
            wanted.append(place)
            wanted += [n for n, _ in spec.get("layers", [])]
            for sound, _, distances in spec.get("sounds", []):
                wanted += [ambience._variant_file(sound, d) for d in distances.split(",")]
        for name in dict.fromkeys(wanted):
            print(f"  {name}: {'made' if ambience.clip(name) else 'not made'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
