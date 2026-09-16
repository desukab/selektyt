"""Support `python3 -m selekyt <args>` (module entry, mirrors the CLI)."""
from selekyt.cli import main

if __name__ == "__main__":
    raise SystemExit(main())