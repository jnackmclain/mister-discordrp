#!/usr/bin/env python3
import os, sys, argparse, configparser
from urllib.parse import quote

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, 'config.ini')
DEFAULT_CONFIG = os.path.join(BASE_DIR, 'config_default.ini')
COMMITS_INI = os.path.join(BASE_DIR, 'libretro_commits.ini')

def _normalize_key(name: str) -> str:
    return ''.join(c for c in name.lower() if c.isalnum())

def _repo_name_from_system_folder(system_folder: str) -> str:
    return system_folder.replace(' - ', '_-_').replace(' ', '_')

def _load_config():
    cfg = configparser.ConfigParser()
    if os.path.exists(CONFIG_FILE):
        cfg.read(CONFIG_FILE)
    elif os.path.exists(DEFAULT_CONFIG):
        cfg.read(DEFAULT_CONFIG)
    return cfg

def _thumbs_root(cfg) -> str:
    env = os.environ.get('RETROARCH_THUMBS_ROOT', os.path.join(BASE_DIR, 'retroarch-thumbnails', 'thumbnails'))
    return cfg.get('mister', 'retroarch_thumbs_root', fallback=env) if cfg.has_section('mister') else env

def _load_commits(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    c = configparser.ConfigParser()
    c.read(path)
    return {k: v for k, v in c.items('commits')} if c.has_section('commits') else {}

def build_cache(root: str, commits: dict, out_path: str):
    if not os.path.isdir(root):
        print(f"Thumbnails root not found: {root}")
        sys.exit(1)

    rows = []
    for system_folder in sorted(os.listdir(root)):
        sys_path = os.path.join(root, system_folder)
        art_dir = os.path.join(sys_path, 'Named_Boxarts')
        if not os.path.isdir(art_dir): 
            continue
        repo = _repo_name_from_system_folder(system_folder)
        commit = commits.get(system_folder, 'master')
        try:
            for fn in sorted(os.listdir(art_dir)):
                if not fn.lower().endswith('.png'):
                    continue
                stem, _ = os.path.splitext(fn)
                key = _normalize_key(stem)
                enc = quote(fn)
                raw_url = f"https://raw.githubusercontent.com/libretro-thumbnails/{repo}/{commit}/Named_Boxarts/{enc}"
                blob_url = f"https://github.com/libretro-thumbnails/{repo}/blob/{commit}/Named_Boxarts/{enc}"
                abs_path = os.path.join(art_dir, fn)
                rows.append((key, system_folder, fn, raw_url, blob_url, abs_path))
        except Exception as e:
            print(f"Skip {art_dir}: {e}")

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("key\tsystem\tfilename\traw_url\tblob_url\tabs_path\n")
        for r in rows:
            f.write("\t".join(r) + "\n")

    print(f"Wrote {len(rows):,} entries -> {out_path}")

def main():
    cfg = _load_config()
    parser = argparse.ArgumentParser(description="Build box art cache txt from local thumbnails.")
    parser.add_argument("-o", "--out", default=os.path.join(BASE_DIR, "boxart_cache.txt"))
    parser.add_argument("--root", default=_thumbs_root(cfg))
    parser.add_argument("--commits", default=COMMITS_INI)
    args = parser.parse_args()
    commits = _load_commits(args.commits)
    build_cache(args.root, commits, args.out)

if __name__ == "__main__":
    main()
