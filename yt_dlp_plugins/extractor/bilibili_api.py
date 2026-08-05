# -*- coding: utf-8 -*-
"""
Ekstraktor Bilibili TANPA webpage (anti-cookie).

Kenapa ada plugin ini?
----------------------
Ekstraktor bawaan yt-dlp (BiliBiliIE) MEMBUTUHKAN download halaman
www.bilibili.com/video/... untuk membaca `window.__INITIAL_STATE__`.
Dari IP server (datacenter) halaman itu sering kena HTTP 412
(Precondition Failed) — blokir WAF Bilibili. Tapi API publik
`api.bilibili.com` TETAP bisa diakses dari IP yang sama.

Plugin ini memakai API langsung:
  1. GET /x/web-interface/view?bvid=... | ?aid=...   → info video (judul,
     cid, durasi, uploader, thumbnail, daftar halaman)
  2. GET /x/player/playurl?bvid=&cid=&qn=127&fnval=4048 → stream DASH
     (video + audio) yang bisa diunduh & digabung dengan ffmpeg.

Tanpa cookie login, tanpa webpage. Header Referer/Origin bilibili dipakai
supaya CDN menerima permintaan (syarat resmi anti-hotlink Bilibili).
"""
import re

from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import ExtractorError

__all__ = ['BiliBiliApiIE']

_HEADERS = {
    'Referer': 'https://www.bilibili.com/',
    'Origin': 'https://www.bilibili.com',
}


class BiliBiliApiIE(InfoExtractor):
    IE_NAME = 'bilibili_api'
    IE_DESC = 'Bilibili via API (tanpa webpage, anti-blokir 412)'
    _VALID_URL = r'https?://(?:(?:www|m)\.)?bilibili\.com/video/(?P<id>a[vV]\d+|BV[0-9A-Za-z]+)'

    _TESTS = []

    def _real_extract(self, url):
        video_id = self._match_id(url)
        vid = video_id.upper() if video_id[:2].upper() == 'BV' else video_id

        # 1) Info video
        if vid.startswith('BV'):
            view_query = {'bvid': vid}
        else:
            view_query = {'aid': vid[2:]}
        view = self._download_json(
            'https://api.bilibili.com/x/web-interface/view', video_id,
            query=view_query, headers=_HEADERS,
            note='Memuat info video Bilibili')
        if view.get('code') != 0:
            raise ExtractorError(
                'Bilibili: %s' % (view.get('message') or 'gagal memuat info'),
                expected=True)
        data = view['data'] or {}
        bvid = data.get('bvid') or vid
        title = data.get('title') or 'Untitled'
        pages = data.get('pages') or []
        desc = data.get('desc') or ''
        stat = data.get('stat') or {}
        owner = data.get('owner') or {}

        # 2) Halaman (anthology / multi-bagian). ?p=N → bagian N.
        part_id = 1
        m = re.search(r'[?&]p=(\d+)', url)
        if m:
            try:
                part_id = max(1, int(m.group(1)))
            except ValueError:
                part_id = 1
        if pages and part_id > len(pages):
            part_id = 1
        page = pages[part_id - 1] if pages else {}
        cid = page.get('cid') or data.get('cid')
        duration = page.get('duration') or data.get('duration')
        if len(pages) > 1:
            part_name = page.get('part') or ''
            title = '%s p%02d %s' % (title, part_id, part_name)
        entry_id = '%s_p%d' % (bvid, part_id)

        # 3) Stream DASH
        play = self._download_json(
            'https://api.bilibili.com/x/player/playurl', entry_id,
            query={'bvid': bvid, 'cid': cid, 'qn': 127, 'fnval': 4048, 'fourk': 1},
            headers=_HEADERS, note='Mengambil stream Bilibili')
        if play.get('code') != 0:
            raise ExtractorError(
                'Bilibili: %s' % (play.get('message') or 'stream tidak tersedia'),
                expected=True)
        pdata = play.get('data') or {}

        formats = []
        seen = set()
        for v in (pdata.get('dash') or {}).get('video') or []:
            u = v.get('baseUrl') or v.get('base_url')
            if not u or u in seen:
                continue
            seen.add(u)
            h = v.get('height') or 0
            fps_raw = v.get('frameRate') or v.get('frame_rate')
            fps = None
            try:
                fps = float(fps_raw)
            except (TypeError, ValueError):
                fps = None
            formats.append({
                'url': u,
                'ext': 'mp4',
                'vcodec': v.get('codecs'),
                'acodec': 'none',
                'width': v.get('width'),
                'height': h,
                'fps': fps,
                'format_id': 'bv-%d' % h,
                'format_note': '%dp' % h,
                'tbr': round((v.get('bandwidth') or 0) / 1000) or None,
                'http_headers': _HEADERS,
            })
        for a in (pdata.get('dash') or {}).get('audio') or []:
            u = a.get('baseUrl') or a.get('base_url')
            if not u or u in seen:
                continue
            seen.add(u)
            formats.append({
                'url': u,
                'ext': 'm4a',
                'vcodec': 'none',
                'acodec': a.get('codecs') or 'mp4a',
                'format_id': 'ba',
                'format_note': 'audio',
                'tbr': round((a.get('bandwidth') or 0) / 1000) or None,
                'http_headers': _HEADERS,
            })
        # durl (fallback bila tanpa DASH — biasanya untuk kualitas rendah)
        for i, du in enumerate(pdata.get('durl') or []):
            u = du.get('url')
            if not u:
                continue
            formats.append({
                'url': u,
                'ext': 'mp4',
                'vcodec': 'avc1',
                'acodec': 'mp4a' if i == 0 else 'none',
                'format_id': 'durl-%d' % i,
                'filesize': du.get('size'),
                'http_headers': _HEADERS,
            })

        if not formats:
            raise ExtractorError('Bilibili: tidak ada stream yang bisa diunduh.',
                                 expected=True)

        return {
            'id': entry_id,
            'title': title,
            'description': desc,
            'uploader': owner.get('name'),
            'uploader_id': str(owner.get('mid') or ''),
            'thumbnail': data.get('pic'),
            'duration': duration,
            'view_count': stat.get('view'),
            'like_count': stat.get('like'),
            'comment_count': stat.get('reply'),
            'webpage_url': 'https://www.bilibili.com/video/%s' % bvid,
            'formats': formats,
        }
