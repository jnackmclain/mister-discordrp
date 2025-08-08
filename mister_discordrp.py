#!/usr/bin/env python3
"""
MiSTer FPGA Discord Rich Presence with Local Thumbnails & Fallback Asset

Save this script as mister_discord_presence.py
Dependencies:
    pip install pypresence requests

Clone the libretro-thumbnails repo next to this script:
    git clone https://github.com/libretro-thumbnails/libretro-thumbnails.git

Place your local fallback PNG (e.g., mister_kun_bw.png) next to this script.

Create a 'config_default.ini' alongside this script:

[discord]
client_id = YOUR_CLIENT_ID_HERE

[mister]
host = localhost
port = 8182
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

# --- Paths & Config ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
THUMB_DIR = os.path.join(BASE_DIR, 'libretro-thumbnails')
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

# --- Discord RPC ---
RPC = Presence(client_id)
IMAGE_KEY = 'mister'

# --- System-folder mapping ---
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
    # extend as needed
}

# --- Thumbnail lookup ---
def find_thumbnail_path(system_name, game_name):
    folder = SYSTEM_MAP.get(system_name)
    if not folder:
        return None
    art_dir = os.path.join(THUMB_DIR, folder, 'Named_Boxarts')
    if not os.path.isdir(art_dir):
        return None
    pngs = [f for f in os.listdir(art_dir) if f.lower().endswith('.png')]
    if not pngs:
        return None
    key = game_name.lower().replace(' ', '')
    # substring
    for f in pngs:
        if key in f.lower().replace(' ', ''):
            return os.path.join(art_dir, f)
    # fuzzy
    names = [os.path.splitext(f)[0] for f in pngs]
    matches = get_close_matches(game_name, names, n=1, cutoff=0.6)
    if matches:
        idx = names.index(matches[0])
        return os.path.join(art_dir, pngs[idx])
    # fallback
    return os.path.join(art_dir, pngs[0])

# --- MiSTer API ---
def fetch_playing(host, port):
    try:
        resp = requests.get(f"http://{host}:{port}/api/games/playing", timeout=5)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Error fetching playing info: {e}", flush=True)
        return None

# --- Time formatting ---
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

# --- Presence update ---
def set_presence(data, start_time, last_game):
    core = data.get('core', '') if data else ''
    system = data.get('systemName', '') if data else ''
    game = data.get('gameName', '') if data else ''

    # Fallback state: use systemName, or if missing, core value
    if not system and not game:
        details = "In Menu"
        state = "MiSTer FPGA"
        img_path = FALLBACK_IMG_PATH
    else:
        details = game or "Unknown"
        state = system if system else (core if core else "MiSTer FPGA")
        thumbnail = find_thumbnail_path(system, game)
        img_path = thumbnail if thumbnail else FALLBACK_IMG_PATH

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
        'large_image': IMAGE_KEY,
        'large_text': details
    }
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

# --- Main loop ---
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
