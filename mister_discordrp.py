#!/usr/bin/env python3
"""
MiSTer FPGA Discord Rich Presence using Box Art Cache (TSV) + Fallback Asset

Save as mister_discord_presence.py
Dependencies:
    pip install pypresence requests

Place your fallback PNG (e.g., mister_kun_bw.png) next to this script.

Create a 'config_default.ini' alongside this script:

[discord]
client_id = YOUR_CLIENT_ID_HERE

[mister]
host = localhost
port = 8182
# optional: override cache path
# boxart_cache = /absolute/or/relative/path/to/boxart_cache.txt
"""

import os
import sys
import time
import requests
import configparser
from datetime import datetime
from difflib import get_close_matches
from pypresence import Presence
from pypresence.exceptions import PipeClosed
from urllib.parse import quote
import re
from difflib import SequenceMatcher

BAD_QUALIFIERS = {"demo", "kiosk", "beta", "prototype", "proto", "sample", "prerelease", "trial", "review", "event", "not for resale"}
SOFT_QUALIFIERS = {"rev", "alt"}
REGION_WORDS = {"usa", "europe", "japan", "world", "asia", "korea", "australia", "canada", "brazil"}

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, 'config.ini')
DEFAULT_CONFIG = os.path.join(BASE_DIR, 'config_default.ini')
FALLBACK_IMG_PATH = os.path.join(BASE_DIR, 'mister_kun_bw.png')

config = configparser.ConfigParser()
if not os.path.exists(CONFIG_FILE):
    if os.path.exists(DEFAULT_CONFIG):
        config.read(DEFAULT_CONFIG)
        with open(CONFIG_FILE, 'w') as cfg:
            config.write(cfg)
        print(f"Created {CONFIG_FILE} from defaults.", flush=True)
    else:
        print("Default configuration file not found.", flush=True)
        sys.exit(1)
else:
    config.read(CONFIG_FILE)

client_id = config['discord'].get('client_id', 'YOUR_CLIENT_ID_HERE')
if client_id == 'YOUR_CLIENT_ID_HERE':
    print("Please update your client_id in config.ini", flush=True)
    sys.exit(1)

mister_host = config['mister'].get('host', 'localhost')
mister_port = config['mister'].get('port', '8182')
BOXART_CACHE_FILE = config['mister'].get('boxart_cache', os.path.join(BASE_DIR, 'boxart_cache.txt'))

# Optional per-system Discord asset map (for small_image)
ASSETS_INI = os.path.join(BASE_DIR, 'discord_assets.ini')
ASSETS = {}
if os.path.isfile(ASSETS_INI):
    _a = configparser.ConfigParser()
    _a.read(ASSETS_INI)
    if _a.has_section('assets'):
        ASSETS = {k: v for k, v in _a.items('assets')}

RPC = Presence(client_id)
IMAGE_KEY = 'mister'

SYSTEM_MAP = {
    'NES': 'Nintendo - Nintendo Entertainment System',
    'SNES': 'Nintendo - Super Nintendo Entertainment System',
    'Game Boy': 'Nintendo - Game Boy',
    'Game Boy Color': 'Nintendo - Game Boy Color',
    'Game Boy Advance': 'Nintendo - Game Boy Advance',
    'Genesis': 'Sega - Mega Drive - Genesis',
    'Master System': 'Sega - Master System - Mark III',
    'SMS': 'Sega - Master System - Mark III',
    'PlayStation': 'Sony - PlayStation',
    'PS2': 'Sony - PlayStation 2',
    'PS3': 'Sony - PlayStation 3',
    'PS4': 'Sony - PlayStation 4',
    'Xbox': 'Microsoft - Xbox',
    'Xbox 360': 'Microsoft - Xbox 360',
}

def _normalize_key(name: str) -> str:
    return ''.join(c for c in (name or '').lower() if c.isalnum())

# --- Cache (TSV) ---
# Expect header: key    system    filename    raw_url    blob_url    abs_path
CACHE = []  # tuples: (system, filename, raw_url, blob_url, key)

def _load_boxart_cache():
    global CACHE
    if CACHE:
        return
    if not os.path.isfile(BOXART_CACHE_FILE):
        print(f"Box art cache not found: {BOXART_CACHE_FILE}\nRun build_boxart_cache.py first.", flush=True)
        return
    with open(BOXART_CACHE_FILE, encoding='utf-8') as f:
        first = f.readline()
        if not first:
            return
        header = first.strip().split('\t')
        if not header or header[0] != 'key':
            f.seek(0)
        for line in f:
            parts = line.rstrip('\n').split('\t')
            if len(parts) < 5:
                continue
            key, system_folder, fn, raw_url, blob_url = parts[:5]
            CACHE.append((system_folder, fn, raw_url, blob_url, key))
    print(f"Loaded {len(CACHE):,} box art entries from cache.", flush=True)

def _canon_system(hint: str):
    if not hint:
        return None
    return SYSTEM_MAP.get(hint, hint)

def _stem(name: str) -> str:
    return os.path.splitext(name)[0]

def _paren_tokens(stem: str):
    toks = [t.strip().lower() for t in re.findall(r"\((.*?)\)", stem)]
    return toks

def _region_tokens_from_title(title: str):
    return set(t for t in _paren_tokens(title) if any(r in t for r in REGION_WORDS))

