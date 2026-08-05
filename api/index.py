"""Vercel Python entry point (WSGI).

Vercel memanggil file ini sebagai handler. `app` diimpor dari app.py
(Flask) — Vercel Python runtime akan memakainya sebagai aplikasi WSGI.
"""
import os
import sys

# pastikan folder proyek (root) ada di path import
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app import app

# alias yang dikenali berbagai runtime serverless
application = app
handler = app
