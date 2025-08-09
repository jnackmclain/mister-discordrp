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
_SYSTEMS_FROM_CACHE = set()
_SYSTEM_ALIAS_MAP = None  # lazy-built map from pretty -> canonical

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
            _SYSTEMS_FROM_CACHE.add(system_folder)
    print(f"Loaded {len(CACHE):,} box art entries from cache.", flush=True)

def _canon_system(hint: str):
    if not hint:
        return None
    return SYSTEM_MAP.get(hint, hint)

CONFIDENCE_MIN_SCOPED = 80   # require this score if we have a system match
CONFIDENCE_MIN_UNSCOPED = 90 # stricter if we had to search globally
PRETTY_SYSTEMS = [
    "Adventure Vision","Arcadia 2001","Atari 2600","Atari 5200","Atari 7800","Atari Lynx",
    "Bally Astrocade","Casio PV-1000","Channel F","ColecoVision","Famicom Disk System",
    "Gamate","Game & Watch","Game Boy","Game Gear","Gameboy (2 Player)","Gameboy Advance",
    "Gameboy Advance (2 Player)","Gameboy Color","Genesis +","Genesis 32X","Intellivision",
    "Magnavox Odyssey2","Master System","Mega Duck","Neo Geo CD","Neo Geo MVS/AES","NES",
    "Nintendo 64","Sony PlayStation","Pocket Challenge 2","Pokemon Mini","Saturn","Sega CD",
    "SG-1000","Super Gameboy","Super NES","SuperGrafx","SuperVision","TurboGrafx-16",
    "TurboGrafx-16 CD","VC 4000","Vectrex","VTech CreatiVision","WonderSwan",
    "WonderSwan Color","Amiga","Amstrad CPC","Amstrad PCW","Apogee BK-01","Apple I",
    "Apple IIe","Atari 800XL","Atom","BBC Micro/Master","BK0011M","Casio PV-2000",
    "Commodore 16","Commodore 64","Commodore PET 2001","Commodore VIC-20","EDSAC",
    "Electron","Galaksija","Interact","Jupiter Ace","Laser 350/500/700","Lynx 48/96K","M5",
    "Macintosh Plus","Mattel Aquarius","MSX (1chipMSX)","MultiComp","Orao","Oric",
    "PC (486SX)","PC/XT","PDP-1","PMD 85-2A","RX-78 Gundam","SAM Coupe","Sinclair QL",
    "Specialist/MX","SV-328","Tandy MC-10","Tatung Einstein","TI-99/4A","TRS-80",
    "TRS-80 CoCo 2","TS-1500","TS-Config","Tutor","UK101","Vector-06C","X68000",
    "ZX Spectrum","ZX Spectrum Next","Arduboy","CHIP-8"
]

def _tok(s: str):
    return re.findall(r"[a-z0-9]+", (s or "").lower())

def _sys_match_score(pretty: str, canon: str) -> float:
    a, b = " ".join(_tok(pretty)), " ".join(_tok(canon))
    r = SequenceMatcher(None, a, b).ratio()
    ta, tb = set(_tok(pretty)), set(_tok(canon))
    overlap = len(ta & tb)
    return r + min(0.10 * overlap, 0.30)  # small bonus for token overlap

def _best_system_from_cache(name: str):
    if not _SYSTEMS_FROM_CACHE:
        return None
    scored = [(_sys_match_score(name, s), s) for s in _SYSTEMS_FROM_CACHE]
    scored.sort(reverse=True)
    score, best = scored[0]
    return (score, best)

