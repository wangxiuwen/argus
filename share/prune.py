#!/usr/bin/env python3
"""argus prune — delete superseded partials from the Hugging Face cache.

Some download backends leave multiple .incomplete files for the same blob.
Only older duplicates are dead weight: the newest partial may still be resumed.
This cleaner keeps that newest copy, every live download, and every finished
blob.  Residual partials for an already-finished blob are also safe to remove.
"""
import argparse
import os
import sys
import time

HUB = os.path.expanduser("~/.cache/huggingface/hub")


def positive_minutes(value):
    try:
        value = int(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError("must be an integer") from e
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1 minute")
    return value


def repo_cache_dir(repo):
    return os.path.join(HUB, "models--" + repo.replace("/", "--"))


def scan(min_age_min, repo=None):
    now = time.time()
    stale, active = [], []
    base = repo_cache_dir(repo) if repo else HUB
    for root, dirs, files in os.walk(base):
        if os.path.basename(root) != "blobs":
            continue
        completed = set(files)
        groups = {}
        for name in files:
            if not name.endswith(".incomplete"):
                continue
            path = os.path.join(root, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            sha = name.split(".", 1)[0]
            groups.setdefault(sha, []).append((path, st.st_size, st.st_mtime))
        for sha, entries in groups.items():
            newest = max(entries, key=lambda entry: entry[2])
            blob_finished = sha in completed
            for path, size, mtime in entries:
                age_min = (now - mtime) / 60
                entry = (path, size, age_min)
                superseded = blob_finished or path != newest[0]
                (stale if superseded and age_min >= min_age_min else active).append(entry)
    return stale, active


def human(n):
    return f"{n / 1e9:.1f} GB" if n >= 1e8 else f"{n / 1e6:.0f} MB"


def main():
    ap = argparse.ArgumentParser(prog="argus prune", description=__doc__)
    ap.add_argument("--min-age", type=positive_minutes, default=30,
                    help="only delete partials untouched for this many minutes (default 30)")
    ap.add_argument("--yes", "-y", action="store_true", help="delete without asking")
    ap.add_argument("--repo", help="limit cleanup to one repo id, e.g. org/model")
    ap.add_argument("--quiet", action="store_true", help="only print errors")
    args = ap.parse_args()

    stale, active = scan(args.min_age, args.repo)
    if active and not args.quiet:
        print(f"live/resumable partials: {len(active)} file(s), "
              f"{human(sum(s for _, s, _ in active))} — leaving them alone")
    if not stale:
        if not args.quiet:
            print("nothing to prune")
        return 0

    total = sum(s for _, s, _ in stale)
    if not args.quiet:
        print(f"\nsuperseded partial downloads ({human(total)}):")
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
            # A paused downloader may resume while this list is on screen.
            # Never delete a file that changed since scan() or is now recent.
            current = os.stat(path)
            age_min = (time.time() - current.st_mtime) / 60
            if current.st_size != size or age_min < args.min_age:
                if not args.quiet:
                    print(f"kept active download {os.path.relpath(path, HUB)}")
                continue
            os.remove(path)
            freed += size
        except OSError as e:
            print(f"could not remove {path}: {e}", file=sys.stderr)
    if not args.quiet:
        print(f"freed {human(freed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
