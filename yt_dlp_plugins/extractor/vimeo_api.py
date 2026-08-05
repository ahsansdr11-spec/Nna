# -*- coding: utf-8 -*-
"""
Ekstraktor Vimeo — TANPA OAuth (bypass client creds yang sudah di-revoke).

Kenapa plugin ini?
------------------
Ekstraktor bawaan yt-dlp (VimeoIE) meminta OAuth token via
api.vimeo.com/oauth/authorize/client dengan client credentials (macos/ios)
yang SUDAH di-revoke Vimeo — dari IP server selalu HTTP 401.

Solusi: video publik Vimeo tetap menyediakan player config di halaman
player.vimeo.com (dengan hash). Plugin ini:
  1. Buka halaman vimeo.com/{id} → ambil URL player (dengan ?h=...)
  2. Buka player page → parse window.playerConfig (brace-matching)
  3. Ambil file progressive (MP4 240p-720p+) & DASH → formats yt-dlp

Tanpa cookie, tanpa OAuth, tanpa login.
"""
import json
import re

from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import ExtractorError

__all__ = ['VimeoApiIE']

_HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/126.0.0.0 Safari/537.36'),
}


def _extract_braced(text, start):
    """Ambil objek JSON {..} mulai dari index 'start' dengan brace matching."""
    depth = 0
    for k in range(start, len(text)):
        c = text[k]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return text[start:k + 1]
    return None


class VimeoApiIE(InfoExtractor):
    IE_NAME = 'vimeo_api'
    IE_DESC = 'Vimeo via player config (tanpa OAuth)'
    _VALID_URL = r'https?://(?:www\.|player\.)?vimeo\.com/(?:video/)?(?P<id>\d+)'
    _TESTS = []

    def _real_extract(self, url):
        video_id = self._match_id(url)

        # 1) Halaman utama → cari URL player (bisa punya ?h= hash)
        main = self._download_webpage(
            'https://vimeo.com/%s' % video_id, video_id,
            headers=_HEADERS, note='Memuat halaman Vimeo')
        m = re.search(r'https://player\.vimeo\.com/video/\d+[^"\']*', main)
        if not m:
            raise ExtractorError('Vimeo: player URL tidak ditemukan.',
                                 expected=True)
        player_url = m.group(0)

        # 2) Player page → playerConfig
        page = self._download_webpage(
            player_url, video_id, headers=_HEADERS,
            note='Memuat konfigurasi pemutar Vimeo')
        i = page.find('window.playerConfig')
        if i < 0:
            i = page.find('playerConfig')
        j = page.find('{', i)
        raw = _extract_braced(page, j)
        if not raw:
            raise ExtractorError('Vimeo: playerConfig tidak ditemukan.',
                                 expected=True)
        try:
            config = json.loads(raw)
        except Exception as e:
            raise ExtractorError('Vimeo: gagal parse playerConfig (%s)' % e,
                                 expected=True)

        request = config.get('request') or {}
        video = config.get('video') or {}
        files = request.get('files') or {}

        # 3) Formats
        formats = []
        seen = set()
        for p in files.get('progressive') or []:
            u = p.get('url')
            if not u or u in seen:
                continue
            seen.add(u)
            q = p.get('quality') or ''
            height = int(re.sub(r'[^0-9]', '', q) or 0) or None
            formats.append({
                'url': u,
                'ext': 'mp4',
                'format_id': 'progressive-%s' % (q or 'sd'),
                'format_note': q,
                'vcodec': 'avc1',
                'acodec': 'mp4a',
                'height': height,
                'filesize': p.get('size'),
                'http_headers': _HEADERS,
            })

        dash = files.get('dash') or {}
        cdns = dash.get('cdns') or {}
        default_cdn = files.get('default_cdn')
        cdn = cdns.get(default_cdn) or (next(iter(cdns.values())) if cdns else None)
        if cdn and cdn.get('avc_url'):
            formats.append({
                'url': cdn['avc_url'],
                'ext': 'mp4',
                'format_id': 'dash-avc',
                'format_note': 'DASH (video)',
                'vcodec': 'avc1',
                'acodec': 'none',
                'http_headers': _HEADERS,
            })
        if cdn and cdn.get('hls_url'):
            formats.append({
                'url': cdn['hls_url'],
                'ext': 'mp4',
                'format_id': 'hls',
                'format_note': 'HLS',
                'http_headers': _HEADERS,
            })

        if not formats:
            raise ExtractorError('Vimeo: tidak ada stream yang bisa diunduh.',
                                 expected=True)

        # 4) Metadata
        title = (video.get('title')
                 or self._html_search_meta('og:title', main, default=None)
                 or 'Video Vimeo %s' % video_id)
        owner = video.get('owner') or {}
        thumbs = video.get('thumbs') or {}
        thumb = (thumbs.get('base') or thumbs.get('720')
                 or self._html_search_meta('og:image', main, default=None))
        duration = video.get('duration')
        if duration is None:
            duration = request.get('duration')

        return {
            'id': video_id,
            'title': title,
            'uploader': owner.get('name'),
            'description': video.get('description'),
            'thumbnail': thumb,
            'duration': duration,
            'view_count': video.get('stats', {}).get('plays'),
            'webpage_url': 'https://vimeo.com/%s' % video_id,
            'formats': formats,
        }
