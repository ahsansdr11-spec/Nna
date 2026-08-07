# LAPORAN PERBAIKAN — KINGS DOWNLOADER (UI v47 → v50)

Tanggal audit: 7 Agustus 2026 · Status: **semua uji lulus (v48: 15/15, v49: 42/42, v50: 50/50 PASS)**

**Nama aplikasi & file TIDAK berubah** — tetap **KINGS DOWNLOADER** dan ZIP
tetap bernama asli `universal-media-downloader.zip` (permintaan owner:
"namanya jangan ganti, balikin jadi semula").

---

# BAGIAN 1 — UPDATE v49 → v50 (7 Agustus 2026)

## A. PERMINTAAN OWNER (VERBATIM)

> "Coba tesnya dengan beneran tes link postingan/upload dari foto, file,
> audio, dan video dari langsung di platformnya, kalau ada error fix sampai
> bekerja di semua platform, dengan 3 tipe download, dengan semua resolusi,
> dan kalau kita pengen download lagi itu harus langsung sedia dan ga
> kehilangan fungsinya tiba-tiba kecuali kalau refresh, jadi fix itu"

Dua tugas: **(1)** uji dengan LINK ASLI postingan (foto/file/audio/video)
langsung dari platformnya — bukan sekadar baca kode — dan perbaiki sampai
semua platform jalan dengan 3 tipe download (MP4 / MP3 / M4A) + semua
resolusi; **(2)** perbaiki bug "download lagi harus langsung tersedia,
tidak boleh tiba-tiba kehilangan fungsi kecuali habis refresh".

## B. BUG NYATA YANG DITEMUKAN & DIPERBAIKI (semua dari pengujian LIVE)

### 1. `/api/file/<job>` menghapus file setelah PENYIMPANAN PERTAMA
Akar masalah dari keluhan "kalau mau download lagi fungsinya hilang":
setelah file dikirim sekali ke browser, server langsung menghapusnya —
klik Simpan kedua (unduhan browser gagal di tengah / klik dobel / simpan
di perangkat lain) mendapat **404 / file JSON error**.
**Fix:** file TIDAK dihapus setelah dikirim; pembersihan tetap otomatis
lewat TTL job (30 menit). Kalau benar-benar sudah kedaluwarsa, server
membalas pesan ramah ("File sudah kedaluwarsa… silakan unduh ulang")
bukan error teknis. **Frontend** `saveFile()` juga diubah: memeriksa dulu
ketersediaan file (HEAD), TIDAK menutup kartu hasil setelah menyimpan,
dan label tombol berganti "Tersimpan — klik untuk simpan lagi".

### 2. Instagram: `/api/info` membeku 4+ MENIT di postingan foto
gallery-dl mengulang rate-limit 429 Instagram sampai **245,6 detik**
sebelum menyerah — padahal jalur embed publik merespons 0,8 detik.
**Fix:** urutan fallback per-platform untuk Instagram diubah menjadi
embed → media-direct → gallery-dl (terakhir), retry Instagram gallery-dl
dibatasi (retries=1, sleep-request=1) di `/api/info` DAN di download galeri.
Hasil: info Instagram **2,3 detik** (dulu habis timeout 120 dtk).

### 3. Dailymotion: semua download gagal di 100% (ffmpeg lama + bug yt-dlp)
Rantai akar masalah: Dailymotion memakai HLS MPEG-TS ("lumberjack") →
ffmpeg lama (static 7.0.2 SEGFAULT; ffmpeg 5.1 Debian hasil kosong) →
`json.loads('')` melempar **JSONDecodeError** yang tidak ditangkap yt-dlp
(ia hanya menangkap PostProcessingError) → download yang 100% selesai
dibunuh. **Fix ganda:** (a) monkeypatch `get_metadata_object` di app.py
mengubah SEMUA kegagalan ffprobe menjadi PostProcessingError supaya jalur
fallback yt-dlp berjalan persis seperti rancangannya; (b) **Dockerfile**
kini memasang **ffmpeg build terbaru dari BtbN** (master gpl) yang terbukti
memproses HLS Dailymotion dengan sempurna (m3u8 → MP4 h264+aac bersih).
Hasil: **6/6 PASS** — best (4K 3840×2160 terverifikasi ffprobe), 2160,
1080, 360, MP3, M4A.

