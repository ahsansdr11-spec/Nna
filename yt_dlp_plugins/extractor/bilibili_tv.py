# -*- coding: utf-8 -*-
"""
Ekstraktor Bilibili TV (bilibili.tv / biliintl.com) — versi internasional.

Kenapa ada plugin ini?
----------------------
Ekstraktor bawaan yt-dlp (BiliIntlIE) untuk bilibili.tv:
  1. Mengandalkan `window.__INITIAL_DATA__` di halaman — tapi halaman sekarang
     pakai SPA yang membungkus state dalam IIFE, sehingga ekstraktor crash
     ("'NoneType' object is not subscriptable").
  2. Memanggil API playurl dengan `platform=web` — dari IP server (datacenter)
     itu kena "版权地区受限" (copyright region restricted) untuk SEMUA video.

Solusi plugin ini:
  • Ambil metadata dari meta og: (title / thumbnail / deskripsi) — andal.
  • Panggil API playurl dengan `platform=android` — dari IP yang sama INI
    TEMBUS (region lock web tidak berlaku untuk client android).
  • Jika tetap kena region lock, beri pesan jelas (bukan crash generik).

Tanpa cookie, tanpa login.
"""
import re

from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import ExtractorError

__all__ = ['BiliBiliTvIE']

_HEADERS = {
    'Referer': 'https://www.bilibili.tv/',
    'Origin': 'https://www.bilibili.tv',
}


class BiliBiliTvIE(InfoExtractor):
    IE_NAME = 'bilibili_tv'
    IE_DESC = 'Bilibili TV (internasional) via API — tanpa webpage, tanpa cookie'
    _VALID_URL = r'https?://(?:www\.)?bili(?:bili\.tv|intl\.com)/(?:[a-zA-Z]{2}/)?(?:play/(?P<season_id>\d+)/(?P<ep_id>\d+)|video/(?P<aid>\d+))'

    _TESTS = []

    def _real_extract(self, url):
        season_id, ep_id, aid = self._match_valid_url(url).group('season_id', 'ep_id', 'aid')
        video_id = ep_id or aid
        display_url = url.split('?')[0]

        # 1) Metadata dari halaman (og: tags — selalu ada walau SPA)
        webpage = self._download_webpage(display_url, video_id, fatal=False) or ''
        title = self._html_search_meta(
            ('og:title', 'twitter:title'), webpage, 'title',
            default=None) or self._html_search_regex(
            r'<title>([^<]+)</title>', webpage, 'title', default=None) or 'Konten Bilibili TV'
        title = title.replace(' | bilibili', '').strip()
        thumbnail = self._html_search_meta(
            ('og:image', 'twitter:image'), webpage, 'thumbnail', default=None)
        description = self._html_search_meta(
            'og:description', webpage, 'description', default=None)

        # Uploader ada di dalam window.__initialState (SPA) dengan pola:
        #   uploader:{mid:"...",name:"<NAMA>",avatar:"..."}
        # atau versi JSON: "uploader":{"mid":...,"name":"<NAMA>"}
        uploader = None
        for pat in (
            r'uploader\s*:\s*\{[^}]*?name\s*:\s*"([^"]+)"',
            r'"uploader"\s*:\s*\{[^}]*?"name"\s*:\s*"([^"]+)"',
            r'<meta[^>]+(?:name|property)="(?:og:video:actor|article:author)"[^>]+content="([^"]+)"',
            r'<meta[^>]+content="([^"]+)"[^>]+(?:name|property)="(?:og:video:actor|article:author)"',
        ):
            m = re.search(pat, webpage)
            if m and m.group(1).strip():
                uploader = m.group(1).strip()
                break
        if not uploader:
            # coba JSON-LD VideoObject -> author/creator
            m = re.search(r'"@type"\s*:\s*"VideoObject".*?"(?:author|creator|uploader)"\s*:\s*\{[^}]*?"name"\s*:\s*"([^"]+)"', webpage, re.S)
            if m:
                uploader = m.group(1).strip()
        if not uploader:
            uploader = 'Unknown'

        # Durasi (detik) ada di __initialState: `duration:109` atau "duration":109
        duration = None
        m = re.search(r'(?:duration|"duration")\s*:\s*(\d+)', webpage)
        if m:
            try:
                duration = int(m.group(1))
            except ValueError:
                duration = None

        # 2) Stream via API — platform ANDROID menembus region-lock web
        query = {'platform': 'android'}
        if ep_id:
            query['ep_id'] = ep_id
        else:
            query['aid'] = aid
        api = self._download_json(
            'https://api.bilibili.tv/intl/gateway/web/playurl', video_id,
            query=query, headers=_HEADERS,
            note='Memuat stream Bilibili TV')
        if api.get('code') != 0:
            msg = api.get('message') or 'gagal memuat stream'
            raise ExtractorError(
                'Bilibili TV: %s' % msg, expected=True)

        playurl = (api.get('data') or {}).get('playurl') or {}
        formats = []
        seen = set()
        for vid in playurl.get('video') or []:
            res = vid.get('video_resource') or {}
            u = res.get('url')
            if not u or u in seen:
                continue
            seen.add(u)
            si = vid.get('stream_info') or {}
            q = si.get('quality')
            formats.append({
                'url': u,
                'ext': 'mp4',
                'vcodec': res.get('codecs') or 'avc1',
                'acodec': 'none',
                'width': res.get('width'),
                'height': res.get('height'),
                'tbr': int_or_none(self, res.get('bandwidth'), scale=1000),
                'filesize': res.get('size'),
                'format_id': 'q-%s' % q if q else 'video',
                'format_note': si.get('desc_words'),
                'http_headers': _HEADERS,
            })
        for aud in playurl.get('audio_resource') or []:
            u = aud.get('url')
            if not u or u in seen:
                continue
            seen.add(u)
            formats.append({
                'url': u,
                'ext': 'm4a',
                'vcodec': 'none',
                'acodec': aud.get('codecs') or 'mp4a',
                'abr': int_or_none(self, aud.get('bandwidth'), scale=1000),
                'filesize': aud.get('size'),
                'format_id': 'audio',
                'format_note': 'audio',
                'http_headers': _HEADERS,
            })

        if not formats:
            raise ExtractorError(
                'Bilibili TV: tidak ada stream yang bisa diunduh untuk video ini.',
                expected=True)

        return {
            'id': video_id,
            'title': title,
            'description': description,
            'uploader': uploader,
            'duration': duration,
            'thumbnail': thumbnail,
            'webpage_url': display_url,
            'formats': formats,
        }


def int_or_none(ie, value, scale=1):
    try:
        if value is None:
            return None
        return round(float(value) / scale) if scale != 1 else int(value)
    except (TypeError, ValueError):
        return None
