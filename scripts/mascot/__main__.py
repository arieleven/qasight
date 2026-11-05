"""Play a mascot animation in the terminal.

Usage: python -m scripts.mascot [flex|smash|hurt|gloves] [--fps N] [--loops N]
"""

from __future__ import annotations

import argparse

from . import ANIMATIONS
from .render import frame_to_ansi, play


def main() -> None:
    parser = argparse.ArgumentParser(description="Play a QASight mascot animation.")
    parser.add_argument("name", nargs="?", default="flex", choices=sorted(ANIMATIONS))
    parser.add_argument("--fps", type=float, default=6.0)
    parser.add_argument("--loops", type=int, default=3)
    parser.add_argument("--still", action="store_true", help="print the first frame and exit")
    args = parser.parse_args()

    animation = ANIMATIONS[args.name]
    if args.still:
        print(frame_to_ansi(animation[0]))
    else:
        play(animation, fps=args.fps, loops=args.loops)


if __name__ == "__main__":
    main()
