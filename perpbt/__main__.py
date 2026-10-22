"""Allow ``python -m perpbt``."""
import sys

from perpbt.cli import main

if __name__ == "__main__":
    sys.exit(main())
