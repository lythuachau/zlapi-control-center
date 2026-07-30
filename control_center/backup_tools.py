from __future__ import annotations

import argparse
from pathlib import Path

from .config import get_settings
from .security import SecretBox


def main() -> None:
    parser = argparse.ArgumentParser(description="Encrypted backup helper")
    subparsers = parser.add_subparsers(dest="command", required=True)
    decrypt = subparsers.add_parser("decrypt")
    decrypt.add_argument("--input", required=True, type=Path)
    decrypt.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    settings = get_settings()
    settings.ensure_directories()
    if args.command == "decrypt":
        source = args.input.resolve()
        target = args.output.resolve()
        if not source.is_file() or source.suffix != ".aes":
            raise SystemExit("Input must be an existing .aes backup")
        if target.exists():
            raise SystemExit("Output already exists; refusing to overwrite")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(SecretBox(settings).decrypt_bytes(source.read_bytes()))
        print(target)


if __name__ == "__main__":
    main()
