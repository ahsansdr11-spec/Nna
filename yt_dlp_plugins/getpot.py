"""POT provider lokal (PO Token) untuk YouTube — multi-endpoint.

Plugin ini memberi yt-dlp PO Token + visitor_data sehingga client YouTube
(web_embedded, tv, dll) bisa lolos challenge "Sign in to confirm you're not a
bot" dari IP datacenter (Railway). Mencoba beberapa endpoint publik secara
bergantian; kalau semua gagal, yt-dlp lanjut tanpa POT (tidak merusak).

Dipakai lewat:  pot_provider=kd-pot-provider
"""
import logging
import urllib.parse

try:
    from yt_dlp.plugins import POTProvider
except Exception:  # pragma: no cover
    POTProvider = object

log = logging.getLogger('kd-pot')

# Endpoint POT publik — dicoba urut sampai ada yang sukses
_POT_ENDPOINTS = [
    'https://bgutil-ytdlp-pot-provider.yt-dlp.org/get_pot',
    'https://pot.yt-dlp.cyou/get_pot',
    'https://pot.ytshorts.savvyclient.com/get_pot',
    'https://ytdlp-pot-provider.herokuapp.com/get_pot',
]
_TIMEOUT = 20


class KDPotProvider(POTProvider if POTProvider is not object else object):
    _PROVIDER_NAME = 'kd-pot-provider'

    def _get_pot_and_visitor_data(self, client, video_id, player_url, **kwargs):
        visitor_data = (kwargs.get('visitor_data') or '').strip()
        pot, vd = self._request(visitor_data, client)
        if pot:
            return pot, vd or None
        return None, None

    def _request(self, visitor_data, client):
        """Minta PO token dari endpoint publik. Kembalikan (pot, visitor_data)."""
        import requests
        last_err = None
        for base in _POT_ENDPOINTS:
            try:
                params = {'visitor_data': visitor_data} if visitor_data else {}
                r = requests.get(base, params=params, timeout=_TIMEOUT,
                                 headers={'User-Agent': 'Mozilla/5.0',
                                          'Referer': 'https://github.com/yt-dlp/yt-dlp'})
                if r.status_code != 200:
                    last_err = 'HTTP %s' % r.status_code
                    continue
                try:
                    d = r.json()
                except Exception:
                    # body polos bisa jadi pot token langsung
                    txt = (r.text or '').strip()
                    if txt and len(txt) > 30:
                        return txt, visitor_data or None
                    last_err = 'bad json'
                    continue
                pot = d.get('pot') or d.get('token') or ''
                vd = d.get('visitor_data') or visitor_data or ''
                if pot:
                    return pot, vd or None
                last_err = 'no pot in body'
            except Exception as e:
                last_err = str(e)[:80]
                continue
        log.debug('POT gagal dari semua endpoint: %s', last_err)
        return None, None
