"""Command line: build a catalogue locally, or run the server.

    python -m menu_catalogs build tim-hortons
    python -m menu_catalogs build mcdonalds --input mcdonalds.har
    python -m menu_catalogs build dairy-queen --input "Food & Treats Nutrition.html"
    python -m menu_catalogs build subway --input CAN_Nutrition_ENG.pdf
    python -m menu_catalogs serve
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .common import CatalogError, validate
from .sources import SOURCES


def build(source_key: str, inputs: list[Path], out: Path) -> int:
    source = SOURCES[source_key]
    try:
        if source.kind == "fetch":
            catalog, notes = source.fetch(), []
        else:
            if len(inputs) != 1:
                print(f"{source.name} needs exactly one --input file. {source.how_to}", file=sys.stderr)
                return 2
            catalog, notes = source.convert(source.prepare(inputs[0].read_bytes()))
    except CatalogError as error:
        print(error, file=sys.stderr)
        return 1

    for note in notes:
        print(f"note: {note}", file=sys.stderr)
    problems = validate(catalog)
    if problems:
        print("Validation failed:\n  " + "\n  ".join(problems[:20]), file=sys.stderr)
        return 1

    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{catalog['catalog']['id']}.catalog.json"
    path.write_text(json.dumps(catalog, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Wrote {len(catalog['foods'])} foods to {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m menu_catalogs", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser("build", help="build one catalogue into a local folder")
    build_parser.add_argument("source", choices=sorted(SOURCES))
    build_parser.add_argument("--input", type=Path, action="append", default=[], help="file saved from a browser")
    build_parser.add_argument("--out", type=Path, default=Path("out"))
    commands.add_parser("serve", help="run the catalogue server (configured by environment variables)")
    args = parser.parse_args()

    if args.command == "build":
        return build(args.source, args.input, args.out)
    from .server import serve
    serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
