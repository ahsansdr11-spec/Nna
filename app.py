"""
KINGS DOWNLOADER
==========================
Web downloader berbasis yt-dlp untuk platform: YouTube, YouTube Shorts,
TikTok, Instagram, Facebook, X (Twitter), Pinterest, dan Spotify (musik
via YouTube Music + yt-dlp). URL dari platform lain yang didukung yt-dlp
tetap bisa dicoba secara generik.

Arsitektur: Flask + yt-dlp + ytmusicapi (mirip pola proyek YT Music Downloader).
Download dijalankan di background thread + polling status, sehingga
tidak ada timeout HTTP walau file besar.
"""

import os
import sys
import re
import time
import glob
import json
import shutil
import secrets
import sqlite3
import hashlib
import hmac
import uuid
import urllib.parse
import datetime
import base64
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET

# Pastikan direktori proyek (berisi yt_dlp_plugins/extractor/ — plugin
# Bilibili API) selalu ada di sys.path SEBELUM yt-dlp di-import. Ini penting
# saat dijalankan lewat gunicorn/WSGI yang mungkin tidak menaruh cwd di path.
_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
import threading
import zipfile
import logging
import html as html_mod
import subprocess

import requests
import yt_dlp
from flask import (Flask, request, jsonify, send_file,
                   after_this_request, Response, stream_with_context)

# ytmusicapi (opsional) — untuk pencarian lagu ala proyek YT Music Downloader
try:
    from ytmusicapi import YTMusic
    YTMUSIC = YTMusic()
    YTMUSIC_AVAILABLE = True
except Exception:
    YTMUSIC = None
    YTMUSIC_AVAILABLE = False

# ytmusicapi tidak 100% thread-safe → kunci semua pemakaian (server memakai
# beberapa thread untuk menangani banyak pengguna sekaligus).
YTMUSIC_LOCK = threading.Lock()

# Cache URL audio hasil resolve (untuk fitur Putar). URL YouTube kedaluwarsa
# ±6 jam, jadi TTL aman 30 menit → putar ulang langsung tanpa ekstrak ulang.
STREAM_CACHE = {}
STREAM_CACHE_LOCK = threading.Lock()
STREAM_CACHE_TTL = 30 * 60

# gallery-dl (opsional) — untuk konten berisi FOTO/STORY: Instagram (foto,
# carousel, story), X/Twitter (foto), Facebook (foto). yt-dlp hanya video.
try:
    from gallery_dl import extractor as gdl_extractor
    from gallery_dl import config as gdl_config
    GDL_AVAILABLE = True
except Exception:
    GDL_AVAILABLE = False

# gallery-dl extractor standalone memakai logging.Logger standar yang tidak
# punya method 'traceback' — ditambahkan oleh LoggerAdapter saat dipakai via
# Job. Tanpa ini, extractor crash saat ada error internal (mis. rate-limit).
if GDL_AVAILABLE:
    try:
        if not hasattr(logging.Logger, 'traceback'):
            def _gdl_traceback(self, exc):
                self.error('%s: %s', type(exc).__name__, exc)
            logging.Logger.traceback = _gdl_traceback
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Konfigurasi dasar
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS_DIR = os.path.join(BASE_DIR, 'downloads')
try:
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
except Exception:
    # Serverless (mis. Vercel): direktori proyek read-only → pakai /tmp
    import tempfile
    DOWNLOADS_DIR = os.path.join(tempfile.gettempdir(), 'universal-dl')
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)

app = Flask(__name__, static_folder='static')

# ============================================================================
# DATABASE (SQLite) — akun, sesi, chat, riwayat, saran platform
# ============================================================================
# DATA_DIR: biarkan kosong → data.db di folder proyek. Untuk deploy yang
# dibangun ulang tiap push (Railway/Render), SET DATA_DIR ke folder volume
# persisten (mis. /data) supaya akun/chat/riwayat TIDAK hilang saat redeploy.
def _pick_data_dir():
    """Tentukan folder penyimpanan data (akun/chat/riwayat/playlist/feedback).

    Prioritas:
      1) env DATA_DIR (mis. '/data' — volume persisten Railway/Render).
      2) /data — volume Railway yang di-mount tanpa env (auto-detect).
      3) folder proyek (data.db di samping app.py).
    Auto-detect /data membuat akun TIDAK hilang saat push, bahkan kalau user
    lupa set DATA_DIR — cukup mount volume di path /data.
    """
    cands = []
    env = (os.environ.get('DATA_DIR') or '').strip()
    if env:
        cands.append(env)
    cands.append('/data')
    cands.append('/app/data')
    for d in cands:
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, '.write_test')
            with open(probe, 'w') as f:
                f.write('ok')
            os.remove(probe)
            return d
        except Exception:
            continue
    return BASE_DIR


_DATA_DIR = _pick_data_dir()
DB_PATH = os.path.join(_DATA_DIR, 'data.db')
if _DATA_DIR != BASE_DIR:
    print('[DB] Data tersimpan di volume: %s (akun/chat/riwayat AMAN saat redeploy)' % _DATA_DIR)
else:
    print('[PERINGATAN] DATA_DIR tidak di-set & volume tidak ditemukan — data.db '
          'di folder proyek akan RESET saat redeploy. Mount volume Railway di /data '
          'atau set env DATA_DIR=/data supaya akun tidak hilang.')
DB_LOCK = threading.Lock()


