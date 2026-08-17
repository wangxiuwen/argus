#!/usr/bin/env python3
"""argus prune — delete abandoned partial downloads from the Hugging Face cache.

An interrupted download leaves a .incomplete blob with a random suffix. A later
attempt at the same blob picks a NEW suffix instead of resuming, so a killed
download becomes dead weight that nothing will ever finish. This removes the
partials that have not been written to for a while, leaving any live download
(and every finished blob) untouched.
"""
import argparse
import os
import sys
import time

HUB = os.path.expanduser("~/.cache/huggingface/hub")


def scan(min_age_min):
    now = time.time()
    stale, active = [], []
    for root, dirs, files in os.walk(HUB):
        if os.path.basename(root) != "blobs":
            continue
        for name in files:
            if not name.endswith(".incomplete"):
                continue
            path = os.path.join(root, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            age_min = (now - st.st_mtime) / 60
            entry = (path, st.st_size, age_min)
            (stale if age_min >= min_age_min else active).append(entry)
    return stale, active


def human(n):
    return f"{n / 1e9:.1f} GB" if n >= 1e8 else f"{n / 1e6:.0f} MB"


def main():
    ap = argparse.ArgumentParser(prog="argus prune", description=__doc__)
    ap.add_argument("--min-age", type=int, default=30,
                    help="only delete partials untouched for this many minutes (default 30)")
    ap.add_argument("--yes", "-y", action="store_true", help="delete without asking")
    args = ap.parse_args()

    stale, active = scan(args.min_age)
    if active:
        print(f"live downloads in progress: {len(active)} file(s), "
              f"{human(sum(s for _, s, _ in active))} — leaving them alone")
    if not stale:
        print("nothing to prune")
        return 0

    total = sum(s for _, s, _ in stale)
    print(f"\nabandoned partial downloads ({human(total)}):")
    for path, size, age in sorted(stale, key=lambda r: -r[1]):
        rel = os.path.relpath(path, HUB)
        print(f"  {human(size):>8s}  untouched {age / 60:.1f}h  {rel}")

    if not args.yes:
        if not sys.stdin.isatty():
            print(f"\nnothing deleted — re-run with --yes to free {human(total)}")
            return 0
        try:
            answer = input(f"\ndelete these and free {human(total)}? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 1
        if answer not in ("y", "yes"):
            print("kept")
            return 0

    freed = 0
    for path, size, _ in stale:
        try:
            os.remove(path)
            freed += size
        except OSError as e:
            print(f"could not remove {path}: {e}")
    print(f"freed {human(freed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
