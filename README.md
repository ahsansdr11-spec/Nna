# Nna

Universal open-source media downloader built with Flask and yt-dlp.

[![MIT License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.x-black?logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![Docker](https://img.shields.io/badge/Docker-ready-blue?logo=docker&logoColor=white)](Dockerfile)
[![GitHub Actions](https://github.com/ahsansdr11-spec/Nna/actions/workflows/ci.yml/badge.svg)](https://github.com/ahsansdr11-spec/Nna/actions/workflows/ci.yml)

## Features

- 26+ supported platforms
- Modern web interface
- Manga reader
- News aggregator
- Music search & streaming
- Background downloads

## 🚀 Live Demo

Try Nna online — no install required:

**https://kings-downloader.up.railway.app/**

---

Web downloader **gratis & open-source** berbasis **yt-dlp** dengan antarmuka **UI Pro** — dark slate elegan, aksen gradien indigo→violet, tipografi Sora + Inter, glass effect, dan micro-interactions halus (desain ulang total). Logo platform memakai **gambar asli** dari internet; logo aplikasi dibuat AI. Pola arsitektur mengikuti proyek *YT Music (Spotify) Downloader*: Flask + static folder + Dockerfile untuk deploy.

Tempel URL → dapatkan file **orisinal** (full quality, tanpa re-encode, tanpa watermark) dari:

- **YouTube** & **YouTube Shorts**
- **TikTok** (video, foto, audio)
- **Instagram** — video, **foto, carousel, story**
- **Facebook** — video, **foto, story**
- **X (Twitter)** — video, **foto**, GIF
- **Pinterest** (foto & video)
- **Spotify** (lagu via YouTube Music — lihat catatan di bawah)
- **RedNote (Xiaohongshu)** — foto & video (post publik; sebagian IP datacenter diblokir RedNote — kalau gagal, tempel link share-nya)

Tautan dari platform lain yang didukung yt-dlp tetap bisa dicoba secara generik.

## Fitur

- **Pemilihan resolusi** — default **1080p** untuk semua platform (pilihan: 360p–4K atau kualitas orisinal).
- Mode download: **Video terbaik (MP4/MKV)**, **MP3 192 kbps**, **Audio M4A** — plus daftar **format mentah** untuk dipilih manual.
- **Manga** (menu "Manga"): baca online via **MangaDex** — cari judul, **filter genre** (Action, Romance, dll), dan **rekomendasi** manga populer. Klik judul → daftar chapter → baca per halaman.
- **Berita live** (menu "Berita"): agregator RSS **15 sumber** × 6 kategori (Indonesia, Internasional, Teknologi, Ekonomi, Olahraga, Hiburan) — mode "Semua sumber" = berita terbaru terurut waktu, **setiap berita ada gambarnya**, bisa **dicari** & disegarkan. Sumber: CNN Indonesia, Antara, iNews, BBC, CNN, The Guardian, NYT, France 24, The Verge, Wired, Ars Technica, Engadget, CNBC Indonesia, Sky Sports, KapanLagi.
- **Musik** (menu "Musik"): cari **lagu, album, artis, atau playlist** di YouTube Music → **putar langsung** (stream, tanpa download) atau **unduh MP3** per lagu / **semua lagu sekaligus** (antrean berurutan).
- **Foto, Story & Slideshow**: Instagram, X, Facebook, dan **TikTok foto geser** — tempel tautan postingan → aplikasi mencoba berlapis **tanpa login** (gallery-dl untuk slideshow TikTok & foto IG/X/FB, embed Instagram, endpoint media IG, fxtwitter + syndication X, plugin Facebook, meta halaman) → pratinjau galeri dengan checkbox → **pilih foto yang mau diunduh** (atau semua) → unduh sebagai **ZIP** (langsung dari CDN, tanpa watermark).
- Download berjalan di **background thread + polling progress bar** — tidak ada timeout HTTP walau file besar.
- **Spotify**: tempel tautan lagu (`open.spotify.com/track/...`) — audio diambil dari YouTube Music (Spotify ber-DRM).
- Impersonasi browser (curl_cffi) untuk lolos blokir TLS/anti-bot.
- Deteksi platform otomatis + chip highlight.

## YouTube diblokir? Tanpa cookie, begini caranya

YouTube kadang menolak unduhan dari IP server (Railway/Render) untuk sebagian video. Aplikasi ini **tidak butuh cookie** — strateginya berlapis:

1. **`android_vr`** (client andalan — penembus blokir IP datacenter, tanpa login).
2. **JS runtime (Deno)** + solver challenge → membuat **PO token lokal** sehingga client `web_embedded`, `tv_downgraded`, `tv_simply`, `android`, dan `mweb` ikut tembus kalau satu client diblokir untuk video tertentu.
3. **Semua client sekaligus** (`all`) sebagai pamungkas.
4. Ada jeda (cooldown) otomatis agar IP server tidak semakin diblokir.

Jika semua tetap gagal: itu berarti YouTube sedang menolak video tersebut dari IP server — coba video lain atau tunggu beberapa menit.

## Batasan

Tempel tautan **postingan** apa pun (video, foto, story, slideshow) — aplikasi mencoba berlapis otomatis. Beberapa platform (X, Facebook) mewajibkan login untuk sebagian konten; jika gagal otomatis, tempel tautan **langsung file media** (URL berakhiran .jpg/.png/.webp/.mp4 atau dari CDN seperti pbs.twimg.com, cdninstagram.com, fbcdn.net, tiktokcdn) — dijamin terunduh.

## Instalasi lokal

```bash
# 1. Python 3.10+ dan ffmpeg harus terpasang
#    (Linux/macOS: sudo apt install ffmpeg / brew install ffmpeg)

# 2. Install dependensi (HANYA pertama kali / kalau ada library baru)
pip install -r requirements.txt

# 3. Jalankan (untuk tes lokal, opsional)
python app.py
# buka http://localhost:5000
```

## Menjalankan di HP Android (Termux)

Panduan lengkap & teruji langkah-demi-langkah ada di **[TERMUX.md](TERMUX.md)** — termasuk cara install Termux dari F-Droid, transfer ZIP, install ffmpeg, dan troubleshooting umum. Update via ZIP **tidak perlu `pip install` ulang** (kecuali ada library baru).

## Deploy

### GitHub (persiapan repo — TANPA diminta token/username lagi)

Pakai script bawaan `push.sh`:

```bash
# Sekali saja — aktifkan penyimpan kredensial
git config --global credential.helper store

# Push (folder baru = git hilang, script bikin ulang otomatis)
bash push.sh "pesan commit"
```

> Push **pertama** diminta username + token GitHub SEKALI saja (username: `ahsansdr11-spec`, password: Personal Access Token).
> Setelah itu token tersimpan → push berikutnya (termasuk `--force`) langsung jalan tanpa ditanya apa-apa.

### Render / Railway (DIREKOMENDASIKAN — download beneran jalan)

**Render:** Dashboard → New → Web Service → pilih repo GitHub → Render otomatis deteksi **Dockerfile** → Deploy.

**Railway:** New Project → Deploy from GitHub repo → otomatis pakai Dockerfile.

### Vercel (hanya UI — download TIDAK berfungsi penuh)

Vercel sudah didukung (`api/index.py` + `vercel.json`). Tapi **ingat batasannya**: Vercel itu *serverless* — tanpa disk permanen, tanpa ffmpeg, timeout fungsi singkat. Jadi halaman UI & pencarian musik bisa tampil, tetapi **download besar/konversi MP3 biasanya gagal**. Untuk download yang benar-benar jalan, pakai **Render/Railway**.

Langkah Vercel: vercel.com → Add New Project → Import repo GitHub → Framework: *Other* → Deploy.

File hasil download disimpan sementara di folder `downloads/` (otomatis dibersihkan); di lingkungan read-only otomatis fallback ke `/tmp`.

## Catatan platform

| Platform | Status |
|---|---|
| Spotify | Audio diambil dari **YouTube Music** (Spotify ber-DRM, file aslinya tidak bisa diunduh). Hanya tautan per-lagu `open.spotify.com/track/...`. Butuh `pip install ytmusicapi` |
| Dailymotion / Archive.org / Twitch | Video — lewat yt-dlp. Twitch: clip publik bisa; VOD yang butuh login tidak |
| SoundCloud / Mixcloud / Bandcamp | Audio — unduh MP3 192 kbps. SoundCloud & Mixcloud pakai tautan per-lagu/show; Bandcamp: track publik (termasuk *name-your-price*/gratis) |
| Streamable | Video publik (`streamable.com/...`) — unduh MP4/MP3 dengan cepat (teruji) |
| Bilibili | Dua versi: **bilibili.com** (`BV…`/`av…`, `b23.tv`) & **bilibili.tv** internasional (`video/…`) — keduanya ekstraktor khusus API (tanpa webpage, tanpa login). Region-lock tertentu diberi pesan jelas. CDN bisa melambat dari luar China (wajar) |
| Instagram / X / Facebook / TikTok | Video lewat yt-dlp; **foto, carousel, story, dan slideshow TikTok** lewat gallery-dl / X syndication / meta halaman (per media atau ZIP dengan checkbox pilihan) |
| Konten butuh login | Aplikasi mencoba otomatis: **embed Instagram** (`/p/CODE/embed/` → CDN foto asli tanpa login), **syndication X** (`cdn.syndication.twimg.com`), dan **plugin Facebook** — sebelum meta halaman. Kalau semua gagal, solusi dijamin jalan: buka foto di browser → klik kanan → **"Salin alamat gambar"** → tempel URL gambar langsung (mendukung tautan `.jpg/.png/.webp/.gif/.mp4` dan CDN seperti `pbs.twimg.com`, `scontent.cdninstagram.com`, `fbcdn.net`) |

## API (ringkas)

| Method | Endpoint | Fungsi |
|---|---|---|
| GET | `/api/info?url=...` | Metadata + daftar format (tanpa download) |
| GET | `/api/music-search?q=...&filter=songs\|albums\|artists\|playlists` | Cari musik di YouTube Music |
| GET | `/api/music-album/<id>` · `/api/music-playlist/<id>` · `/api/music-artist/<id>` | Detail album/playlist/artis (daftar lagu) |
| POST | `/api/download` | `{url, mode: best\|mp3\|custom, format_id, resolution}` → `{job_id}` (resolution default `1080`) |
| POST | `/api/gallery-download` | `{url}` → download semua foto/story sebagai ZIP → `{job_id}` |
| GET | `/api/job/<id>` | Status & progress (polling) |
| GET | `/api/file/<id>` | Unduh file hasil (otomatis dihapus setelah dikirim) |
| GET | `/api/thumbnail?url=...&dl=1` | Proxy gambar/video (bisa diunduh sebagai lampiran) |

## Disclaimer

Alat ini hanya mengambil konten **publik**. Pengguna bertanggung jawab penuh atas apa yang diunduh dan bagaimana menggunakannya. Hormati hak cipta dan ketentuan layanan platform.

## 💾 Akun & data tidak hilang saat push (Railway/Render)

Railway membangun ulang app dari repo tiap push → file `data.db` di dalam container
**ikut terhapus** (akun, chat, riwayat, playlist). Solusinya: simpan database di
**Volume persisten** Railway.

1. Buka dashboard Railway → service `nna-production` → tab **Volumes**.
2. Klik **New Volume** → mount path: `/data` (bisa juga `/app/data`).
3. Tambah **Environment Variable**: `DATA_DIR` = `/data`.
4. Deploy ulang (atau cukup otomatis setelah diset) — mulai sekarang akun,
   chat, riwayat download, riwayat baca manga, dan playlist **tersimpan permanen**
   di volume, tidak hilang lagi saat push berikutnya.

Tanpa volume: data tetap jalan tapi reset tiap deploy. Kode sudah membaca env
`DATA_DIR` — kalau tidak di-set, fallback ke `data.db` di folder proyek.

## 🌐 Cara ganti nama domain (Railway)

Domain default: `https://nna-production.up.railway.app`. Mau pakai domain sendiri
(mis. `kingsdownloader.my.id`)? Ikuti ini:

1. **Beli domain** dulu (IDCloudHost, Niagahoster, Cloudflare, atau registrar lain).
2. Buka dashboard **Railway** → service → tab **Settings** → **Networking**.
3. Di bagian **Domains** → klik **Generate Domain** (biarkan) atau langsung
   **Custom Domain** → masukkan domain kamu → **Add**.
   Railway akan menampilkan **CNAME target** (mis. `xyz.up.railway.app`).
4. Buka panel DNS registrar kamu → tambahkan record:
   - **CNAME** `@` (atau `www`) → target CNAME dari Railway, atau
   - **A record** `@` → IP `76.76.21.21` (Railway mengarahkan ke sini).
   - Kalau pakai **www**, tambahkan CNAME `www` → target yang sama.
5. Tunggu 5–30 menit sampai DNS menyebar, lalu buka domain kamu — Railway
   mengurus sertifikat **HTTPS** otomatis (Let's Encrypt), tidak perlu konfigurasi
   SSL manual.
6. (Opsional) Aktifkan **Force HTTPS** di Railway supaya semua pengunjung
   diarahkan ke versi aman.

> Catatan: ganti domain **tidak** menghapus data/akun — data tersimpan di volume
> (`DATA_DIR`), bukan di domain.

## Platform (26+)

YouTube, TikTok, Instagram, Facebook, X (Twitter), Pinterest, Spotify, Dailymotion,
SoundCloud, Archive.org, Twitch, Bandcamp, Mixcloud, Streamable, Bilibili, Vimeo,
SnackVideo, RedNote, Videy, **GitHub, MediaFire, Threads, Snapchat, Reddit,
Douyin, Rutube**.


## 🐛 Lapor Bug / Feedback

Ada halaman "Lapor Bug / Feedback" di tab **Cara Pakai** — tulis kendala/fitur,
laporan masuk ke daftar feedback global (tersimpan di database, aman di volume).

## 💸 Trial Railway habis? Cara tetap publikasi (gratis & murah)

Railway menawarkan trial sementara; kalau habis, beberapa pilihan:

1. **Railway (bayar, paling mudah)** — upgrade ke plan berbayar (mulai ~$5/bln).
   Data di volume `/data` tetap aman — tidak perlu konfigurasi ulang apa pun.

2. **Render (gratis)** — deploy ulang dari repo GitHub yang sama:
   - Buat akun di render.com → **New → Web Service** → pilih repo `Nna`.
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn --bind 0.0.0.0:${PORT:-10000} --timeout 120 --workers 1 --threads 8 app:app`
   - Tambah **Persistent Disk** (mount path `/data`) + env `DATA_DIR=/data`
     → akun/chat/riwayat tidak hilang saat redeploy.
   - Gratis (spin-down saat tidak dipakai, nyala lagi otomatis saat dikunjungi).

3. **Fly.io (gratis tier kecil)** — deploy dengan `flyctl`, pakai volume `fly volume`.

4. **Hosting sendiri / VPS murah** — VPS Rp20–50rb/bln (Contabo, DigitalOcean,
   dll): pasang Python + ffmpeg, jalankan `gunicorn`, dan ganti domain di sana.
   Ini paling fleksibel & data 100% milikmu.

5. **Dari rumah / Termux** — app bisa jalan di HP-mu sendiri (lihat TERMUX.md);
   bisa diakses teman lewat Wi-Fi/LAN, atau pakai tunnel (Cloudflare Tunnel /
   Tailscale Funnel) untuk akses publik tanpa server.

> Ganti host TIDAK menghapus data selama database ada di volume/DATA_DIR
> (atau kamu salin file `data.db` + folder `chat_uploads` dari volume lama).

## 🛡️ Strategi anti-blokir YouTube (berlapis maksimal)

YouTube memblokir IP datacenter (Railway/Render) secara berkala. Aplikasi ini
menggunakan pertahanan berlapis:

1. **Rotasi 14 client** — `android_vr` → `web_embedded` → `tv_downgraded` →
   `tv_simply` → `web_music` → `android_music` → `ios_music` → `web_safari` →
   `android` → `mweb` → `ios` → `tv_embedded` → `tv` → default.
2. **PO Token (POT)** — plugin lokal `yt_dlp_plugins/getpot.py` mencoba beberapa
   endpoint publik (`bgutil`, `pot.yt-dlp.cyou`, dll) untuk mendapat PO token +
   visitor data, sehingga client `web_embedded`/`tv` bisa lolos challenge
   "Sign in to confirm". Provider bgutil resmi (`yt-dlp-get-pot`) dipasang juga
   sebagai cadangan saat build Railway.
3. **Fallback Piped/Invidious** — kalau semua client yt-dlp gagal, aplikasi
   mencoba mengambil stream dari instance publik (Piped → Invidious) — request
   pergi ke instance, bukan ke YouTube, jadi lolos blokir IP server.
4. **Dedupe job** — dua klik download video yang sama TIDAK membuat download
   ganda (penyebab umum rate-limit & kegagalan diam-diam).
5. **Cooldown countdown** — kalau YouTube memaksa jeda, kartu menampilkan
   "Menunggu jeda anti-bot (N detik)".
6. **Pesan jujur** — kalau semua lapisan gagal (IP diblokir total), muncul
   penjelasan + saran, bukan error mentah.

> Catatan jujur: YouTube sewaktu-waktu bisa memblokir total sebuah IP datacenter
> untuk download (rate-limit). Tidak ada kode yang 100% menembus itu — tapi
> dengan lapisan di atas, peluang berhasil jauh lebih tinggi & kegagalan tidak
> pernah lagi "diam tanpa pesan".
