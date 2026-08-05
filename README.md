# Universal Media Downloader

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