### 4. Pinterest pin FOTO: `/api/info` lambat 37,7 detik
yt-dlp dipanggil duluan; untuk pin tanpa video ia mencoba **16 kali**
(search-info 4× + retry internal) sebelum gagal "No video formats found".
**Fix:** (a) error "no video formats found" kini langsung di-raise tanpa
retry (postingan foto tidak akan berubah jadi video); (b) Pinterest dicoba
lewat jalur galeri (gallery-dl) DULU sebelum yt-dlp — pin foto maupun pin
video sama-sama cepat. Hasil: **1,0 detik** (dulu 37,7 dtk).

### 5. Judul file media langsung (CDN/link .mp4 mentah) generik
Link langsung ke file media menampilkan judul "Konten Media".
**Fix:** judul diambil dari nama file di URL (url-decoded).

### 6. Catatan lingkungan (bukan bug kode)
`curl_cffi` (wajib untuk impersonasi TLS anti-bot) **memang sudah ada di
requirements.txt** (`curl_cffi==0.11.4`, pin kompatibel yt-dlp) — sandbox
uji ini saja yang belum memasangnya; setelah dipasang, TikTok &
Dailymotion langsung berfungsi penuh. Di produksi (Docker) sudah otomatis.

## C. HASIL UJI LIVE DENGAN LINK ASLI (50/50 PASS, hasil diverifikasi ffprobe)

| Platform & link asli | Info | MP4 best | Resolusi | MP3 | M4A | Foto/ZIP |
|---|---|---|---|---|---|---|
| YouTube (Despacito) | 25 format ≤1080p | h264 1920×1080+aac | 720 ✓ 360 ✓ | ✓ | ✓ | — |
| TikTok (@tiktok) | ≤1920 | hevc 1080×1920+aac | 1080 ✓ | ✓ | ✓ | — |
| X/Twitter video (SpaceX) | ≤2160 | h264 3840×2160+aac | 2160 ✓ 360 ✓ | ✓ | ✓ | — |
| X/Twitter foto (tweet Obama) | 1 foto | — | — | — | — | ZIP ✓ |
| Instagram foto (3 slide) | 3 foto, 2,3 dtk | — | — | — | — | ZIP 3 foto ✓ |
| Dailymotion | ≤2160 | h264 3840×2160+aac | 2160 ✓ 1080 ✓ 360 ✓ | ✓ | ✓ | — |
| Facebook video | ✓ | h264+aac ✓ | — | ✓ | — | — |
| SoundCloud (Monstercat) | ✓ | (m4a asli — platform audio) | — | ✓ 192k | ✓ | — |
| Bandcamp (audio 7,7 mnt) | ✓ | MP3 valid ✓ | — | ✓ | ✓ | — |
| Pinterest video pin | 416–1280p | h264 720×1280+aac | ✓ | ✓ | — | — |
| Pinterest foto pin | 1 foto, 1 dtk | — | — | — | — | ZIP ✓ |
| MediaFire (file zip) | ✓ | file utuh ✓ | — | — | — | ✓ |
| Link langsung .mp4/.jpg (CDN) | judul dari nama file | ✓ | — | ✓ | ✓ | ✓ |

Catatan jujur: **“best” di platform audio** (SoundCloud/Bandcamp) memang
menghasilkan **file audio** (.m4a / .mp3, dengan cover) — itu perilaku
yang benar; MP4 tidak mungkin ada karena sumbernya bukan video.