def _score_candidate(stem: str, key: str, target_key: str, region_hint: set, target_stem_len: int) -> int:
    if key == target_key:
        base = 100
    elif key.startswith(target_key) or target_key.startswith(key):
        base = 96
    elif target_key in key:
        base = 92
    else:
        base = int(60 * SequenceMatcher(None, target_key, key).ratio())

    cand_paren = _paren_tokens(stem)
    cand_regions = set(t for t in cand_paren if any(r in t for r in REGION_WORDS))
    if region_hint and (cand_regions & region_hint):
        base += 5

    cand_blob = " ".join(cand_paren)
    if any(q in cand_blob for q in BAD_QUALIFIERS):
        base -= 30
    if any(q in cand_blob for q in SOFT_QUALIFIERS):
        base -= 5

    non_region_extras = [t for t in cand_paren if t not in cand_regions]
    if non_region_extras:
        base -= 3 * len(non_region_extras)

    # prefer shorter candidates close to the requested title length
    base -= min(5, max(0, (len(stem) - target_stem_len) // 10))
    return base

def find_boxart_and_url(system_hint: str, game_name: str):
    _load_boxart_cache()
    if not CACHE:
        return None, None

    target_key = _normalize_key(game_name)
    region_hint = _region_tokens_from_title(game_name)
    sys_hint = _canon_system(system_hint)
    target_stem_len = len(_stem(game_name))

    candidates = CACHE
    if sys_hint:
        scoped = [t for t in CACHE if t[0] == sys_hint]
        if scoped:
            candidates = scoped

    lowered_title_stem = _stem(game_name).lower()
    for sys_folder, fn, raw_url, blob_url, key in candidates:
        if _stem(fn).lower() == lowered_title_stem:
            print(f"Boxart match (exact stem): [{sys_folder}] {fn}", flush=True)
            return raw_url, blob_url

    scored = []
    for sys_folder, fn, raw_url, blob_url, key in candidates:
        score = _score_candidate(_stem(fn), key, target_key, region_hint, target_stem_len)
        scored.append((score, len(fn), sys_folder, fn, raw_url, blob_url))

    if not scored:
        print(f"No boxart in cache for '{game_name}'", flush=True)
        return None, None

    scored.sort(reverse=True)
    top = scored[0]
    print(f"Boxart match (scored {top[0]}): [{top[2]}] {top[3]}", flush=True)
    return top[4], top[5]

def is_valid_image(url: str) -> bool:
    try:
        r = requests.head(url, timeout=5, allow_redirects=True)
        if r.status_code == 200 and 'image' in r.headers.get('Content-Type', ''):
            return True
        r = requests.get(url, timeout=7, stream=True)
        ct = r.headers.get('Content-Type', '')
        return r.status_code == 200 and 'image' in ct
    except Exception as e:
        print(f"Image check failed for {url}: {e}", flush=True)
        return False

# --- MiSTer API ---
def fetch_playing(host, port):
    try:
        resp = requests.get(f"http://{host}:{port}/api/games/playing", timeout=5)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Error fetching playing info: {e}", flush=True)
        return None

def format_elapsed(start):
    delta = datetime.now() - start
    d, rem = delta.days, delta.seconds
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    if d:
        return f"for {d}d {h}h {m}m"
    if h:
        return f"for {h}h {m}m"
    return f"for {m}m"

def set_presence(data, start_time, last_game):
    raw_boxart_url = None
    github_blob_url = None
    small_asset = IMAGE_KEY
    core = data.get('core', '') if data else ''
    system = data.get('systemName', '') if data else ''
    game = data.get('gameName', '') if data else ''

    if not system and not game:
        details = "In Menu"
        state = "MiSTer FPGA"
    else:
        details = game or "Unknown"
        state = system if system else (core if core else "MiSTer FPGA")
        hint = system if system else core
        if hint and hint in ASSETS:
            small_asset = ASSETS[hint]
            print(f"Using small_image asset: {small_asset} for '{hint}'", flush=True)
        raw_boxart_url, github_blob_url = find_boxart_and_url(system, game)
        if raw_boxart_url:
            print(f"Using boxart URL: {raw_boxart_url}", flush=True)
        else:
            print(f"No boxart match; using fallback asset: {IMAGE_KEY}", flush=True)
        if github_blob_url:
            print(f"Box art URL: {github_blob_url}", flush=True)

    current = f"{system}/{game}"
    if current != last_game:
        print(f"Detected change: {details} (Core: {core or 'N/A'})", flush=True)
        start_time = datetime.now()
        last_game = current

    elapsed = format_elapsed(start_time)
    print(f"Updating presence: {details} on {state} {elapsed}", flush=True)

    payload = {
        'details': details,
        'state': state,
        'start': int(start_time.timestamp()),
        'large_image': (raw_boxart_url if (raw_boxart_url and is_valid_image(raw_boxart_url)) else IMAGE_KEY),
        'large_text': details,
        'small_image': small_asset,
        'small_text': state
    }
    print(f"large_image => {payload['large_image']} | small_image => {payload.get('small_image')}", flush=True)
    if github_blob_url:
        payload['buttons'] = [{"label": "Box Art", "url": github_blob_url}]
    try:
        RPC.update(**payload)
    except PipeClosed:
        print("Discord pipe closed, reconnecting...", flush=True)
        try:
            RPC.close()
        except:
            pass
        RPC.connect()
        RPC.update(**payload)

    return start_time, last_game

def main():
    host = sys.argv[1] if len(sys.argv) > 1 else mister_host
    port = sys.argv[2] if len(sys.argv) > 2 else mister_port
    RPC.connect()
    print(f"Connected to Discord RPC (Client ID: {client_id})", flush=True)

    start_time = datetime.now()
    last_game = None
    try:
        while True:
            data = fetch_playing(host, port)
            start_time, last_game = set_presence(data, start_time, last_game)
            time.sleep(15)
    except KeyboardInterrupt:
        print("Disconnecting from Discord...", flush=True)
        RPC.clear()
        RPC.close()
        print("Disconnected. Goodbye!", flush=True)

if __name__ == "__main__":
    main()