def db_init():
    with sqlite3.connect(DB_PATH) as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            pass_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            is_guest INTEGER DEFAULT 0,
            created REAL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER,
            created REAL
        );
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            title TEXT, platform TEXT, mode TEXT,
            filename TEXT, size_mb REAL,
            created REAL
        );
        CREATE TABLE IF NOT EXISTS chat (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, username TEXT,
            message TEXT, created REAL
        );
        CREATE TABLE IF NOT EXISTS platform_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT, platform TEXT,
            created REAL
        );
        CREATE TABLE IF NOT EXISTS manga_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            manga_id TEXT, title TEXT, cover TEXT,
            chapter TEXT, chapter_id TEXT, lang TEXT,
            created REAL,
            UNIQUE(user_id, manga_id)
        );
        CREATE TABLE IF NOT EXISTS playlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, name TEXT,
            created REAL
        );
        CREATE TABLE IF NOT EXISTS playlist_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            playlist_id INTEGER,
            video_id TEXT, title TEXT, artist TEXT, thumbnail TEXT,
            pos INTEGER, created REAL
        );
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            message TEXT,
            page TEXT,
            created REAL
        );
        ''')
        c.commit()


db_init()


def db_query(sql, args=()):
    with DB_LOCK:
        with sqlite3.connect(DB_PATH) as c:
            c.row_factory = sqlite3.Row
            return c.execute(sql, args).fetchall()


def db_exec(sql, args=()):
    with DB_LOCK:
        with sqlite3.connect(DB_PATH) as c:
            cur = c.execute(sql, args)
            c.commit()
            return cur.lastrowid


def hash_password(password, salt=None):
    if not salt:
        salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
    return salt, digest.hex()


def verify_password(password, salt, expected):
    _, digest = hash_password(password, salt)
    return hmac.compare_digest(digest, expected)


def create_session(user_id):
    token = secrets.token_hex(24)
    db_exec("INSERT INTO sessions (token, user_id, created) VALUES (?,?,?)",
            (token, user_id, time.time()))
    return token


def get_user_by_token(token):
    if not token:
        return None
    rows = db_query(
        "SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token=?",
        (token,))
    return rows[0] if rows else None


def get_username(user):
    return user['username'] if user else 'Tamu'


def _auth_from_request():
    """Ambil user dari header X-Auth-Token (atau ?token=)."""
    token = request.headers.get('X-Auth-Token') or request.args.get('token')
    return get_user_by_token(token)

USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36')

BROWSER_HEADERS = {
    'User-Agent': USER_AGENT,
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Upgrade-Insecure-Requests': '1',
}

JOB_TTL = 30 * 60          # job + file dibersihkan setelah 30 menit

# ---------------------------------------------------------------------------
# Pemilihan resolusi (default 1080p untuk semua platform)
# ---------------------------------------------------------------------------
RESOLUTIONS = {
    '2160':    'bv*[height<=2160]+ba/b[height<=2160]',
    '1440':    'bv*[height<=1440]+ba/b[height<=1440]',
    '1080':    'bv*[height<=1080]+ba/b[height<=1080]',   # default
    '720':     'bv*[height<=720]+ba/b[height<=720]',
    '480':     'bv*[height<=480]+ba/b[height<=480]',
    '360':     'bv*[height<=360]+ba/b[height<=360]',
    'original': 'bv*+ba/b',
}
DEFAULT_RESOLUTION = '1080'
JOBS = {}
JOBS_LOCK = threading.Lock()

# ---------------------------------------------------------------------------
# Daftar platform yang ditampilkan di UI
# ---------------------------------------------------------------------------
PLATFORMS = [
    {'key': 'youtube',        'name': 'YouTube',           'icon': '/static/icons/youtube.png',     'urls': ['youtube.com', 'youtu.be']},
    {'key': 'tiktok',         'name': 'TikTok',            'icon': '/static/icons/tiktok.png',      'urls': ['tiktok.com', 'vt.tiktok.com', 'vm.tiktok.com']},
    {'key': 'instagram',      'name': 'Instagram',         'icon': '/static/icons/instagram.png',   'urls': ['instagram.com', 'instagr.am', 'ig.me']},
    {'key': 'facebook',       'name': 'Facebook',          'icon': '/static/icons/facebook.png',    'urls': ['facebook.com', 'fb.watch', 'fb.com', 'fb.me', 'm.facebook.com']},
    {'key': 'x',              'name': 'X (Twitter)',       'icon': '/static/icons/x.png',           'urls': ['x.com', 'twitter.com']},
    {'key': 'pinterest',      'name': 'Pinterest',         'icon': '/static/icons/pinterest.png',   'urls': ['pinterest.com', 'pin.it']},
    {'key': 'spotify',        'name': 'Spotify',           'icon': '/static/icons/spotify.png',     'urls': ['open.spotify.com', 'spotify.link']},
    {'key': 'dailymotion',    'name': 'Dailymotion',       'icon': '/static/icons/dailymotion.png', 'urls': ['dailymotion.com', 'dai.ly']},
    {'key': 'soundcloud',     'name': 'SoundCloud',        'icon': '/static/icons/soundcloud.png',  'urls': ['soundcloud.com', 'on.soundcloud.com', 'snd.sc']},
    {'key': 'archiveorg',     'name': 'Archive.org',       'icon': '/static/icons/archiveorg.png',  'urls': ['archive.org']},
    {'key': 'twitch',         'name': 'Twitch',            'icon': '/static/icons/twitch.png',      'urls': ['twitch.tv', 'clips.twitch.tv']},
    {'key': 'bandcamp',       'name': 'Bandcamp',          'icon': '/static/icons/bandcamp.png',    'urls': ['bandcamp.com']},
    {'key': 'mixcloud',       'name': 'Mixcloud',          'icon': '/static/icons/mixcloud.png',    'urls': ['mixcloud.com']},
    {'key': 'streamable',     'name': 'Streamable',        'icon': '/static/icons/streamable.png', 'urls': ['streamable.com']},
    {'key': 'bilibili',       'name': 'Bilibili',          'icon': '/static/icons/bilibili.png',    'urls': ['bilibili.com', 'bilibili.tv', 'biliintl.com', 'b23.tv']},
    {'key': 'vimeo',          'name': 'Vimeo',            'icon': '/static/icons/vimeo.png',      'urls': ['vimeo.com', 'player.vimeo.com']},
    {'key': 'snackvideo',     'name': 'SnackVideo',       'icon': '/static/icons/snackvideo.png', 'urls': ['snackvideo.com', 's.snackvideo.com', 'sck.io']},
    {'key': 'rednote',        'name': 'RedNote',          'icon': '/static/icons/rednote.png',    'urls': ['xiaohongshu.com', 'xhslink.com']},
    {'key': 'videy',          'name': 'Videy',            'icon': '/static/icons/videy.png',      'urls': ['videy.co', 'cdn.videy.co']},
    {'key': 'github',         'name': 'GitHub',           'icon': '/static/icons/github.png',     'urls': ['github.com', 'raw.githubusercontent.com']},
    {'key': 'mediafire',      'name': 'MediaFire',        'icon': '/static/icons/mediafire.png',  'urls': ['mediafire.com']},
    {'key': 'threads',        'name': 'Threads',          'icon': '/static/icons/threads.png',    'urls': ['threads.net', 'threads.com']},
    {'key': 'snapchat',       'name': 'Snapchat',         'icon': '/static/icons/snapchat.png',   'urls': ['snapchat.com']},
    {'key': 'reddit',         'name': 'Reddit',           'icon': '/static/icons/reddit.png',     'urls': ['reddit.com', 'redd.it', 'reddit.app']},
    {'key': 'douyin',         'name': 'Douyin',           'icon': '/static/icons/douyin.png',     'urls': ['douyin.com', 'v.douyin.com']},
    {'key': 'rutube',         'name': 'Rutube',           'icon': '/static/icons/rutube.png',     'urls': ['rutube.ru', 'rutube.com']},
]




# Pilihan MANUAL platform: platform key → daftar ekstraktor yt-dlp yang dicoba
# (dipakai saat user memilih platform secara manual — fitur opsional; auto tetap
# jadi default). None = biarkan yt-dlp mencocokkan URL sendiri (paling aman
# untuk platform dengan banyak ekstraktor seperti Bilibili .com vs .tv).
PLATFORM_IE_KEYS = {
    'youtube':        ['Youtube'],
    'tiktok':         ['TikTok'],
    'instagram':      ['Instagram'],
    'facebook':       ['Facebook'],
    'x':              ['Twitter', 'X'],
    'pinterest':      ['Pinterest'],
    'dailymotion':    ['Dailymotion'],
    'soundcloud':     ['Soundcloud'],
    'archiveorg':     ['ArchiveOrg'],
    'twitch':         ['Twitch', 'TwitchClips'],
    'bandcamp':       ['Bandcamp'],
    'mixcloud':       ['Mixcloud'],
    'streamable':     ['Streamable'],
    'vimeo':          ['VimeoApi'],
    'snackvideo':     None,   # generic yt-dlp (s.snackvideo.com)
    'rednote':        ['XiaoHongShu'],
    'videy':          None,   # custom (cdn.videy.co langsung)
    'github':         None,   # custom (raw / releases)
    'mediafire':      None,   # custom (parse halaman → direct link)
    'threads':        None,   # custom (parse og:video / video_versions)
    'snapchat':       ['SnapchatSpotlight'],
    'reddit':         None,   # custom (oEmbed / .json — butuh IP non-datacenter)
    'douyin':         ['Douyin'],
    'rutube':         ['rutube'],
    'bilibili':       None,   # .com/.tv — biarkan auto
    'spotify':        None,   # di-handle khusus
}


def find_platform(key):
    """Cari entry platform berdasarkan key. None kalau tidak ada."""
    for p in PLATFORMS:
        if p['key'] == key:
            return p
    return None


def detect_platform(url):
    """Deteksi platform berdasarkan domain URL (lebih spesifik didahulukan)."""
    url = (url or '').lower()
    ordered = sorted(PLATFORMS, key=lambda p: -max(len(d) for d in p['urls']))
    for p in ordered:
        if any(d in url for d in p['urls']):
            return p
    return None


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def safe_filename(name, maxlen=120):
    """Bersihkan karakter ilegal untuk nama file."""
    name = re.sub(r'[\\/*?:"<>|\x00-\x1f]', '', str(name)).strip()
    name = re.sub(r'\s+', ' ', name)
    if not name:
        name = 'download'
    return name[:maxlen]


def format_duration(seconds):
    """Format durasi. None/'' → '' (tampil '—' di UI, bukan '0:00' palsu)."""
    if seconds is None or seconds == '':
        return ''
    seconds = int(seconds)
    if seconds <= 0:
        return ''
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def mb(filesize):
    if not filesize:
        return None
    return round(filesize / (1024 * 1024), 1)


_IMPERSONATE_CACHE = None

# ---------------------------------------------------------------------------
# Cooldown ANTI-BOT PER-PLATFORM
# Setelah satu kegagalan yang menandakan platform memblokir/membatasi IP kita
# (bot-check, rate-limit, 403, challenge, dsb.), kita mencatat "sampai kapan"
# untuk platform itu. Request berikutnya ke platform yang sama menunggu dulu.
# Ini mencegah efek domino: satu kegagalan tidak lagi membuat SEMUA permintaan
# berikutnya ke platform itu ikut diblokir.
# ---------------------------------------------------------------------------
PLATFORM_COOLDOWN = {}          # key platform -> timestamp (time.time + durasi)
PLATFORM_COOLDOWN_LOCK = threading.Lock()
COOLDOWN_SECS = 10              # durasi jeda per platform (pendek, biar UI cepat respons)
COOLDOWN_MAX_WAIT = 10          # maksimal lama menunggu sekali request

# Sinyal bahwa platform membatasi/memblokir kita (harus menunggu dulu)
BLOCK_SIGNALS = (
    'sign in to confirm', 'not a bot', 'bot check', 'unable to extract initial data',
    'rehydration', 'challenge', 'rate limit', 'too many requests',
    '429', '403 forbidden', 'blocked', 'temporarily', 'try again later',
    'ip address is blocked', 'access denied', 'login required', 'log in to continue',
    'http error 429', 'http error 403', 'solving js challenge', 'verification',
    'unable to extract universal data',
)


def platform_key(url):
    p = detect_platform(url or '')
    return p['key'] if p else 'generic'


def is_block_signal(exc):
    m = str(exc).lower()
    return any(s in m for s in BLOCK_SIGNALS)


def mark_platform_cooldown(url, secs=COOLDOWN_SECS):
    key = platform_key(url)
    with PLATFORM_COOLDOWN_LOCK:
        PLATFORM_COOLDOWN[key] = time.time() + secs


def wait_platform_cooldown(url):
    """Tunggu sisa cooldown platform ini (jika ada), maks COOLDOWN_MAX_WAIT."""
    remaining = remaining_platform_cooldown(url)
    if remaining > 0:
        time.sleep(min(remaining, COOLDOWN_MAX_WAIT))


def remaining_platform_cooldown(url):
    """Sisa waktu cooldown platform ini (detik). 0 = tidak perlu menunggu."""
    key = platform_key(url)
    with PLATFORM_COOLDOWN_LOCK:
        until = PLATFORM_COOLDOWN.get(key, 0)
    rem = until - time.time()
    return rem if rem > 0 else 0


# Alias kompatibilitas untuk kode lama
YT_COOLDOWN_UNTIL = 0.0


# Jeda pendek ANTAR PERCOBAAN yang GAGAL dalam satu job (biar tidak nge-stuck
# lama). Jeda TIDAK dipakai sebelum percobaan pertama — jadi jalan yang sukses
# langsung jalan tanpa nunda-nunda.
YT_INTRA_WAIT = 1.0


def _yt_cooldown_wait():
    time.sleep(YT_INTRA_WAIT)


def _yt_mark_cooldown():
    mark_platform_cooldown('https://www.youtube.com/')


def make_impersonate():
    """Aktifkan impersonasi browser (butuh curl_cffi) untuk lolos blokir
    TLS/anti-bot. Memilih target Chrome yang BENAR-BENAR tersedia di versi
    curl_cffi terpasang (target bisa beda antar versi: chrome-136, 131, 120…).
    Kalau curl_cffi tidak ada / tidak ada target yang valid → kembalikan None
    (yt-dlp jalan normal tanpa impersonasi, tidak error)."""
    global _IMPERSONATE_CACHE
    if _IMPERSONATE_CACHE is not None:
        return _IMPERSONATE_CACHE
    result = None
    try:
        import curl_cffi  # noqa: F401
        from yt_dlp.networking.impersonate import ImpersonateTarget
        from yt_dlp import YoutubeDL

        candidates = [
            ImpersonateTarget(client='chrome', version='136'),
            ImpersonateTarget(client='chrome', version='131'),
            ImpersonateTarget(client='chrome', version='126'),
            ImpersonateTarget(client='chrome', version='120'),
            ImpersonateTarget(client='chrome', version='116'),
            ImpersonateTarget(client='chrome'),          # generic chrome
        ]
        for t in candidates:
            try:
                with YoutubeDL({'quiet': True, 'impersonate': t}) as ydl:
                    pass
                result = t
                break
            except Exception:
                continue
    except Exception:
        result = None
    _IMPERSONATE_CACHE = result
    return result


def extract_with_fallback(url, opts, ie_key=None):
    """extract_info dengan fallback: kalau impersonasi tidak tersedia/gagal,
    ulangi sekali tanpa impersonate. Error transien (TikTok challenge, dll)
    juga dicoba ulang sekali. Ada cooldown per-platform anti-bot.
    ie_key (opsional) = paksa ekstraktor tertentu (pilihan platform manual);
    kalau paksaan gagal, otomatis fallback ke mode auto (default)."""
    def run(opts_, key):
        with yt_dlp.YoutubeDL(opts_) as ydl:
            if key:
                return ydl.extract_info(url, download=False, ie_key=key)
            return ydl.extract_info(url, download=False)

    wait_platform_cooldown(url)
    if ie_key:
        # Coba dulu dengan paksaan manual; gagal → lanjut ke auto di bawah
        try:
            return run(opts, ie_key)
        except Exception:
            pass
    try:
        return run(opts, None)
    except Exception as e:
        msg = str(e)

        # 0) Sinyal blokir platform → catat cooldown (request berikutnya menunggu)
        if is_block_signal(e):
            mark_platform_cooldown(url)

        # 1) YouTube bot-check → retry dengan player client berbeda
        if is_yt_bot_error(e):
            try:
                return yt_extract_with_retry(url, opts)
            except Exception as e2:
                raise e2

        # 2) Impersonasi bermasalah → coba tanpa impersonate
        if opts.get('impersonate') and ('impersonate' in msg.lower() or 'tls fingerprint' in msg.lower()):
            opts2 = dict(opts)
            opts2.pop('impersonate', None)
            return run(opts2)

        # 3) Error transien TikTok / challenge / jaringan → coba sekali lagi
        transient = ('rehydration' in msg.lower() or 'challenge' in msg.lower()
                     or 'temporarily' in msg.lower() or 'try again later' in msg.lower()
                     or 'blocked' in msg.lower() or 'http error 5' in msg.lower()
                     or 'timeout' in msg.lower() or 'connection' in msg.lower())
        if transient:
            try:
                return run(opts)
            except Exception:
                pass
        raise


def friendly_error(msg):
    """Ubah pesan error teknis menjadi kalimat ramah & sederhana (untuk publik)."""
    msg = str(msg)
    low = msg.lower()

    # Kasus spesifik lebih dulu (sebelum catch-all "not found")
    # Bilibili TV: region lock / crash ekstraktor lama
    if 'bilibili tv' in low or '版权地区受限' in msg or '10015001' in msg:
        return ('Video Bilibili TV ini dibatasi wilayah (geo-block) dari server — '
                'hanya bisa diputar di negara/region tertentu sesuai lisensi. '
                'Coba video lain, atau pakai tautan bilibili.com (versi China) '
                'yang biasanya bisa diunduh.')
    if 'play-av tag not found' in low:
        return ('Halaman arsip ini tidak punya media yang bisa diputar langsung. '
                'Coba tempel tautan file spesifiknya (klik salah satu file di '
                'halaman Archive.org, lalu salin alamatnya).')
    if 'does not exist' in low and 'twitch' in low:
        return ('Video siaran (VOD) Twitch ini tidak ditemukan atau sudah dihapus. '
                'Clip yang masih publik tetap bisa diunduh ya!')
    # RedNote (Xiaohongshu) — blokir anti-bot IP
    if 'rednote' in low or 'xiaohongshu' in low or 'xhslink' in low:
        return ('RedNote sedang menolak permintaan dari server ini (anti-bot). '
                'Ini bukan salahmu! Coba lagi beberapa menit, atau tempel link '
                'share post-nya (tombol Bagikan → Salin Tautan) — biasanya lebih berhasil.')

    # Konten tidak ditemukan / dihapus / tautan salah
    if (('404' in msg and ('not found' in low or 'http error' in low))
            or 'does not exist' in low or 'not found' in low
            or 'no longer available' in low):
        return ('Konten ini tidak ditemukan — mungkin sudah dihapus, URL-nya salah, '
                'atau videonya sudah kedaluwarsa. Cek kembali tautannya ya!')

    if 'rehydration' in low and 'tiktok' in low:
        return ('TikTok sedang sibuk melindungi kontennya dan menolak permintaan ini. '
                'Tenang, ini bukan salahmu! Coba: ganti jaringan (Wi-Fi ke data seluler, '
                'atau sebaliknya), tunggu beberapa menit, lalu coba lagi.')
    if ('ffmpeg' in low and ('not found' in low or 'ffprobe' in low
                             or 'is not installed' in low or 'merge' in low)):
        return ('Fitur MP3 butuh ffmpeg yang belum terpasang di server ini. '
                'Hubungi pengelola website untuk memasangnya (atau gunakan mode video).')
    if 'drm' in low or ('protected' in low and 'youtube' in low):
        return ('Video ini dilindungi DRM oleh YouTube — artinya hanya bisa diputar di '
                'aplikasi resmi dan tidak bisa diunduh oleh siapa pun. Coba video lain ya!')
    if 'impersonate target' in low:
        return ('Server ini belum punya komponen anti-bot yang optimal, tapi tidak masalah — '
                'kamu tetap bisa mengunduh dari kebanyakan platform.')
    if 'sign in to confirm' in low or ('cookies' in low and ('sign in' in low or 'confirm' in low)):
        if 'youtube' in low or 'youtu.be' in low:
            return ('YouTube sedang sibuk dan menolak unduhan untuk video ini dari server. '
                    'Ini bukan masalah di aplikasi. Coba video lain dulu, atau tunggu 2–3 '
                    'menit lalu coba lagi.')
        return ('Platform ini sedang meminta verifikasi login. Coba tautan lain yang lebih '
                'publik, atau tunggu sebentar lalu coba lagi.')
    if 'unsupported url' in low:
        return ('Hmm, tautan ini belum dikenal. Pastikan tautannya dari YouTube, TikTok, '
                'Instagram, Facebook, X, Pinterest, atau platform lain yang didukung ya.')
    if 'ip address is blocked' in low or 'blocked due to' in low or 'tls fingerprint' in low:
        return ('Platform ini sedang memblokir permintaan dari server. Coba tunggu beberapa '
                'menit, atau gunakan video/postingan lain.')
    if 'no video formats' in low or 'no video could be found' in low:
        return ('Postingan ini tidak berisi video yang bisa diunduh. '
                'Mungkin ini foto, atau videonya bersifat privat.')
    if 'private' in low or 'login' in low:
        return ('Konten ini bersifat privat atau butuh login — jadi tidak bisa diunduh. '
                'Coba postingan publik lain ya!')
    if 'video unavailable' in low or 'not available' in low:
        return ('Video ini tidak tersedia (mungkin dihapus, privat, atau dibatasi negara). '
                'Coba video lain!')
    if 'unable to download video data' in low or '403 forbidden' in low:
        return ('Server video sedang menolak unduhan untuk konten ini dari server. '
                'Ini bukan masalah di aplikasi — coba video lain, atau tunggu '
                'beberapa menit lalu coba lagi.')
    return ('Ups, ada kendala saat memproses tautan ini. Coba lagi sebentar lagi, '
            'atau gunakan tautan lain.')


def base_ydl_opts():
    opts = {
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'retries': 4,
        'fragment_retries': 4,
        'file_access_retries': 3,
        'socket_timeout': 15,
        'extractor_args': {
            'youtube': {'player_client': ['android_vr']}
        },
    }
    imp = make_impersonate()
    if imp is not None:
        opts['impersonate'] = imp
    return opts


# Indikator YouTube memblokir bot (minta login verifikasi)
YT_BOT_HINTS = ('sign in to confirm', 'confirm you', 'not a bot', 'bot check', 'unable to extract initial data')


POT_AVAILABLE = False
try:
    # plugin yt-dlp-get-pot dimuat via namespace yt_dlp_plugins (getpot.py)
    import yt_dlp_plugins.extractor.getpot  # noqa: F401
    POT_AVAILABLE = True
except Exception:
    try:
        import pkgutil, yt_dlp_plugins
        POT_AVAILABLE = any('getpot' in m.name for m in pkgutil.iter_modules(yt_dlp_plugins.__path__))
    except Exception:
        POT_AVAILABLE = False

POT_PROVIDER = 'bgutil-ytdlp-pot-provider'
# Provider POT cadangan yang dicoba kalau provider utama gagal (urutan)
POT_PROVIDERS = ['bgutil-ytdlp-pot-provider']


def with_player_client(opts, client, pot=False):
    """Salin opts dengan player client YouTube tertentu (None = biarkan default).
    pot=True → tambahkan PO token provider (best-effort, untuk lolos bot-check
    YouTube di IP datacenter)."""
    o = dict(opts)
    ea = dict(o.get('extractor_args') or {})
    yt = dict(ea.get('youtube') or {})
    if client is None:
        yt.pop('player_client', None)
    else:
        yt['player_client'] = client
    if pot and POT_AVAILABLE:
        yt['pot_provider'] = POT_PROVIDER
    elif not pot:
        yt.pop('pot_provider', None)
    if yt:
        ea['youtube'] = yt
        o['extractor_args'] = ea
    else:
        o.pop('extractor_args', None)
    return o


def try_pot_with_backoff(url, opts, want_info=False):
    """Coba unduh/ekstrak YouTube memakai PO token. Kalau provider POT down,
    tunggu lebih lama lalu coba sekali lagi (backoff). Kembalikan info_dict
    kalau want_info, None kalau sukses download, raise kalau semua gagal."""
    if not POT_AVAILABLE:
        raise RuntimeError('PO token plugin tidak tersedia')
    last = None
    for attempt in range(2):   # 2 percobaan: langsung + backoff
        for cl in (['android_vr'], ['ios'], ['android'], ['tv']):
            try:
                with yt_dlp.YoutubeDL(with_player_client(opts, cl, pot=True)) as ydl:
                    if want_info:
                        return ydl.extract_info(url, download=False)
                    ydl.download([url])
                    return None
            except Exception as e:
                last = e
                if is_drm_error(e):
                    continue
                if is_yt_bot_error(e):
                    time.sleep(1.2)
                    continue
                if is_format_unavailable(e):
                    # client ini tidak punya format yang cocok → coba client lain
                    continue
                raise
        # semua client POT gagal → backoff singkat lalu coba sekali lagi
        if attempt == 0:
            time.sleep(4)
    raise last


def is_youtube(url):
    return 'youtube.com' in (url or '') or 'youtu.be' in (url or '')


def is_yt_bot_error(exc):
    m = str(exc).lower()
    return any(h in m for h in YT_BOT_HINTS)


def is_drm_error(exc):
    m = str(exc).lower()
    return 'drm' in m or 'protected' in m or 'widevine' in m


def is_format_unavailable(exc):
    m = str(exc).lower()
    return 'requested format is not available' in m or 'no matching format' in m


# Urutan client YouTube yang dicoba (dari yang paling sering tembus).
# android_vr = penembus blokir IP datacenter paling andal (tanpa cookie).
# web_embedded / tv_downgraded / tv_simply / android / mweb ikut jalan
# saat ada JS runtime (deno) untuk solver challenge — jadi kalau satu
# client diblokir YouTube untuk video tertentu, client lain bisa lolos.
YT_CLIENTS = [
    ['android_vr'],
    ['web_embedded'],
    ['tv_downgraded'],
    ['tv_simply'],
    ['android'],
    ['mweb'],
    ['ios'],
    ['tv'],
    None,                     # default yt-dlp (android_vr / web_safari)
]
# Jurus pamungkas: minta yt-dlp mencoba SEMUA client sekaligus (secara
# internal dia melewati client yang gagal & memakai yang berhasil).
YT_CLIENTS_LAST = ['all']


def yt_download_with_retry(url, opts):
    """ydl.download dengan retry ganti player client saat YouTube minta
    verifikasi bot / blokir / DRM-only dari satu client. Client dicoba
    berurutan (android_vr paling depan — penembus blokir IP datacenter),
    lalu sebagai pamungkas minta yt-dlp mencoba semua client sekaligus.
    Ada cooldown agar kegagalan beruntun tidak memblokir IP."""
    last = None
    for cl in YT_CLIENTS:
        o = with_player_client(opts, cl)
        try:
            with yt_dlp.YoutubeDL(o) as ydl:
                ydl.download([url])
            return
        except Exception as e:
            last = e
            # Jeda hanya DIPAKAI setelah percobaan gagal — jalan pertama yang
            # sukses tidak perlu menunggu sama sekali.
            _yt_cooldown_wait()
            if is_yt_bot_error(e):
                _yt_mark_cooldown()
                time.sleep(1.2)
                continue
            if is_drm_error(e):
                time.sleep(1.0)
                continue
            raise
    # Last resort: semua client sekaligus (yt-dlp otomatis lewati yang gagal)
    try:
        with yt_dlp.YoutubeDL(with_player_client(opts, YT_CLIENTS_LAST)) as ydl:
            ydl.download([url])
        return
    except Exception as e:
        last = e
    # Terakhir: PO token kalau plugin tersedia (opsional, best-effort)
    if POT_AVAILABLE:
        try:
            try_pot_with_backoff(url, opts, want_info=False)
            return
        except Exception as e:
            last = e
    raise last


def yt_extract_with_retry(url, opts):
    """extract_info (metadata) dengan retry ganti player client saat
    bot-check / DRM-only dari satu client (dengan cooldown)."""
    last = None
    for cl in YT_CLIENTS:
        o = with_player_client(opts, cl)
        try:
            with yt_dlp.YoutubeDL(o) as ydl:
                return ydl.extract_info(url, download=False)
        except Exception as e:
            last = e
            _yt_cooldown_wait()
            if is_yt_bot_error(e):
                _yt_mark_cooldown()
                time.sleep(1.2)
                continue
            if is_drm_error(e):
                time.sleep(1.0)
                continue
            raise
    # Last resort: semua client sekaligus (yt-dlp otomatis lewati yang gagal)
    try:
        with yt_dlp.YoutubeDL(with_player_client(opts, YT_CLIENTS_LAST)) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        last = e
    # Terakhir: PO token kalau plugin tersedia (opsional, best-effort)
    if POT_AVAILABLE:
        try:
            return try_pot_with_backoff(url, opts, want_info=True)
        except Exception as e:
            last = e
    raise last


def format_label(f):
    """Buat label ramah-manusia untuk sebuah format."""
    h = f.get('height') or 0
    res = f"{h}p" if h else ''
    vcodec = f.get('vcodec') or ''
    acodec = f.get('acodec') or ''
    if vcodec and vcodec != 'none' and acodec and acodec != 'none':
        kind = 'video+audio'
    elif vcodec and vcodec != 'none':
        kind = 'video only'
    elif acodec and acodec != 'none':
        kind = 'audio only'
    else:
        kind = ''

    bits = [p for p in [res, kind, (f.get('ext') or '').upper()] if p]
    if f.get('fps') and f.get('fps') > 30:
        bits.append(f"{f.get('fps')}fps")
    tbr = f.get('tbr')
    if tbr:
        bits.append(f"{round(tbr)}kbps")
    size = mb(f.get('filesize') or f.get('filesize_approx'))
    if size:
        bits.append(f"{size}MB")
    return ' · '.join(bits) if bits else (f.get('format_id') or '?')


def pick_thumbnail(info):
    thumbs = info.get('thumbnails') or []
    for t in reversed(thumbs):            # thumbnail terbesar biasanya terakhir
        url = t.get('url')
        if url:
            return url
    return info.get('thumbnail')


def parse_info(info):
    """Rangkum hasil extract_info menjadi JSON yang aman & ringan untuk UI."""
    formats = []
    seen = set()
    for f in (info.get('formats') or []):
        fid = f.get('format_id')
        # Skip storyboard / format kosong tanpa id
        if not fid or str(fid).startswith('sb'):
            continue
        if fid in seen:
            continue
        seen.add(fid)
        vcodec = f.get('vcodec')
        acodec = f.get('acodec')
        # Situs lama (mis. archive.org) tidak mengisi codec sama sekali;
        # tetap tampilkan sebagai opsi bila punya URL download.
        if not vcodec and not acodec and not f.get('url'):
            continue
        formats.append({
            'format_id': fid,
            'label': format_label(f),
            'ext': f.get('ext'),
            'height': f.get('height'),
            'width': f.get('width'),
            'fps': f.get('fps'),
            'vcodec': vcodec,
            'acodec': acodec,
            'tbr': round(f.get('tbr') or 0) or None,
            'filesize_mb': mb(f.get('filesize') or f.get('filesize_approx')),
        })

    # Beberapa ekstraktor hanya memberi 'url' langsung tanpa formats
    if not formats and info.get('url'):
        formats.append({
            'format_id': 'direct',
            'label': 'Stream langsung (source)',
            'ext': info.get('ext'),
            'vcodec': None,
            'acodec': None,
            'filesize_mb': mb(info.get('filesize')),
        })

    platform = detect_platform(info.get('webpage_url') or '')
    return {
        'id': info.get('id'),
        'title': info.get('title') or 'Untitled',
        'uploader': info.get('uploader') or info.get('channel') or info.get('creator') or 'Unknown',
        'duration': info.get('duration'),
        'duration_text': format_duration(info.get('duration')),
        'thumbnail': pick_thumbnail(info),
        'webpage_url': info.get('webpage_url') or info.get('original_url'),
        'ext': info.get('ext'),
        'view_count': info.get('view_count'),
        'like_count': info.get('like_count'),
        'formats': formats,
        'has_video': any(
            (f.get('vcodec') and f.get('vcodec') != 'none') or (f.get('height') or 0) > 0
            or (f.get('ext') in ('mp4', 'webm', 'mkv', 'mov', 'm4v') and not f.get('acodec'))
            for f in formats
        ),
        'has_audio': any(
            (f.get('acodec') and f.get('acodec') != 'none')
            or (not f.get('height') and f.get('tbr'))
            or (f.get('ext') in ('mp3', 'm4a', 'aac', 'opus', 'ogg', 'wav') and not f.get('vcodec'))
            for f in formats
        ),
        'max_height': max((f.get('height') or 0) for f in formats) if formats else 0,
        'platform': platform,
    }


def parse_ytm_song(song):
    """Konversi satu hasil pencarian ytmusicapi menjadi struktur mirip info."""
    thumbs = song.get('thumbnails') or []
    thumb = thumbs[-1]['url'] if thumbs else None
    artists = song.get('artists') or []
    artist = ", ".join(a.get('name', '') for a in artists if a.get('name')) or 'Unknown'
    album = (song.get('album') or {}).get('name') if isinstance(song.get('album'), dict) else (song.get('album') or '')
    dur = song.get('duration_seconds') or 0
    return {
        'id': song.get('videoId'),
        'title': song.get('title') or 'Untitled',
        'uploader': artist,
        'album': album or 'Single',
        'duration': dur,
        'duration_text': format_duration(dur),
        'thumbnail': thumb,
        'videoId': song.get('videoId'),
        'artist': artist,
    }


def resolve_spotify(url):
    """Resolusi URL Spotify (track) → lagu di YouTube Music, lalu sediakan format audio.

    Spotify tidak menyediakan file asli (DRM), jadi seperti proyek
    YT Music Downloader: judul/artis dibaca dari oEmbed Spotify,
    lagu dicari di YouTube Music (ytmusicapi), audio diunduh via yt-dlp.
    """
    if not YTMUSIC_AVAILABLE:
        raise RuntimeError(
            'Fitur Spotify butuh library ytmusicapi. Install dulu di Termux: '
            'pip install ytmusicapi'
        )

    r = requests.get(
        'https://open.spotify.com/oembed?url=' + url,
        timeout=20,
        headers=BROWSER_HEADERS,
    )
    if r.status_code != 200:
        raise RuntimeError(
            'Tidak dapat membaca metadata lagu Spotify. Pastikan tautannya valid '
            '(track publik), atau coba tautan lain.'
        )
    data = r.json()
    title = (data.get('title') or '').strip()
    if not title:
        raise RuntimeError('Tidak ada judul lagu yang bisa dibaca dari tautan Spotify.')

    # Untuk album/playlist, oEmbed cuma memberi judul koleksi — minta buka per-lagu
    low_url = url.lower()
    if '/track/' not in low_url:
        raise RuntimeError(
            'Tautan ini bukan single track. Spotify hanya kami dukung per-lagu: '
            'buka lagunya (open.spotify.com/track/...) lalu tempel tautannya.'
        )

    with YTMUSIC_LOCK:
        results = YTMUSIC.search(title, filter='songs', limit=5)
    if not results:
        raise RuntimeError(
            f'Tidak menemukan lagu "{title}" di YouTube Music. '
            'Coba cari manual lewat panel "Cari lagu" (klik kartu Spotify).'
        )

    # Pilih hasil dengan judul paling mirip
    best = results[0]
    for item in results:
        if (item.get('title') or '').strip().lower() == title.lower():
            best = item
            break

    song = parse_ytm_song(best)
    video_id = song['videoId']
    if not video_id:
        raise RuntimeError('Hasil pencarian tidak memiliki ID video yang valid.')

    return {
        'ok': True,
        'id': video_id,
        'title': f"{song['title']} — {song['artist']}",
        'uploader': song['artist'],
        'album': song['album'],
        'duration': song['duration'],
        'duration_text': song['duration_text'],
        'thumbnail': song['thumbnail'],
        'webpage_url': f'https://www.youtube.com/watch?v={video_id}',
        'ext': 'webm',
        'view_count': None,
        'like_count': None,
        'formats': [
            {'format_id': 'mp3', 'label': 'Audio MP3 — 192 kbps (via YouTube Music)',
             'ext': 'mp3', 'vcodec': None, 'acodec': 'mp3', 'tbr': 192, 'filesize_mb': None},
            {'format_id': 'bestaudio', 'label': 'Audio asli — tanpa konversi',
             'ext': 'webm', 'vcodec': None, 'acodec': 'opus', 'tbr': None, 'filesize_mb': None},
        ],
        'has_video': False,
        'has_audio': True,
        'max_height': 0,
        'platform': {'key': 'spotify', 'name': 'Spotify', 'icon': '/static/icons/spotify.png'},
        'note': 'Audio diambil dari YouTube Music (Spotify tidak menyediakan file asli).',
    }


def clean_name(value):
    """Ubah nilai 'uploader/author/username' apa pun menjadi string yang aman.
    gallery-dl sering memberi dict (mis. TikTok author = {nickname, uniqueId, ...})."""
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        for k in ('nickname', 'name', 'username', 'uniqueId', 'unique_id',
                  'screen_name', 'full_name', 'fullname', 'title'):
            v = value.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return None
    if isinstance(value, (list, tuple)) and value:
        return clean_name(value[0])
    try:
        s = str(value).strip()
        return s or None
    except Exception:
        return None



def _extract_meta(post, url):
    """Ambil metadata sebanyak mungkin dari post dict gallery-dl
    (uploader, views, likes, durasi) — lintas platform."""
    post = post or {}
    stats = post.get('stats') or {}
    stats_v2 = post.get('statsV2') or {}
    video = post.get('video') or {}
    if not isinstance(stats, dict):
        stats = {}
    if not isinstance(stats_v2, dict):
        stats_v2 = {}
    if not isinstance(video, dict):
        video = {}

    # views: banyak nama field
    views = (post.get('views') or post.get('view_count')
             or stats.get('playCount') or stats_v2.get('playCount')
             or stats.get('viewCount') or stats_v2.get('viewCount')
             or stats.get('views') or stats_v2.get('views')
             or post.get('playCount'))
    try:
        views = int(views)
    except Exception:
        views = None

    # likes
    likes = (post.get('likes') or post.get('like_count')
             or stats.get('diggCount') or stats_v2.get('diggCount')
             or stats.get('likeCount') or stats_v2.get('likeCount')
             or post.get('likeCount'))
    try:
        likes = int(likes)
    except Exception:
        likes = None

    # durasi (video) dari post atau sub-dict video
    dur = post.get('duration') or video.get('duration') or post.get('video_duration')
    try:
        dur = int(dur)
    except Exception:
        dur = None

    author_raw = post.get('username') or post.get('author') or post.get('user') or post.get('owner')
    username = clean_name(author_raw)
    if not username:
        # kalau author adalah dict, clean_name sudah ambil nickname/name/username
        username = clean_name(post.get('author') or post.get('owner'))

    return {
        'username': username,
        'description': (post.get('description') or post.get('text') or post.get('desc') or '').split('\n')[0][:150],
        'likes': likes,
        'views': views,
        'duration': dur,
        'post_id': post.get('post_id') or post.get('id'),
    }


def extract_gallery(url):
    """Ekstrak media via gallery-dl: foto/carousel/story Instagram, foto X/Twitter,
    slideshow TikTok, dan beberapa konten Facebook.

    gallery-dl menghasilkan tuple (Message, url, post_dict) — bukan dict —
    jadi kita tangani kedua bentuk itu."""
    if not GDL_AVAILABLE:
        raise RuntimeError(
            'Media ini tidak berupa video biasa (kemungkinan foto). Untuk mengunduh '
            'foto/story, install gallery-dl: pip install gallery-dl'
        )

    # Cooldown anti-bot per-platform sebelum request ke platform ini
    wait_platform_cooldown(url)

    # Konfigurasi gallery-dl agar TikTok & platform lain ambil video + foto
    # (mencegah mode 'audio only' / salah deteksi), + retry beberapa kali
    # karena platform (TikTok) sering memberi 403 lalu lolos di percobaan kedua.
    try:
        gdl_config.set(('extractor', 'tiktok'), 'videos', True)
        gdl_config.set(('extractor', 'tiktok'), 'audio', False)
        gdl_config.set(('extractor', 'instagram'), 'videos', True)
        gdl_config.set(('extractor', 'twitter'), 'videos', True)
        gdl_config.set(('extractor', 'generic'), 'request', 0)
    except Exception:
        pass

    last_err = None
    for attempt in range(2):
        try:
            ex = gdl_extractor.find(url)
            if ex is None:
                raise RuntimeError('Tautan ini belum dikenal sistem.')
            break
        except Exception as e:
            last_err = e
            if attempt == 0:
                time.sleep(1.2)
    else:
        raise RuntimeError(f'gallery-dl tidak mengenali URL ini. {last_err}') if last_err else None

    # konstanta Message gallery-dl: Directory=2, Url=3, Queue=6
    MSG_DIR = 2
    MSG_URL = 3
    MSG_QUEUE = 6

    items = []
    meta = {}
    _visited = set()

    def process(ex, depth=0):
        """Walk hasil gallery-dl; follow Message.Queue (URL yang harus
        diproses extractor lain, mis. short link TikTok → URL panjang)."""
        nonlocal meta
        if depth > 4:
            return
        for item in ex:
            msg = None
            value = None
            post = {}
            if isinstance(item, dict):
                msg, value, post = MSG_URL, item.get('url'), item
            elif isinstance(item, (tuple, list)) and len(item) >= 2:
                msg, value = item[0], item[1]
                post = item[2] if len(item) > 2 and isinstance(item[2], dict) else {}
                # Message.Url bisa membawa daftar URL fallback
                if isinstance(value, (list, tuple)):
                    value = next((u for u in value if isinstance(u, str) and u.startswith('http')), None)

            if msg == MSG_DIR:
                if not meta and post:
                    meta = _extract_meta(post, url)
                continue

            if msg == MSG_QUEUE and isinstance(value, str) and value.startswith('http'):
                # URL eksternal → proses dengan extractor yang cocok
                if value in _visited:
                    continue
                _visited.add(value)
                ex2 = gdl_extractor.find(value)
                if ex2 is not None:
                    process(ex2, depth + 1)
                continue

            if msg == MSG_URL and isinstance(value, str) and value.startswith('http'):
                media_url = value
                if not meta and post:
                    meta = _extract_meta(post, url)
                ext = post.get('extension') or post.get('ext')
                if not ext:
                    head = media_url.split('?')[0]
                    ext = head.rsplit('.', 1)[-1] if '.' in head.rsplit('/', 1)[-1] else 'jpg'
                if ext not in ('jpg', 'jpeg', 'png', 'webp', 'gif', 'mp4', 'webm', 'mov'):
                    ext = 'jpg' if post.get('type') != 'video' else 'mp4'
                # deteksi video tambahan (URL CDN video tanpa ekstensi jelas)
                is_video = (post.get('type') == 'video' or ext in ('mp4', 'webm', 'mov')
                            or '/video/' in media_url.split('?')[0])
                if is_video and ext not in ('mp4', 'webm', 'mov', 'm4v'):
                    ext = 'mp4'
                # thumbnail/cover per item (untuk video, pakai cover dari post)
                it_thumb = None
                if not is_video:
                    it_thumb = media_url
                else:
                    tv = (post.get('video') or {})
                    it_thumb = (post.get('thumbnail') or post.get('thumb')
                                or (tv.get('cover') if isinstance(tv, dict) else None)
                                or (tv.get('thumbnail') if isinstance(tv, dict) else None))
                items.append({
                    'url': media_url,
                    'ext': ext,
                    'width': post.get('width'),
                    'height': post.get('height'),
                    'type': 'video' if is_video else 'image',
                    'thumbnail': it_thumb,
                })

    try:
        process(ex)
    except Exception as e:
        if is_block_signal(e):
            mark_platform_cooldown(url)
        raise RuntimeError(f'gallery-dl gagal membaca konten: {str(e)[:200]}')

    if not items:
        raise RuntimeError(
            'Tidak menemukan media di tautan ini (mungkin konten privat, butuh login, atau sudah kedaluwarsa).'
        )
    return {'meta': meta, 'items': items}


# Ekstensi file media langsung + CDN media sosial yang jelas-jelas file media
DIRECT_MEDIA_EXT = ('jpg', 'jpeg', 'png', 'webp', 'gif', 'avif', 'mp4', 'webm', 'mov', 'm4v')
DIRECT_MEDIA_CDN = (
    'pbs.twimg.com/media', 'video.twimg.com', 'scontent.', '.cdninstagram.com',
    'scontent.cdninstagram.com', 'video.xx.fbcdn.net', 'scontent-', 'lookaside.fbsbx.com',
    'v16-webapp.tiktok.com', 'p16-sign-va.tiktokcdn.com', 'tiktokcdn', 'akamaized.net',
    'fbcdn.net', 'twimg.com/media',
)
# Pola URL yang biasanya bukan foto asli postingan (logo/avatar/dll.)
JUNK_IMAGE_HINTS = ('/logo', 'logo.', 'avatar', 'profile_', 'profile-images',
                    'favicon', 'blank', 'spacer', 'pixel', 'default_avatar', 'icons/')
OG_PROPERTIES = (
    'og:image', 'og:image:secure_url', 'og:image:url', 'og:image0',
    'og:image1', 'og:image2', 'og:image3',
    'og:video', 'og:video:secure_url', 'og:video:url',
    'twitter:image', 'twitter:image:src', 'twitter:video',
)


def looks_like_direct_media(url):
    """Deteksi tautan langsung ke file media (URL berakhiran ekstensi gambar/video
    atau dari CDN media sosial seperti pbs.twimg.com, scontent.cdninstagram.com, dll)."""
    if not url:
        return False
    try:
        path = url.split('?')[0].split('#')[0].rstrip('/')
        last = path.rsplit('/', 1)[-1]
        ext = last.rsplit('.', 1)[-1].lower() if '.' in last else ''
        if ext in DIRECT_MEDIA_EXT:
            return True
        return any(c in url for c in DIRECT_MEDIA_CDN)
    except Exception:
        return False


def extract_direct_media(url):
    """Tautan langsung ke file media → satu item (dijamin jalan, tanpa login)."""
    if not looks_like_direct_media(url):
        raise RuntimeError('Bukan tautan langsung ke file media.')
    path = url.split('?')[0]
    last = path.rsplit('/', 1)[-1]
    ext = last.rsplit('.', 1)[-1].lower() if '.' in last else 'jpg'
    if ext not in DIRECT_MEDIA_EXT:
        ext = 'jpg'
    is_video = ext in ('mp4', 'webm', 'mov', 'm4v')
    return {
        'meta': {},
        'items': [{'url': url, 'ext': ext, 'type': 'video' if is_video else 'image'}],
    }


def probe_direct_media(url):
    """Cek apakah URL merespons sebagai file media (image/* atau video/*).
    Pakai GET stream lalu tutup segera (hanya membaca header), karena
    sebagian CDN (picsum, dll.) menolak HEAD."""
    try:
        r = requests.get(url, headers=BROWSER_HEADERS, timeout=20,
                         stream=True, allow_redirects=True)
        try:
            ctype = (r.headers.get('Content-Type') or '').split(';')[0].strip().lower()
            if ctype.startswith('image/'):
                ext = ctype.split('/')[1]
                if ext not in ('png', 'webp', 'gif'):
                    ext = 'jpg'
                return {'url': r.url or url, 'ext': ext, 'type': 'image'}
            if ctype.startswith('video/'):
                ext = ctype.split('/')[1]
                if ext not in ('mp4', 'webm', 'mov'):
                    ext = 'mp4'
                return {'url': r.url or url, 'ext': ext, 'type': 'video'}
        finally:
            r.close()
    except Exception:
        pass
    raise RuntimeError('URL bukan file media langsung.')


def extract_instagram_embed(url):
    """Instagram TANPA login: halaman embed publik (/p/CODE/embed/captioned/)
    memuat URL CDN foto asli (scontent*.cdninstagram.com). Dipakai saat
    gallery-dl ditolak karena login."""
    m = re.search(r'instagram\.com/(?:p|reel|tv|share)(?:/[^/?#]+)?/([A-Za-z0-9_-]{5,})', url)
    if not m:
        raise RuntimeError('URL Instagram tidak dikenali (butuh tautan /p/, /reel/, atau /tv/).')
    code = m.group(1)

    page = None
    # Coba beberapa variasi URL embed + retry bila kena challenge/rate-limit
    for attempt in range(3):
        for ep in (f'https://www.instagram.com/p/{code}/embed/captioned/',
                   f'https://www.instagram.com/p/{code}/embed/',
                   f'https://www.instagram.com/reel/{code}/embed/'):
            try:
                r = requests.get(ep, headers=BROWSER_HEADERS, timeout=20)
                if r.status_code == 200 and 'scontent' in r.text:
                    page = r.text
                    break
            except Exception:
                continue
        if page:
            break
        time.sleep(1 + attempt)  # jeda singkat sebelum retry

    # Kalau embed gagal total, coba oEmbed Instagram (kadang memberikan thumbnail)
    if not page:
        try:
            r = requests.get('https://api.instagram.com/oembed?url='
                             + requests.utils.quote(url, safe=''),
                             headers=BROWSER_HEADERS, timeout=15)
            if r.status_code == 200:
                d = r.json()
                thumb = d.get('thumbnail_url')
                if thumb:
                    return {'meta': {'post_id': code},
                            'items': [{'url': thumb, 'ext': 'jpg', 'type': 'image'}]}
        except Exception:
            pass

    if not page:
        raise RuntimeError('Instagram sedang sibuk. Coba beberapa menit lagi ya!')

    # Kumpulkan URL CDN dari berbagai atribut (src, srcset, content, data-*),
    # group per file (path dasar) untuk pilih resolusi terbaik.
    groups = {}
    for u in set(re.findall(r'https://[^"\'\s<>]+', page)):
        u = u.replace('&amp;', '&')
        if 'cdninstagram.com' not in u or 'static.cdninstagram.com' in u or 'rsrc.php' in u:
            continue
        if re.search(r'_s\d{2,3}(?:x|_|&|$)', u):   # avatar profil (s100/s150) → buang
            continue
        # potong bila URL punya ekstensi lalu karakter lain (mis. tanda petik tersisa)
        base = re.sub(r'(\.(?:jpg|jpeg|png|webp)).*$', r'\1', u, flags=re.I) if re.search(r'\.(?:jpg|jpeg|png|webp)[?&]', u, re.I) else u.split('?')[0]
        groups.setdefault(base, []).append(u)

    items = []
    for base, us in groups.items():
        def area(uu):
            m2 = re.search(r'p(\d+)x(\d+)', uu)
            return int(m2.group(1)) * int(m2.group(2)) if m2 else 0
        best = max(us, key=area)
        items.append({'url': best, 'ext': 'jpg', 'type': 'image'})

    if not items:
        raise RuntimeError('Instagram tidak menampilkan foto ini (mungkin privat atau butuh login).')
    # username dari halaman embed (opsional, tidak fatal kalau tidak ketemu)
    uname = None
    um = (
        re.search(r'"author_name"\s*:\s*"([^"]+)"', page)
        or re.search(r'"username"\s*:\s*"([^"]+)"', page)
        or re.search(r'<a[^>]+href="https://www\.instagram\.com/([A-Za-z0-9_.]{1,30})/"[^>]*>\s*<span', page)
        or re.search(r'instagram\.com/([A-Za-z0-9_.]{1,30})/?"[^>]*>\s*<img', page)
        or re.search(r'<a[^>]+href="https://www\.instagram\.com/([A-Za-z0-9_.]{1,30})/"', page)
    )
    if um:
        uname = um.group(1)
    return {'meta': {'post_id': code, 'username': uname}, 'items': items}


def extract_facebook_embed(url):
    """Facebook TANPA login: resolve tautan (share → URL panjang) lalu coba
    halaman plugin post (/plugins/post.php?href=...) yang memuat URL gambar
    CDN (fbcdn) untuk konten publik. Fallback: meta og:image halaman."""
    try:
        # 1) resolve tautan share/redirect → URL panjang (posts/photo)
        try:
            rr = requests.get(url, headers=BROWSER_HEADERS, timeout=20, allow_redirects=True)
            if rr.status_code == 200:
                url = rr.url
        except Exception:
            pass

        # 2) coba plugin post
        ep = 'https://www.facebook.com/plugins/post.php?href=' + requests.utils.quote(url, safe='') + '&show_text=true'
        r = requests.get(ep, headers=BROWSER_HEADERS, timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f'Facebook plugin post menolak (HTTP {r.status_code}).')
        html = r.text
        items = []
        seen = set()
        for u in re.findall(r'https://[^"\'\s]+', html):
            u = html_mod.unescape(u.replace('&amp;', '&'))
            if ('fbcdn.net' not in u and 'fbsbx.com' not in u) or 'static.' in u or 'rsrc.php' in u:
                continue
            if u in seen:
                continue
            seen.add(u)
            ext = u.split('?')[0].rsplit('.', 1)[-1].lower()
            if ext not in ('jpg', 'jpeg', 'png', 'webp', 'gif'):
                ext = 'jpg'
            items.append({'url': u, 'ext': ext, 'type': 'image'})
        if not items:
            raise RuntimeError('Facebook tidak menampilkan gambar ini (mungkin privat atau butuh login).')

        # author & view dari halaman plugin (opsional, defensif)
        author = None
        views = None
        am = (re.search(r'"author_name"\s*:\s*"([^"]+)"', html)
              or re.search(r'"username"\s*:\s*"([^"]+)"', html)
              or re.search(r'<title>([^<|]{1,60})', html))
        if am:
            author = am.group(1).strip()
        vm = (re.search(r'"reactionCount"\s*:\s*(\d+)', html)
              or re.search(r'(\d[\.\d]*(?:[KMB]?))\s*(?:reactions|likes|views)', html, re.I))
        if vm:
            try:
                raw = vm.group(1)
                if 'K' in raw.upper():
                    views = int(float(raw.replace('K', '').replace('k', '')) * 1000)
                elif 'M' in raw.upper():
                    views = int(float(raw.replace('M', '').replace('m', '')) * 1000000)
                else:
                    views = int(float(raw))
            except Exception:
                views = None
        return {'meta': {'username': author, 'views': views}, 'items': items[:10]}
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f'Gagal membaca embed Facebook: {str(e)[:120]}')


def extract_instagram_media_direct(url):
    """Instagram: endpoint /p/CODE/media/?size=l → redirect ke gambar ukuran
    besar dari CDN (umumnya berfungsi tanpa login untuk postingan publik)."""
    m = re.search(r'instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]{5,})', url)
    if not m:
        raise RuntimeError('URL Instagram tidak dikenali.')
    code = m.group(1)
    r = requests.get(f'https://www.instagram.com/p/{code}/media/?size=l',
                     headers=BROWSER_HEADERS, timeout=20,
                     allow_redirects=True, stream=True)
    try:
        ctype = (r.headers.get('Content-Type') or '').split(';')[0].strip().lower()
        if r.status_code == 200 and ctype.startswith('image/'):
            return {'meta': {'post_id': code},
                    'items': [{'url': r.url or f'https://www.instagram.com/p/{code}/media/?size=l',
                               'ext': 'jpg', 'type': 'image'}]}
    finally:
        r.close()
    raise RuntimeError('Endpoint media Instagram tidak memberi gambar (privat / wajib login).')


def extract_tiktok_json(url):
    """TikTok: parse __UNIVERSAL_DATA_FOR_REHYDRATION__ dari halaman postingan →
    ambil SEMUA foto slideshow (imagePost) langsung dari CDN tiktokcdn, tanpa login.
    Mendukung tautan pendek (vt.tiktok.com / vm.tiktok.com / tiktok.com/t/)."""
    def long_url(u):
        m = re.search(r'tiktok\.com/@([^/?#]+)/(?:video|photo)/(\d+)', u)
        if m:
            return u, m
        # tautan pendek → ikuti redirect (sekali) sampai dapat URL panjang
        try:
            r = requests.get(u, headers=BROWSER_HEADERS, timeout=20,
                             allow_redirects=True)
            if r.url and r.url != u:
                m2 = re.search(r'tiktok\.com/@([^/?#]+)/(?:video|photo)/(\d+)', r.url)
                if m2:
                    return r.url, m2
        except Exception:
            pass
        return u, None

    resolved, m = long_url(url)
    if not m:
        raise RuntimeError('URL TikTok tidak dikenali (perlu tautan @user/video atau @user/photo).')
    user, post_id = m.group(1), m.group(2)

    # gunakan bentuk URL panjang yang sama dengan yang disalin user (video/photo),
    # supaya TikTok tidak redirect ke halaman lain
    path = resolved.split('/')
    kind = 'video'
    for seg in path:
        if seg in ('video', 'photo'):
            kind = seg
            break
    page_url = f'https://www.tiktok.com/@{user}/{kind}/{post_id}'

    r = requests.get(page_url, headers=BROWSER_HEADERS, timeout=25)
    r.raise_for_status()
    html = r.text
    # data berada di dalam <script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" ...>{...}</script>
    m2 = re.search(r'id=["\']__UNIVERSAL_DATA_FOR_REHYDRATION__["\'][^>]*>\s*(\{.*?\})\s*</script>', html, re.S) \
        or re.search(r'__UNIVERSAL_DATA_FOR_REHYDRATION__\s*=\s*(\{.*?\})\s*;', html, re.S)
    if not m2:
        raise RuntimeError('Data TikTok tidak ditemukan di halaman (mungkin kena blokir/rate-limit).')
    try:
        data = json.loads(m2.group(1))
    except Exception:
        raise RuntimeError('Data TikTok gagal diparse.')

    post = None
    try:
        post = data['__DEFAULT_SCOPE__']['webapp.video-detail']['itemInfo']['itemStruct']
    except Exception:
        pass
    if post is None:
        raise RuntimeError('Struktur data TikTok berubah — coba perbarui aplikasi.')

    images = (post.get('imagePost') or {}).get('images') or []
    items = []
    for img in images:
        urls = ((img.get('imageURL') or {}).get('urlList')) or []
        if urls:
            items.append({'url': urls[0], 'ext': 'jpg', 'type': 'image',
                          'width': img.get('imageWidth'), 'height': img.get('imageHeight')})
    if not items:
        raise RuntimeError('Postingan TikTok ini tidak berisi foto.')
    return {'meta': {'username': user,
                     'description': (post.get('desc') or '')[:150],
                     'post_id': post_id}, 'items': items}


def extract_x_tweet(url):
    """X/Twitter TANPA login: pakai API fxtwitter (api.fxtwitter.com) yang
    menyediakan foto tweet sebagai JSON publik — lalu syndication twimg
    sebagai cadangan."""
    m = re.search(r'(?:twitter\.com|x\.com)/([^/?#]+)/status/(\d+)', url)
    if not m:
        raise RuntimeError('URL X/Twitter tidak dikenali untuk ekstraksi media.')
    user, tweet_id = m.group(1), m.group(2)

    # 1) fxtwitter — andal & tanpa login
    try:
        r = requests.get(f'https://api.fxtwitter.com/{user}/status/{tweet_id}',
                         headers=BROWSER_HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
        tw = data.get('tweet') or {}
        photos = ((tw.get('media') or {}).get('photos')) or []
        videos = ((tw.get('media') or {}).get('videos')) or []
        items = [{'url': ph.get('url'), 'ext': 'jpg', 'type': 'image',
                  'width': ph.get('width'), 'height': ph.get('height')}
                 for ph in photos if ph.get('url')]
        items += [{'url': vd.get('url'), 'ext': 'mp4', 'type': 'video',
                   'width': vd.get('width'), 'height': vd.get('height')}
                  for vd in videos if vd.get('url')]
        if items:
            return {'meta': {
                'username': (tw.get('author') or {}).get('screen_name') or user,
                'description': (tw.get('text') or '')[:150],
                'post_id': tweet_id,
            }, 'items': items}
    except Exception:
        pass

    # 2) Syndication twimg (cadangan)
    embed = requests.get(
        f'https://platform.twitter.com/embed/Tweet.html?id={tweet_id}',
        headers=BROWSER_HEADERS, timeout=20)
    embed.raise_for_status()
    tok = re.search(r'"token"\s*:\s*"([^"]+)"', embed.text)
    if not tok:
        raise RuntimeError('Tidak dapat mengambil token syndication X.')
    api = requests.get(
        f'https://cdn.syndication.twimg.com/tweet-result?id={tweet_id}&lang=en&token={tok.group(1)}',
        headers=BROWSER_HEADERS, timeout=20)
    api.raise_for_status()
    data = api.json()
    items = []
    for ph in (data.get('photos') or []):
        u = ph.get('url')
        if u:
            items.append({'url': u, 'ext': 'jpg', 'type': 'image',
                          'width': ph.get('width'), 'height': ph.get('height')})
    if not items:
        raise RuntimeError('Tweet ini tidak berisi foto.')
    usr = data.get('user') or {}
    return {'meta': {'username': usr.get('screen_name'),
                     'description': (data.get('text') or '')[:150],
                     'post_id': tweet_id}, 'items': items}


def page_username(url):
    """Ambil username/nama author dari halaman (og:title / JSON). Tidak butuh
    og:image — jadi tetap bekerja walau halaman butuh login (mis. Facebook)."""
    try:
        r = requests.get(url, headers=BROWSER_HEADERS, timeout=18, allow_redirects=True)
        r.raise_for_status()
    except Exception:
        return None
    html = r.text
    title = None
    tm = (re.search(r'<meta[^>]+property=["\']og:title["\'][^>]*content=["\']([^"\']+)["\']', html, re.I)
          or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*property=["\']og:title["\']', html, re.I)
          or re.search(r'<title[^>]*>([^<]{1,160})</title>', html, re.I))
    if tm:
        title = html_mod.unescape(tm.group(1)).strip()

    author = None
    um = (re.search(r'"username"\s*:\s*"([^"]+)"', html)
          or re.search(r'"screen_name"\s*:\s*"([^"]+)"', html)
          or re.search(r'"author_name"\s*:\s*"([^"]+)"', html))
    if um:
        author = um.group(1).strip()
    if not author and title:
        am = (re.search(r'oleh\s+([A-Za-z0-9_.\s]{1,40})', title, re.I)
              or re.search(r'\bby\s+([A-Za-z0-9_.\s]{1,40})', title, re.I)
              or re.search(r'pin by\s+([A-Za-z0-9_.\s]{1,40})', title, re.I))
        if am:
            cand = am.group(1).strip().strip('|').strip()
            cand = re.split(r'\s*[|—–-]\s*', cand)[0].strip()
            if cand and len(cand) <= 40 and cand.lower() != 'user':
                author = cand
        elif '|' in title:
            # FB group/page: judul "Nama Grup | Deskripsi" → nama grup = author
            cand = title.split('|')[0].strip()
            if 2 <= len(cand) <= 50 and 'facebook' not in cand.lower() and 'instagram' not in cand.lower():
                author = cand
    return author or None


def extract_og_media(url):
    """Fallback: baca meta og:image / og:video dari halaman. Lebih tahan banting:
    urutan atribut meta bisa bolak-balik, filter gambar sampah (logo/avatar)."""
    r = requests.get(url, headers=BROWSER_HEADERS, timeout=20, allow_redirects=True)
    r.raise_for_status()
    html = r.text
    found, seen = [], set()

    for mt in re.finditer(r'<meta\b[^>]*>', html, re.I):
        tag = mt.group(0)
        prop = re.search(r'(?:property|name)=["\']([^"\']+)["\']', tag, re.I)
        cont = re.search(r'content=["\']([^"\']*)["\']', tag, re.I)
        if not prop or not cont:
            continue
        pname = prop.group(1).strip().lower()
        if pname not in OG_PROPERTIES:
            continue
        val = html_mod.unescape(cont.group(1).strip())
        if not val.startswith('http') or val in seen:
            continue
        low = val.lower()
        if any(h in low for h in JUNK_IMAGE_HINTS):
            continue
        is_video = 'video' in pname
        head = val.split('?')[0]
        last = head.rsplit('/', 1)[-1]
        ext_guess = last.rsplit('.', 1)[-1].lower() if '.' in last else ''
        ext = ext_guess if ext_guess in DIRECT_MEDIA_EXT else ('mp4' if is_video else 'jpg')
        seen.add(val)
        found.append({'url': val, 'ext': ext, 'type': 'video' if is_video else 'image'})
        if len(found) >= 12:
            break

    # <link rel="image_src"> sebagai tambahan
    for lm in re.finditer(r'<link[^>]+rel=["\']image_src["\'][^>]*>', html, re.I):
        tag = lm.group(0)
        href = re.search(r'href=["\']([^"\']+)["\']', tag, re.I)
        if href and href.group(1).startswith('http') and href.group(1) not in seen:
            val = html_mod.unescape(href.group(1))
            if any(h in val.lower() for h in JUNK_IMAGE_HINTS):
                continue
            head = val.split('?')[0]
            last = head.rsplit('/', 1)[-1]
            ext_guess = last.rsplit('.', 1)[-1].lower() if '.' in last else 'jpg'
            ext = ext_guess if ext_guess in DIRECT_MEDIA_EXT else 'jpg'
            seen.add(val)
            found.append({'url': val, 'ext': ext, 'type': 'image'})
            if len(found) >= 12:
                break

    if not found:
        raise RuntimeError('Halaman ini tidak menyediakan gambar/video yang bisa diunduh.')

    # coba ambil judul halaman (og:title / <title>) untuk uploader (Pinterest, FB, dll.)
    title = None
    tm = (re.search(r'<meta[^>]+property=["\']og:title["\'][^>]*content=["\']([^"\']+)["\']', html, re.I)
          or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*property=["\']og:title["\']', html, re.I)
          or re.search(r'<title[^>]*>([^<]{1,160})</title>', html, re.I))
    if tm:
        title = html_mod.unescape(tm.group(1)).strip()

    author = None
    if title:
        am = (re.search(r'oleh\s+([A-Za-z0-9_.\s]{1,40})', title, re.I)
              or re.search(r'\bby\s+([A-Za-z0-9_.\s]{1,40})', title, re.I)
              or re.search(r'pin by\s+([A-Za-z0-9_.\s]{1,40})', title, re.I))
        if am:
            cand = am.group(1).strip().strip('|').strip()
            # potong di pemisah umum (|, -, —) biar hanya nama
            cand = re.split(r'\s*[|—–-]\s*', cand)[0].strip()
            if cand and len(cand) <= 40 and cand.lower() != 'user':
                author = cand

    # beberapa platform menaruh username di JSON halaman (mis. Pinterest)
    if not author:
        um = (re.search(r'"username"\s*:\s*"([^"]+)"', html)
              or re.search(r'"screen_name"\s*:\s*"([^"]+)"', html)
              or re.search(r'"author_name"\s*:\s*"([^"]+)"', html))
        if um:
            author = um.group(1).strip()

    return {'meta': {'username': author, 'title': title}, 'items': found}


def resolve_short_url(url):
    """Resolve URL pendek (b23.tv, on.soundcloud.com, dll) → URL asli
    dengan mengikuti redirect HTTP (HEAD/GET, tanpa download body besar)."""
    try:
        r = requests.get(url, headers=BROWSER_HEADERS, timeout=15,
                         allow_redirects=True, stream=True)
        r.close()
        return r.url or url
    except Exception:
        return url


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# RedNote (Xiaohongshu) — ekstraktor berlapis (anti-bot sangat ketat)
# ---------------------------------------------------------------------------
# Strategi, berlapis dari yang paling akurat:
#   1) Sesion tamu: generate a1/webId → login/activate → web_session (TANPA login).
#   2) API resmi web (edith.xiaohongshu.com/api/sns/web/v1/feed) dengan tanda
#      tangan x-s / x-t / x-rap-param (algoritma hasil reverse-engineering,
#      pustaka xhshow — pure Python). Foto → imageList, video → masterUrl (HLS).
#   3) SSR halaman explore → parse window.__INITIAL_STATE__ (butuh xsec_token
#      dari link share).
#   4) Fallback ekstraktor yt-dlp (XiaoHongShu).
# Catatan jujur: RedNote MEMBLOKIR sebagian IP datacenter di level detail-note
#   (kode "300031 / current note cannot be viewed"). Dari IP yang tidak
#   diblokir (rumah / Termux / sebagian Railway) lapisan 1-2 terbukti jalan;
#   dari IP diblokir, semua lapisan akan gagal — itu keputusan server XHS,
#   bukan bug kode. CDN media-nya sendiri (xhscdn.com) BISA diakses langsung.

XHS_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
          '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')
XHS_BASE_HEADERS = {
    'User-Agent': XHS_UA,
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Origin': 'https://www.xiaohongshu.com',
    'Content-Type': 'application/json;charset=UTF-8',
    'sec-ch-ua': '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-site',
}
XHS_SESSION = {}
XHS_SESSION_LOCK = threading.Lock()


def _xhs_sign(method, uri, cookies, payload=None, params=None):
    """Tanda tangan x-s/x-t/x-rap-param via xhshow (pure Python)."""
    try:
        from xhshow import Xhshow
        cl = Xhshow()
        if method == 'POST':
            return cl.sign_headers_post(uri=uri, cookies=cookies,
                                        payload=payload or {}, x_rap=True)
        return cl.sign_headers_get(uri=uri, cookies=cookies, params=params or {})
    except Exception:
        return None


def _xhs_session():
    """Bootstrap sesi tamu (a1, webId, web_session) — valid ±6 jam, dibuat ulang otomatis."""
    global XHS_SESSION
    with XHS_SESSION_LOCK:
        if XHS_SESSION and XHS_SESSION.get('web_session') \
                and time.time() - XHS_SESSION.get('_ts', 0) < 6 * 3600:
            return XHS_SESSION
        try:
            from xhshow import Xhshow
            cl = Xhshow()
            a1 = cl.generate_a1()
            webId = cl.generate_web_id(a1)
            cookies = {'a1': a1, 'webId': webId}
            payload = {'client_public_key_base64': base64.b64encode(secrets.token_bytes(32)).decode()}
            h = _xhs_sign('POST', 'https://edith.xiaohongshu.com/api/sns/web/v1/login/activate',
                          cookies, payload)
            if not h:
                return None
            headers = dict(XHS_BASE_HEADERS)
            headers.update(h)
            headers['Referer'] = 'https://www.xiaohongshu.com/'
            try:
                from curl_cffi import requests as creq
                r = creq.post('https://edith.xiaohongshu.com/api/sns/web/v1/login/activate',
                              json=payload, headers=headers, cookies=cookies,
                              impersonate='chrome124', timeout=25)
            except Exception:
                r = requests.post('https://edith.xiaohongshu.com/api/sns/web/v1/login/activate',
                                  json=payload, headers=headers, cookies=cookies, timeout=25)
            ws = None
            for sc in r.headers.get_list('set-cookie'):
                if sc.strip().startswith('web_session='):
                    ws = sc.split(';')[0].split('=', 1)[1]
            if ws:
                cookies['web_session'] = ws
                cookies['_ts'] = time.time()
                XHS_SESSION = cookies
                return cookies
        except Exception:
            pass
        return None


def _xhs_post_json(uri, payload, cookies, referer):
    headers = dict(XHS_BASE_HEADERS)
    h = _xhs_sign('POST', uri, cookies, payload)
    if not h:
        return None
    headers.update(h)
    headers['Referer'] = referer
    try:
        from curl_cffi import requests as creq
        return creq.post(uri, json=payload, headers=headers, cookies=cookies,
                         impersonate='chrome124', timeout=25)
    except Exception:
        try:
            return requests.post(uri, json=payload, headers=headers,
                                 cookies=cookies, timeout=25)
        except Exception:
            return None


def _xhs_fetch_note_card(note_id, xsec_token=None):
    """API feed (edith) → note_card berisi imageList / video masterUrl."""
    cookies = _xhs_session()
    if not cookies:
        return None
    payload = {'source_note_id': note_id}
    if xsec_token:
        payload['xsec_token'] = xsec_token
        payload['xsec_source'] = 'pc_feed'
    r = _xhs_post_json('https://edith.xiaohongshu.com/api/sns/web/v1/feed', payload,
                       cookies, 'https://www.xiaohongshu.com/explore/' + note_id)
    if r is None:
        return None
    try:
        d = r.json()
    except Exception:
        return None
    items = ((d.get('data') or {}).get('items') or [])
    if items and items[0].get('note_card'):
        return items[0]['note_card']
    return None


def _xhs_brace(text, start):
    """Ambil objek JS {...} mulai dari index start (brace-matching, aman string)."""
    depth = 0
    in_str = False
    esc = False
    for k in range(start, len(text)):
        c = text[k]
        if in_str:
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return text[start:k + 1]
    return None


def _xhs_parse_ssr(html):
    """Parse window.__INITIAL_STATE__ (SSR explore) → note (dict) atau None."""
    m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*)', html, re.S)
    if not m:
        return None
    blob = m.group(1)
    i = blob.find('"noteDetailMap"')
    if i < 0:
        return None
    j = blob.find('{', i)
    obj = _xhs_brace(blob, j)
    if not obj:
        return None
    try:
        d = json.loads(obj)
    except Exception:
        try:
            import yaml
            d = yaml.safe_load(obj)
        except Exception:
            return None
    if not isinstance(d, dict):
        return None
    for _k, v in d.items():
        note = (v or {}).get('note') or {}
        if note and isinstance(note, dict):
            return note
    return None


def _xhs_fetch_ssr(url):
    """Fetch halaman explore dengan cookie → note (dict) via __INITIAL_STATE__."""
    cookies = _xhs_session()
    if not cookies:
        return None
    try:
        from curl_cffi import requests as creq
        r = creq.get(url, headers={
            'User-Agent': XHS_UA,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Referer': 'https://www.xiaohongshu.com/',
        }, cookies=cookies, impersonate='chrome124', timeout=25, allow_redirects=True)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    return _xhs_parse_ssr(r.text)


def _xhs_clean_url(u):
    """Bersihkan URL CDN: escape unicode + buang suffix processing (!nc_n_webp_…)."""
    if not u:
        return None
    u = u.replace('\\u002F', '/')
    u = re.sub(r'!.*$', '', u)
    if u.startswith(('http://', 'https://')):
        return u
    return None


def _xhs_note_to_media(note, url, note_id):
    """Ubah note (SSR/API) → respons media RedNote (foto atau video)."""
    note = note or {}
    title = (note.get('displayTitle') or note.get('title')
             or note.get('desc') or 'Post RedNote')
    user = note.get('user') or {}
    uploader = user.get('nickName') or user.get('nickname') or 'Unknown'
    # foto
    images = []
    for im in (note.get('imageList') or []):
        u = _xhs_clean_url(im.get('urlDefault') or im.get('urlPre'))
        if u:
            images.append({'url': u, 'ext': 'jpg', 'type': 'image'})
    # video (HLS m3u8 — CDN xhscdn, tanpa watermark)
    video = note.get('video') or {}
    streams = ((video.get('media') or {}).get('stream') or {})
    master = None
    for key in ('h264', 'h265', 'av1'):
        for st in (streams.get(key) or []):
            if st.get('masterUrl') or st.get('master_url'):
                master = _xhs_clean_url(st.get('masterUrl') or st.get('master_url'))
                break
        if master:
            break
    # thumbnail
    cover = note.get('cover') or {}
    thumb = _xhs_clean_url(cover.get('urlDefault') or cover.get('urlPre'))
    dur = None
    try:
        dur = int((video.get('capa') or {}).get('duration') or 0) or None
    except Exception:
        pass
    platform = {'key': 'rednote', 'name': 'RedNote', 'icon': '/static/icons/rednote.png'}
    if master:
        return {
            'ok': True, 'id': note_id, 'title': title, 'uploader': uploader,
            'duration': dur, 'duration_text': format_duration(dur),
            'thumbnail': thumb, 'webpage_url': url,
            'formats': [{'format_id': 'source', 'label': 'Video asli (HD)', 'ext': 'mp4',
                         'height': 1080, 'vcodec': 'h264', 'acodec': 'aac',
                         'filesize_mb': None}],
            'has_video': True, 'has_audio': True, 'has_image': False,
            'images': [], 'image_count': 0, 'video_count': 1, 'max_height': 1080,
            'direct_urls': [master],
            'platform': platform,
            'note': 'Video RedNote diambil dari CDN resmi, tanpa watermark.',
        }
    if images:
        return {
            'ok': True, 'id': note_id, 'title': title, 'uploader': uploader,
            'duration': dur, 'duration_text': format_duration(dur),
            'thumbnail': thumb or images[0]['url'], 'webpage_url': url,
            'formats': [], 'has_video': False, 'has_audio': False,
            'has_image': True, 'images': images, 'image_count': len(images),
            'video_count': 0, 'max_height': 0,
            'platform': platform,
            'note': 'Foto RedNote diambil dari CDN resmi, tanpa watermark.',
        }
    return None


def parse_rednote_url(url):
    """Ambil note_id + xsec_token dari berbagai format tautan RedNote."""
    url = normalize_url(url)
    q = urllib.parse.urlparse(url)
    # link share pendek → ikuti redirect supaya dapat note_id & token asli
    if 'xhslink.com' in url:
        try:
            r = requests.get(url, headers={'User-Agent': XHS_UA}, timeout=20,
                             allow_redirects=True)
            if r.status_code == 200 and r.url:
                url = r.url
                q = urllib.parse.urlparse(url)
        except Exception:
            pass
    m = re.search(r'/(?:explore|discovery/item)/([0-9a-f]{24})', url)
    if not m:
        m = re.search(r'/user/profile/[^/]+/([0-9a-f]{24})', url)
    note_id = m.group(1) if m else None
    xsec = None
    qs = urllib.parse.parse_qs(q.query)
    if 'xsec_token' in qs:
        xsec = qs['xsec_token'][0]
    return note_id, xsec


def extract_rednote(url):
    """Ekstraktor berlapis: SSR (token) → API signed → yt-dlp. Foto & video."""
    note_id, xsec = parse_rednote_url(url)
    if not note_id:
        raise RuntimeError(
            'Tautan RedNote tidak dikenali. Tempel link post (explore/discovery/item), '
            'link share (xhslink.com), atau salin teks share-nya.')

    # Lapisan 1 — SSR halaman explore (butuh xsec_token dari link share)
    if xsec:
        page = ('https://www.xiaohongshu.com/explore/%s?xsec_token=%s&xsec_source=pc_feed'
                % (note_id, urllib.parse.quote(xsec)))
        note = _xhs_fetch_ssr(page)
        if note:
            media = _xhs_note_to_media(note, url, note_id)
            if media:
                return media

    # Lapisan 2 — API resmi web (tanda tangan x-s), foto & video
    note = _xhs_fetch_note_card(note_id, xsec)
    if note:
        media = _xhs_note_to_media(note, url, note_id)
        if media:
            return media

    # Lapisan 3 — yt-dlp (XiaoHongShu) sebagai cadangan (sekali, cepat)
    try:
        opts = base_ydl_opts()
        opts['socket_timeout'] = 10
        opts['retries'] = 1
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False, ie_key='XiaoHongShu')
        if info and info.get('formats'):
            parsed = parse_info(info)
            if parsed and (parsed.get('has_video') or parsed.get('has_audio')):
                return {'ok': True, **parsed}
    except Exception:
        pass

    raise RuntimeError(
        'RedNote menolak mengambil post ini dari IP server (blokir anti-bot). '
        'Coba lagi beberapa menit, atau tempel link share post-nya '
        '(dengan xsec_token) — biasanya lebih berhasil.')


def is_rednote(url):
    return 'xiaohongshu.com' in url or 'xhslink.com' in url


# ---------------------------------------------------------------------------
# Platform baru — GitHub, MediaFire, Threads, Reddit, Douyin (custom)
# ---------------------------------------------------------------------------

def _github_respond(dl_url, name, url, note='File GitHub diunduh langsung dari CDN resmi.'):
    return {'ok': True, 'id': name, 'title': name, 'uploader': 'GitHub',
            'thumbnail': 'https://github.com/fluidicon.png', 'webpage_url': url,
            'formats': [{'format_id': 'direct', 'label': 'File asli',
                         'ext': name.rsplit('.', 1)[-1].split('?')[0].lower() if '.' in name else 'bin',
                         'vcodec': None, 'acodec': None, 'filesize_mb': None}],
            'has_video': False, 'has_audio': False, 'has_image': False,
            'images': [], 'image_count': 0, 'max_height': 0,
            'direct_urls': [dl_url],
            'platform': {'key': 'github', 'name': 'GitHub', 'icon': '/static/icons/github.png'},
            'note': note}


def extract_github(url):
    """GitHub: release asset langsung (paling andal) → raw/blob via CDN →
    fallback API contents (base64). Terbukti bekerja dari IP server mana pun."""
    # release asset langsung — selalu bekerja (release-assets.githubusercontent.com)
    if '/releases/download/' in url:
        return _github_respond(url, url.rstrip('/').split('/')[-1], url)
    # raw.githubusercontent langsung
    if 'raw.githubusercontent.com' in url:
        return _github_respond(url, url.rstrip('/').split('/')[-1], url)
    # halaman release → ambil asset pertama
    if '/releases' in url:
        try:
            r = requests.get(url, headers=BROWSER_HEADERS, timeout=20)
            m = re.search(r'href="([^"]+/releases/download/[^"]+)"', r.text)
            if m:
                return extract_github('https://github.com' + m.group(1))
        except Exception:
            pass
        raise RuntimeError('Halaman release GitHub ini tidak punya file yang bisa diunduh.')
    # blob / raw → raw.githubusercontent (CDN). Cek cepat: kalau dibalas HTML
    # (rate-limit/anti-bot), fallback ke API contents (base64).
    raw = re.sub(r'github\.com/([^/]+/[^/]+)/blob/', r'raw.githubusercontent.com/\1/', url)
    raw = re.sub(r'github\.com/([^/]+/[^/]+)/raw/', r'raw.githubusercontent.com/\1/', raw)
    if 'raw.githubusercontent.com' in raw:
        name = url.rstrip('/').split('/')[-1].split('?')[0]
        try:
            r = requests.get(raw, headers={'User-Agent': 'curl/8.5.0',
                                           'Accept-Encoding': 'identity'}, timeout=20)
            head = r.content[:200]
            if r.status_code == 200 and head[:1] not in (b'<', b'{'):
                return _github_respond(raw, name, url)
        except Exception:
            pass
        # fallback: api.github.com contents (base64 untuk file < 1MB)
        m = re.match(r'https?://(?:raw\.)?github(?:usercontent)?\.com/([^/]+/[^/]+)/(?:blob/|raw/)?(.*)', url)
        if m:
            user_repo = m.group(1)
            path = m.group(2)
            api_url = 'https://api.github.com/repos/%s/contents/%s' % (user_repo, path)
            try:
                r = requests.get(api_url, headers={
                    'User-Agent': 'KingsDownloader/1.0',
                    'Accept': 'application/vnd.github+json'}, timeout=20)
                if r.status_code == 200:
                    d = r.json()
                    b64 = d.get('content')
                    if b64:
                        import base64 as _b64
                        content = _b64.b64decode(b64)
                        ext = name.rsplit('.', 1)[-1] if '.' in name else 'txt'
                        # simpan sebagai data URI? tidak — pakai endpoint khusus
                        # simpan ke cache file agar bisa diunduh nanti
                        return {'ok': True, 'id': name, 'title': name, 'uploader': 'GitHub',
                                'thumbnail': 'https://github.com/fluidicon.png', 'webpage_url': url,
                                'formats': [{'format_id': 'direct', 'label': 'File asli', 'ext': ext,
                                             'vcodec': None, 'acodec': None, 'filesize_mb': None}],
                                'has_video': False, 'has_audio': False, 'has_image': False,
                                'images': [], 'image_count': 0, 'max_height': 0,
                                'direct_urls': [raw],   # tetap raw; kalau HTML, fallback bawah
                                'inline_base64': content,
                                'inline_ext': ext,
                                'platform': {'key': 'github', 'name': 'GitHub', 'icon': '/static/icons/github.png'},
                                'note': 'File GitHub diunduh dari CDN resmi (via API).'}
            except Exception:
                pass
        raise RuntimeError('GitHub sedang membatasi unduhan file dari IP ini (rate-limit). '
                           'Coba link release (github.com/…/releases/download/…) atau tunggu beberapa menit.')
    raise RuntimeError('Tautan GitHub belum dikenali. Tempel link file (blob/raw) atau release.')


def extract_mediafire(url):
    """MediaFire: parse halaman → direct link download*.mediafire.com.
    Terbukti bekerja dari IP server mana pun."""
    r = requests.get(url, headers=BROWSER_HEADERS, timeout=25)
    if r.status_code != 200:
        raise RuntimeError('MediaFire tidak merespons (HTTP %s).' % r.status_code)
    t = r.text
    m = re.search(r'https://download[0-9]+\.mediafire\.com[^"\']+', t)
    if not m:
        raise RuntimeError('File MediaFire ini tidak bisa diunduh (mungkin kena limit/captcha). Coba lagi nanti.')
    dl = m.group(0)
    title = re.search(r'<title>(.*?)</title>', t, re.S)
    name = (title.group(1).strip() if title else 'File MediaFire')
    return {'ok': True, 'id': url.rstrip('/').split('/')[-2] if url.rstrip('/').split('/')[-2] != 'file' else url.rstrip('/').split('/')[-1],
            'title': name, 'uploader': 'MediaFire',
            'thumbnail': 'https://www.mediafire.com/favicon.ico', 'webpage_url': url,
            'formats': [{'format_id': 'direct', 'label': 'File asli',
                         'ext': name.rsplit('.', 1)[-1].split('?')[0].lower() if '.' in name else 'bin',
                         'vcodec': None, 'acodec': None, 'filesize_mb': None}],
            'has_video': False, 'has_audio': False, 'has_image': False,
            'images': [], 'image_count': 0, 'max_height': 0,
            'direct_urls': [dl],
            'platform': {'key': 'mediafire', 'name': 'MediaFire', 'icon': '/static/icons/mediafire.png'},
            'note': 'File MediaFire diunduh langsung dari CDN resmi.'}


def extract_threads(url):
    """Threads: parse halaman post → video (video_versions) atau foto
    (image_versions2 / og:image). Halaman Threads bisa diakses dari IP server
    (200) — video ada di JSON embedded (CDN scontent.cdninstagram.com)."""
    t = None
    for attempt in range(3):
        try:
            from curl_cffi import requests as creq
            r = creq.get(url, impersonate='chrome124', headers=BROWSER_HEADERS, timeout=25)
        except Exception:
            try:
                r = requests.get(url, headers=BROWSER_HEADERS, timeout=25)
            except Exception:
                r = None
        if r is not None and r.status_code == 200:
            t = r.text
            # kalau halaman tidak menyertakan data post (login wall), coba lagi
            if 'video_versions' in t or 'image_versions2' in t or 'og:video' in t:
                break
        time.sleep(0.8)
    if not t:
        raise RuntimeError('Threads tidak merespons (HTTP %s).' % (r.status_code if r else '?'))
    title = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', t)
    name = (html_mod.unescape(title.group(1)).strip() if title else 'Post Threads')
    if 'Log in' in name or 'log in' in name.lower():
        name = 'Post Threads'
    # video: og:video
    vid = re.search(r'<meta[^>]+property="og:video"[^>]+content="([^"]+)"', t)
    # video: video_versions dari JSON embedded
    vv = re.search(r'"video_versions":\s*(\[[^\]]*\])', t)
    video_url = None
    if vid:
        video_url = vid.group(1).replace('\\u002F', '/')
    elif vv:
        try:
            arr = json.loads(vv.group(1))
            if arr and arr[0].get('url'):
                video_url = arr[0]['url'].replace('\\u002F', '/')
        except Exception:
            pass
    # foto: image_versions2 (JSON) lalu og:image
    img_url = None
    iv = re.search(r'"image_versions2":\s*\{"candidates":\s*(\[[^\]]*\])', t)
    if iv:
        try:
            cands = json.loads(iv.group(1))
            for c in cands:
                u = (c.get('url') or '').replace('\\u002F', '/')
                if u.startswith('http') and 'rsrc.php' not in u:
                    img_url = u
                    break
        except Exception:
            pass
    if not img_url:
        im = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', t)
        img_url = im.group(1) if im else None
    platform = {'key': 'threads', 'name': 'Threads', 'icon': '/static/icons/threads.png'}
    if video_url and video_url.startswith('http'):
        return {'ok': True, 'id': url.rstrip('/').split('/')[-1], 'title': name,
                'uploader': 'Threads', 'thumbnail': img_url if img_url and 'rsrc.php' not in img_url else None,
                'webpage_url': url,
                'formats': [{'format_id': 'direct', 'label': 'Video asli (MP4)', 'ext': 'mp4',
                             'height': 1080, 'vcodec': 'h264', 'acodec': 'aac', 'filesize_mb': None}],
                'has_video': True, 'has_audio': True, 'has_image': False,
                'images': [], 'image_count': 0, 'video_count': 1, 'max_height': 1080,
                'direct_urls': [video_url],
                'platform': platform,
                'note': 'Video Threads diunduh dari CDN resmi (Instagram), tanpa watermark.'}
    if img_url and 'static.cdninstagram.com' not in img_url and 'rsrc.php' not in img_url:
        return {'ok': True, 'id': url.rstrip('/').split('/')[-1], 'title': name,
                'uploader': 'Threads', 'thumbnail': img_url, 'webpage_url': url,
                'formats': [], 'has_video': False, 'has_audio': False, 'has_image': True,
                'images': [{'url': img_url, 'ext': 'jpg', 'type': 'image'}],
                'image_count': 1, 'video_count': 0, 'max_height': 0,
                'platform': platform,
                'note': 'Foto Threads diunduh dari CDN resmi.'}
    raise RuntimeError('Post Threads ini tidak berisi media yang bisa diunduh (privat/terhapus). '
                       'Coba post lain, atau tunggu sebentar lalu coba lagi.')


def extract_reddit(url):
    """Reddit: oEmbed (foto/thumbnail) dulu, lalu .json untuk video (v.redd.it).
    Catatan jujur: Reddit MEMBLOKIR sebagian IP datacenter (403). Dari IP yang
    tidak diblokir (rumah/Termux/sebagian Railway) jalur ini terbukti bekerja."""
    try:
        from curl_cffi import requests as creq
        # coba .json (video reddit_video → fallback_url mp4)
        r = creq.get(url.rstrip('/') + '.json?raw_json=1', impersonate='chrome124',
                     headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36'},
                     timeout=20)
        if r.status_code == 200:
            d = r.json()
            post = d[0]['data']['children'][0]['data']
            title = post.get('title') or 'Post Reddit'
            rv = ((post.get('media') or {}).get('reddit_video') or {})
            fb = (rv.get('fallback_url') or '')
            if fb:
                fb = fb.split('?')[0]
                platform = {'key': 'reddit', 'name': 'Reddit', 'icon': '/static/icons/reddit.png'}
                return {'ok': True, 'id': post.get('id') or url.rstrip('/').split('/')[-2],
                        'title': title, 'uploader': post.get('author') or 'Reddit',
                        'thumbnail': post.get('thumbnail') if post.get('thumbnail', '').startswith('http') else None,
                        'webpage_url': url,
                        'formats': [{'format_id': 'direct', 'label': 'Video asli (MP4)', 'ext': 'mp4',
                                     'height': rv.get('height') or 720, 'vcodec': 'h264', 'acodec': 'aac',
                                     'filesize_mb': None}],
                        'has_video': True, 'has_audio': True, 'has_image': False,
                        'images': [], 'image_count': 0, 'video_count': 1,
                        'max_height': rv.get('height') or 720,
                        'direct_urls': [fb],
                        'platform': platform,
                        'note': 'Video Reddit diunduh dari CDN resmi (v.redd.it).'}
            # galeri foto
            gallery = post.get('gallery_data') or {}
            mm = post.get('media_metadata') or {}
            items = []
            for it in (gallery.get('items') or []):
                mid = it.get('media_id')
                meta = mm.get(mid) or {}
                for s in (meta.get('s') or []):
                    if s.get('u'):
                        items.append({'url': s['u'].replace('&amp;', '&'), 'ext': 'jpg', 'type': 'image'})
                        break
            if items:
                platform = {'key': 'reddit', 'name': 'Reddit', 'icon': '/static/icons/reddit.png'}
                return {'ok': True, 'id': post.get('id'), 'title': title,
                        'uploader': post.get('author') or 'Reddit',
                        'thumbnail': items[0]['url'], 'webpage_url': url,
                        'formats': [], 'has_video': False, 'has_audio': False,
                        'has_image': True, 'images': items, 'image_count': len(items),
                        'video_count': 0, 'max_height': 0,
                        'platform': platform,
                        'note': 'Galeri Reddit diunduh dari CDN resmi.'}
            # post gambar tunggal
            if post.get('url', '').startswith('https://i.redd.it') or post.get('url', '').startswith('https://preview.redd.it'):
                platform = {'key': 'reddit', 'name': 'Reddit', 'icon': '/static/icons/reddit.png'}
                return {'ok': True, 'id': post.get('id'), 'title': title,
                        'uploader': post.get('author') or 'Reddit', 'thumbnail': post['url'],
                        'webpage_url': url, 'formats': [], 'has_video': False,
                        'has_audio': False, 'has_image': True,
                        'images': [{'url': post['url'], 'ext': 'jpg', 'type': 'image'}],
                        'image_count': 1, 'video_count': 0, 'max_height': 0,
                        'platform': platform,
                        'note': 'Gambar Reddit diunduh dari CDN resmi.'}
            raise RuntimeError('Post Reddit ini tidak berisi media yang bisa diunduh.')
    except Exception:
        pass
    # fallback oEmbed (thumbnail saja) — biasanya selalu 200
    try:
        oe = requests.get('https://www.reddit.com/oembed?url=' + urllib.parse.quote(url, safe=''),
                          headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        if oe.status_code == 200:
            j = oe.json()
            thumb = j.get('thumbnail_url')
            title = j.get('title') or 'Post Reddit'
            if thumb:
                platform = {'key': 'reddit', 'name': 'Reddit', 'icon': '/static/icons/reddit.png'}
                return {'ok': True, 'id': url.rstrip('/').split('/')[-2],
                        'title': title, 'uploader': j.get('author_name') or 'Reddit',
                        'thumbnail': thumb, 'webpage_url': url,
                        'formats': [], 'has_video': False, 'has_audio': False,
                        'has_image': True,
                        'images': [{'url': thumb, 'ext': 'jpg', 'type': 'image'}],
                        'image_count': 1, 'video_count': 0, 'max_height': 0,
                        'platform': {'key': 'reddit', 'name': 'Reddit', 'icon': '/static/icons/reddit.png'},
                        'note': 'Reddit memblokir detail dari IP ini — hanya thumbnail yang bisa diambil.'}
    except Exception:
        pass
    raise RuntimeError('Reddit menolak mengambil post ini dari IP server (blokir anti-bot). '
                       'Coba dari jaringan lain, atau tempel tautan file media langsungnya.')


def extract_douyin(url):
    """Douyin: yt-dlp + sesi cookie segar (kunjungi homepage dulu untuk cookie).
    Catatan jujur: Douyin memblokir sebagian IP datacenter — dari IP yang tidak
    diblokir jalur ini bekerja."""
    try:
        from curl_cffi import requests as creq
        s = creq.Session(impersonate='chrome124')
        s.get('https://www.douyin.com/', headers={'User-Agent': USER_AGENT}, timeout=15)
        cookies = {k: v for k, v in s.cookies.items()}
    except Exception:
        cookies = {}
    opts = base_ydl_opts()
    opts['socket_timeout'] = 15
    if cookies:
        opts['http_cookies'] = '; '.join('%s=%s' % (k, v) for k, v in cookies.items())
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if info and info.get('formats'):
            parsed = parse_info(info)
            if parsed and (parsed.get('has_video') or parsed.get('has_audio')):
                return {'ok': True, **parsed}
    except Exception:
        pass
    # coba tanpa cookies (extractor bisa jalan dari IP bagus)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if info and info.get('formats'):
            parsed = parse_info(info)
            if parsed and (parsed.get('has_video') or parsed.get('has_audio')):
                return {'ok': True, **parsed}
    except Exception:
        pass
    raise RuntimeError('Douyin menolak mengambil video ini dari IP server (butuh cookies segar/anti-bot). '
                       'Coba dari jaringan lain, atau tempel tautan video Douyin yang lain.')


def is_github(url): return 'github.com' in url or 'raw.githubusercontent.com' in url
def is_mediafire(url): return 'mediafire.com' in url
def is_threads(url): return 'threads.net' in url or 'threads.com' in url
def is_reddit(url): return 'reddit.com' in url or 'redd.it' in url
def is_douyin(url): return 'douyin.com' in url or 'v.douyin.com' in url


# ---------------------------------------------------------------------------
# Videy — ekstraktor tambahan (CDN langsung)
# ---------------------------------------------------------------------------
# Videy: yt-dlp belum support; CDN-nya langsung bisa diakses
# (cdn.videy.co/{id}.mp4) — tinggal ambil id dari URL.


def extract_videy(url):
    """Videy: ambil id dari URL → CDN cdn.videy.co/{id}.mp4."""
    m = re.search(r'id=([A-Za-z0-9_-]+)', url)
    if not m:
        m = re.search(r'cdn\.videy\.co/([A-Za-z0-9_-]+)\.mp4', url)
    if not m:
        raise RuntimeError('Tautan Videy tidak dikenali. Gunakan format '
                           'videy.co/v/?id=XXXX.')
    vid = m.group(1)
    media_url = 'https://cdn.videy.co/%s.mp4' % vid
    return {
        'ok': True,
        'id': vid,
        'title': 'Video Videy (%s)' % vid,
        'uploader': 'Unknown',
        'duration': None,
        'duration_text': '',
        'thumbnail': None,
        'webpage_url': url,
        'formats': [{
            'format_id': 'videy', 'label': 'Video Videy (MP4)',
            'ext': 'mp4', 'vcodec': 'avc1', 'acodec': 'mp4a',
            'filesize_mb': None,
        }],
        'has_video': True,
        'has_audio': True,
        'has_image': False,
        'images': [],
        'max_height': 0,
        'platform': {'key': 'videy', 'name': 'Videy',
                     'icon': '/static/icons/videy.png'},
        'direct_urls': [media_url],
        'note': 'Video Videy diambil dari CDN resmi.',
    }


def build_gallery_response(g, url, platform=None):
    """Buat respons JSON untuk konten berisi foto/album/story (Instagram, X, Facebook)."""
    meta = g['meta']
    items = g['items']
    images = [it for it in items if it['type'] == 'image']
    videos = [it for it in items if it['type'] == 'video']
    if platform is None:
        platform = detect_platform(url) or {'key': 'media', 'name': 'Media', 'icon': '/static/img/logo_64.png'}
    name = platform.get('name', 'Media')

    # thumbnail: prefer gambar pertama (bukan video) supaya proxy thumbnail jalan
    thumb = None
    for it in items:
        if it['type'] == 'image':
            thumb = it['url']
            break
    if thumb is None:
        thumb = items[0].get('thumbnail') or (items[0]['url'] if items else None)

    dur = meta.get('duration')
    title_meta = meta.get('description') or meta.get('title') or f'Konten {name}'
    uploader = clean_name(meta.get('username'))
    if not uploader:
        uploader = clean_name(meta.get('author'))
    return {
        'ok': True,
        'id': meta.get('post_id') or (url.rstrip('/').split('/')[-1] or 'media'),
        'title': title_meta,
        'uploader': uploader or 'Unknown',
        'duration': dur,
        'duration_text': format_duration(dur),
        'view_count': meta.get('views'),
        'like_count': meta.get('likes'),
        'thumbnail': thumb,
        'webpage_url': url,
        'formats': [],
        'has_video': bool(videos),
        'has_audio': False,
        'has_image': bool(images),
        'images': items,
        'image_count': len(items),
        'video_count': len(videos),
        'max_height': max((it.get('height') or 0) for it in items) or 0,
        'platform': platform,
        'note': f'Konten {name} berisi foto/video — unduh per media atau semuanya sebagai ZIP (langsung dari CDN, tanpa watermark).',
    }


# ---------------------------------------------------------------------------
# Manajemen job background
# ---------------------------------------------------------------------------
def new_job():
    job_id = secrets.token_hex(6)
    job = {
        'id': job_id,
        'status': 'queued',
        'progress': 0.0,
        'message': 'Antrean…',
        'error': None,
        'filename': None,
        'filepath': None,
        'filesize_mb': None,
        'created': time.time(),
    }
    with JOBS_LOCK:
        JOBS[job_id] = job
    purge_old_jobs()
    return job


def purge_old_jobs():
    """Hapus job & file yang sudah lewat TTL."""
    now = time.time()
    with JOBS_LOCK:
        stale = [jid for jid, j in JOBS.items() if now - j['created'] > JOB_TTL]
        for jid in stale:
            JOBS.pop(jid, None)
            for fp in glob.glob(os.path.join(DOWNLOADS_DIR, jid + '.*')):
                try:
                    os.remove(fp)
                except OSError:
                    pass


def find_job_file(job_id):
    for f in os.listdir(DOWNLOADS_DIR):
        if f.startswith(job_id + '.'):
            if f.endswith(('.part', '.ytdl', '.temp', '.temp.mp4', '.temp.m4a', '.temp.webm')):
                continue
            return os.path.join(DOWNLOADS_DIR, f)
    return None


def cleanup_job_files(job_id):
    for fp in glob.glob(os.path.join(DOWNLOADS_DIR, job_id + '.*')):
        try:
            os.remove(fp)
        except OSError:
            pass


def human_speed(bps):
    if not bps:
        return ''
    bps = float(bps)
    if bps >= 1024 ** 3:
        return f"{bps / 1024**3:.1f} GB/s"
    if bps >= 1024 ** 2:
        return f"{bps / 1024**2:.1f} MB/s"
    return f"{bps / 1024:.0f} KB/s"


def fmt_elapsed(seconds):
    """Format durasi menjadi MM:SS (atau H:MM:SS untuk > 1 jam)."""
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_eta(seconds):
    """Format sisa waktu (countdown) menjadi MM:SS."""
    seconds = max(0, int(seconds or 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def elapsed_of(job):
    start = job.get('_start')
    return fmt_elapsed(time.time() - start) if start else '0:00'


def probe_duration(filepath):
    """Ukur durasi asli file media (detik) via ffprobe. Kembalikan None kalau gagal."""
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        return None
    try:
        import subprocess
        out = subprocess.run(
            [ffprobe, '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', filepath],
            capture_output=True, text=True, timeout=30)
        if out.returncode == 0 and out.stdout.strip():
            return float(out.stdout.strip())
    except Exception:
        pass
    return None


def make_progress_hook(job):
    def hook(d):
        if d['status'] == 'downloading':
            job['status'] = 'downloading'
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            done = d.get('downloaded_bytes') or 0
            if total:
                job['progress'] = round(done / total * 100, 1)
                pct = f"{done / total * 100:.1f}%"
            else:
                pct = ''
            eta = d.get('eta')
            parts = [pct, human_speed(d.get('speed'))]
            if eta:
                parts.append('sisa ' + fmt_eta(eta))
            parts.append(elapsed_of(job))
            parts = [p for p in parts if p]
            job['message'] = 'Mengunduh ' + ' • '.join(parts)
        elif d['status'] == 'finished':
            job['status'] = 'processing'
            job['message'] = 'Download selesai (' + elapsed_of(job) + '), memproses file…'
            job['progress'] = 100.0
    return hook


def run_download(job, url, mode, format_id, resolution=DEFAULT_RESOLUTION,
                 force_ie=None):
    """Jalankan download yt-dlp di background thread — dengan strategi
    berlapis agar hasilnya andal (terutama MP3): coba beberapa format
    berurutan + retry + validasi file benar-benar media (bukan error).

    force_ie (opsional) = paksa ekstraktor tertentu (pilihan platform manual).
    Kalau paksaan gagal, otomatis fallback ke mode auto (tanpa paksaan).

    Status langsung di-set 'downloading' sejak awal + seluruh proses
    dibungkus try/except agar UI tidak pernah terlihat diam di 'Antre'."""
    job_id = job['id']
    job['_start'] = time.time()
    job['status'] = 'downloading'
    job['message'] = 'Menyiapkan…'
    job['progress'] = 0
    outtmpl = os.path.join(DOWNLOADS_DIR, job_id + '.%(ext)s')

    base = base_ydl_opts()
    base['outtmpl'] = outtmpl
    base['progress_hooks'] = [make_progress_hook(job)]

    res_sel = RESOLUTIONS.get(resolution, RESOLUTIONS[DEFAULT_RESOLUTION])

    def mk(fmt, merge=None, mp3=False, m4a=False):
        o = dict(base)
        o['format'] = fmt
        o.pop('merge_output_format', None)
        if merge:
            o['merge_output_format'] = merge
        if mp3:
            o['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
        if m4a:
            o['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'm4a',
                'preferredquality': '0',
            }]
        return o

    if mode == 'mp3':
        strategies = [
            mk('bestaudio/best', mp3=True),
            mk('bestvideo*+bestaudio/best', merge='mp4', mp3=True),
            mk('bestaudio/best', mp3=True),                    # retry transien
            mk('worstvideo*+worstaudio/worst', merge='mp4', mp3=True),  # cadangan terakhir
        ]
    elif mode == 'm4a':
        # M4A (AAC) — audio asli, kualitas lebih tinggi dari MP3, tanpa konversi ulang
        strategies = [
            mk('bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio/best', m4a=True),
            mk('bestvideo*+bestaudio/best', merge='mp4', m4a=True),
            mk('bestaudio/best', m4a=True),                    # retry transien
        ]
    elif mode == 'bestaudio':
        strategies = [mk('bestaudio/best'), mk('bestvideo*+bestaudio/best', merge='mp4')]
    elif mode == 'vonly':
        strategies = [mk(res_sel.split('+ba')[0] + '/b' if '+ba' in res_sel else 'bv/b')]
    elif mode == 'custom' and format_id:
        strategies = [mk(format_id)]
    else:  # 'best'
        strategies = [mk(res_sel, merge='mp4'), mk(res_sel, merge='mkv'),
                      mk('best/bv*+ba', merge='mp4')]   # fallback format tanpa height

    last_err = None
    # Batas waktu (anti-hang). YouTube: 4 menit (blokir biasanya gagal cepat;
    # retry 8 client + cooldown tidak boleh bikin user nunggu lama).
    # Platform lain: 15 menit — konten panjang (mix 1 jam, arsip besar) bisa
    # butuh waktu walau koneksi pelan, dan itu NORMAL (bukan hang).
    TOTAL_TIMEOUT = 240 if is_youtube(url) else 900

    # Paksaan ekstraktor hanya dipakai pada percobaan pertama; kalau gagal,
    # percobaan berikutnya otomatis pakai mode auto (paling andal).
    force_used = [bool(force_ie)]

    def attempt(o):
        """Coba satu strategi. Kembali True kalau sukses & file valid."""
        nonlocal last_err
        try:
            # info jeda anti-bot (kalau ada) supaya UI tidak terlihat diam
            job['message'] = 'Mengunduh…'
            if remaining_platform_cooldown(url) > 0:
                job['message'] = 'Menunggu jeda anti-bot, harap sabar…'
            if (time.time() - job['_start']) > TOTAL_TIMEOUT:
                raise RuntimeError('Terlalu lama — coba lagi beberapa menit ya!')
            cleanup_job_files(job_id)
            ie = force_ie if force_used[0] else None
            custom_dl = None
            if is_github(url) and not ie:
                custom_dl = extract_github(url)
            elif is_mediafire(url) and not ie:
                custom_dl = extract_mediafire(url)
            elif is_threads(url) and not ie:
                custom_dl = extract_threads(url)
            elif is_reddit(url) and not ie:
                custom_dl = extract_reddit(url)
            elif is_douyin(url) and not ie:
                custom_dl = extract_douyin(url)
            if custom_dl and (custom_dl.get('direct_urls') or [None])[0]:
                vu = custom_dl['direct_urls'][0]
                if vu:
                    o2 = dict(o)
                    o2.pop('impersonate', None)
                    o2['http_headers'] = {
                        'User-Agent': USER_AGENT,
                        'Referer': url,
                    }
                    with yt_dlp.YoutubeDL(o2) as ydl:
                        ydl.download([vu])
            elif is_rednote(url) and not ie:
                # RedNote: video = HLS (m3u8) dari CDN xhscdn. Impersonate
                # mengganggu generic extractor untuk file langsung, jadi buang;
                # tambahkan header Referer/UA supaya CDN menerima permintaan.
                rinfo = extract_rednote(url)
                vu = (rinfo.get('direct_urls') or [None])[0]
                if not vu:
                    raise RuntimeError('Tautan RedNote tidak berisi video yang bisa diunduh.')
                o2 = dict(o)
                o2.pop('impersonate', None)
                o2['http_headers'] = {
                    'User-Agent': USER_AGENT,
                    'Referer': 'https://www.xiaohongshu.com/',
                }
                with yt_dlp.YoutubeDL(o2) as ydl:
                    ydl.download([vu])
            elif 'videy.co' in url and not ie:
                # Videy: unduh file CDN langsung. Impersonate (curl_cffi)
                # membuat generic extractor menolak URL ini ("Unsupported URL"),
                # jadi buang dulu — file langsung mp4 tidak butuh impersonasi.
                vinfo = extract_videy(url)
                vu = (vinfo.get('direct_urls') or [None])[0]
                if not vu:
                    raise RuntimeError('Tautan Videy tidak berisi video.')
                o2 = dict(o)
                o2.pop('impersonate', None)
                with yt_dlp.YoutubeDL(o2) as ydl:
                    ydl.download([vu])
            elif ie:
                # Pilihan platform manual: paksa ekstraktor tertentu
                with yt_dlp.YoutubeDL(o) as ydl:
                    ydl.extract_info(url, download=True, ie_key=ie)
            elif is_youtube(url):
                yt_download_with_retry(url, o)
            else:
                with yt_dlp.YoutubeDL(o) as ydl:
                    ydl.download([url])
            fp = find_job_file(job_id)
            if not fp:
                raise RuntimeError('File hasil tidak ditemukan setelah proses download.')
            with open(fp, 'rb') as f:
                head = f.read(16)
            if not head:
                raise RuntimeError('File hasil kosong.')
            # validasi: MP3 harus punya ID3 / frame sync; M4A harus ftyp/mp4; semua bukan HTML/JSON
            if mode == 'mp3' and not (head[:3] == b'ID3' or head[0] == 0xFF):
                raise RuntimeError('Hasil bukan MP3 valid (mungkin server membalas error).')
            if mode == 'm4a' and not (head[:4] == b'\x00\x00\x00\x18' or head[4:8] == b'ftyp'
                                      or head[:4] in (b'ftyp', b'\x00\x00\x00\x20')):
                # m4a = MPEG-4 container; cek juga 'ftyp' di 4-8
                if b'ftyp' not in head[:12]:
                    raise RuntimeError('Hasil bukan M4A valid (mungkin server membalas error).')
            if head.lstrip()[:1] in (b'<', b'{'):
                raise RuntimeError('Hasil bukan file media (halaman/JSON error).')
            job['status'] = 'done'
            job['filepath'] = fp
            job['filename'] = os.path.basename(fp)
            job['filesize_mb'] = mb(os.path.getsize(fp))
            dur = probe_duration(fp)
            job['duration'] = round(dur) if dur else None
            job['duration_text'] = format_duration(job['duration'])
            job['message'] = 'Selesai dalam ' + elapsed_of(job)
            return True
        except Exception as e:
            last_err = e
            # Paksaan platform gagal → percobaan berikutnya pakai auto
            force_used[0] = False
            if is_block_signal(e):
                mark_platform_cooldown(url)
            return False

    try:
        drm_tries = 0
        for st in strategies:
            if attempt(st):
                return
            # Impersonasi bermasalah → coba tanpa impersonate (sekali per strategi)
            if st.get('impersonate') and ('impersonate' in str(last_err).lower()
                                          or 'tls fingerprint' in str(last_err).lower()):
                st.pop('impersonate', None)
                if attempt(st):
                    return
            # FAIL-FAST: bot-check YouTube → jangan perbanyak request (nanti
            # memblokir IP dan SEMUA lagu berikutnya ikut gagal). Sudah dicoba
            # client retry di dalam yt_download_with_retry.
            if is_youtube(url) and is_yt_bot_error(last_err):
                break
            # DRM: boleh coba maks 2 strategi (format beda), lalu berhenti.
            if is_drm_error(last_err):
                drm_tries += 1
                if drm_tries >= 2:
                    break
            time.sleep(0.7)

        job['status'] = 'error'
        job['error'] = friendly_error(last_err) if last_err else 'Gagal mengunduh.'
        job['message'] = 'Gagal'
        cleanup_job_files(job_id)
    except Exception as e:
        # JANGAN pernah biarkan job menggantung tanpa status error
        job['status'] = 'error'
        job['error'] = friendly_error(e)
        job['message'] = 'Gagal'
        cleanup_job_files(job_id)


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------
@app.route('/')
def index():
    return send_file(os.path.join(app.static_folder, 'index.html'))


@app.route('/api/platforms')
def api_platforms():
    return jsonify({
        'platforms': PLATFORMS,
        'ytdlp_version': yt_dlp.version.__version__,
        'ytmusic': YTMUSIC_AVAILABLE,
    })


def normalize_url(url):
    """Perbaiki URL hasil copy dari aplikasi chat (WhatsApp, Telegram, dll):
    - &amp; → &  (HTML entity yang sering menempel saat share link)
    - hapus spasi
    """
    u = (url or '').strip()
    u = u.replace('&amp;', '&')
    u = re.sub(r'\s+', '', u)
    return u


def enriched_gallery_response(g, url, platform=None):
    """build_gallery_response + isi uploader kalau masih Unknown
    (satu request tambahan ke halaman, hanya saat perlu)."""
    resp = build_gallery_response(g, url, platform)
    if resp.get('uploader') == 'Unknown':
        try:
            un = clean_name(page_username(url))
            if un:
                resp['uploader'] = un
        except Exception:
            pass
    return resp


@app.route('/api/info')
def api_info():
    """Ambil metadata + daftar format dari URL (tanpa mendownload file)."""
    url = normalize_url(request.args.get('url') or '')
    if not url:
        return jsonify({'error': 'Parameter "url" wajib diisi.'}), 400
    if not url.startswith(('http://', 'https://')):
        return jsonify({'error': 'URL harus diawali http:// atau https://'}), 400

    # URL pendek (b23.tv, on.soundcloud.com, snd.sc, dai.ly) → resolve
    # dulu supaya deteksi platform & ekstraktor dapat URL asli.
    if any(s in url for s in ('b23.tv', 'on.soundcloud.com',
                              'snd.sc', 'dai.ly', 'spotify.link')):
        resolved = resolve_short_url(url)
        if resolved and resolved != url:
            url = resolved

    # Pilihan MANUAL platform (opsional) — kalau user pilih platform tertentu,
    # paksa pakai platform itu (auto tetap default kalau kosong).
    platform_param = (request.args.get('platform') or '').strip().lower()
    forced_platform = find_platform(platform_param) if platform_param else None

    platform = detect_platform(url)
    if forced_platform:
        platform = forced_platform

    # Kunci ekstraktor yt-dlp untuk pilihan manual (None = biarkan auto)
    force_ie = None
    if forced_platform:
        keys = PLATFORM_IE_KEYS.get(forced_platform['key'])
        if keys:
            force_ie = keys[0]

    # Platform baru dengan ekstraktor custom (GitHub, MediaFire, Threads,
    # Reddit, Douyin) — jalur cepat tanpa yt-dlp untuk yang punya direct URL.
    custom_extractors = {
        'github': extract_github,
        'mediafire': extract_mediafire,
        'threads': extract_threads,
        'reddit': extract_reddit,
        'douyin': extract_douyin,
    }
    if platform and platform['key'] in custom_extractors:
        try:
            return jsonify(custom_extractors[platform['key']](url))
        except Exception as e:
            # Pesan dari extractor custom sudah ditulis ramah → tampilkan langsung
            return jsonify({'error': str(e)[:500]}), 500

    # RedNote: ekstraktor berlapis (SSR / API signed / yt-dlp)
    if platform and platform['key'] == 'rednote':
        try:
            return jsonify(extract_rednote(url))
        except Exception as e:
            return jsonify({'error': friendly_error(e)[:500]}), 500

    # Videy: custom (CDN langsung)
    if platform and platform['key'] == 'videy':
        try:
            return jsonify(extract_videy(url))
        except Exception as e:
            return jsonify({'error': friendly_error(e)[:500]}), 500

    # Spotify: resolusi khusus (oEmbed + YouTube Music)
    if platform and platform['key'] == 'spotify':
        try:
            return jsonify(resolve_spotify(url))
        except Exception as e:
            return jsonify({'error': friendly_error(e)[:800]}), 400

    opts = base_ydl_opts()
    opts['skip_download'] = True

    # Tautan langsung ke file media (mis. URL gambar yang disalin dari browser)
    # → langsung tampilkan, dijamin jalan tanpa login. CEK DULU sebelum jalur
    # khusus platform supaya URL CDN langsung (twimg, fbcdn, dll) tetap
    # diproses sebagai media langsung.
    if looks_like_direct_media(url):
        try:
            g = extract_direct_media(url)
            return jsonify(enriched_gallery_response(g, url, platform))
        except Exception:
            pass

    # URL tanpa platform yang dikenal: cek apakah itu file media langsung
    # (mis. https://picsum.photos/400 yang redirect ke CDN gambar)
    if platform is None:
        try:
            item = probe_direct_media(url)
            g = {'meta': {}, 'items': [item]}
            return jsonify(build_gallery_response(g, url, None))
        except Exception:
            pass

    # Platform media sosial: coba video (yt-dlp) dulu; kalau berupa foto/story
    # (tidak ada video), fallback berjenjang: gallery-dl → syndication X →
    # meta og:image → pesan diagnostik dengan saran.
    photo_capable = platform and platform['key'] in ('instagram', 'x', 'facebook', 'tiktok', 'pinterest')

    if photo_capable:
        reasons = []

        # 1) Coba video via yt-dlp
        try:
            info = extract_with_fallback(url, opts, ie_key=force_ie)
        except Exception as e:
            reasons.append(f'yt-dlp: {str(e)[:100]}')
            info = None

        if info and info.get('formats'):
            try:
                parsed = parse_info(info)
            except Exception as e:
                reasons.append(f'parse: {str(e)[:100]}')
                parsed = None
            if parsed and parsed.get('has_video'):
                return jsonify({'ok': True, **parsed})
            # yt-dlp sukses tapi bukan video (audio-only slideshow, dll)
            # → lanjut ke galeri foto/video

        # 2) gallery-dl (foto, carousel, story IG, slideshow TikTok)
        try:
            g = extract_gallery(url)
            if g['items']:
                resp = enriched_gallery_response(g, url, platform)
                # kalau uploader belum ketemu (mis. Pinterest/FB), enrich cepat
                # dari meta og: halaman (satu request tambahan, hanya saat perlu)
                if resp.get('uploader') == 'Unknown':
                    try:
                        un = clean_name(page_username(url))
                        if un:
                            resp['uploader'] = un
                    except Exception:
                        pass
                return jsonify(resp)
        except Exception as e:
            reasons.append(f'gallery-dl: {str(e)[:100]}')

        # 3) Fallback spesifik platform TANPA login (berlapis):
        #    X/Twitter: fxtwitter → syndication twimg
        #    Instagram: embed publik → endpoint media ?size=l
        #    Facebook:  plugin post embed
        #    TikTok:    parse __UNIVERSAL_DATA_FOR_REHYDRATION__ (foto slideshow)
        pkey = platform['key'] if platform else ''
        extra = {
            'x':         [('fxtwitter', extract_x_tweet)],
            'instagram': [('embed IG', extract_instagram_embed),
                          ('media IG', extract_instagram_media_direct)],
            'facebook':  [('embed FB', extract_facebook_embed)],
            'tiktok':    [('data TikTok', extract_tiktok_json)],
        }
        for label, fn in (extra.get(pkey) or []):
            try:
                g = fn(url)
                if g['items']:
                    resp = enriched_gallery_response(g, url, platform)
                    if resp.get('uploader') == 'Unknown':
                        try:
                            un = clean_name(page_username(url))
                            if un:
                                resp['uploader'] = un
                        except Exception:
                            pass
                    return jsonify(resp)
            except Exception as e:
                reasons.append(f'{label}: {str(e)[:100]}')

        # 4) Meta og:image / og:video dari halaman
        try:
            g = extract_og_media(url)
            if g['items']:
                return jsonify(enriched_gallery_response(g, url, platform))
        except Exception as e:
            reasons.append(f'meta halaman: {str(e)[:100]}')

        # Pesan error ringkas (tanpa instruksi panjang)
        pname = platform['name'] if platform else 'platform ini'
        return jsonify({'error': friendly_error(
            f'Konten dari {pname} tidak bisa diambil otomatis.')}), 500

    try:
        info = extract_with_fallback(url, opts, ie_key=force_ie)
        if info is None:
            return jsonify({'error': 'Ups, tidak bisa membaca konten dari tautan ini. Coba tautan lain ya!'}), 500
        parsed = parse_info(info)
        if (not parsed.get('has_video') and not parsed.get('has_audio')
                and not parsed.get('has_image', False) and parsed['formats']):
            # hasil kosong tidak berguna — beri saran
            raise RuntimeError('Tidak ada media yang bisa diunduh dari tautan ini.')
        return jsonify({'ok': True, **parsed})
    except Exception as e:
        return jsonify({'error': friendly_error(e)[:800]}), 500


def run_gallery_download(job, url, items=None):
    """Download media foto/story/slideshow lalu zip.

    items: daftar {url, ext} yang dipilih user (None = ambil semua)."""
    job_id = job['id']
    job['_start'] = time.time()
    outdir = os.path.join(DOWNLOADS_DIR, job_id)
    os.makedirs(outdir, exist_ok=True)
    try:
        job['status'] = 'downloading'
        wait_platform_cooldown(url)
        if items:
            g = {'items': items}
        else:
            g = None
            # urutan fallback sesuai platform
            pkey = (detect_platform(url) or {}).get('key', '')
            extra = {
                'x': [extract_x_tweet],
                'instagram': [extract_instagram_embed, extract_instagram_media_direct],
                'facebook': [extract_facebook_embed],
                'tiktok': [extract_tiktok_json],
                'pinterest': [],
            }
            chain = [extract_gallery]
            if pkey in extra:
                chain += extra[pkey]
            chain += [extract_direct_media, extract_og_media]
            for fn in chain:
                try:
                    g = fn(url)
                    if g.get('items'):
                        break
                except Exception:
                    continue
            if g is None or not g.get('items'):
                raise RuntimeError('Tidak ada media yang bisa diambil otomatis dari tautan ini.')
        items = g['items']
        total = len(items)
        if not total:
            raise RuntimeError('Tidak ada media yang ditemukan untuk diunduh.')

        saved = 0
        for i, it in enumerate(items, 1):
            job['message'] = f'Mengunduh media {i}/{total} • ' + elapsed_of(job)
            try:
                r = requests.get(it['url'], headers={
                    'User-Agent': USER_AGENT,
                    'Accept': 'image/avif,image/webp,image/apng,image/*,video/*,*/*;q=0.8',
                    'Referer': url,
                    'Sec-Fetch-Dest': 'media',
                }, timeout=90)
                r.raise_for_status()
                # JANGAN simpan kalau server membalas JSON/HTML (login wall, error)
                if not is_media_payload(r.headers.get('Content-Type', ''), r.content):
                    continue
            except Exception:
                continue  # lewati item yang gagal, lanjut ke berikutnya
            # tentukan ekstensi dari Content-Type asli (bukan dari URL)
            ctype = r.headers.get('Content-Type', '').split(';')[0].strip().lower()
            ext = MEDIA_EXT_BY_CTYPE.get(ctype) or it.get('ext') or 'jpg'
            saved += 1
            fn = os.path.join(outdir, f"{saved:02d}.{ext}")
            with open(fn, 'wb') as f:
                f.write(r.content)
            job['progress'] = round(saved / total * 100, 1)

        if saved == 0:
            raise RuntimeError('Tidak ada media valid yang bisa diunduh (server membalas JSON/HTML atau gagal).')

        zip_path = os.path.join(DOWNLOADS_DIR, job_id + '.zip')
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for fname in sorted(os.listdir(outdir)):
                zf.write(os.path.join(outdir, fname), arcname=fname)
        shutil.rmtree(outdir, ignore_errors=True)

        job['status'] = 'done'
        job['filepath'] = zip_path
        job['filename'] = os.path.basename(zip_path)
        job['filesize_mb'] = mb(os.path.getsize(zip_path))
        job['duration'] = None
        job['duration_text'] = ''
        job['message'] = 'Selesai dalam ' + elapsed_of(job)
    except Exception as e:
        job['status'] = 'error'
        job['error'] = friendly_error(e)
        job['message'] = 'Gagal'
        shutil.rmtree(outdir, ignore_errors=True)
        cleanup_job_files(job_id)


@app.route('/api/music-search')
def api_music_search():
    """Cari musik via YouTube Music: lagu, album, artis, atau playlist."""
    q = (request.args.get('q') or '').strip()
    f = (request.args.get('filter') or 'songs').strip()
    if not q:
        return jsonify({'error': 'Parameter "q" (judul lagu / artis / album) wajib diisi.'}), 400
    if f not in ('songs', 'albums', 'artists', 'playlists'):
        f = 'songs'
    if not YTMUSIC_AVAILABLE:
        return jsonify({'error': 'Fitur musik butuh ytmusicapi. Install: pip install ytmusicapi'}), 500

    try:
        with YTMUSIC_LOCK:
            results = YTMUSIC.search(q, filter=f, limit=20)
        parsed = []
        for item in results:
            thumbs = item.get('thumbnails') or []
            thumb = thumbs[-1]['url'] if thumbs else None
            base = {
                'id': item.get('videoId') or item.get('browseId'),
                'title': item.get('title') or 'Untitled',
                'thumbnail': thumb,
            }
            if f == 'songs':
                artists = item.get('artists') or []
                base.update({
                    'artist': ', '.join(a.get('name', '') for a in artists if a.get('name')) or 'Unknown',
                    'album': (item.get('album') or {}).get('name') if isinstance(item.get('album'), dict) else (item.get('album') or ''),
                    'duration_text': format_duration(item.get('duration_seconds') or 0),
                    'videoId': item.get('videoId'),
                    'is_explicit': item.get('isExplicit'),
                })
            elif f == 'albums':
                artists = item.get('artists') or []
                base.update({
                    'artist': ', '.join(a.get('name', '') for a in artists if a.get('name')) or (item.get('artist') or 'Unknown'),
                    'year': item.get('year') or '',
                    'type': 'album',
                })
            elif f == 'playlists':
                base.update({'author': item.get('author') or 'Unknown', 'type': 'playlist'})
            else:  # artists
                base.update({
                    'name': item.get('artist') or item.get('title') or 'Unknown',
                    'subscribers': item.get('subscribers') or '',
                    'type': 'artist',
                })
            parsed.append(base)
        return jsonify({'ok': True, 'type': f, 'query': q, 'results': parsed})
    except Exception as e:
        return jsonify({'error': friendly_error(e)[:500]}), 500


@app.route('/api/music-album/<album_id>')
def api_music_album(album_id):
    """Detail album → daftar lagu."""
    if not YTMUSIC_AVAILABLE:
        return jsonify({'error': 'Fitur musik butuh ytmusicapi. Install: pip install ytmusicapi'}), 500
    try:
        with YTMUSIC_LOCK:
            album = YTMUSIC.get_album(album_id)
        tracks = []
        for t in album.get('tracks') or []:
            thumbs = t.get('thumbnails') or []
            tracks.append({
                'videoId': t.get('videoId'),
                'title': t.get('title'),
                'artist': ', '.join(a.get('name', '') for a in (t.get('artists') or []) if a.get('name')),
                'duration_text': format_duration(t.get('duration_seconds') or 0),
            })
        thumbs = album.get('thumbnails') or []
        return jsonify({
            'ok': True, 'type': 'album',
            'id': album_id,
            'title': album.get('title'),
            'artist': ', '.join(a.get('name', '') for a in (album.get('artists') or []) if a.get('name')),
            'thumbnail': thumbs[-1]['url'] if thumbs else None,
            'year': album.get('year') or '',
            'tracks': [t for t in tracks if t['videoId']],
        })
    except Exception as e:
        return jsonify({'error': friendly_error(e)[:400]}), 500


@app.route('/api/music-playlist/<playlist_id>')
def api_music_playlist(playlist_id):
    """Detail playlist → daftar lagu."""
    if not YTMUSIC_AVAILABLE:
        return jsonify({'error': 'Fitur musik butuh ytmusicapi. Install: pip install ytmusicapi'}), 500
    try:
        with YTMUSIC_LOCK:
            pl = YTMUSIC.get_playlist(playlist_id, limit=100)
        tracks = []
        for t in pl.get('tracks') or []:
            thumbs = t.get('thumbnails') or []
            tracks.append({
                'videoId': t.get('videoId'),
                'title': t.get('title'),
                'artist': ', '.join(a.get('name', '') for a in (t.get('artists') or []) if a.get('name')),
                'duration_text': format_duration(t.get('duration_seconds') or 0),
            })
        thumbs = pl.get('thumbnails') or []
        return jsonify({
            'ok': True, 'type': 'playlist',
            'id': playlist_id,
            'title': pl.get('title'),
            'author': pl.get('author') or 'Unknown',
            'thumbnail': thumbs[-1]['url'] if thumbs else None,
            'tracks': [t for t in tracks if t['videoId']],
        })
    except Exception as e:
        return jsonify({'error': friendly_error(e)[:400]}), 500


@app.route('/api/music-artist/<artist_id>')
def api_music_artist(artist_id):
    """Detail artis → lagu top + album."""
    if not YTMUSIC_AVAILABLE:
        return jsonify({'error': 'Fitur musik butuh ytmusicapi. Install: pip install ytmusicapi'}), 500
    try:
        with YTMUSIC_LOCK:
            artist = YTMUSIC.get_artist(artist_id)
        songs = []
        for t in ((artist.get('songs') or {}).get('results') or [])[:15]:
            thumbs = t.get('thumbnails') or []
            songs.append({
                'videoId': t.get('videoId'),
                'title': t.get('title'),
                'artist': ', '.join(a.get('name', '') for a in (t.get('artists') or []) if a.get('name')) or artist.get('name', ''),
                'duration_text': format_duration(t.get('duration_seconds') or 0),
            })
        albums = []
        for a in ((artist.get('albums') or {}).get('results') or [])[:8]:
            thumbs = a.get('thumbnails') or []
            albums.append({
                'browseId': a.get('browseId'),
                'title': a.get('title'),
                'year': a.get('year') or '',
                'thumbnail': thumbs[-1]['url'] if thumbs else None,
            })
        thumbs = artist.get('thumbnails') or []
        return jsonify({
            'ok': True, 'type': 'artist',
            'id': artist_id,
            'name': artist.get('name'),
            'thumbnail': thumbs[-1]['url'] if thumbs else None,
            'subscribers': artist.get('subscribers') or '',
            'songs': [s for s in songs if s['videoId']],
            'albums': [al for al in albums if al['browseId']],
        })
    except Exception as e:
        return jsonify({'error': friendly_error(e)[:400]}), 500


def _resolve_stream_url(video_id):
    """Cari URL audio yang bisa diputar untuk sebuah lagu YouTube — JALUR
    CEPAT khusus pemutar. Rotasi client PENUH (sama seperti jalur download:
    android_vr → web_embedded → tv_downgraded → … → all → PO token) supaya
    tetap tembus walau satu client diblokir YouTube di IP datacenter.
    Cache pendek (URL kedaluwarsa ±6 jam) → putar ulang langsung tanpa ekstrak.
    Tanpa cookie, tanpa layanan pihak ketiga."""
    with STREAM_CACHE_LOCK:
        cached = STREAM_CACHE.get(video_id)
    if cached and (time.time() - cached[0]) < STREAM_CACHE_TTL:
        return cached[1]

    url = 'https://www.youtube.com/watch?v=' + video_id
    # Jangan tunggu cooldown lama di jalur pemutar — maksimal 5 detik saja,
    # supaya tombol Putar selalu merespons cepat.
    rem = remaining_platform_cooldown(url)
    if rem > 0:
        time.sleep(min(rem, 5))

    base = base_ydl_opts()
    last = None

    def pick(fmts):
        fmts = [f for f in fmts
                if f.get('url') and f.get('acodec') and f['acodec'] != 'none'
                and f.get('vcodec') in ('none', None)]
        if not fmts:
            return None
        # Prioritas: m4a (AAC, paling kompatibel) → webm/mp3 (Opus) → lainnya
        def score(f):
            e = (f.get('ext') or '').lower()
            if e == 'm4a':
                return 0
            if e in ('webm', 'mp3'):
                return 1
            return 2
        fmts.sort(key=score)
        return fmts[0]['url']

    # Rotasi client penuh — sama dengan download (paling andal di datacenter)
    for cl in YT_CLIENTS:
        try:
            with yt_dlp.YoutubeDL(with_player_client(base, cl)) as ydl:
                info = ydl.extract_info(url, download=False)
            src = pick(info.get('formats', []))
            if src:
                with STREAM_CACHE_LOCK:
                    STREAM_CACHE[video_id] = (time.time(), src)
                return src
            last = RuntimeError('Lagu ini tidak punya audio yang bisa diputar.')
        except Exception as e:
            last = e
            _yt_cooldown_wait()
            if is_yt_bot_error(e):
                _yt_mark_cooldown()
                time.sleep(1.0)
                continue
            if is_drm_error(e):
                time.sleep(0.8)
                continue
            if is_format_unavailable(e):
                continue
            # error lain: lanjut client berikutnya (jangan langsung gagal)
            time.sleep(0.4)
            continue
    # Pamungkas: semua client sekaligus
    try:
        with yt_dlp.YoutubeDL(with_player_client(base, YT_CLIENTS_LAST)) as ydl:
            info = ydl.extract_info(url, download=False)
        src = pick(info.get('formats', []))
        if src:
            with STREAM_CACHE_LOCK:
                STREAM_CACHE[video_id] = (time.time(), src)
            return src
    except Exception as e:
        last = e
    # Terakhir: PO token kalau tersedia (best-effort)
    if POT_AVAILABLE:
        try:
            info = try_pot_with_backoff(url, base, want_info=True)
            src = pick(info.get('formats', []))
            if src:
                with STREAM_CACHE_LOCK:
                    STREAM_CACHE[video_id] = (time.time(), src)
                return src
        except Exception as e:
            last = e
    if last is None:
        last = RuntimeError('Lagu ini tidak punya audio yang bisa diputar.')
    raise last


@app.route('/api/music-stream/<video_id>')
def api_music_stream(video_id):
    """Proxy audio agar lagu bisa DIPUTAR langsung di browser (tanpa
    download). Mendukung pencarian posisi (Range) — sama seperti pola
    proyek YT Music Downloader asli, tapi memakai strategi anti-blokir
    android_vr dan tanpa cookie. Tidak menyimpan file apa pun."""
    video_id = re.sub(r'[^0-9A-Za-z_-]', '', video_id or '')
    if not video_id:
        return jsonify({'error': 'ID lagu tidak valid.'}), 400

    src = None
    try:
        src = _resolve_stream_url(video_id)
    except Exception as e:
        return jsonify({'error': friendly_error(e)[:400]}), 500

    req_headers = {
        'User-Agent': USER_AGENT,
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    # Teruskan header Range dari browser → pemutar bisa lompat ke posisi mana pun
    if 'Range' in request.headers:
        req_headers['Range'] = request.headers['Range']

    def fetch():
        return requests.get(src, headers=req_headers, stream=True, timeout=25)

    r = None
    try:
        r = fetch()
        if r.status_code in (403, 401) or 'json' in (r.headers.get('content-type') or ''):
            # URL cache basi / diblokir → resolve ulang sekali (URL baru), lalu coba lagi
            with STREAM_CACHE_LOCK:
                STREAM_CACHE.pop(video_id, None)
            r.close()
            r = None
            try:
                src, ctype = _resolve_stream_url(video_id)
                r = fetch()
            except Exception:
                pass
    except Exception:
        if r:
            r.close()
        return jsonify({'error': 'Gagal terhubung ke sumber audio. Coba unduh MP3-nya saja ya!'}), 502

    if r is None or r.status_code >= 400:
        if r:
            r.close()
        return jsonify({'error': 'Lagu ini sedang tidak bisa diputar dari server. Coba unduh MP3-nya saja ya!'}), 502

    def generate():
        try:
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk
        except Exception:
            pass
        finally:
            try:
                r.close()
            except Exception:
                pass

    resp = Response(stream_with_context(generate()), status=r.status_code)
    for header in ('content-type', 'content-length', 'content-range', 'accept-ranges'):
        val = r.headers.get(header)
        if val:
            resp.headers[header] = val
    if 'accept-ranges' not in [k.lower() for k in resp.headers.keys()]:
        resp.headers['Accept-Ranges'] = 'bytes'
    # Tambahkan parameter codec untuk MP4/AAC — beberapa browser Android butuh
    # ini untuk menerima stream DASH fMP4 (kalau tidak, durasi jadi 0 & diam).
    ct = (resp.headers.get('content-type') or '').lower()
    if ct.startswith('audio/mp4') and 'codecs' not in ct:
        resp.headers['Content-Type'] = 'audio/mp4; codecs="mp4a.40.2"'
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@app.route('/api/gallery-download', methods=['POST'])
def api_gallery_download():
    """Download foto/story/slideshow → ZIP. Kirim items [] untuk pilih media
    tertentu, atau kosongkan untuk mengambil semua."""
    data = request.get_json(silent=True) or {}
    url = (data.get('url') or '').strip()
    if not url:
        return jsonify({'error': 'Parameter "url" wajib diisi.'}), 400

    items = None
    raw = data.get('items')
    if isinstance(raw, list) and raw:
        items = [{'url': it.get('url'), 'ext': it.get('ext', 'jpg')}
                 for it in raw if it.get('url')]

    job = new_job()
    if data.get('title'):
        job['_title'] = str(data['title'])[:120]
    t = threading.Thread(target=run_gallery_download, args=(job, url, items), daemon=True)
    t.start()
    return jsonify({'ok': True, 'job_id': job['id']})


@app.route('/api/download', methods=['POST'])
def api_download():
    """Mulai job download di background, kembalikan job_id untuk dipoll."""
    data = request.get_json(silent=True) or {}
    url = (data.get('url') or '').strip()
    mode = data.get('mode') or 'best'
    format_id = data.get('format_id')
    resolution = data.get('resolution') or DEFAULT_RESOLUTION

    if not url:
        return jsonify({'error': 'Parameter "url" wajib diisi.'}), 400
    if mode not in ('best', 'mp3', 'm4a', 'bestaudio', 'vonly', 'custom'):
        mode = 'best'
    if resolution not in RESOLUTIONS:
        resolution = DEFAULT_RESOLUTION

    # Pilihan MANUAL platform (opsional) — sama seperti di /api/info
    force_ie = None
    plat_key = (data.get('platform') or '').strip().lower()
    if plat_key and find_platform(plat_key):
        keys = PLATFORM_IE_KEYS.get(plat_key)
        if keys:
            force_ie = keys[0]

    job = new_job()
    if data.get('title'):
        job['_title'] = str(data['title'])[:120]
    detected = detect_platform(url) or {}
    job['_platform'] = plat_key or detected.get('key') or ''
    job['_mode'] = mode
    # Platform dengan direct download (file/media biasa) → jalur khusus
    if detected.get('key') in ('github', 'mediafire', 'threads', 'reddit') and not force_ie:
        t = threading.Thread(target=run_custom_platform,
                             args=(job, url, detected['key'], mode), daemon=True)
    else:
        t = threading.Thread(target=run_download,
                             args=(job, url, mode, format_id, resolution, force_ie),
                             daemon=True)
    t.start()
    return jsonify({'ok': True, 'job_id': job['id']})


def run_direct_file_download(job, url, direct_url, title, platform_key, mode='best'):
    """Download file/media langsung via requests (GitHub raw, MediaFire,
    Threads, Reddit). Menyimpan file asli; mode mp3/m4a hanya berlaku kalau
    file-nya audio/video (dikonversi via ffmpeg)."""
    job_id = job['id']
    job['_start'] = time.time()
    job['status'] = 'downloading'
    job['message'] = 'Menyiapkan…'
    job['progress'] = 0
    try:
        job['message'] = 'Mengunduh…'
        with requests.get(direct_url, stream=True, timeout=60,
                          headers={'User-Agent': USER_AGENT, 'Referer': url,
                                   'Accept-Encoding': 'identity'}) as r:
            r.raise_for_status()
            ctype = (r.headers.get('Content-Type') or '').split(';')[0].strip().lower()
            total = int(r.headers.get('Content-Length') or 0)
            ext = MEDIA_EXT_BY_CTYPE.get(ctype) or 'bin'
            if ext == 'bin':
                # coba dari nama file URL
                fn = urllib.parse.unquote(direct_url.split('?')[0].rstrip('/').split('/')[-1])
                if '.' in fn:
                    ext = fn.rsplit('.', 1)[-1].lower()[:8]
            raw_path = os.path.join(DOWNLOADS_DIR, job_id + '.raw.' + ext)
            downloaded = 0
            with open(raw_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            job['progress'] = round(downloaded / total * 100, 1)
            if not os.path.exists(raw_path) or os.path.getsize(raw_path) == 0:
                raise RuntimeError('File hasil kosong.')
            with open(raw_path, 'rb') as f:
                head = f.read(16)
            if head.lstrip()[:1] in (b'<', b'{'):
                raise RuntimeError('Server membalas halaman/JSON error, bukan file.')

            # mode mp3/m4a: konversi kalau file audio/video
            final_path = raw_path
            if mode in ('mp3', 'm4a') and (ctype.startswith('audio/') or ctype.startswith('video/')):
                out_ext = 'mp3' if mode == 'mp3' else 'm4a'
                ff = shutil.which('ffmpeg')
                if ff:
                    final_path = os.path.join(DOWNLOADS_DIR, job_id + '.' + out_ext)
                    codec = 'libmp3lame' if out_ext == 'mp3' else 'aac'
                    subprocess.run([ff, '-y', '-i', raw_path, '-vn',
                                    '-c:a', codec, '-b:a', '192k' if out_ext == 'mp3' else '256k',
                                    final_path],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
                    if not os.path.exists(final_path) or os.path.getsize(final_path) == 0:
                        final_path = raw_path  # gagal konversi → tetap file asli
            job['filepath'] = final_path
            job['status'] = 'done'
            job['filename'] = os.path.basename(final_path)
            job['filesize_mb'] = mb(os.path.getsize(final_path))
            dur = probe_duration(final_path)
            job['duration'] = round(dur) if dur else None
            job['duration_text'] = format_duration(job['duration'])
            job['message'] = 'Selesai dalam ' + elapsed_of(job)
            return True
    except Exception as e:
        job['status'] = 'error'
        job['error'] = str(e)[:400]
        return False


def run_custom_platform(job, url, plat_key, mode):
    """Resolve custom platform lalu download langsung (GitHub/MediaFire/
    Threads/Reddit). Douyin/Rutube tetap lewat yt-dlp (run_download)."""
    extractor = {'github': extract_github, 'mediafire': extract_mediafire,
                 'threads': extract_threads, 'reddit': extract_reddit}.get(plat_key)
    try:
        info = extractor(url)
        # fallback base64 (GitHub API) — simpan langsung tanpa download eksternal
        if info.get('inline_base64'):
            job_id = job['id']
            job['_start'] = time.time()
            ext = info.get('inline_ext') or 'bin'
            fp = os.path.join(DOWNLOADS_DIR, job_id + '.' + ext)
            with open(fp, 'wb') as f:
                f.write(info['inline_base64'])
            job['status'] = 'done'
            job['filepath'] = fp
            job['filename'] = os.path.basename(fp)
            job['filesize_mb'] = mb(os.path.getsize(fp))
            job['message'] = 'Selesai dalam ' + elapsed_of(job)
            return
        du = (info.get('direct_urls') or [None])[0]
        if not du:
            raise RuntimeError('Tautan ini tidak berisi media yang bisa diunduh.')
        run_direct_file_download(job, url, du, info.get('title') or 'File', plat_key, mode)
    except Exception as e:
        job['status'] = 'error'
        job['error'] = str(e)[:400]


@app.route('/api/job/<job_id>')
def api_job(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({'error': 'Job tidak ditemukan atau sudah kedaluwarsa.'}), 404
    return jsonify({
        'status': job['status'],
        'progress': job['progress'],
        'message': job['message'],
        'error': job['error'],
        'filename': job['filename'],
        'filesize_mb': job['filesize_mb'],
        'duration': job.get('duration'),
        'duration_text': job.get('duration_text'),
    })


@app.route('/api/file/<job_id>')
def api_file(job_id):
    """Kirim file hasil download sekali, lalu bersihkan (mirip pola proyek asli)."""
    job = JOBS.get(job_id)
    if not job or job['status'] != 'done' or not job['filepath']:
        return jsonify({'error': 'File belum siap.'}), 404

    filepath = job['filepath']
    if not os.path.exists(filepath):
        return jsonify({'error': 'File sudah terhapus.'}), 404

    ext = os.path.splitext(filepath)[1] or ''
    download_name = safe_filename(job.get('_title') or 'download') + ext

    # Catat ke riwayat akun (jika user login)
    try:
        user = _auth_from_request()
        if user:
            plat = job.get('_platform') or ''
            record_history(user, job.get('_title'), plat, job.get('_mode'),
                           os.path.basename(filepath), job.get('filesize_mb'))
    except Exception:
        pass

    @after_this_request
    def cleanup(resp):
        try:
            os.remove(filepath)
        except OSError:
            pass
        return resp

    return send_file(filepath, as_attachment=True, download_name=download_name)


MEDIA_EXT_BY_CTYPE = {
    'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp',
    'image/gif': 'gif', 'image/avif': 'avif', 'image/bmp': 'bmp',
    'video/mp4': 'mp4', 'video/webm': 'webm', 'video/quicktime': 'mov',
}


def is_media_payload(ctype, payload):
    """Cek apakah respons benar-benar file media (bukan JSON/HTML login wall)."""
    ctype = (ctype or '').split(';')[0].strip().lower()
    if ctype.startswith('image/') or ctype.startswith('video/'):
        return True
    # cek magic bytes sebagai cadangan (kalau server salah kasih content-type)
    if payload[:3] in (b'\xff\xd8\xff', b'GIF') or payload[:4] in (b'\x89PNG', b'RIFF', b'\x00\x00\x00'):
        return True
    if payload[:4] == b'\x1aE\xdf\xa3':   # webm/mkv
        return True
    return False


@app.route('/api/thumbnail')
def api_thumbnail():
    """Proxy thumbnail/gambar (menghindari hotlink-block dari CDN platform).
    Pakai ?dl=1 untuk mengunduh sebagai lampiran (attachment).
    Validasi Content-Type & magic bytes — TIDAK pernah mengirim JSON/HTML
    sebagai file media."""
    url = (request.args.get('url') or '').strip().replace('&amp;', '&')
    dl = request.args.get('dl') == '1'
    if not url.startswith('http'):
        return jsonify({'error': 'URL thumbnail tidak valid.'}), 400

    # Beberapa CDN (bstarstatic, dll) kadang nolak sesaat (403/5xx) saat
    # kena rate-limit. Coba ulang dengan kombinasi header berbeda sebelum
    # menyerah, supaya thumbnail jarang tampil kosong.
    header_sets = [
        {   # standar: referer = url itu sendiri
            'User-Agent': USER_AGENT,
            'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
            'Referer': url,
            'Sec-Fetch-Dest': 'image',
            'Sec-Fetch-Mode': 'no-cors',
        },
        {   # tanpa referer (beberapa CDN benci referer)
            'User-Agent': USER_AGENT,
            'Accept': 'image/*,*/*;q=0.8',
        },
    ]
    r = None
    last_err = None
    for attempt in range(4):
        hs = header_sets[attempt % len(header_sets)]
        try:
            r = requests.get(url, timeout=30, headers=hs)
            if r.status_code == 200:
                break
            last_err = RuntimeError('HTTP %d' % r.status_code)
        except Exception as e:
            last_err = e
        r = None
        time.sleep(0.6 * (attempt + 1))
    if r is None or r.status_code != 200:
        return jsonify({'error': f'Gagal mengambil media: {last_err or r.status_code}'}), 502

    ctype = r.headers.get('Content-Type', '')
    payload = r.content
    if not is_media_payload(ctype, payload):
        return jsonify({'error': 'Server mengembalikan bukan file media (login/JSON).'}), 502

    ctype_clean = ctype.split(';')[0].strip().lower() if ctype else ''
    if dl:
        ext = MEDIA_EXT_BY_CTYPE.get(ctype_clean, 'bin')
        resp = Response(payload, mimetype=ctype_clean or 'application/octet-stream')
        resp.headers['Content-Disposition'] = f'attachment; filename="media.{ext}"'
        return resp
    return Response(payload, mimetype=ctype_clean or 'image/jpeg')


# ============================================================================
# AUTH — akun web langsung (signup/login/guest), session token
# ============================================================================
def _auth_payload(user):
    if not user:
        return {'authenticated': False, 'username': 'Tamu', 'is_guest': False}
    return {
        'authenticated': True,
        'username': user['username'],
        'is_guest': bool(user['is_guest']),
    }


@app.route('/api/auth/signup', methods=['POST'])
def api_auth_signup():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    # Username bebas: emoji, simbol, spasi — sekreatif mungkin.
    # Batas wajar: tidak boleh kosong, maks 50 karakter, tanpa karakter kontrol.
    if not username or len(username) > 50:
        return jsonify({'error': 'Username tidak boleh kosong, maksimal 50 karakter.'}), 400
    if any(ord(c) < 32 for c in username):
        return jsonify({'error': 'Username tidak boleh berisi karakter kontrol.'}), 400
    if len(password) < 4:
        return jsonify({'error': 'Password minimal 4 karakter.'}), 400
    exists = db_query("SELECT id FROM users WHERE username=?", (username,))
    if exists:
        return jsonify({'error': 'Username sudah dipakai.'}), 409
    salt, digest = hash_password(password)
    uid = db_exec(
        "INSERT INTO users (username, pass_hash, salt, is_guest, created) VALUES (?,?,?,0,?)",
        (username, digest, salt, time.time()))
    token = create_session(uid)
    return jsonify({'ok': True, 'token': token, **_auth_payload(get_user_by_token(token))})


@app.route('/api/auth/login', methods=['POST'])
def api_auth_login():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    rows = db_query("SELECT * FROM users WHERE username=? AND is_guest=0", (username,))
    if not rows or not verify_password(password, rows[0]['salt'], rows[0]['pass_hash']):
        return jsonify({'error': 'Username atau password salah.'}), 401
    token = create_session(rows[0]['id'])
    return jsonify({'ok': True, 'token': token, **_auth_payload(rows[0])})


@app.route('/api/auth/guest', methods=['POST'])
def api_auth_guest():
    # Akun guest per perangkat: username unik + password acak (tidak bisa login ulang)
    guest_name = 'guest_' + secrets.token_hex(4)
    salt, digest = hash_password(secrets.token_hex(8))
    uid = db_exec(
        "INSERT INTO users (username, pass_hash, salt, is_guest, created) VALUES (?,?,?,1,?)",
        (guest_name, digest, salt, time.time()))
    token = create_session(uid)
    return jsonify({'ok': True, 'token': token, **_auth_payload(get_user_by_token(token))})


@app.route('/api/auth/logout', methods=['POST'])
def api_auth_logout():
    token = request.headers.get('X-Auth-Token') or request.args.get('token')
    if token:
        db_exec("DELETE FROM sessions WHERE token=?", (token,))
    return jsonify({'ok': True})


@app.route('/api/auth/me')
def api_auth_me():
    return jsonify(_auth_payload(_auth_from_request()))


# ============================================================================
# RIWAYAT DOWNLOAD per akun
# ============================================================================
def record_history(user, title, platform, mode, filename, size_mb):
    if not user:
        return
    db_exec(
        "INSERT INTO history (user_id, title, platform, mode, filename, size_mb, created) "
        "VALUES (?,?,?,?,?,?,?)",
        (user['id'], (title or '')[:200], platform or '', mode or '',
         filename or '', size_mb, time.time()))


@app.route('/api/history')
def api_history():
    user = _auth_from_request()
    if not user:
        return jsonify({'error': 'Login dulu untuk melihat riwayat.'}), 401
    rows = db_query(
        "SELECT title, platform, mode, filename, size_mb, created FROM history "
        "WHERE user_id=? ORDER BY id DESC LIMIT 100", (user['id'],))
    return jsonify({'ok': True, 'history': [dict(r) for r in rows]})


@app.route('/api/history', methods=['POST'])
def api_history_add():
    user = _auth_from_request()
    if not user:
        return jsonify({'error': 'Login dulu.'}), 401
    d = request.get_json(silent=True) or {}
    record_history(user, d.get('title'), d.get('platform'), d.get('mode'),
                   d.get('filename'), d.get('size_mb'))
    return jsonify({'ok': True})


# ============================================================================
# CHAT GLOBAL
# ============================================================================
@app.route('/api/chat', methods=['GET'])
def api_chat_get():
    since = 0
    try:
        since = int(request.args.get('since') or 0)
    except ValueError:
        pass
    rows = db_query(
        "SELECT id, username, message, created FROM chat WHERE id>? ORDER BY id ASC LIMIT 200",
        (since,))
    return jsonify({'ok': True, 'messages': [dict(r) for r in rows]})


@app.route('/api/chat', methods=['POST'])
def api_chat_post():
    user = _auth_from_request()
    d = request.get_json(silent=True) or {}
    msg = (d.get('message') or '').strip()[:500]
    if not msg:
        return jsonify({'error': 'Pesan kosong.'}), 400
    username = get_username(user)
    if not user:
        # tamu tanpa akun → tetap boleh, label "Tamu"
        username = 'Tamu'
    # anti-spam: maks 1 pesan / 2 detik per sesi
    db_exec("INSERT INTO chat (user_id, username, message, created) VALUES (?,?,?,?)",
            (user['id'] if user else None, username, msg, time.time()))
    return jsonify({'ok': True, 'username': username})


# ============================================================================
# SARAN PLATFORM (kotak saran global)
# ============================================================================
@app.route('/api/platform-requests', methods=['GET'])
def api_platform_requests():
    rows = db_query(
        "SELECT username, platform, created FROM platform_requests ORDER BY id DESC LIMIT 100")
    return jsonify({'ok': True, 'requests': [dict(r) for r in rows]})


@app.route('/api/platform-requests', methods=['POST'])
def api_platform_request():
    d = request.get_json(silent=True) or {}
    plat = (d.get('platform') or '').strip()[:80]
    if not plat:
        return jsonify({'error': 'Tulis nama platform yang diinginkan.'}), 400
    # anti-duplikat: jangan spam platform yang sama < 10 menit
    recent = db_query(
        "SELECT id FROM platform_requests WHERE platform=? AND created>?",
        (plat, time.time() - 600))
    if recent:
        return jsonify({'error': 'Platform ini sudah diusulkan baru-baru ini.'}), 429
    user = _auth_from_request()
    db_exec("INSERT INTO platform_requests (username, platform, created) VALUES (?,?,?)",
            (get_username(user), plat, time.time()))
    return jsonify({'ok': True})


# ============================================================================
# MANGA — API MangaDex (publik)
# ============================================================================
MANGADEX = 'https://api.mangadex.org'
MANGADEX_HEADERS = {'User-Agent': 'UniversalMediaDownloader/1.0 (publik)'}

# Genre populer untuk filter kategori (ID tag resmi dari /manga/tag)
MANGA_GENRES = [
    {'key': 'action',      'id': '391b0423-d847-456f-aff0-8b0cfc03066b', 'name': 'Action'},
    {'key': 'adventure',   'id': '87cc87cd-a395-47af-b27a-93258283bbc6', 'name': 'Adventure'},
    {'key': 'comedy',      'id': '4d32cc48-9f00-4cca-9b5a-a839f0764984', 'name': 'Comedy'},
    {'key': 'drama',       'id': 'b9af3a63-f058-46de-a9a0-e0c13906197a', 'name': 'Drama'},
    {'key': 'fantasy',     'id': 'cdc58593-87dd-415e-bbc0-2ec27bf404cc', 'name': 'Fantasy'},
    {'key': 'horror',      'id': 'cdad7e68-1419-41dd-bdce-27753074a640', 'name': 'Horror'},
    {'key': 'mystery',     'id': 'ee968100-4191-4968-93d3-f82d72be7e46', 'name': 'Mystery'},
    {'key': 'romance',     'id': '423e2eae-a7a2-4a8b-ac03-a8351462d71d', 'name': 'Romance'},
    {'key': 'scifi',       'id': '256c8bd9-4904-4360-bf4f-508a76d67183', 'name': 'Sci-Fi'},
    {'key': 'sliceoflife', 'id': 'e5301a23-ebd9-49dd-a0cb-2add944c7fe9', 'name': 'Slice of Life'},
    {'key': 'sports',      'id': '69964a64-2f90-4d33-beeb-f3ed2875eb4c', 'name': 'Sports'},
    {'key': 'thriller',    'id': '07251805-a27e-4d59-b488-f0bfbec15168', 'name': 'Thriller'},
    {'key': 'isekai',      'id': 'ace04997-f6bd-436e-b261-779182193d3d', 'name': 'Isekai'},
    {'key': 'historical',  'id': '33771934-028e-4cb3-8744-691e866a923e', 'name': 'Historical'},
    {'key': 'psychological','id': '3b60b75c-a2d7-4860-ab56-05f391bb889c', 'name': 'Psychological'},
    {'key': 'mecha',       'id': '50880a9d-5440-4732-9afb-8f457127e836', 'name': 'Mecha'},
    {'key': 'superhero',   'id': '7064a261-a137-4d3a-8848-2d385de3a99c', 'name': 'Superhero'},
    {'key': 'crime',       'id': '5ca48985-9a9d-4bd8-be29-80dc0303db72', 'name': 'Crime'},
]


def _manga_cover(m):
    """URL cover dari relationships cover_art."""
    for rr in (m.get('relationships') or []):
        if rr.get('type') == 'cover_art':
            fn = (rr.get('attributes') or {}).get('fileName')
            if fn:
                return 'https://uploads.mangadex.org/covers/%s/%s' % (m['id'], fn)
    return None


def _manga_tags(m):
    """Daftar nama genre (tag) dari attributes.tags."""
    out = []
    for t in ((m.get('attributes') or {}).get('tags') or []):
        nm = (t.get('attributes') or {}).get('name') or {}
        out.append(nm.get('en') or next(iter(nm.values()), ''))
    return [x for x in out if x][:3]


def _manga_simple(m):
    attrs = m.get('attributes') or {}
    return {
        'id': m['id'],
        'title': (attrs.get('title') or {}).get('en')
                 or next(iter((attrs.get('title') or {}).values()), 'Untitled'),
        'description': ((attrs.get('description') or {}).get('en') or '')[:200],
        'status': attrs.get('status'),
        'year': attrs.get('year'),
        'cover': _manga_cover(m),
        'tags': _manga_tags(m),
        'rating': attrs.get('contentRating'),
    }


def _manga_search(title=None, tag_id=None, sort='relevance', limit=12):
    """Pencarian manga + rekomendasi (populer/terbaru) + filter genre."""
    params = {
        'limit': limit,
        'includes[]': ['cover_art', 'tag'],
        # aman untuk semua umur — tanpa konten 18+
        'contentRating[]': 'safe',
        # hanya manga yang benar-benar punya chapter terbaca
        'hasAvailableChapters': 'true',
    }
    if title:
        params['title'] = title
        params['order[relevance]'] = 'desc'
    if tag_id:
        params['includedTags[]'] = tag_id
    if sort == 'popular':
        params['order[followedCount]'] = 'desc'
    elif sort == 'latest':
        params['order[latestUploadedChapter]'] = 'desc'
    r = requests.get(MANGADEX + '/manga', params=params,
                     headers=MANGADEX_HEADERS, timeout=20)
    r.raise_for_status()
    return [_manga_simple(m) for m in (r.json().get('data') or [])]


@app.route('/api/manga-genres')
def api_manga_genres():
    return jsonify({'ok': True, 'genres': MANGA_GENRES})


@app.route('/api/manga-recommend')
def api_manga_recommend():
    tag = (request.args.get('tag') or '').strip()
    sort = (request.args.get('sort') or 'popular').strip()
    g = next((x for x in MANGA_GENRES if x['key'] == tag or x['id'] == tag), None)
    try:
        return jsonify({'ok': True, 'results': _manga_search(
            tag_id=(g['id'] if g else None), sort=sort, limit=12)})
    except Exception as e:
        return jsonify({'error': 'MangaDex tidak merespons: %s' % str(e)[:80]}), 502


@app.route('/api/manga-search')
def api_manga_search():
    q = (request.args.get('q') or '').strip()
    tag = (request.args.get('tag') or '').strip()
    sort = (request.args.get('sort') or 'relevance').strip()
    g = next((x for x in MANGA_GENRES if x['key'] == tag or x['id'] == tag), None)
    if not q and not g:
        return jsonify({'error': 'Parameter q atau tag wajib.'}), 400
    try:
        return jsonify({'ok': True, 'results': _manga_search(
            title=(q or None), tag_id=(g['id'] if g else None), sort=sort, limit=12)})
    except Exception as e:
        return jsonify({'error': 'MangaDex tidak merespons: %s' % str(e)[:80]}), 502


@app.route('/api/manga/<mid>')
def api_manga_detail(mid):
    try:
        r = requests.get(MANGADEX + '/manga/' + mid, params={'includes[]': ['cover_art', 'author']},
                         headers=MANGADEX_HEADERS, timeout=20)
        r.raise_for_status()
        m = r.json().get('data') or {}
        attrs = m.get('attributes') or {}
        title = (attrs.get('title') or {}).get('en') or next(iter((attrs.get('title') or {}).values()), 'Untitled')
        cover = None
        for rr in m.get('relationships') or []:
            if rr.get('type') == 'cover_art':
                fn = (rr.get('attributes') or {}).get('fileName')
                if fn:
                    cover = 'https://uploads.mangadex.org/covers/%s/%s' % (mid, fn)
        # Pilihan bahasa: original (bahasa asli manga) / en / id
        # Otomatis mengganti daftar chapter sesuai bahasa yang dipilih.
        lang = (request.args.get('lang') or 'en').strip()
        orig_lang = attrs.get('originalLanguage') or 'ja'
        if lang == 'original':
            want = [orig_lang]
        elif lang == 'all':
            want = None
        else:
            want = [lang] if lang in ('en', 'id') else ['en']

        # daftar chapter sesuai bahasa pilihan
        def fetch_feed(langs):
            params = {'order[chapter]': 'asc', 'limit': 500}
            if langs:
                params['translatedLanguage[]'] = langs
            r = requests.get(MANGADEX + '/manga/' + mid + '/feed', params=params,
                             headers=MANGADEX_HEADERS, timeout=25)
            r.raise_for_status()
            return r.json().get('data') or []

        raw = fetch_feed(want)
        chapters = []
        for ch in raw:
            ca = ch.get('attributes') or {}
            # lewati chapter external (link luar, tanpa halaman) & yang tak tersedia
            if ca.get('externalUrl') or ca.get('isUnavailable'):
                continue
            pages = ca.get('pages') or 0
            if pages <= 0:
                continue
            chapters.append({
                'id': ch['id'],
                'chapter': ca.get('chapter'),
                'title': ca.get('title'),
                'lang': ca.get('translatedLanguage'),
                'pages': pages,
            })
        # sort by chapter number, lalu bahasa
        chapters.sort(key=lambda c: (float(c['chapter'] or 0),
                                     c['lang'] or ''))
        return jsonify({'ok': True, 'id': mid, 'title': title,
                        'cover': cover, 'status': attrs.get('status'),
                        'description': ((attrs.get('description') or {}).get('en') or ''),
                        'lang': lang, 'original_language': orig_lang,
                        'chapters': chapters[:400]})
    except Exception as e:
        return jsonify({'error': 'Gagal ambil manga: %s' % str(e)[:80]}), 502


@app.route('/api/manga/read/<cid>')
def api_manga_read(cid):
    """Ambil URL gambar chapter dari MangaDex (at-home/server)."""
    try:
        r = requests.get(MANGADEX + '/at-home/server/' + cid,
                         headers=MANGADEX_HEADERS, timeout=25)
        r.raise_for_status()
        d = r.json()
        base = d.get('baseUrl') or 'https://uploads.mangadex.org'
        chapter = d.get('chapter') or {}
        hash_ = chapter.get('hash')
        files = chapter.get('data') or chapter.get('dataSaver') or []
        pages = [{'url': '%s/data/%s/%s' % (base, hash_, f), 'file': f} for f in files]
        return jsonify({'ok': True, 'pages': pages,
                        'title': chapter.get('title')})
    except Exception as e:
        return jsonify({'error': 'Gagal buka chapter: %s' % str(e)[:80]}), 502


# Proxy gambar manga (biar aman dari hotlink & dimuat di <img>)
@app.route('/api/manga-img')
def api_manga_img():
    url = request.args.get('url') or ''
    if not (url.startswith(('https://uploads.mangadex.org', 'https://mangadex.org',
                            'https://api.mangadex.org', 'https://uploads.mangadex'))
            or '.mangadex.network' in url or 'mangadex.org' in url):
        return jsonify({'error': 'URL tidak diizinkan.'}), 400
    try:
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://mangadex.org/'},
                         timeout=25)
        ct = (r.headers.get('Content-Type') or 'image/jpeg').split(';')[0]
        return Response(r.content, mimetype=ct)
    except Exception:
        return jsonify({'error': 'Gagal ambil gambar.'}), 502


# ============================================================================
# BERITA — agregator RSS multi-sumber & multi-kategori (live)
# ============================================================================
NEWS_CATEGORIES = {
    'indonesia': 'Indonesia',
    'internasional': 'Internasional',
    'teknologi': 'Teknologi',
    'ekonomi': 'Ekonomi',
    'olahraga': 'Olahraga',
    'hiburan': 'Hiburan',
}
NEWS_SOURCES = [
    {'key': 'cnn_id', 'name': 'CNN Indonesia', 'cat': 'indonesia',
     'feeds': {
        'indonesia': 'https://www.cnnindonesia.com/nasional/rss',
        'internasional': 'https://www.cnnindonesia.com/internasional/rss',
        'teknologi': 'https://www.cnnindonesia.com/teknologi/rss',
        'ekonomi': 'https://www.cnnindonesia.com/ekonomi/rss',
        'olahraga': 'https://www.cnnindonesia.com/olahraga/rss',
        'hiburan': 'https://www.cnnindonesia.com/hiburan/rss'}},
    {'key': 'antara', 'name': 'Antara News', 'cat': 'indonesia',
     'feeds': {
        'indonesia': 'https://www.antaranews.com/rss/terkini',
        'internasional': 'https://www.antaranews.com/rss/dunia',
        'teknologi': 'https://www.antaranews.com/rss/tekno',
        'ekonomi': 'https://www.antaranews.com/rss/ekonomi',
        'olahraga': 'https://www.antaranews.com/rss/olahraga',
        'hiburan': 'https://www.antaranews.com/rss/hiburan'}},
    {'key': 'inews', 'name': 'iNews', 'cat': 'indonesia',
     'feeds': {'indonesia': 'https://www.inews.id/feed'}},
    {'key': 'bbc', 'name': 'BBC News', 'cat': 'internasional',
     'feeds': {
        'internasional': 'https://feeds.bbci.co.uk/news/world/rss.xml',
        'teknologi': 'https://feeds.bbci.co.uk/news/technology/rss.xml',
        'ekonomi': 'https://feeds.bbci.co.uk/news/business/rss.xml',
        'olahraga': 'https://feeds.bbci.co.uk/sport/rss.xml'}},
    {'key': 'cnn', 'name': 'CNN (US)', 'cat': 'internasional',
     'feeds': {
        'internasional': 'http://rss.cnn.com/rss/cnn_world.rss',
        'teknologi': 'http://rss.cnn.com/rss/cnn_tech.rss'}},
    {'key': 'guardian', 'name': 'The Guardian', 'cat': 'internasional',
     'feeds': {
        'internasional': 'https://www.theguardian.com/world/rss',
        'teknologi': 'https://www.theguardian.com/technology/rss',
        'ekonomi': 'https://www.theguardian.com/uk/business/rss',
        'olahraga': 'https://www.theguardian.com/uk/sport/rss'}},
    {'key': 'nyt', 'name': 'NY Times', 'cat': 'internasional',
     'feeds': {
        'internasional': 'https://rss.nytimes.com/services/xml/rss/nyt/World.xml',
        'teknologi': 'https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml',
        'ekonomi': 'https://rss.nytimes.com/services/xml/rss/nyt/Business.xml'}},
    {'key': 'france24', 'name': 'France 24', 'cat': 'internasional',
     'feeds': {'internasional': 'https://www.france24.com/en/rss'}},
    {'key': 'theverge', 'name': 'The Verge', 'cat': 'teknologi',
     'feeds': {'teknologi': 'https://www.theverge.com/rss/index.xml'}},
    {'key': 'wired', 'name': 'Wired', 'cat': 'teknologi',
     'feeds': {'teknologi': 'https://www.wired.com/feed/rss'}},
    {'key': 'ars', 'name': 'Ars Technica', 'cat': 'teknologi',
     'feeds': {'teknologi': 'https://feeds.arstechnica.com/arstechnica/index'}},
    {'key': 'engadget', 'name': 'Engadget', 'cat': 'teknologi',
     'feeds': {'teknologi': 'https://www.engadget.com/rss.xml'}},
    {'key': 'cnbc', 'name': 'CNBC Indonesia', 'cat': 'ekonomi',
     'feeds': {'ekonomi': 'https://www.cnbcindonesia.com/rss'}},
    {'key': 'skysports', 'name': 'Sky Sports', 'cat': 'olahraga',
     'feeds': {'olahraga': 'https://www.skysports.com/rss/12040'}},
    {'key': 'kapanlagi', 'name': 'KapanLagi', 'cat': 'hiburan',
     'feeds': {'hiburan': 'https://www.kapanlagi.com/feed'}},
]
NEWS_CACHE = {}
NEWS_IMG_CACHE = {}
NEWS_CACHE_LOCK = threading.Lock()
NEWS_TTL = 5 * 60


def _rss_local(tag):
    return tag.rsplit('}', 1)[-1].lower()


def _parse_news_date(s):
    s = (s or '').strip()
    if not s:
        return None
    try:
        return int(parsedate_to_datetime(s).timestamp())
    except Exception:
        pass
    try:
        dt = datetime.datetime.fromisoformat(s.replace('Z', '+00:00'))
        return int(dt.timestamp())
    except Exception:
        return None


def _item_img(it):
    # 1) media:content / media:thumbnail / enclosure (namespace aman)
    for ch in it.iter():
        t = _rss_local(ch.tag)
        if t in ('content', 'thumbnail'):
            u = ch.get('url')
            if u and u.startswith('http'):
                return u
        if t == 'enclosure':
            u = ch.get('url')
            if u and 'image' in (ch.get('type') or '').lower():
                return u
    # 2) <img> pertama di dalam description/summary/content
    for ch in it:
        if _rss_local(ch.tag) in ('description', 'summary', 'content'):
            m = re.search(r'<img[^>]+src=["\']([^"\']+)', ch.text or '')
            if m:
                return m.group(1)
    return None


def _fetch_rss(url):
    try:
        r = requests.get(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36'},
            timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        items = []
        for it in root.iter():
            if _rss_local(it.tag) not in ('item', 'entry'):
                continue

            def txt(tag):
                for ch in it:
                    if _rss_local(ch.tag) == tag.lower():
                        return ch.text or ''
                return ''

            title = (txt('title') or '').strip() or 'Tanpa judul'
            link = txt('link')
            if not link:
                for ch in it:
                    if _rss_local(ch.tag) == 'link' and ch.get('href'):
                        link = ch.get('href')
                        break
            desc = (txt('description') or txt('summary') or '').strip()
            desc = re.sub(r'<[^>]+>', '', desc)[:240]
            img = _item_img(it)
            # Setiap berita WAJIB punya gambar → lewati yang tidak ada
            if not img:
                continue
            pub_raw = txt('pubDate') or txt('published') or txt('updated')
            items.append({
                'title': title, 'link': link, 'desc': desc,
                'pub': pub_raw, 'ts': _parse_news_date(pub_raw), 'img': img,
            })
            if len(items) >= 30:
                break
        return items
    except Exception:
        return []


def _rss_cached(cache_key, url):
    with NEWS_CACHE_LOCK:
        hit = NEWS_CACHE.get(cache_key)
    if hit and (time.time() - hit[0]) < NEWS_TTL:
        return hit[1]
    items = _fetch_rss(url)
    with NEWS_CACHE_LOCK:
        NEWS_CACHE[cache_key] = (time.time(), items)
    return items


@app.route('/api/news-sources')
def api_news_sources():
    cats = {}
    for cat, label in NEWS_CATEGORIES.items():
        lst = [{'key': s['key'], 'name': s['name']} for s in NEWS_SOURCES if cat in s['feeds']]
        if lst:
            cats[cat] = {'label': label, 'sources': lst}
    return jsonify({'ok': True, 'categories': cats})


@app.route('/api/news')
def api_news():
    source = (request.args.get('source') or '').strip()
    category = (request.args.get('category') or '').strip()
    if category not in NEWS_CATEGORIES:
        category = 'indonesia'
    q = (request.args.get('q') or '').strip().lower()
    now = int(time.time())

    # mode "Semua sumber" = gabungkan semua feed di kategori itu → feed terbaru live
    if not source or source == 'all':
        srcs = [s for s in NEWS_SOURCES if category in s['feeds']]

        def fetch_one(s):
            url = s['feeds'].get(category) or next(iter(s['feeds'].values()))
            try:
                items = _rss_cached(s['key'] + '|' + category, url)
            except Exception:
                items = []
            out = []
            for it in items:
                item = dict(it)
                item['source'] = s['name']
                out.append(item)
            return out

        # Ambil semua feed SECARA PARALEL (dulu berurutan → kategori dengan 10
        # sumber bisa makan waktu >100 detik & timeout). Fail-cepat per sumber:
        # satu feed gagal tidak menahan yang lain.
        try:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(len(srcs), 8)) as pool:
                chunks = list(pool.map(fetch_one, srcs))
        except Exception:
            chunks = [fetch_one(s) for s in srcs]
        merged, seen = [], set()
        for chunk in chunks:
            for it in chunk:
                key = (it.get('title') or '').strip().lower()
                if key and key in seen:
                    continue
                seen.add(key)
                merged.append(it)
        merged.sort(key=lambda x: x.get('ts') or 0, reverse=True)
        if q:
            merged = [x for x in merged if q in (x.get('title') or '').lower()
                      or q in (x.get('desc') or '').lower()]
        return jsonify({'ok': True, 'source': 'Semua sumber', 'category': category,
                        'updated_at': now, 'items': merged[:60]})

    src = next((s for s in NEWS_SOURCES if s['key'] == source), NEWS_SOURCES[0])
    feed_url = src['feeds'].get(category) or next(iter(src['feeds'].values()))
    items = _rss_cached(src['key'] + '|' + category, feed_url)
    if q:
        items = [x for x in items if q in (x.get('title') or '').lower()
                 or q in (x.get('desc') or '').lower()]
    return jsonify({'ok': True, 'source': src['name'], 'category': category,
                    'updated_at': now, 'items': items})


# Proxy gambar berita (biar selalu tampil, aman dari hotlink)
@app.route('/api/news-img')
def api_news_img():
    url = request.args.get('url') or ''
    if not url.startswith(('http://', 'https://')):
        return jsonify({'error': 'URL tidak valid.'}), 400
    host = urllib.parse.urlparse(url).hostname or ''
    if host in ('localhost', '127.0.0.1', '0.0.0.0', '::1') or host.endswith(('.local', '.internal')):
        return jsonify({'error': 'URL dilarang.'}), 400
    try:
        import socket
        ip = socket.gethostbyname(host)
        if ip.startswith(('10.', '192.168.', '172.16.', '172.17.', '172.18.', '172.19.',
                          '172.20.', '172.21.', '172.22.', '172.23.', '172.24.', '172.25.',
                          '172.26.', '172.27.', '172.28.', '172.29.', '172.30.', '172.31.',
                          '127.', '169.254.')) or ip == '0.0.0.0':
            return jsonify({'error': 'URL dilarang.'}), 400
    except Exception:
        pass
    with NEWS_CACHE_LOCK:
        hit = NEWS_IMG_CACHE.get(url)
    if hit and (time.time() - hit[0]) < 1800:
        return Response(hit[1], mimetype=hit[2])
    try:
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://news.google.com/'},
                         timeout=15)
        ct = (r.headers.get('Content-Type') or 'image/jpeg').split(';')[0]
        if not ct.startswith('image/'):
            return jsonify({'error': 'Bukan gambar.'}), 502
        body = r.content
        if len(NEWS_IMG_CACHE) > 400:
            NEWS_IMG_CACHE.clear()
        with NEWS_CACHE_LOCK:
            NEWS_IMG_CACHE[url] = (time.time(), body, ct)
        return Response(body, mimetype=ct)
    except Exception:
        return jsonify({'error': 'Gagal ambil gambar.'}), 502


# ============================================================================
# LIRIK — pencarian lirik lagu (Spotify-like)
# ============================================================================
LYRICS_CACHE = {}
LYRICS_CACHE_LOCK = threading.Lock()
LYRICS_TTL = 24 * 3600


@app.route('/api/music-lyrics')
def api_music_lyrics():
    title = (request.args.get('title') or '').strip()
    artist = (request.args.get('artist') or '').strip()
    if not title:
        return jsonify({'error': 'Judul lagu wajib.'}), 400
    key = (artist + '|' + title).lower()
    with LYRICS_CACHE_LOCK:
        hit = LYRICS_CACHE.get(key)
    if hit and (time.time() - hit[0]) < LYRICS_TTL:
        return jsonify(hit[1])
    out = {'ok': True, 'found': False, 'lyrics': None, 'synced': None, 'source': ''}

    def lrclib_plain(t, a):
        try:
            r = requests.get('https://lrclib.net/api/get', params={
                'artist_name': a, 'track_name': t,
            }, headers={'User-Agent': 'UniversalMediaDownloader/1.0 (publik)'}, timeout=12)
            if r.status_code == 200:
                d = r.json()
                return d.get('plainLyrics') or d.get('syncedLyrics'), bool(d.get('syncedLyrics'))
        except Exception:
            pass
        return None, False

    def lrclib_search(t, a):
        try:
            r = requests.get('https://lrclib.net/api/search', params={
                'q': (t + ' ' + a).strip(), 'track_name': t,
            }, headers={'User-Agent': 'UniversalMediaDownloader/1.0 (publik)'}, timeout=12)
            if r.status_code == 200:
                arr = r.json()
                if arr:
                    d = arr[0]
                    return d.get('plainLyrics') or d.get('syncedLyrics'), bool(d.get('syncedLyrics'))
        except Exception:
            pass
        return None, False

    def ovh_plain(t, a):
        try:
            r = requests.get('https://api.lyrics.ovh/v1/' + a + '/' + t, timeout=12)
            if r.status_code == 200:
                d = r.json()
                if d.get('lyrics'):
                    return d['lyrics'], False
        except Exception:
            pass
        return None, False

    for fn in (lambda: lrclib_plain(title, artist),
               lambda: lrclib_search(title, artist),
               lambda: ovh_plain(title, artist)):
        try:
            ly, synced = fn()
            if ly and ly.strip():
                out['found'] = True
                out['synced'] = synced
                out['lyrics'] = ly.strip()
                out['source'] = 'lrclib' if 'lrclib' in str(fn) else 'ovh'
                break
        except Exception:
            continue
    with LYRICS_CACHE_LOCK:
        if len(LYRICS_CACHE) > 500:
            LYRICS_CACHE.clear()
        LYRICS_CACHE[key] = (time.time(), out)
    return jsonify(out)


# ============================================================================
# PLAYLIST — playlist musik per akun (Spotify-like)
# ============================================================================
def _auth_user_row():
    """User (Row) dari token request, atau None kalau tidak login."""
    try:
        return _auth_from_request()
    except Exception:
        return None


@app.route('/api/playlists', methods=['GET', 'POST'])
def api_playlists():
    user = _auth_user_row()
    if not user:
        return jsonify({'error': 'Login dulu untuk pakai playlist.'}), 401
    if request.method == 'GET':
        rows = db_query(
            "SELECT p.id, p.name, p.created, "
            "(SELECT COUNT(*) FROM playlist_items i WHERE i.playlist_id=p.id) AS cnt "
            "FROM playlists p WHERE p.user_id=? ORDER BY p.created DESC", (user['id'],))
        return jsonify({'ok': True, 'playlists': [dict(r) for r in rows]})
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()[:60]
    if not name:
        return jsonify({'error': 'Nama playlist wajib diisi.'}), 400
    pid = db_exec("INSERT INTO playlists (user_id, name, created) VALUES (?,?,?)",
                  (user['id'], name, time.time()))
    return jsonify({'ok': True, 'id': pid})


@app.route('/api/playlists/<int:pid>', methods=['GET', 'DELETE'])
def api_playlist_detail(pid):
    user = _auth_user_row()
    if not user:
        return jsonify({'error': 'Login dulu untuk pakai playlist.'}), 401
    if request.method == 'DELETE':
        db_exec("DELETE FROM playlists WHERE id=? AND user_id=?", (pid, user['id']))
        db_exec("DELETE FROM playlist_items WHERE playlist_id=?", (pid,))
        return jsonify({'ok': True})
    row = db_query("SELECT * FROM playlists WHERE id=? AND user_id=?", (pid, user['id']))
    if not row:
        return jsonify({'error': 'Playlist tidak ditemukan.'}), 404
    items = db_query(
        "SELECT id, video_id, title, artist, thumbnail FROM playlist_items "
        "WHERE playlist_id=? ORDER BY pos", (pid,))
    return jsonify({'ok': True, 'playlist': dict(row[0]),
                    'items': [dict(i) for i in items]})


@app.route('/api/playlists/<int:pid>/items', methods=['POST'])
def api_playlist_add(pid):
    user = _auth_user_row()
    if not user:
        return jsonify({'error': 'Login dulu untuk pakai playlist.'}), 401
    row = db_query("SELECT id FROM playlists WHERE id=? AND user_id=?", (pid, user['id']))
    if not row:
        return jsonify({'error': 'Playlist tidak ditemukan.'}), 404
    data = request.get_json(silent=True) or {}
    video_id = (data.get('video_id') or '').strip()
    if not video_id:
        return jsonify({'error': 'Lagu tidak valid.'}), 400
    exists = db_query("SELECT id FROM playlist_items WHERE playlist_id=? AND video_id=?",
                      (pid, video_id))
    if exists:
        return jsonify({'ok': True, 'duplicate': True})
    pos = db_query("SELECT COALESCE(MAX(pos),0)+1 AS p FROM playlist_items WHERE playlist_id=?", (pid,))[0]['p']
    db_exec("INSERT INTO playlist_items (playlist_id, video_id, title, artist, thumbnail, pos, created) "
            "VALUES (?,?,?,?,?,?,?)",
            (pid, video_id, (data.get('title') or '')[:200], (data.get('artist') or '')[:200],
             (data.get('thumbnail') or '')[:500], pos, time.time()))
    return jsonify({'ok': True})


@app.route('/api/playlists/<int:pid>/items/<int:iid>', methods=['DELETE'])
def api_playlist_remove(pid, iid):
    user = _auth_user_row()
    if not user:
        return jsonify({'error': 'Login dulu untuk pakai playlist.'}), 401
    row = db_query("SELECT id FROM playlists WHERE id=? AND user_id=?", (pid, user['id']))
    if not row:
        return jsonify({'error': 'Playlist tidak ditemukan.'}), 404
    db_exec("DELETE FROM playlist_items WHERE id=? AND playlist_id=?", (iid, pid))
    return jsonify({'ok': True})


# ============================================================================
# MANGA HISTORY — riwayat baca manga per akun
# ============================================================================
@app.route('/api/manga/history', methods=['GET', 'POST'])
def api_manga_history():
    user = _auth_user_row()
    if not user:
        return jsonify({'error': 'Login dulu untuk simpan riwayat baca.'}), 401
    if request.method == 'GET':
        rows = db_query(
            "SELECT manga_id, title, cover, chapter, chapter_id, lang, created "
            "FROM manga_history WHERE user_id=? ORDER BY created DESC LIMIT 20",
            (user['id'],))
        return jsonify({'ok': True, 'items': [dict(r) for r in rows]})
    data = request.get_json(silent=True) or {}
    manga_id = (data.get('manga_id') or '').strip()
    if not manga_id:
        return jsonify({'error': 'ID manga wajib.'}), 400
    title = (data.get('title') or 'Manga')[:200]
    cover = (data.get('cover') or '')[:500]
    chapter = str(data.get('chapter') or '?')[:20]
    chapter_id = (data.get('chapter_id') or '')[:64]
    lang = (data.get('lang') or '')[:10]
    db_exec(
        "INSERT INTO manga_history (user_id, manga_id, title, cover, chapter, chapter_id, lang, created) "
        "VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(user_id, manga_id) DO UPDATE SET "
        "title=excluded.title, cover=excluded.cover, chapter=excluded.chapter, "
        "chapter_id=excluded.chapter_id, lang=excluded.lang, created=excluded.created",
        (user['id'], manga_id, title, cover, chapter, chapter_id, lang, time.time()))
    return jsonify({'ok': True})


@app.route('/api/manga/history/clear', methods=['POST'])
def api_manga_history_clear():
    user = _auth_user_row()
    if not user:
        return jsonify({'error': 'Login dulu.'}), 401
    db_exec("DELETE FROM manga_history WHERE user_id=?", (user['id'],))
    return jsonify({'ok': True})


# ============================================================================
# FEEDBACK BUG — lapor bug / saran dari pengguna
# ============================================================================
@app.route('/api/feedback', methods=['GET', 'POST'])
def api_feedback():
    if request.method == 'GET':
        rows = db_query(
            "SELECT username, message, page, created FROM feedback "
            "ORDER BY created DESC LIMIT 30")
        return jsonify({'ok': True, 'items': [dict(r) for r in rows]})
    data = request.get_json(silent=True) or {}
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({'error': 'Tulis pesan dulu.'}), 400
    if len(message) > 2000:
        return jsonify({'error': 'Pesan terlalu panjang (maks 2000 karakter).'}), 400
    user = None
    try:
        user = _auth_from_request()
    except Exception:
        pass
    username = (user['username'] if user else 'Tamu')
    user_id = user['id'] if user else None
    page = (data.get('page') or '')[:40]
    db_exec("INSERT INTO feedback (user_id, username, message, page, created) VALUES (?,?,?,?,?)",
            (user_id, username, message, page, time.time()))
    return jsonify({'ok': True, 'message': 'Terima kasih! Laporanmu sudah masuk. 🙏'})


if __name__ == '__main__':
    print(f"yt-dlp {yt_dlp.version.__version__} — KINGS DOWNLOADER")
    print(f"ffmpeg tersedia: {bool(shutil.which('ffmpeg'))}")
    app.run(host='0.0.0.0', port=5000, debug=True)