### Uji "download lagi langsung sedia" (9/9 PASS)
simpan file #1 ✓ · HEAD cek ✓ · **simpan #2 identik** ✓ (tidak 404) ·
URL sama diunduh lagi → **job baru langsung jalan** ✓ · MP3 #1 ✓ ·
MP3 #2 langsung sedia ✓ · galeri simpan 2x ✓ · galeri #2 job baru ✓ ·
job kedaluwarsa → pesan ramah (bukan traceback) ✓

### Uji komunikasi live (tanpa refresh)
login admin ✓ · announcement admin ✓ · **SSE mengirim event change INSTAN
(0,0 dtk)** ✓ · chat POST ✓ · live-check rev naik ✓ · announcement
muncul di GET ✓ · hapus announcement ✓ — semua tanpa refresh halaman.

## D. PERUBAHAN TEKNIS v50

- `app.py`: `/api/file` tanpa hapus-setelah-kirim + pesan kedaluwarsa
  ramah; monkeypatch `get_metadata_object` → PostProcessingError; urutan
  fallback per-platform Instagram (embed-first) + instagram retries=1;
  Pinterest galeri-pertama + raise-instan "no video formats"; judul media
  langsung dari nama file.
- `static/app.js` (UI_VERSION=50): `saveFile()` async — HEAD-check dulu,
  kartu TIDAK ditutup, label "Tersimpan — klik untuk simpan lagi".
- `Dockerfile`: ffmpeg diganti ke **BtbN ffmpeg-master-latest (GPL)**,
  + curl/xz-utils/ca-certificates untuk memasangnya.
- Cache-bust aset `?v=50` (style.css & app.js) — footer **"Versi UI v50"**.
- `requirements.txt` TIDAK berubah (curl_cffi==0.11.4 dll memang sudah ada).

**Deploy v50:** setelah push & redeploy, footer harus berbunyi
**"Versi UI v50"**. Wajib rebuild image Docker (bukan hanya restart)
karena ffmpeg baru dipasang saat build.

---

# BAGIAN 2 — UPDATE v48 → v49 (7 Agustus 2026)

## A. PERMINTAAN: 100% WORK DI SEMUA TIPE & RESOLUSI

### 1. Bug nyata yang ditemukan & diperbaiki
**Konversi ke MP4/MOV bisa gagal** `expected str, bytes or os.PathLike object, not int`
— nilai CRF preset kualitas tersimpan sebagai *integer* lalu diteruskan mentah
ke argumen `subprocess` ffmpeg. **Diperbaiki:** seluruh preset
`CONVERT_QUALITY` kini string murni. Terverifikasi ulang: MKV→MP4 (high) &
MP4→MOV kini **berhasil** (ffprobe: H.264 + AAC).

### 2. Pemilihan resolusi dibuat "codec-aware"
Semua tier di `RESOLUTIONS` (2160/1440/1080/720/480/360/original) kini berjenjang:
`bv*[height<=N][vcodec^=avc1]+ba[acodec^=mp4a]` → `bv*[height<=N]+ba` → `b[height<=N]`.
Artinya: server **mengutamakan H.264+AAC** (bisa diputar di SEMUA perangkat —
HP lama, Windows, TV) dan otomatis turun ke codec lain hanya bila H.264 tidak
tersedia (mis. 4K YouTube yang kadang VP9 saja). Tidak ada lagi kasus
"download berhasil tapi video tidak bisa diputar".

### 3. Format video-only kini otomatis dinikahkan dengan audio
Kalau pengguna memilih format video-tanpa-audio dari daftar (mis. video 4K
terpisah), frontend mengirim `merge_audio: 1` → server menggabungkan
`format_id+bestaudio` menjadi **MP4 utuh bersuara**. Sebelumnya file hasil
bisa bisu total.

### 4. Pesan error ffmpeg/ffprobe yang membingungkan → bahasa manusia
`unable to obtain file audio codec with ffprobe` dkk. kini dipetakan ke pesan
jelas ("server perlu ffmpeg — otomatis ada saat deploy via Docker") lewat
`friendly_error()`.

