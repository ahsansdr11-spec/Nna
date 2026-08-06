# 👑 KINGS DOWNLOADER — Panduan Lengkap Menjalankan di Termux (Android)

Panduan ini khusus untuk **Termux di Android** (aarch64 / ARM64, Android 8+).
Ikuti langkah demi langkah **tanpa melewatkan apa pun**. Tanda `$` = perintah yang diketik di Termux.

---

## Bagian 0 — Apa yang akan kamu dapat

Web app downloader yang berjalan di HP kamu:
- **26+ platform**: YouTube, TikTok, Instagram, Facebook, X (Twitter), Pinterest, Spotify, Dailymotion, SoundCloud, Archive.org, Twitch, Bandcamp, Mixcloud, Streamable, Bilibili, Vimeo, SnackVideo, RedNote, Videy, GitHub, MediaFire, Threads, Snapchat, Reddit, Douyin, Rutube
- **Video**: pilih resolusi (default 1080p, bisa 4K)
- **Foto & Story**: Instagram, X, Facebook (per media atau ZIP)
- **Musik**: cari lagu/album/artis/playlist → unduh MP3 (via YouTube Music)
- **Spotify**: tempel tautan lagu → otomatis dicari & diunduh

Buka dari browser HP (`http://127.0.0.1:5000`) atau dari PC/laptop di Wi-Fi yang sama.

---

## Bagian 1 — Install Termux

1. **JANGAN** install dari Google Play Store (versinya sudah usang & tidak dirawat).
2. Download **F-Droid** di browser HP: buka `https://f-droid.org` → install APK-nya.
3. Buka **F-Droid** → cari **Termux** → **Install**.
4. Buka aplikasi **Termux**. Tunggu sampai muncul prompt `$`.

> Kalau muncul notifikasi "Install package 'termux-api'?" → pilih **No** dulu (tidak wajib).

---

## Bagian 2 — Update sistem & install paket

Jalankan per baris, tekan Enter setelah tiap baris:

```bash
pkg update -y
```

```bash
pkg upgrade -y
```

```bash
pkg install -y python ffmpeg unzip git
```

> `ffmpeg` **wajib** untuk konversi MP3 & penggabungan video+audio.
> Kalau ada pertanyaan "proceed? [Y/n]" ketik `Y` lalu Enter.
> (Bisa makan waktu 2–5 menit — wajar.)

---

## Bagian 3 — Taruh file proyek di HP

### Cara A — Transfer ZIP (paling mudah)
1. Kirim `universal-media-downloader.zip` ke HP (WhatsApp ke diri sendiri, USB, atau Drive).
2. Simpan di folder **Download**.
3. Di Termux, beri akses penyimpanan lalu ekstrak:

```bash
termux-setup-storage
```

> Muncul dialog izin penyimpanan di Android → **Izinkan**.

```bash
cp /sdcard/Download/universal-media-downloader.zip ~/
cd ~
unzip -o universal-media-downloader.zip
cd universal-media-downloader
```

### Cara B — Git clone (kalau kamu push ke GitHub)
```bash
git clone https://github.com/ahsansdr11-spec/Nna.git
cd Nna
```

---

## Bagian 4 — Install library Python (HANYA kalau ada library baru)

> ⚠️ **TIDAK perlu `pip install` setiap update!** Library sudah terpasang
> permanen di Termux — `unzip` file baru tidak menghapusnya.
> Jalankan bagian ini HANYA kalau ada library baru yang disebut di
> changelog / instruksi (mis. `yt-dlp-ejs`, `ytmusicapi`, dll).

```bash
pip install flask yt-dlp requests gunicorn ytmusicapi gallery-dl
```

> **curl_cffi OPSIONAL di Termux** — jangan install, sering gagal build dan
> app tetap jalan tanpa itu. (Di Railway otomatis terinstall.)

---

## Bagian 5 — Update via ZIP (yang ini tiap ada versi baru)

```bash
cd ~
rm -rf universal-media-downloader
cp /sdcard/Download/universal-media-downloader.zip ~/
unzip -o universal-media-downloader.zip
cd universal-media-downloader
```

> Selesai. Kalau mau langsung push ke GitHub, lanjut ke Bagian 6.

**Jangan tutup aplikasi Termux** — cukup tekan tombol Home (server tetap jalan di background selama Termux tidak dibunuh Android).

---

## Bagian 6 — Buka aplikasinya

### Di HP itu sendiri
Buka browser apa pun (Chrome, Firefox) → ketik:

```
http://127.0.0.1:5000
```