def _build_system_aliases():
    # Build once, after cache is loaded
    global _SYSTEM_ALIAS_MAP
    if _SYSTEM_ALIAS_MAP is not None:
        return
    _SYSTEM_ALIAS_MAP = {}
    for pretty in PRETTY_SYSTEMS:
        got = _best_system_from_cache(pretty)
        if not got:
            continue
        score, canon = got
        # Require a decent match so we don't create bad aliases
        if score >= 0.75:
            _SYSTEM_ALIAS_MAP[pretty] = canon
    # Add a few common extra aliases that aren't in PRETTY_SYSTEMS
    extras = {
        "PSX": "Sony PlayStation",
        "PS1": "Sony PlayStation",
        "SNES": "Super NES",
        "SFC": "Super NES",
        "FDS": "Famicom Disk System",
        "N64": "Nintendo 64",
        "SGB": "Super Gameboy",
        "Genesis": "Genesis +",
        "Mega Drive": "Genesis +",
        "32X": "Genesis 32X",
        "PCE": "TurboGrafx-16",
        "PCE-CD": "TurboGrafx-16 CD",
        "TG-16": "TurboGrafx-16",
        "TG-CD": "TurboGrafx-16 CD",
    }
    for k, v in extras.items():
        if v in _SYSTEM_ALIAS_MAP:
            _SYSTEM_ALIAS_MAP[k] = _SYSTEM_ALIAS_MAP[v]

def _canon_system(hint: str):
    if not hint:
        return None
    _build_boxart_ready = bool(CACHE) and bool(_SYSTEMS_FROM_CACHE)
    if not _build_boxart_ready:
        _load_boxart_cache()
    _build_system_aliases()

    # direct alias first
    if hint in _SYSTEM_ALIAS_MAP:
        return _SYSTEM_ALIAS_MAP[hint]

    # exact case-insensitive match to cache systems
    low = hint.lower()
    for s in _SYSTEMS_FROM_CACHE:
        if s.lower() == low:
            return s

    # fuzzy to cache systems (log if weak)
    got = _best_system_from_cache(hint)
    if got and got[0] >= 0.80:
        return got[1]

    print(f"System hint '{hint}' not confidently recognized; not scoping.", flush=True)
    return None

def _stem(name: str) -> str:
    return os.path.splitext(name)[0]

def _paren_tokens(stem: str):
    return [t.strip().lower() for t in re.findall(r"\((.*?)\)", stem)]

def _region_tokens_from_title(title: str):
    return set(t for t in _paren_tokens(title) if any(r in t for r in REGION_WORDS))

def _base_tokens(title: str):
    # non-parenthetical, longish tokens (helps kill "Anniversary Edition" false matches)
    base = re.sub(r"\s*\([^)]*\)", "", title or "")
    toks = re.findall(r"[A-Za-z0-9]+", base.lower())
    return [t for t in toks if len(t) >= 4]

def _score_candidate(stem: str, key: str, target_key: str, region_hint: set, target_stem_len: int, base_tokens) -> int:
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

    # must share at least one strong base token; otherwise heavily penalize
    if base_tokens and not any(t in key for t in base_tokens):
        base -= 40

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
    base_tokens = _base_tokens(game_name)

    candidates = CACHE
    scoped = []
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
        score = _score_candidate(_stem(fn), key, target_key, region_hint, target_stem_len, base_tokens)
        scored.append((score, len(fn), sys_folder, fn, raw_url, blob_url, key))

    if not scored:
        print(f"No boxart in cache for '{game_name}'", flush=True)
        return None, None

    scored.sort(reverse=True)
    top_score, _, top_sys, top_fn, top_raw, top_blob, _ = scored[0]
    min_needed = CONFIDENCE_MIN_SCOPED if scoped else CONFIDENCE_MIN_UNSCOPED
    if top_score < min_needed:
        print(f"Low-confidence match (score {top_score} < {min_needed}); skipping image.", flush=True)
        return None, None
    if sys_hint and top_sys != sys_hint:
        print(f"Top match is from different system [{top_sys}] than hint [{sys_hint}]; skipping image.", flush=True)
        return None, None

    print(f"Boxart match (scored {top_score}): [{top_sys}] {top_fn}", flush=True)
    return top_raw, top_blob

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
    try:
        RPC.connect()
    except KeyboardInterrupt:
        print("Cancelled before connecting. Goodbye!", flush=True)
        return
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
        try:
            RPC.clear()
            RPC.close()
        finally:
            print("Disconnected. Goodbye!", flush=True)

if __name__ == "__main__":
    main()
