"""Optional terminal interface; desktop is the beginner entry point."""

import argparse
import json
import os
import sys

from . import __version__
from .files import read_text, write_report, write_text
from .prompts import DEPTHS, copy_prompt
from .providers import Client, ProviderConfig
from .service import analyze, offline, rewrite
from .style import GENRES
from .unicode_check import CleanOptions


def main(argv=None) -> int:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(
                encoding="utf-8", errors="strict" if stream is sys.stdin else "backslashreplace"
            )
    parser = argparse.ArgumentParser(
        description="Shelter Humanizer — редактор русскоязычного текста"
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("mode", choices=("gui", "analyze", "clean", "light", "prompt", "rewrite"))
    parser.add_argument("input", nargs="?", help="UTF-8 TXT; иначе stdin")
    parser.add_argument("-o", "--output")
    parser.add_argument("--report", help="JSON-отчёт, включает исходник и результат")
    parser.add_argument("--genre", choices=tuple(GENRES), default="plain")
    parser.add_argument("--depth", choices=tuple(DEPTHS), default="edit")
    parser.add_argument("--spaces", action="store_true")
    parser.add_argument("--confusables", action="store_true")
    parser.add_argument("--nfc", action="store_true")
    parser.add_argument("--provider", choices=("ollama", "api"), default="ollama")
    parser.add_argument(
        "--url", help="Ollama base URL или OpenAI-compatible base URL (до /chat/completions)"
    )
    parser.add_argument("--model", default="")
    parser.add_argument("--voice", help="TXT-образец голоса, до 3000 символов")
    parser.add_argument("--protect", action="append", default=[])
    parser.add_argument("--one-pass", action="store_true")
    args = parser.parse_args(argv)
    if args.mode == "gui":
        from .gui import launch

        launch()
        return 0
    try:
        text = read_text(args.input) if args.input else sys.stdin.read(200_001)
        if args.mode == "analyze":
            from dataclasses import asdict

            output = json.dumps(asdict(analyze(text, args.genre)), ensure_ascii=False, indent=2)
            if args.report:
                raise ValueError("Для analyze используйте -o для сохранения JSON.")
        elif args.mode == "prompt":
            analyze(text, args.genre)
            output = copy_prompt(
                text,
                args.genre,
                read_text(args.voice) if args.voice else "",
                depth=args.depth,
                terms=tuple(args.protect),
            )
            if args.report:
                raise ValueError("Режим prompt ещё не содержит результата для отчёта.")
        else:
            if args.mode == "rewrite":
                url = args.url or (
                    "http://localhost:11434"
                    if args.provider == "ollama"
                    else "https://openrouter.ai/api/v1"
                )
                config = ProviderConfig(
                    args.provider, url, args.model, os.getenv("SHELTER_API_KEY", "")
                )
                result = rewrite(
                    text,
                    Client(config),
                    genre=args.genre,
                    voice=read_text(args.voice) if args.voice else "",
                    terms=tuple(args.protect),
                    second_pass=not args.one_pass,
                    depth=args.depth,
                    options=CleanOptions(args.spaces, args.confusables, args.nfc),
                )
            else:
                result = offline(
                    text,
                    args.mode,
                    args.genre,
                    CleanOptions(args.spaces, args.confusables, args.nfc),
                )
            output = result.text
            for warning in result.warnings:
                print(warning, file=sys.stderr)
            if args.report:
                write_report(args.report, result)
        if args.output:
            write_text(args.output, output)
        else:
            print(output)
        return 0
    except (ValueError, OSError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
