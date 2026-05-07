"""Entry point: `python -m daely_google_bridge ...` and the `bridge` script."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
