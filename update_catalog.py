"""Update or verify catalogs, reports and client PNGs offline."""

import argparse
from pathlib import Path

import build_client_catalog
import update_index


def run(root, check=False):
    root = Path(root).resolve()
    outputs = update_index.build_outputs(root)
    if check:
        update_index.check_outputs(outputs, root)
    else:
        update_index.write_outputs(outputs, root)
    build_client_catalog.publish(
        build_client_catalog.build_outputs(root), root, check=check
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the entire pipeline without writing",
    )
    args = parser.parse_args()
    try:
        run(Path(__file__).resolve().parent, check=args.check)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"Catalog pipeline failed: {error}\n")
    print(f"Catalog pipeline {'validated' if args.check else 'updated'}")


if __name__ == "__main__":
    main()