### Di laptop/PC (satu Wi-Fi yang sama)
1. Cari IP Termux:
```bash
ifconfig
```
atau:
```bash
ip -4 addr show
```
2. Lihat baris `wlan0` → `inet 192.168.x.x` (contoh: `192.168.1.15`).
3. Di browser PC buka:
```
http://192.168.1.15:5000
```
(HP & PC harus di jaringan yang sama; kadang perlu izin "Private Network" di Android.)

---

## 🎵 Cara pakai fitur MUSIK

1. Klik menu **Musik** di pojok kanan atas.
2. Ketik judul lagu / artis / album → **Cari**.
3. Pilih filter: **Lagu** (daftar), **Album / Artis / Playlist** (kartu).
4. **Putar langsung**: klik baris lagu / tombol **Putar** → lagu langsung berbunyi (ada pemutar di bawah layar; bisa next/prev dalam daftar).
5. **Unduh MP3**: klik tombol **MP3** di baris lagu → progress muncul → **Simpan file**.
6. **Album / Playlist / Artis**: klik kartunya → lihat daftar lagu → putar / unduh per lagu atau **Unduh semua**.
7. **Spotify**: tempel tautan `open.spotify.com/track/...` di kolom Beranda → otomatis dicari & diunduh.

---

## 🌐 Platform tambahan

- **Dailymotion, Archive.org, Twitch, SoundCloud, Mixcloud, Bandcamp, Streamable, Bilibili** — sudah terpasang otomatis (tidak perlu install apa pun).
- **Bilibili** memakai ekstraktor khusus API bawaan proyek (`yt_dlp_plugins/extractor/bilibili_api.py` untuk `.com`, `bilibili_tv.py` untuk `.tv`) — jangan dihapus folder `yt_dlp_plugins` saat pindah-pindah file.
- **Bilibili** dari luar China kadang pelan (CDN). Wajar, bukan hang.

## 🛠 Troubleshooting

| Masalah | Solusi |
|---|---|
| `pip: command not found` | `pkg install -y python`, lalu buka sesi Termux baru |
| `ERROR: Installing pip is forbidden` | Normal di Termux — **jangan** upgrade pip. Langsung install library |
| `Impersonate target "chrome-136" is not available` | `curl_cffi` belum terpasang → **abaikan saja** (app tetap jalan). Di Termux jangan paksa install — sering gagal build |
| TikTok: `Unable to extract universal data for rehydration` | TikTok memblokir bot. 1) `pip install -U yt-dlp` 2) pastikan curl_cffi 3) ganti jaringan/VPN 4) coba lagi nanti |
| Foto postingan gagal otomatis | Tempel tautan **langsung file media** (URL berakhiran .jpg/.png/.webp/.mp4 atau dari CDN seperti pbs.twimg.com, cdninstagram.com, fbcdn.net, tiktokcdn) — dijamin terunduh |
| Foto/story IG/X/FB/TikTok gagal | Aplikasi kini mencoba berlapis otomatis: video (yt-dlp) → foto (gallery-dl) → **embed Instagram / syndication X / plugin Facebook** (tanpa login) → meta halaman. Kalau semua gagal, **solusi dijamin jalan**: buka foto di browser → klik kanan/tahan → **"Salin alamat gambar"** → tempel URL itu di kolom aplikasi |
| Error "Salin alamat gambar" disarankan | Bukan error — itu saran. Aplikasi mendukung tautan langsung gambar: berakhiran `.jpg/.png/.webp/.gif/.mp4` atau dari CDN (pbs.twimg.com, cdninstagram.com, fbcdn.net, tiktokcdn) — langsung terunduh tanpa login |
| Instagram foto otomatis | Dipakai via **halaman embed publik** (tanpa login) — fotonya diambil langsung dari CDN `scontent*.cdninstagram.com` |
| Fitur musik error | Pastikan `pip install ytmusicapi` |
| YouTube minta login/verifikasi ("not a bot") | Ini karena YouTube menolak bot. Tanpa cookie: `pkg install -y deno` lalu `pip install yt-dlp-ejs` — aplikasi bisa bikin token anti-bot sendiri. Atau tunggu beberapa menit / coba video lain |
| MP3 gagal / error ffmpeg | Pastikan `pkg install -y ffmpeg` sudah dijalankan |
| "Address already in use" (port 5000) | Stop sesi Termux lain, atau jalankan: `gunicorn --bind 0.0.0.0:5001 --timeout 120 --workers 1 app:app` lalu buka `http://127.0.0.1:5001` |
| Server mati saat layar HP mati | Aktifkan wakelock: `pkg install -y termux-api` lalu `termux-wake-lock` |
| Server mati saat keluar Termux | Pakai tmux: `pkg install -y tmux` → `tmux new -s dl` → `python app.py` → keluar dengan `Ctrl+B` lalu `D` → kembali dengan `tmux attach -t dl` |
| App dibunuh Android (proses mati) | Android 12+ suka membunuh proses background. Kunci aplikasi Termux dari menu task, atau jalankan di foreground |