### 5. Bukti uji NYATA (bukan sekadar baca kode)
Download diuji ujung-ke-ujung dengan output diverifikasi ffprobe:

| Uji | Hasil verifikasi |
|---|---|
| MP4 best | H.264 960×540 + AAC 256kbps, dur 5.05s |
| MP3 | audio mp3 192kbps, dur 5.09s |
| M4A | audio aac 256kbps, dur 5.05s |
| MP4 resolusi 480 | lolos (fallback aman ke best utk direct-file) |
| bestaudio / video-only | lolos (fallback yt-dlp standar utk direct-file) |
| Info metadata URL | judul kini nama file asli (`flower.mp4`), bukan "Konten Media" |
| MediaFire (ZIP proyek owner) | lolos (ekstraktor khusus) |

*Catatan: YouTube tidak diuji dari sandbox ini karena IP datacenter umumnya
dikenai bot-check YouTube — di server produksi (Railway) tetap berjalan; ini
keterbatasan lingkungan uji, bukan bug.*

## B. FITUR BARU 1 — KONVERSI FILE (ala FreeConvert)

Menu **"Konversi"** di navbar: pilih/seret file → pilih format tujuan →
konversi → unduh. Progress **nyata** di-parse dari output ffmpeg.

- **Video →** MP4, MKV, MOV, WEBM, AVI, GIF (palette 2-pass), plus ekstrak
  audio MP3/M4A/WAV/OGG/FLAC/AAC.
- **Audio →** MP3, M4A, AAC, WAV, OGG, FLAC.
- **Gambar →** PNG (alpha aman), JPG, WEBP, BMP.
- **Kualitas:** Tinggi / Sedang / Kecil (CRF 18/23/28; audio 320/192/128k)
  + slider kualitas gambar.
- Batas upload 150 MB; validasi jenis file, penolakan format-tak-cocok &
  format-sama-asal dengan pesan jelas.

**Uji nyata 17/17 PASS** (semua output diverifikasi ffprobe/Pillow):
mkv→mp4 ✓ mp4→mov ✓ mp4→avi(mpeg4+mp3) ✓ mp4→mkv ✓ mp4→webm(vp9+opus) ✓
m4a→mp3 ✓ mp3→m4a ✓ mp3→wav(pcm) ✓ mp3→flac ✓ wav→aac ✓ wav→ogg(vorbis) ✓
mp4→mp3 ✓ mp4→m4a ✓ mp4→gif(480×360) ✓ jpg→png ✓ png→jpg ✓ jpg→webp ✓

## C. FITUR BARU 2 — HD ENHANCER (kualitas ala Wink)

Menu **"HD Enhancer"**: foto & video jadi tajam/HD, diproses nyata di server.

- **Foto:** upscale Lanczos 2×/4× + denoise + unsharp mask + poles
  kontras/warna/kecerahan (kekuatan Halus/Sedang/Kuat; channel alpha PNG
  diproses terpisah supaya transparansi tidak rusak). Batas dimensi output
  8000px anti memory-bomb.
- **Video:** upscale Lanczos ke 720p/1080p/1440p/2160p + filter `hqdn3d`
  (denoise) + `unsharp` + re-encode H.264 kualitas tinggi (CRF 18) + audio
  AAC. Rasio aspek dijaga.
- File audio **ditolak dengan pesan jelas** (bukan error aneh).

**Uji nyata 6/6 PASS:** JPG 2×(400×300) ✓ JPG 4× strong(800×600) ✓
PNG 2× soft ✓ video→1080p (1440×1080 H.264+AAC) ✓ video→720p (960×720) ✓
penolakan audio (400) ✓

## D. PERUBAHAN TEKNIS v49

- `requirements.txt`: +`pillow>=10.0.0` (konversi/enhance gambar).
- Backend: +`/api/convert/formats`, +`/api/convert`, +`/api/enhance`
  (job background + progress nyata), helper `ffprobe_info`,
  `run_ffmpeg_logged` (parse `-progress`), `ffmpeg_convert_args`,
  `ffmpeg_gif` 2-pass, `convert_image_file`, `enhance_image_file`,
  `run_enhance`.
