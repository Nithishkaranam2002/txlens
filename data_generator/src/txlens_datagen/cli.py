"""Command-line entry point for the synthetic data generator (`make generate-data`)."""

import sys


def main() -> int:
    """Run the generator. The real generator is built in Step 1.2."""
    print("Synthetic data generator is not built yet (BUILD_PLAN Step 1.2).", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
