"""Source and frozen desktop entry point."""

import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        from shelter_humanizer.selftest import run

        return run(sys.argv[2])
    if len(sys.argv) > 1:
        from shelter_humanizer.cli import main as cli_main

        return cli_main()
    from shelter_humanizer.gui import launch

    launch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