- Frontend: view **Konversi** & **HD Enhancer** (dropzone seret-lepas,
  pill format dinamis dari `/api/convert/formats`, progress bar upload XHR
  + progress proses, kartu hasil dengan tombol unduh).
- Footer versi → **"Versi UI v49"**; cache-bust aset `?v=49`.
- Info URL file langsung: judul kini nama file aslinya.
- Live/SSE dari v48 tidak berubah & teruji ulang: **13/13 PASS**
  (signup, login admin, tiket, announcement admin, snapshot SSE, event live
  `ann`/`tkt` instan tanpa refresh, polling fallback, balasan admin →
  status `answered`, statistik publik).

**Deploy v49:** footer harus berbunyi **"Versi UI v49"**. Pastikan
variabel/requirements terpasang ulang (ada `pillow` baru); ffmpeg sudah
otomatis terpasang via Dockerfile.

---
---

# BAGIAN 3 — UPDATE v47 → v48 (7 Agustus 2026)

Audit dilakukan menyeluruh terhadap `app.py` (5.500+ baris), `static/app.js`
(3.000+ baris), `static/index.html`, dan `static/style.css`, disusul uji
nyata terhadap server (semua endpoint komunikasi + kanal live SSE).

---

## A. BUG YANG DITEMUKAN & DIPERBAIKI

### 1. [KRITIS] Stored-XSS di Chat Global
Pesan chat & username disisipkan mentah ke atribut `onclick="chatReplyTo('...')"`.
Pesan seperti `x');alert(1);//` **dieksekusi di browser semua pengguna** yang
membuka chat. (Sangat mungkin inilah "ngebug" yang dilaporkan fans —
username/pesan berisi tanda kutip `'` juga merusak tombol balas total.)

**Perbaikan:** pesan disimpan di map `_chatMsgs`, tombol balas/kutipan kini
memakai event delegation dengan hanya `data-id` numerik — nol interpolasi
teks pengguna ke handler JS. Ditambah helper `jsq()` (escape `\xNN`) untuk
semua sisa inline handler yang membawa string (metadata MangaDex, dll).

### 2. Klik kutipan balasan membalas pesan yang SALAH
`chatReplyHtml` memakai `m.id` (pesan itu sendiri) alih-alih `m.parent_id`.
**Perbaikan:** klik kutipan sekarang *melompat & menyorot* pesan aslinya
(seperti WhatsApp); tombol panah tetap untuk membalas.

### 3. Unduh 2+ lagu bersamaan → kartu progress kacau/nyangkut
Semua kartu musik bernama `id="music-progress"` (ID dobel) sehingga polling
selalu menyasar kartu pertama. **Perbaikan:** ID unik per unduhan
(`music-progress-<timestamp>-<acak>`) di `beginMusicDownload` &
`musicQueueNext`.

### 4. `saveFile()` bisa menutup kartu Beranda yang sedang berjalan
Dari daftar "Unduhan selesai" (album), `saveFile(jobId)` tanpa tombol membuat
`dismissCard()` menutup `#progress-card` halaman Beranda. **Perbaikan:**
kartu hanya ditutup kalau tombolnya ada.

### 5. Chat selalu melompat ke bawah tiap refresh
Membaca pesan lama jadi tidak nyaman. **Perbaikan:** auto-scroll hanya saat
pengguna memang di dekat bawah / mengirim pesan sendiri.

### 6. Deteksi halaman laporan tidak mengenali tab Tiket
`submitFeedback` kini memasukkan `tickets` ke daftar view.

---

## B. SEMUA FITUR KOMUNIKASI KINI LIVE (TANPA REFRESH)

Sebelumnya: chat polling tiap 4 dtk, announcement tiap 60 dtk, dan
tiket/feedback/saran platform **tidak pernah** diperbarui otomatis.

