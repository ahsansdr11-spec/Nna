# Menggunakan base image Python resmi yang ringan (pola sama dengan proyek YT Music Downloader)
FROM python:3.11-slim

# ffmpeg dari Debian (bookworm) itu LAMA (5.1.x) dan bisa CRASH saat memproses
# HLS tertentu (terbukti: segfault di HLS Dailymotion → download gagal total).
# Pasang build statis TERBARU dari BtbN (FFmpeg-Builds resmi komunitas, selalu
# versi terkini dengan libx264/opus lengkap) — WAJIB untuk merge video+audio,
# konversi MP3, fitur Konversi & HD Enhancer. unzip untuk deno.
RUN apt-get update && apt-get install -y \
    curl \
    xz-utils \
    unzip \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL -o /tmp/ffmpeg.tar.xz \
        https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz \
    && tar -xJf /tmp/ffmpeg.tar.xz -C /tmp \
    && cp /tmp/ffmpeg-master-latest-linux64-gpl/bin/ffmpeg /usr/local/bin/ffmpeg \
    && cp /tmp/ffmpeg-master-latest-linux64-gpl/bin/ffprobe /usr/local/bin/ffprobe \
    && chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe \
    && rm -rf /tmp/ffmpeg.tar.xz /tmp/ffmpeg-master-latest-linux64-gpl \
    && ffmpeg -version | head -1 && ffprobe -version | head -1

# Install Deno — JS runtime yang dipakai yt-dlp untuk menyelesaikan challenge
# anti-bot YouTube & membuat PO token LOKAL (tanpa cookie, tanpa layanan pihak
# ketiga). Ini yang membuat client web_embedded / tv_downgraded / tv_simply /
# android / mweb ikut tembus saat android_vr diblokir untuk video tertentu.
ARG DENO_VERSION=v2.9.4
RUN curl -fsSL -o /tmp/deno.zip \
        https://github.com/denoland/deno/releases/download/${DENO_VERSION}/deno-x86_64-unknown-linux-gnu.zip \
    && unzip -o /tmp/deno.zip -d /usr/local/bin \
    && rm /tmp/deno.zip \
    && deno --version

# Set direktori kerja di dalam container
WORKDIR /app

# Salin requirements terlebih dahulu (untuk caching layer)
COPY requirements.txt .

# Install paket Python pendukung
RUN pip install --no-cache-dir -r requirements.txt

# Salin semua kode aplikasi ke dalam container
COPY . .

# Buat folder downloads jika belum ada
RUN mkdir -p downloads

# Bind ke port yang ditentukan env (Render/Railway memakai $PORT)
ENV PORT=5000

# Jalankan dengan Gunicorn — 1 worker agar status job download & kanal live
# (SSE /api/live, keduanya di memori proses) selalu konsisten; download berjalan
# di background thread sehingga timeout gunicorn tidak masalah untuk file besar.
# --threads 16 dipakai supaya pemutar musik, koneksi live SSE, dan permintaan
# lain bisa jalan bersamaan untuk banyak pengguna (SSE melepas thread tiap ±100
# detik lalu browser menyambung ulang otomatis).
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-5000} --timeout 120 --workers 1 --threads 16 app:app"]
