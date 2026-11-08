#!/usr/bin/env python3
"""
Downloads the Gamer's Mod bundle files into StreamingAssets/aa/WebGL.

Run it from the ROOT of your repo (the folder that contains index.html):
    python3 download_bundles.py

It shows the number of files and the total size first, and asks before
downloading. Run it again any time: files already downloaded are skipped.
Add --yes to skip the question.
"""
import os
import re
import sys
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor

BASE = "https://storage.yandexcloud.net/itsjasonstudiobasebacket/GamersMod/Adressables/"
SUBFOLDERS = ["1.4/"]                 # the real folder, taken from the original game's network log
OUT_DIR = "StreamingAssets/aa/WebGL"  # where the game looks (matches index.html)
CATALOG = "catalog_0.1.0"
HEADERS = {"User-Agent": "Mozilla/5.0"}
GITHUB_FILE_LIMIT = 95 * 1024 * 1024      # GitHub rejects files over 100 MB
REPO_SOFT_LIMIT = 900 * 1024 * 1024       # GitHub repos should stay under ~1 GB


def fetch(url, method="GET", extra=None, timeout=60):
    headers = dict(HEADERS)
    if extra:
        headers.update(extra)
    req = urllib.request.Request(url, headers=headers, method=method)
    return urllib.request.urlopen(req, timeout=timeout)


def get_bytes(url):
    with fetch(url) as r:
        return r.read()


def remote_size(url):
    """Return the file size in bytes, or None if the file does not exist."""
    try:
        with fetch(url, method="HEAD") as r:
            length = r.headers.get("Content-Length")
            if length is not None:
                return int(length)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
    except Exception:
        pass
    # Some servers refuse HEAD: ask for the first byte only instead.
    try:
        with fetch(url, extra={"Range": "bytes=0-0"}) as r:
            cr = r.headers.get("Content-Range", "")
            if "/" in cr:
                return int(cr.split("/")[-1])
            length = r.headers.get("Content-Length")
            return int(length) if length is not None else 0
    except Exception:
        return None


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024


def find_root():
    for sub in SUBFOLDERS:
        url = BASE + sub + CATALOG + ".hash"
        try:
            data = get_bytes(url)
            print(f"Found the server folder: {BASE + sub}")
            return BASE + sub, data
        except Exception as e:
            print(f"  not at {BASE + sub} ({e})")
    return None, None


def resolve(root, name):
    """Find the real file name. The catalog is binary, so a stray leading
    character can stick to a name; if the exact name is missing, try
    removing 1 or 2 characters from the start."""
    for cut in (0, 1, 2):
        candidate = name[cut:]
        if len(candidate) < 40:
            break
        size = remote_size(root + candidate)
        if size is not None:
            return candidate, size
    return None


def download(root, name, size):
    dest = os.path.join(OUT_DIR, name)
    if os.path.exists(dest) and os.path.getsize(dest) == size:
        return name, "skipped"
    tmp = dest + ".part"
    try:
        with fetch(root + name, timeout=120) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
        os.replace(tmp, dest)
        return name, "ok"
    except Exception as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        return name, f"FAILED: {e}"


def main():
    auto_yes = "--yes" in sys.argv
    os.makedirs(OUT_DIR, exist_ok=True)

    print("Looking for the server folder...")
    root, hash_data = find_root()
    if root is None:
        print("\nCould not reach the catalog on the server.")
        print("Copy the full web address of one file (see instructions) and send it to Claude.")
        sys.exit(1)

    with open(os.path.join(OUT_DIR, CATALOG + ".hash"), "wb") as f:
        f.write(hash_data)

    print("Downloading the catalog (the list of files)...")
    catalog = None
    for ext in (".bin", ".json"):
        try:
            catalog = get_bytes(root + CATALOG + ext)
            with open(os.path.join(OUT_DIR, CATALOG + ext), "wb") as f:
                f.write(catalog)
            print(f"  got {CATALOG}{ext} ({human(len(catalog))})")
            break
        except Exception as e:
            print(f"  {CATALOG}{ext} not available ({e})")
    if catalog is None:
        print("Could not download the catalog.")
        sys.exit(1)

    names = sorted(set(re.findall(rb"[A-Za-z0-9_\-\.]+\.bundle", catalog)))
    names = [n.decode("ascii") for n in names]
    # The catalog also stores each bundle's bare hash ("<32 hex>.bundle").
    # Those are not separate files, so leave out any that match the end of a longer name.
    hash_only = re.compile(r"[0-9a-f]{32}\.bundle")
    long_names = [n for n in names if not hash_only.fullmatch(n)]
    names = [n for n in names
             if not (hash_only.fullmatch(n) and any(l.endswith("_" + n) for l in long_names))]
    print(f"The catalog lists {len(names)} bundle files. Checking sizes...")

    found = {}
    missing = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for name, res in zip(names, pool.map(lambda n: resolve(root, n), names)):
            if res is None:
                missing.append(name)
            else:
                found[res[0]] = res[1]

    total = sum(found.values())
    too_big = [n for n, s in found.items() if s > GITHUB_FILE_LIMIT]
    print(f"\nFiles found on the server: {len(found)}")
    print(f"Total size: {human(total)}")
    if missing:
        print(f"Not found on the server: {len(missing)} (they will be skipped)")
    if too_big:
        print(f"\nWARNING: {len(too_big)} file(s) are over 95 MB. GitHub will REFUSE these:")
        for n in too_big:
            print(f"   {n} ({human(found[n])})")
    if total > REPO_SOFT_LIMIT:
        print("\nWARNING: this is close to or over GitHub's recommended 1 GB repo size.")
        print("Tell Claude the total above and we will pick another place to host the files.")

    if not auto_yes:
        answer = input("\nDownload everything now? (y/n): ").strip().lower()
        if answer != "y":
            print("Stopped. Nothing was downloaded.")
            return

    done = skipped = 0
    failed = []
    items = sorted(found.items())
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(download, root, n, s) for n, s in items]
        for i, fut in enumerate(futures, 1):
            name, status = fut.result()
            if status == "ok":
                done += 1
            elif status == "skipped":
                skipped += 1
            else:
                failed.append((name, status))
            print(f"[{i}/{len(items)}] {name[:70]} -> {status}")

    print(f"\nFinished. Downloaded: {done}, already had: {skipped}, failed: {len(failed)}")
    for name, status in failed:
        print(f"  {name}: {status}")
    if failed:
        print("Run the script again to retry the failed files.")
    print(f"\nFiles are in: {OUT_DIR}")


if __name__ == "__main__":
    main()
