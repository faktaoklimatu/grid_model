#!/usr/bin/env python
import argparse
from pathlib import Path
from shutil import copyfile

DEFAULT_OUTPUTS_DIR = Path(__file__).parent.parent / "output"
DEFAULT_TARGET_DIR = Path(__file__).parent / "nuclear-scenarios"

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--clean", action="store_true",
                        help="Clean target directory before copying")
    parser.add_argument("-t", "--target-dir", type=Path, default=DEFAULT_TARGET_DIR,
                        help="Directory to store model output copies in. Will be created if it "
                        "does not exist")
    parser.add_argument("OUTPUTS_DIR", nargs="?", type=Path, default=DEFAULT_OUTPUTS_DIR)

    args = parser.parse_args()
    outputs_dir: Path = args.OUTPUTS_DIR
    target_dir: Path = args.target_dir

    # Create target directory if necessary.
    if not target_dir.is_dir():
        target_dir.mkdir(parents=True)
        print(f"Created {target_dir}")

    # Remove CSVs in target directory if requested.
    if args.clean:
        print("Cleaning target directory...")
        num_files = len([path.unlink() for path in target_dir.glob("*.csv")])
        print(f"Removed {num_files} CSV files")

    print(f"Scanning {outputs_dir} for CSV model outputs...")
    csv_paths = outputs_dir.glob("*/*-complete.csv")

    # Copy files one by one.
    for source_path in csv_paths:
        dest_path = target_dir / source_path.name
        copyfile(source_path, dest_path)
        print(f"Copied {source_path.name}")
