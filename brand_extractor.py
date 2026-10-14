"""Find the brands that sponsor or partner with an Instagram account.

    python brand_extractor.py                         # interactive prompts
    python brand_extractor.py engifest_dtu -n 100 --login your_username

See README.md for details. The code lives in the brandscan/ package.
"""
import sys

from brandscan.cli import main

if __name__ == "__main__":
    sys.exit(main())