## Bagian 6 — Push ke GitHub (TANPA diminta token/username lagi)

Pakai script bawaan `push.sh` — sekali setup, push berikutnya **langsung jalan tanpa diminta apa-apa** (token & username disimpan otomatis oleh git).

```bash
# SEKALI SAJA — aktivasi penyimpan kredensial git
git config --global credential.helper store

# Lalu push pakai script (folder baru = git hilang, jadi script bikin ulang)
bash push.sh "pesan commit kamu"
```

Saat **push pertama** akan diminta 2x (sekali saja):
- **Username:** `ahsansdr11-spec`
- **Password:** tempel **Personal Access Token** GitHub-mu (bukan password akun)

Setelah itu token tersimpan → push berikutnya (`bash push.sh "update"`) **tidak akan minta apa-apa lagi**, termasuk saat `--force`.

> Kalau GitHub tetap meminta password lagi: cek token sudah punya scope `repo`,
> atau hapus simpanan lama dengan `rm ~/.git-credentials` lalu ulangi langkah di atas.

---

## ⚡ Ringkasan perintah cepat (copy semua sekaligus)

```bash
pkg update -y && pkg upgrade -y
pkg install -y python ffmpeg unzip git
termux-setup-storage
cp /sdcard/Download/universal-media-downloader.zip ~/ && cd ~
unzip -o universal-media-downloader.zip
cd universal-media-downloader
git config --global credential.helper store
bash push.sh "update terbaru"
```

> Tanpa `pip install` (kecuali library baru) & tanpa `python app.py` —
> itu hanya untuk tes lokal kalau mau, bukan bagian dari update.

---

## 💡 Tips perawatan (penting!)

Platform seperti TikTok & YouTube sering berubah, dan yt-dlp diperbarui hampir tiap minggu. **Saat ada error aneh, update dulu yt-dlp:**

```bash
pip install -U yt-dlp
```

Ini **aman** di Termux (hanya `pip install --upgrade pip` yang dilarang). Lakukan sebulan sekali, atau kapan pun download tiba-tiba error.

Selamat mencoba! Kalau ada langkah yang error, baca bagian Troubleshooting di atas.

---

## 💾 Biar akun/chat/riwayat tidak hilang tiap push (Railway)

Aplikasi menyimpan data di `data.db`. Kalau di-deploy ke Railway, file itu ada di
container yang dibangun ulang tiap push → data hilang. Solusi: pakai **Volume**:

1. Dashboard Railway → service → tab **Volumes** → **New Volume**.
2. Mount path: `/data`.
3. Deploy ulang sekali. Aplikasi **otomatis mendeteksi** volume `/data` —
   tidak perlu set env apa pun. Akun, chat, riwayat, playlist, feedback tersimpan permanen.

Kode otomatis memakai `DATA_DIR` kalau di-set; kalau tidak, pakai `data.db` biasa.


---

## 🌐 Cara ganti nama domain (Railway)

1. Beli domain (IDCloudHost / Niagahoster / Cloudflare / lainnya).
2. Railway → service → **Settings** → **Networking** → **Domains** → **Custom Domain**.
3. Masukkan domain → Railway kasih **target CNAME**.
4. Di panel DNS registrar: tambah **CNAME** `@`/`www` → target itu (atau **A** `@` → `76.76.21.21`).
5. Tunggu 5–30 menit → HTTPS otomatis oleh Railway.

Ganti domain tidak menghapus akun/data (data di volume `DATA_DIR`).


---

## 💸 Trial Railway habis? Tetap publikasi

1. **Railway berbayar** (paling mudah) — upgrade plan; data di volume `/data` aman.
2. **Render (gratis)** — deploy repo yang sama:
   - New Web Service → repo `Nna`
   - Build: `pip install -r requirements.txt`
   - Start: `gunicorn --bind 0.0.0.0:${PORT:-10000} --timeout 120 --workers 1 --threads 8 app:app`
   - Tambah **Persistent Disk** mount `/data` + env `DATA_DIR=/data`.
3. **VPS murah** — pasang Python+ffmpeg, jalankan gunicorn, arahkan domain.
4. **Termux + Cloudflare Tunnel** — jalankan di HP, buka publik gratis lewat tunnel.

Ganti host tidak menghapus data selama database di volume (`data.db` + `chat_uploads`).