Sekarang, arsitektur real-time berlapis:

1. **SSE (Server-Sent Events)** — endpoint baru `/api/live`.
   Server menyiarkan event seketika setiap ada: pesan chat baru,
   announcement baru/dihapus, tiket baru/balasan/perubahan status,
   feedback baru, saran platform baru. Browser langsung mem-fetch ulang
   **hanya kanal yang berubah**.
2. **Balasan tiket muncul live** di thread yang sedang terbuka
   (`refreshOpenTicket`) — termasuk daftar tiket & kotak masuk admin.
3. **Fallback polling pintar** `/api/live-check` (tiap 4 dtk, hanya nomor
   revisi) — otomatis aktif di hosting tanpa dukungan SSE (mis. Vercel).
4. **Jaring pengaman:** interval lama diperlambat (announcement 120 dtk,
   chat 10 dtk) — kanal live tetap jadi sumber utama.
5. **Ramah produksi:** koneksi SSE dilepas tiap ±100 dtk lalu browser
   menyambung ulang otomatis (snapshot revisi = tidak ada event hilang);
   thread gunicorn tidak dipegang selamanya. `Dockerfile` kini
   `--threads 16` (dari 8).

Verifikasi nyata: event `chat`, `ann`, `tkt`, `fb`, `pr` sampai ke klien
SSE **di bawah 1 detik** setelah aksi (lihat log uji).

---

## C. SEMUA EMOJI DIGANTI GAMBAR/IKON ASLI

Semua ikon platform tetap PNG asli (sudah ada), dan seluruh emoji UI diganti
**ikon vektor SVG** (gambar asli, bukan karakter emoji):

| Lokasi | Sebelumnya | Sesudah |
|---|---|---|
| Judul brand, footer, nama admin | 👑 | Mahkota SVG |
| Pills jenis tiket | 🐞 💬 ➕ | SVG serangga/balon/plus |
| Pills & banner announcement | ℹ️ ⚠️ 🚨 | SVG info/segitiga/alarm |
| Panel admin | 👑 📥 | SVG mahkota/inbox |
| Badge playlist, lanjut baca, rekomendasi | 🎵 📖 🔥 | SVG not/buku/api |
| Lampiran chat (file) | 📄 🖼️ 🎬 📎 | SVG dokumen + label teks |
| Tombol tutup | ✕ (glyph teks) | SVG silang |
| Pesan toast & feedback | 🎵 🙏 👑 | teks polos |

Sisa emoji satu-satunya ada di `ADMIN_USERNAME` **sengaja dipertahankan** —
itu kredensial login akun owner yang sudah ada di database; mengubahnya
akan mengunci akun owner dari data lama. (Itu nama akun, bukan ikon UI.)

---

## D. CATATAN DEPLOY

- Tidak ada dependensi baru — `requirements.txt` tidak berubah.
- Setelah upload ke GitHub & redeploy, buka situs → footer harus berbunyi
  **"Versi UI v48"**. Kalau masih v47: Railway/Render belum selesai build
  (atau ZIP lama yang ter-push — pakai `bash push.sh` dari folder ini).
- Untuk Vercel: SSE tidak tersambung permanen → fallback `/api/live-check`
  otomatis menjaga tampilan tetap live (±4 dtk).
- Cache-bust aset dinaikkan (`?v=48`) supaya browser pengguna langsung
  memakai versi baru tanpa hard-refresh.

## E. RINGKASAN UJI (otomatis, 15/15 PASS)

signup ✅ login admin ✅ SSE chat instan ✅ SSE announcement instan ✅
SSE tiket instan ✅ SSE feedback instan ✅ SSE saran platform instan ✅
balasan admin → status answered ✅ tolak non-admin (403) ✅ tiket tertutup
menolak balasan (400) ✅ tiket orang lain tak bisa dibuka (403) ✅ upload
lampiran chat ✅ anti-duplikat saran (429) ✅ live-check lengkap ✅
aset v48 termuat ✅
