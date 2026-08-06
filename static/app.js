/* KINGS DOWNLOADER — frontend logic */

// Nomor versi UI (build). NAIKKAN 1 tiap rombak frontend — tampil di footer
// supaya bisa dicek tanpa buka inspect element. Kunci dari "cara ngecek bump".
const UI_VERSION = 43;

const $ = (s) => document.querySelector(s);

let currentInfo = null;
let pollTimer = null;
let resSel = '1080';   // resolusi terpilih (default 1080p)
let dlStart = 0;       // waktu mulai download (untuk timer menit:detik)
let metaTimer = null;   // timer saat menunggu metadata
let metaStart = 0;

function fmtDur(sec) {
    sec = Math.max(0, Math.floor(sec || 0));
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    return h ? h + ':' + String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0')
             : m + ':' + String(s).padStart(2, '0');
}
function tickProgressTime() {
    const el = document.getElementById('progress-time');
    if (el && dlStart) el.textContent = fmtDur((Date.now() - dlStart) / 1000);
}

function setRes(btn) {
    resSel = btn.dataset.v;
    document.querySelectorAll('#res-pills .res-pill').forEach(b => b.classList.toggle('active', b === btn));
    const lbl = document.getElementById('res-selected');
    if (lbl) lbl.textContent = btn.textContent.trim();
}

/* ---------- Helper ---------- */
function esc(str) {
    return String(str == null ? '' : str)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

async function fetchJSON(url, opts) {
    // Timeout 100 detik — kalau server lambat, jangan biarkan request
    // menggantung selamanya (kartu progress "Antre" yang nyangkut).
    const ctl = new AbortController();
    const tm = setTimeout(() => ctl.abort(), 100000);
    // Otomatis bawa token login (X-Auth-Token) untuk endpoint yang butuh akun
    // (playlist, riwayat manga, chat, dll) — selama header belum di-set manual.
    const merged = Object.assign({}, opts || {});
    const hasAuth = (merged.headers || {})['X-Auth-Token'];
    if (_authToken && !hasAuth) {
        merged.headers = Object.assign({}, merged.headers || {}, { 'X-Auth-Token': _authToken });
    }
    try {
        const r = await fetch(url, { ...merged, signal: ctl.signal });
        let j = {};
        try { j = await r.json(); } catch (e) { /* ignore */ }
        if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
        return j;
    } finally {
        clearTimeout(tm);
    }
}

/* Tutup kartu progress/done apa pun (home #progress-card maupun #music-progress).
   Dipanggil dari tombol ✕ di dalam kartu, atau dari saveFile(). */
function dismissCard(btn) {
    const card = btn && btn.closest ? btn.closest('.progress-card') : null;
    const box = card || document.querySelector('#progress-card');
    if (!box) return;
    if (box.dataset.jobId) unregisterPoll(box.dataset.jobId);
    box.classList.add('fading');
    setTimeout(() => {
        box.classList.add('hidden');
        box.innerHTML = '';
        box.removeAttribute('data-jobId');
    }, 240);
}

function dismissDoneCard() {
    dismissCard(document.querySelector('.done-close'));
}

function toast(msg, isError) {
    const t = $('#toast');
    t.textContent = msg;
    t.classList.toggle('err', !!isError);
    t.classList.add('show');
    clearTimeout(t._tm);
    t._tm = setTimeout(() => t.classList.remove('show'), isError ? 5000 : 2600);
}

function fmtMB(mb) { return mb != null ? mb + ' MB' : '—'; }

function svg(path, cls) {
    return `<svg class="${cls}" viewBox="0 0 24 24"><path d="${path}"/></svg>`;
}
/* ikon (stroke) fungsional, bukan emoji */
const IC = {
    download: '<svg class="ic" viewBox="0 0 24 24"><path d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg>',
    music:    '<svg class="ic" viewBox="0 0 24 24"><path d="M9 18V5l12-2v13M9 18a3 3 0 11-6 0 3 3 0 016 0zm12-2a3 3 0 11-6 0 3 3 0 016 0z"/></svg>',
    film:     '<svg class="ic" viewBox="0 0 24 24"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="M7 4v16M17 4v16M2 9h5M2 15h5M17 9h5M17 15h5"/></svg>',
    image:    '<svg class="ic" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>',
    check:    '<svg class="ic" viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg>',
    play:     '<svg class="ic" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>',
    pause:    '<svg class="ic" viewBox="0 0 24 24"><path d="M6 5h4v14H6zM14 5h4v14h-4z"/></svg>',
};

/* ---------- View switching ---------- */
// Job yang sedang aktif di-poll (biar tahu kartu mana yang masih "hidup"
// dan mana yang nyangkut/sudah mati — yang mati wajib dibersihkan).
let activePolls = new Set();

function showView(name) {
    $('#view-home').classList.toggle('hidden', name !== 'home');
    $('#view-music').classList.toggle('hidden', name !== 'music');
    $('#view-manga').classList.toggle('hidden', name !== 'manga');
    $('#view-news').classList.toggle('hidden', name !== 'news');
    $('#view-chat').classList.toggle('hidden', name !== 'chat');
    $('#view-about').classList.toggle('hidden', name !== 'about');
    // aktifkan tombol nav di atas (Beranda/Musik/Cara Pakai)
    document.querySelectorAll('[data-nav]').forEach(b =>
        b.classList.toggle('active', b.dataset.nav === name));

    // Bersihkan elemen yang nyangkut dari aktivitas SEBELUMNYA supaya tidak
    // ada kotak kosong yang menutupi tab:
    //  - state-box (kotak "Mengambil metadata…") jangan menetap
    const sb = $('#state-box');
    if (sb && !sb.classList.contains('hidden')) {
        stopBusy();
        sb.classList.add('hidden');
        sb.innerHTML = '';
    }
    if (name === 'manga') {
        // Saat masuk tab Manga dari level daftar (home/search), refresh —
        // supaya "Lanjut Baca" selalu terbaru. Kalau lagi baca/detail, biarkan.
        if (_mangaView === 'home' || _mangaView === 'search') {
            const q = document.getElementById('manga-q').value.trim();
            if (q) mangaSearch(); else mangaRecommend();
        }
    }
    if (name === 'music') {
        //  - kartu progress musik yang sudah tidak di-poll (nyangkut) → buang
        const mp = $('#music-progress');
        if (mp) {
            const jid = mp.dataset.jobId || mp.dataset.jobid;
            if (!jid || !activePolls.has(jid)) {
                mp.remove();
            }
        }
        //  - kartu done yang sudah tidak aktif → buang
        document.querySelectorAll('#music-results .progress-card[data-jobdone="1"]').forEach(el => el.remove());
        // CATATAN: TIDAK ada auto-focus di sini — keyboard tidak boleh
        // muncul sendiri saat masuk tab musik.
    } else if (name === 'home') {
        //  - kartu progress home yang SUDAH SELESAI & sudah tidak di-poll →
        //    tetap tampil (user masih bisa klik "Simpan file") sampai ditutup
        //    via ✕ / setelah menyimpan.
        //  - kartu yang NYANGKUT (belum selesai tapi tidak di-poll) → sembunyikan
        const pc = $('#progress-card');
        if (pc && !pc.querySelector('.done-card')) {
            const jid = pc.dataset.jobId || pc.dataset.jobid;
            if (!jid || !activePolls.has(jid)) {
                pc.classList.add('hidden');
            }
        }
    }
}

function registerPoll(jobId) {
    activePolls.add(jobId);
}
function unregisterPoll(jobId) {
    activePolls.delete(jobId);
}

/* ---------- Pilihan manual platform (opsional) ---------- */
// State pilihan platform manual ('' = otomatis)
let _platKey = '';
let _platName = '';

function platformGet() { return _platKey; }

function setPlatformSel(key, name) {
    _platKey = key || '';
    _platName = name || '';
    // update label tombol dropdown
    const val = document.getElementById('plat-value');
    if (val) {
        if (_platKey && _platName) {
            val.innerHTML = `<img class="dd-opt-ic" src="/static/icons/${esc(_platKey)}.png" alt=""> <span>${esc(_platName)}</span>`;
        } else {
            val.innerHTML = `<span class="dd-auto-ic"><svg viewBox="0 0 24 24" class="ic"><path d="M12 3v18M3 12h18M5.6 5.6l12.8 12.8M18.4 5.6L5.6 18.4"/></svg></span> <span>Otomatis</span>`;
        }
    }
    // highlight opsi aktif di menu
    document.querySelectorAll('#plat-menu .dd-opt').forEach(o =>
        o.classList.toggle('active', o.dataset.v === _platKey));
    // hint
    const hint = document.getElementById('platform-hint');
    if (hint) {
        hint.textContent = _platKey
            ? 'Platform di-set ke ' + _platName + ' — analisis akan memakainya.'
            : 'Pilih manual hanya kalau perlu — otomatis sudah jalan sendiri.';
    }
}

function ddToggle(ev) {
    ev && ev.stopPropagation();
    const menu = document.getElementById('plat-menu');
    const dd = document.getElementById('plat-dd');
    if (!menu) return;
    const open = menu.classList.toggle('hidden');
    dd.classList.toggle('open', !open);
    const btn = dd.querySelector('.dd-btn');
    if (btn) btn.setAttribute('aria-expanded', String(!open));
}

function ddPick(key) {
    const opt = document.querySelector(`#plat-menu .dd-opt[data-v="${CSS.escape(key)}"]`);
    const name = opt ? opt.querySelector('.dd-opt-t').textContent : (key || '');
    setPlatformSel(key, name);
    if (key) toast('Platform: ' + name + ' (manual)');
    else toast('Platform: Otomatis');
    document.getElementById('plat-menu').classList.add('hidden');
    document.getElementById('plat-dd').classList.remove('open');
}

function pickPlatform(key, name) {
    setPlatformSel(key, name);
    // TIDAK fokus ke input — keyboard tidak boleh muncul sendiri.
}

/* ---------- Isi cepat (quick chips di hero) ----------
   Klik chip = set platform manual + isi URL dasar platform (user tinggal ganti
   dengan link aslinya). */
function quickFill(key, name) {
    setPlatformSel(key, name);
    const home = PLATFORM_HOME[key] || ('https://' + key + '.com/');
    const input = $('#url-input');
    input.value = home;
    // TIDAK fokus — biarkan user yang mengetuk kolom kalau mau
    input.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

// URL dasar tiap platform (untuk quick chips)
const PLATFORM_HOME = {
    youtube: 'https://www.youtube.com/',
    tiktok: 'https://www.tiktok.com/', instagram: 'https://www.instagram.com/',
    facebook: 'https://www.facebook.com/', x: 'https://x.com/',
    pinterest: 'https://www.pinterest.com/', spotify: 'https://open.spotify.com/',
    dailymotion: 'https://www.dailymotion.com/', soundcloud: 'https://soundcloud.com/',
    archiveorg: 'https://archive.org/', twitch: 'https://www.twitch.tv/',
    bandcamp: 'https://bandcamp.com/', mixcloud: 'https://www.mixcloud.com/',
    streamable: 'https://streamable.com/', bilibili: 'https://www.bilibili.com/',
    vimeo: 'https://vimeo.com/', snackvideo: 'https://www.snackvideo.com/',
    rednote: 'https://www.xiaohongshu.com/explore', videy: 'https://videy.co/',
    github: 'https://github.com/', mediafire: 'https://www.mediafire.com/',
    threads: 'https://www.threads.net/', snapchat: 'https://www.snapchat.com/',
    reddit: 'https://www.reddit.com/', douyin: 'https://www.douyin.com/',
    rutube: 'https://rutube.ru/',
};

/* Warna brand per platform (untuk tile berwarna di grid) */
const PLATFORM_BRAND = {
    youtube: '#ff0000',
    tiktok: '#25f4ee', instagram: '#e1306c', facebook: '#1877f2',
    x: '#e7e9ea', pinterest: '#e60023', spotify: '#1db954',
    dailymotion: '#00aaff', soundcloud: '#ff5500', archiveorg: '#ffffff',
    twitch: '#9146ff', bandcamp: '#1da0c3', mixcloud: '#5000ff',
    streamable: '#0f90fa', bilibili: '#00a1d6',
    vimeo: '#1ab7ea', snackvideo: '#ffb800',
    rednote: '#ff2442', videy: '#2f2f2f',
    github: '#181717', mediafire: '#1e90ff', threads: '#000000',
    snapchat: '#fffc00', reddit: '#ff4500', douyin: '#000000',
    rutube: '#e62e1d',
};

/* ---------- Init ---------- */
async function init() {
    try {
        const data = await fetchJSON('/api/platforms');
        // Nomor versi UI — kelihatan di footer, buat cek "bump" dengan mudah.
        const uv = document.getElementById('ui-version');
        if (uv) uv.textContent = UI_VERSION;

        // Grid platform — tile vertikal dengan glow warna brand.
        // Klik kartu = pilih platform manual (opsional; otomatis tetap default).
        const grid = $('#platform-grid');
        grid.innerHTML = data.platforms.map(p => {
            const brand = PLATFORM_BRAND[p.key] || '#6366f1';
            return `<div class="platform-card" data-key="${esc(p.key)}" style="--brand:${brand}"
                     title="Pilih ${esc(p.name)} sebagai platform (manual)" onclick="pickPlatform('${esc(p.key)}', '${esc(p.name)}')">
                <div class="pc-logo"><img src="${esc(p.icon)}" alt="${esc(p.name)}" loading="lazy"
                     onerror="this.src='/static/img/logo_64.png'"></div>
                <span class="name">${esc(p.name)}</span>
            </div>`;
        }).join('');
        const cnt = document.getElementById('platform-count');
        if (cnt) cnt.textContent = data.platforms.length + ' platform';

        // Isi MENU dropdown custom (logo tiap platform)
        const menu = document.getElementById('plat-menu');
        if (menu) {
            menu.insertAdjacentHTML('beforeend', data.platforms.map(p =>
                `<button type="button" class="dd-opt" data-v="${esc(p.key)}" onclick="ddPick('${esc(p.key)}')" role="option">
                    <span class="dd-opt-ic"><img src="${esc(p.icon)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'"></span>
                    <span class="dd-opt-t">${esc(p.name)}</span>
                    <span class="dd-opt-d">${esc(p.name)}</span>
                </button>`).join(''));
        }
        // Tutup dropdown kalau klik di luar
        document.addEventListener('click', () => {
            const m = document.getElementById('plat-menu');
            if (m && !m.classList.contains('hidden')) {
                m.classList.add('hidden');
                document.getElementById('plat-dd').classList.remove('open');
            }
        });

        // Isi quick chips — SEMUA platform (klik = set manual + isi URL dasar)
        const qc = document.getElementById('quick-chips');
        if (qc) {
            qc.insertAdjacentHTML('beforeend', data.platforms.map(p =>
                `<button class="qc" data-key="${esc(p.key)}" onclick="quickFill('${esc(p.key)}', '${esc(p.name)}')">
                    <img src="${esc(p.icon)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">
                    ${esc(p.name)}
                </button>`).join(''));
        }
    } catch (e) {
        toast('Ups, gagal memuat data: ' + e.message, true);
    }

    $('#url-input').addEventListener('keydown', (ev) => { if (ev.key === 'Enter') handleSearch(); });
    const mq = document.getElementById('music-q');
    if (mq) mq.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') musicSearch(); });
    const mgq = document.getElementById('manga-q');
    if (mgq) mgq.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') mangaSearch(); });
    const nq = document.getElementById('news-q');
    if (nq) {
        nq.addEventListener('input', () => newsFilterNow());
        nq.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') newsSearchEnter(); });
    }

    // Bersihkan elemen sisa dari sesi sebelumnya (kalau ada) supaya tab selalu
    // bersih saat pertama dibuka — tidak ada kotak progress/done yang nyangkut.
    const mr = document.getElementById('music-results');
    if (mr) {
        mr.querySelectorAll('.progress-card').forEach(el => el.remove());
    }
    const pc0 = document.getElementById('progress-card');
    if (pc0) {
        pc0.classList.add('hidden');
        pc0.innerHTML = '';
    }
    const sb0 = document.getElementById('state-box');
    if (sb0) {
        sb0.classList.add('hidden');
        sb0.innerHTML = '';
    }
}

/* ---------- Analisis URL ---------- */
async function handleSearch() {
    const url = $('#url-input').value.trim();
    if (!url) { toast('Tempel tautan URL terlebih dahulu', true); return; }
    if (!/^https?:\/\//i.test(url)) { toast('URL harus diawali http:// atau https://', true); return; }

    $('#info-card').classList.add('hidden');
    $('#progress-card').classList.add('hidden');
    document.querySelectorAll('.platform-card').forEach(c => c.classList.remove('hl'));

    showLoading('Mengambil metadata…');

    // Pilihan manual platform (opsional): default otomatis
    const plat = platformGet();

    try {
        let apiUrl = '/api/info?url=' + encodeURIComponent(url);
        if (plat) apiUrl += '&platform=' + encodeURIComponent(plat);
        const info = await fetchJSON(apiUrl);
        currentInfo = info;
        renderInfo(info);
    } catch (e) {
        currentInfo = null;
        showError(e.message);
    }
}

function showBusy(box, text) {
    box.classList.remove('hidden');
    box.innerHTML = `
        <div class="meta-loading">
            <div class="meta-head">
                <span class="meta-label">${esc(text)}</span>
                <span class="progress-time" id="meta-time">0:00</span>
            </div>
            <div class="progress-track"><div class="progress-fill meta-indet" id="meta-fill"></div></div>
            <p class="progress-msg" id="meta-msg">Mengambil data dari server…</p>
        </div>`;
    metaStart = Date.now();
    clearInterval(metaTimer);
    metaTimer = setInterval(() => {
        const el = document.getElementById('meta-time');
        if (el) el.textContent = fmtDur((Date.now() - metaStart) / 1000);
    }, 500);
    box.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function stopBusy() {
    clearInterval(metaTimer);
    metaTimer = null;
}

function showLoading(text) {
    showBusy($('#state-box'), text);
}
function showError(msg) {
    stopBusy();
    $('#state-box').classList.remove('hidden');
    $('#state-box').innerHTML = `
        <div class="state-error">
            <b>Ups, ada kendala</b>
            <p>${esc(msg)}</p>
        </div>`;
    $('#state-box').scrollIntoView({ behavior: 'smooth', block: 'center' });
}
function hideState() { stopBusy(); $('#state-box').classList.add('hidden'); }

/* ---------- Render info ---------- */
function renderInfo(info) {
    currentInfo = info;   // selalu sinkronkan (aman dipanggil dari mana pun)
    hideState();

    if (info.platform && info.platform.key) {
        const card = document.querySelector(`.platform-card[data-key="${info.platform.key}"]`);
        if (card) card.classList.add('hl');
    }

    const thumb = info.thumbnail
        ? `<img src="/api/thumbnail?url=${encodeURIComponent(info.thumbnail)}"
                onerror="this.parentElement.innerHTML='<div class=no-thumb></div>'">`
        : '<div class="no-thumb"></div>';

    const platformChip = info.platform
        ? `<span class="chip"><img src="${esc(info.platform.icon)}" alt="">${esc(info.platform.name)}</span>`
        : `<span class="chip">Lainnya</span>`;

    let options = '';
    if (info.has_video) options += `<option value="best">Video terbaik — video + audio</option>`;
    options += `<option value="mp3">Audio MP3 — 192 kbps</option>`;
    options += `<option value="m4a">Audio M4A — kualitas asli</option>`;
    const rawFormats = (info.formats || []).filter(f => !['mp3', 'bestaudio'].includes(f.format_id));
    if (rawFormats.length) {
        options += `<optgroup label="Format mentah (detail)">`;
        rawFormats.forEach(f => { options += `<option value="custom:${esc(f.format_id)}">${esc(f.label)}</option>`; });
        options += `</optgroup>`;
    }

    // Resolusi: pill buttons (default 1080p)
    const resList = [
        { v: 'original', l: 'Asli' },
        { v: '2160', l: '4K' },
        { v: '1440', l: '2K' },
        { v: '1080', l: '1080p' },
        { v: '720', l: '720p' },
        { v: '480', l: '480p' },
        { v: '360', l: '360p' },
    ];

    const note = info.note ? `<p class="info-note">${esc(info.note)}</p>` : '';

    // Grid preview untuk foto/story/slideshow (Instagram, X, Facebook, TikTok)
    // Setiap media punya checkbox → pilih mana yang mau diunduh (default: semua).
    let galleryHtml = '';
    if (info.has_image && info.images && info.images.length) {
        const thumbs = info.images.map((it, idx) => {
            const icon = it.type === 'video' ? `<span class="g-item-video">${IC.film}</span>` : '';
            const label = it.type === 'video' ? 'Video' : 'Foto';
            const tSrc = it.thumbnail || it.url;
            return `<div class="g-item">
                <input type="checkbox" class="g-check" id="gcheck-${idx}" checked
                       onchange="galleryUpdateCount()" title="Pilih media ini">
                <img src="/api/thumbnail?url=${encodeURIComponent(tSrc)}" loading="lazy"
                     onerror="this.parentElement.classList.add('g-no-thumb')">
                ${icon}
                <button class="btn ghost g-dl" onclick="downloadMedia(${idx})" title="Unduh media ini">${IC.download} ${label}</button>
            </div>`;
        }).join('');
        galleryHtml = `<div class="gallery-block">
            <div class="actions" style="margin-bottom:12px;align-items:center">
                <button class="btn primary" onclick="startGalleryDownload()" id="gallery-dl-btn">${IC.download} Unduh dipilih (ZIP) — ${info.images.length} media</button>
                <button class="btn ghost" onclick="galleryToggleAll()">Pilih / batal semua</button>
                <span class="muted" id="gallery-count" style="font-size:12px"></span>
            </div>
            <div class="gallery-grid">${thumbs}</div>
        </div>`;
    }

    const hasCustom = info.has_video || rawFormats.length > 0;
    const resBlock = info.has_video ? `
        <div class="res-block">
            <div class="res-head">
                <span class="res-label-text">Resolusi</span>
                <span class="res-selected" id="res-selected">${esc(resList.find(r => r.v === resSel)?.l || '1080p')}</span>
            </div>
            <div class="res-pills" id="res-pills">
                ${resList.map(r => `<button type="button" class="res-pill${r.v === resSel ? ' active' : ''}" data-v="${r.v}" onclick="setRes(this)">${r.l}</button>`).join('')}
            </div>
        </div>` : '';
    const formatRow = hasCustom ? `
        <div class="format-row">
            ${resBlock}
            <select id="format-select">${options}</select>
            <button class="btn primary" onclick="downloadSelected()">${IC.download} Download pilihan</button>
        </div>` : '';

    const metaMediaCount = info.has_image
        ? `<div class="meta-item"><span class="k">Media</span><div class="v">${info.image_count} foto${info.video_count ? ' + ' + info.video_count + ' video' : ''}</div></div>
           <div class="meta-item"><span class="k">Views</span><div class="v">${info.view_count != null ? Number(info.view_count).toLocaleString('id-ID') : '—'}</div></div>
           <div class="meta-item"><span class="k">Suka</span><div class="v">${info.like_count != null ? Number(info.like_count).toLocaleString('id-ID') : '—'}</div></div>`
        : `<div class="meta-item"><span class="k">Views</span><div class="v">${info.view_count != null ? Number(info.view_count).toLocaleString('id-ID') : '—'}</div></div>
           ${info.like_count != null ? `<div class="meta-item"><span class="k">Suka</span><div class="v">${Number(info.like_count).toLocaleString('id-ID')}</div></div>` : ''}`;

    $('#info-card').classList.remove('hidden');
    const upName = (info.uploader && info.uploader !== 'Unknown')
        ? esc(info.uploader) : '—';

    $('#info-card').innerHTML = `
        <div class="info-media">
            ${thumb}
            <span class="info-dur">${esc(info.duration_text || '—')}</span>
        </div>
        <div class="info-body">
            <p class="info-eyebrow">Hasil analisis</p>
            <div class="info-head">
                <h2 class="info-title no-translate">${esc(info.title)}</h2>
                ${platformChip}
            </div>
            ${note}
            <div class="meta-row">
                <div class="meta-item"><span class="k">Uploader</span><div class="v">${upName}</div></div>
                <div class="meta-item"><span class="k">Durasi</span><div class="v">${esc(info.duration_text || '—')}</div></div>
                <div class="meta-item"><span class="k">Resolusi</span><div class="v">${info.max_height ? info.max_height + 'p' : '—'}</div></div>
                ${metaMediaCount}
            </div>
            <div class="actions">
                ${info.has_video ? `<button class="btn primary" onclick="startDownload('best')">${IC.download} Video terbaik</button>` : ''}
                ${!info.has_image ? `<button class="btn ghost" onclick="startDownload('mp3')">${IC.music} MP3</button>` : ''}
                ${!info.has_image ? `<button class="btn ghost" onclick="startDownload('m4a')">${IC.music} M4A</button>` : ''}
                ${info.thumbnail && !info.has_image ? `<button class="btn ghost" onclick="downloadThumbnail()">${IC.image} Thumbnail</button>` : ''}
            </div>
            ${formatRow}
            ${galleryHtml}
        </div>`;
    $('#info-card').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* ---------- Download galeri (foto/album Instagram) ---------- */
function gallerySelectedItems() {
    if (!currentInfo || !currentInfo.images) return [];
    return currentInfo.images
        .map((it, idx) => {
            const cb = document.getElementById('gcheck-' + idx);
            if (cb && !cb.checked) return null;
            return { url: it.url, ext: it.ext };
        })
        .filter(Boolean);
}

function galleryUpdateCount() {
    const n = gallerySelectedItems().length;
    const total = currentInfo && currentInfo.images ? currentInfo.images.length : 0;
    const btn = document.getElementById('gallery-dl-btn');
    if (btn) btn.innerHTML = `${IC.download} Unduh dipilih (ZIP) — ${n} dari ${total} media`;
    const cnt = document.getElementById('gallery-count');
    if (cnt) cnt.textContent = n ? `${n} media terpilih` : 'Tidak ada yang dipilih';
}

function galleryToggleAll() {
    const all = currentInfo && currentInfo.images ? currentInfo.images.length : 0;
    let anyUnchecked = false;
    for (let i = 0; i < all; i++) {
        const cb = document.getElementById('gcheck-' + i);
        if (cb && !cb.checked) { anyUnchecked = true; break; }
    }
    for (let i = 0; i < all; i++) {
        const cb = document.getElementById('gcheck-' + i);
        if (cb) cb.checked = anyUnchecked; // jika ada yang belum dicentang → centang semua; jika sudah semua → batal
    }
    galleryUpdateCount();
}

async function startGalleryDownload() {
    if (!currentInfo) return;
    const selected = gallerySelectedItems();
    if (!selected.length) { toast('Tidak ada media yang dipilih', true); return; }

    dlStart = Date.now();
    $('#progress-card').classList.remove('hidden');
    $('#progress-card').innerHTML = `
        <div class="progress-head">
            <span class="progress-title no-translate">${esc(currentInfo.title)}</span>
                <span class="progress-status" id="progress-status">Antre</span>
                <span class="progress-time" id="progress-time">0:00</span>
                <button class="done-close card-close" onclick="dismissCard(this)" title="Tutup" aria-label="Tutup">
                    <svg viewBox="0 0 24 24"><path d="M18 6L6 18M6 6l12 12" style="fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round"/></svg>
                </button>
            </div>
            <div class="progress-track"><div class="progress-fill" id="progress-fill"></div></div>
            <p class="progress-msg" id="progress-msg">Menyiapkan unduhan ${selected.length} media…</p>
            <p class="progress-eta" id="progress-eta"></p>`;

    $('#progress-card').scrollIntoView({ behavior: 'smooth', block: 'center' });
    try {
        const res = await fetchJSON('/api/gallery-download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: currentInfo.webpage_url, title: currentInfo.title || '', items: selected }),
        });
        pollJob(res.job_id);
    } catch (e) {
        toast('Gagal memulai unduhan: ' + e.message, true);
        $('#progress-card').classList.add('hidden');
    }
}

async function downloadMedia(idx) {
    const it = currentInfo && currentInfo.images && currentInfo.images[idx];
    if (!it) return;
    toast('Mengunduh media…');
    try {
        // fetch dulu: hanya unduh kalau server benar-benar mengirim file media
        const r = await fetch('/api/thumbnail?url=' + encodeURIComponent(it.url) + '&dl=1');
        if (!r.ok) {
            let msg = 'Server menolak media ini';
            try { const j = await r.json(); if (j.error) msg = j.error; } catch (e) {}
            toast(msg, true);
            return;
        }
        const blob = await r.blob();
        const cd = r.headers.get('Content-Disposition') || '';
        let fname = 'media';
        const m = cd.match(/filename="?([^";]+)"?/);
        if (m) fname = m[1];
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = fname;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 4000);
        toast('Media terunduh');
    } catch (e) {
        toast('Gagal mengunduh media', true);
    }
}

/* ---------- Download ---------- */
async function startDownload(mode) {
    if (!currentInfo) return;
    await beginDownload(mode, null);
}

async function downloadSelected() {
    if (!currentInfo) return;
    const val = $('#format-select').value;
    if (val.startsWith('custom:')) await beginDownload('custom', val.slice(7));
    else await beginDownload(val, null);
}

async function beginDownload(mode, formatId) {
    dlStart = Date.now();
    $('#progress-card').classList.remove('hidden');
    $('#progress-card').innerHTML = `
        <div class="progress-head">
            <span class="progress-title no-translate">${esc(currentInfo ? currentInfo.title : 'Mengunduh…')}</span>
                <span class="progress-status" id="progress-status">Antre</span>
                <span class="progress-time" id="progress-time">0:00</span>
                <button class="done-close card-close" onclick="dismissCard(this)" title="Tutup" aria-label="Tutup">
                    <svg viewBox="0 0 24 24"><path d="M18 6L6 18M6 6l12 12" style="fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round"/></svg>
                </button>
            </div>
            <div class="progress-pctwrap">
                <span class="progress-pct" id="progress-pct">0%</span>
                <div class="progress-track"><div class="progress-fill" id="progress-fill"></div></div>
            </div>
            <p class="progress-msg" id="progress-msg">Memulai download…</p>
            <p class="progress-eta" id="progress-eta"></p>`;

    $('#progress-card').scrollIntoView({ behavior: 'smooth', block: 'center' });

    // Baca resolusi yang dipilih (default 1080p)
    let resolution = resSel || '1080';

    // Ikutkan pilihan manual platform (kalau ada) biar download konsisten
    const platSel = platformGet();

    try {
        const res = await fetchJSON('/api/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                url: currentInfo.webpage_url,
                mode: mode,
                format_id: formatId || undefined,
                title: currentInfo.title || '',
                resolution: resolution,
                platform: platSel || undefined,
            }),
        });
        pollJob(res.job_id);
    } catch (e) {
        toast('Gagal memulai download: ' + e.message, true);
        $('#progress-card').classList.add('hidden');
    }
}

async function pollJob(jobId) {
    clearInterval(pollTimer);
    registerPoll(jobId);
    const pc = $('#progress-card');
    if (pc) pc.dataset.jobId = jobId;
    let fails = 0;
    pollTimer = setInterval(async () => {
        if ($('#progress-card').classList.contains('hidden')) { clearInterval(pollTimer); unregisterPoll(jobId); return; }
        try {
            const j = await fetchJSON('/api/job/' + jobId);
            fails = 0;
            const map = { downloading: 'Mengunduh…', processing: 'Memproses…', done: 'Selesai', error: 'Gagal', queued: 'Antre' };
            $('#progress-status').textContent = map[j.status] || j.status;
            $('#progress-msg').textContent = j.message || '';
            const etaEl = $('#progress-eta');
            if (etaEl) {
                const m = (j.message || '').match(/sisa (\d+:\d{2})/);
                etaEl.textContent = m ? 'Perkiraan selesai: ' + m[1] : '';
            }
            $('#progress-fill').style.width = Math.min(100, Math.max(0, j.progress || 0)) + '%';
            const pctEl = $('#progress-pct');
            if (pctEl) pctEl.textContent = Math.round(j.progress || 0) + '%';
            tickProgressTime();

            if (j.status === 'done') {
                clearInterval(pollTimer);
                unregisterPoll(jobId);
                const totalT = fmtDur((Date.now() - dlStart) / 1000);
                $('#progress-fill').style.width = '100%';
                revealDoneCard(jobId, j, totalT);
                toast('Download selesai dalam ' + totalT + '! Klik Simpan file.');
            } else if (j.status === 'error') {
                clearInterval(pollTimer);
                unregisterPoll(jobId);
                toast('Ups, download gagal', true);
                $('#progress-card').innerHTML = `
                    <div class="state-error">
                        <b>Ups, download gagal</b>
                        <p>${esc(j.error || 'Kesalahan tidak diketahui')}</p>
                    </div>`;
            }
        } catch (e) {
            // Gagal sesaat JANGAN mematikan polling (biar tidak "stuck sampai
            // refresh"). Lewati siklus; kalau gagal terus-menerus baru berhenti.
            fails++;
            if (fails >= 8) {
                clearInterval(pollTimer);
                unregisterPoll(jobId);
                toast('Koneksi ke server terputus sesaat. Muat ulang halaman untuk melihat status.', true);
            }
        }
    }, 1200);
}

function saveFile(jobId, btn) {
    const a = document.createElement('a');
    a.href = '/api/file/' + jobId;
    a.download = '';
    document.body.appendChild(a);
    a.click();
    a.remove();
    // Setelah file tersimpan, tutup kartu "Selesai" (yang berisi tombol ini)
    dismissCard(btn);
}

function dismissDoneCard() {
    dismissCard(document.querySelector('.done-close'));
}

/* Markup kartu "Selesai" — dipakai di semua alur download (home & musik).
   Ada tombol ✕ Tutup supaya kartu bisa dihilangkan, dan auto-scroll
   memastikan kartu tampil penuh (tidak terpotong bar bawah / nav mobile). */
function doneCardHtml(jobId, j, totalT) {
    // CATATAN: dipakai sebagai innerHTML dari container berclass .progress-card
    // (home #progress-card maupun #music-progress), jadi di sini TIDAK boleh
    // membungkus lagi dengan .progress-card — cukup isi done-card-nya.
    return `
        <div class="done-card">
            <button class="done-close" onclick="dismissCard(this)" title="Tutup" aria-label="Tutup">
                <svg viewBox="0 0 24 24"><path d="M18 6L6 18M6 6l12 12" style="fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round"/></svg>
            </button>
            <div class="done-icon">${IC.check}</div>
            <div class="done-info">
                <b>Selesai dalam ${totalT}</b>
                <p>${esc(j.filename)} — ${fmtMB(j.filesize_mb)}${j.duration_text ? ' — Durasi asli ' + esc(j.duration_text) : ''}</p>
            </div>
            <button class="btn success" onclick="saveFile('${jobId}', this)">${IC.download} Simpan file</button>
        </div>`;
}

function revealDoneCard(jobId, j, totalT) {
    const box = document.querySelector('#progress-card');
    if (!box) return;
    box.classList.remove('fading', 'hidden');
    box.innerHTML = doneCardHtml(jobId, j, totalT);
    // Scroll ulang supaya kartu selesai tampil PENUH di layar (bukan terpotong).
    box.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

async function downloadThumbnail() {
    if (!currentInfo || !currentInfo.thumbnail) return;
    toast('Mengunduh thumbnail…');
    try {
        const r = await fetch('/api/thumbnail?url=' + encodeURIComponent(currentInfo.thumbnail) + '&dl=1');
        if (!r.ok) { toast('Server menolak thumbnail ini', true); return; }
        const blob = await r.blob();
        const cd = r.headers.get('Content-Disposition') || '';
        let fname = 'thumbnail.jpg';
        const m = cd.match(/filename="?([^";]+)"?/);
        if (m) fname = m[1];
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = fname;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 4000);
    } catch (e) {
        toast('Gagal mengunduh thumbnail', true);
    }
}

/* ============================================================
   MUSIK — pencarian & unduhan lagu via YouTube Music
   ============================================================ */
let musicFilter = 'songs';

function musicSetFilter(f) {
    musicFilter = f;
    document.querySelectorAll('#music-filters .pill').forEach(p => {
        p.classList.toggle('active', p.dataset.f === f);
    });
    const q = $('#music-q').value.trim();
    if (q) musicSearch();
    else {
        $('#music-results').innerHTML = '';
    }
}

async function musicSearch() {
    const q = $('#music-q').value.trim();
    if (!q) { toast('Tulis judul lagu, artis, atau album dulu', true); return; }
    const box = $('#music-results');
    showBusy(box, 'Mencari di YouTube Music…');
    try {
        const data = await fetchJSON('/api/music-search?q=' + encodeURIComponent(q) + '&filter=' + musicFilter);
        stopBusy();
        if (!data.results || !data.results.length) {
            box.innerHTML = `<div class="music-empty"><p>Tidak ada hasil untuk "${esc(q)}".</p><p class="muted">Coba kata kunci lain atau ganti filter.</p></div>`;
            return;
        }
        if (musicFilter === 'songs') renderSongs(data.results);
        else renderCards(data.results, musicFilter);
    } catch (e) {
        stopBusy();
        box.innerHTML = `<div class="state-error"><b>Ups, tidak ketemu</b><p>${esc(e.message)}</p></div>`;
    }
}

function renderSongs(songs) {
    const box = $('#music-results');
    box.innerHTML = `<div class="track-table">
        <div class="track-row track-head">
            <span class="t-idx">#</span>
            <span class="t-main">Judul</span>
            <span class="t-album">Album</span>
            <span class="t-dur">Durasi</span>
            <span class="t-actions"></span>
        </div>
        ${songs.map((s, i) => `
        <div class="track-row" onclick="musicOpenSong(${i})">
            <span class="t-idx">${i + 1}</span>
            <span class="t-idx-play"><svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg></span>
            <span class="t-main">
                <img src="${esc(s.thumbnail || '')}" alt="" loading="lazy"
                     onerror="this.style.visibility='hidden'">
                <span class="t-info">
                    <span class="t-title no-translate">${esc(s.title)}${s.is_explicit ? ' <i class="explicit">E</i>' : ''}</span>
                    <span class="t-artist no-translate">${esc(s.artist)}</span>
                </span>
            </span>
            <span class="t-album no-translate">${esc(s.album || '—')}</span>
            <span class="t-dur">${esc(s.duration_text || '—')}</span>
            <span class="t-actions" onclick="event.stopPropagation()">
                <button class="btn mini" onclick="musicPlay(${i})" title="Putar">${IC.play} Putar</button>
                <button class="btn mini" onclick="musicDownload('${esc(s.videoId)}', ${i}, 'mp3')" title="Unduh MP3">${IC.download} MP3</button>
                <button class="btn mini" onclick="musicDownload('${esc(s.videoId)}', ${i}, 'm4a')" title="Unduh M4A">${IC.download} M4A</button>
                <button class="btn mini ghost" onclick="openPlaylistPicker(${i}, event)" title="Simpan ke playlist"><svg class="ic" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg></button>
            </span>
        </div>`).join('')}
    </div>`;
    window._musicSongs = songs;
}

function musicOpenSong(i) {
    const s = window._musicSongs && window._musicSongs[i];
    if (!s) return;
    playSongAt(s, window._musicSongs, i);
}

function musicPlay(i) {
    const s = window._musicSongs && window._musicSongs[i];
    if (!s) return;
    playSongAt(s, window._musicSongs, i);
}

function downloadSong(videoId, i) {
    musicDownload(videoId, i, 'mp3');
}

function musicDownload(videoId, i, mode) {
    const s = window._musicSongs && window._musicSongs[i];
    const title = s ? `${s.title} — ${s.artist}` : 'Lagu';
    beginMusicDownload(videoId, title, mode);
}

function beginMusicDownload(videoId, title, mode) {
    mode = mode || 'mp3';
    dlStart = Date.now();
    const box = $('#music-results');
    box.insertAdjacentHTML('beforeend', `
        <div class="progress-card" id="music-progress">
            <div class="progress-head">
                <span class="progress-title no-translate">${esc(title)}</span>
                <span class="progress-status" id="progress-status">Antre</span>
                <span class="progress-time" id="progress-time">0:00</span>
                <button class="done-close card-close" onclick="dismissCard(this)" title="Tutup" aria-label="Tutup">
                    <svg viewBox="0 0 24 24"><path d="M18 6L6 18M6 6l12 12" style="fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round"/></svg>
                </button>
            </div>
            <div class="progress-pctwrap">
                <span class="progress-pct" id="progress-pct">0%</span>
                <div class="progress-track"><div class="progress-fill" id="progress-fill"></div></div>
            </div>
            <p class="progress-msg" id="progress-msg">Memulai unduhan MP3…</p>
            <p class="progress-eta" id="progress-eta"></p>
        </div>`);
    $('#music-progress').scrollIntoView({ behavior: 'smooth', block: 'center' });

    const currentInfoBackup = currentInfo;
    currentInfo = {
        webpage_url: 'https://www.youtube.com/watch?v=' + videoId,
        title: title,
        formats: [{ format_id: 'mp3' }],
        has_video: false, has_audio: true, max_height: 0,
    };
    const resPromise = fetchJSON('/api/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            url: currentInfo.webpage_url, mode: mode,
            format_id: undefined, title: title, resolution: '1080',
        }),
    });
    currentInfo = currentInfoBackup;
    resPromise.then(res => pollJobInto(res.job_id, '#music-progress'))
        .catch(e => {
            toast('Gagal memulai unduhan: ' + e.message, true);
            const p = $('#music-progress');
            if (p) p.remove();
        });
}

function pollJobInto(jobId, selector) {
    clearInterval(pollTimer);
    registerPoll(jobId);
    const box0 = document.querySelector(selector);
    if (box0) box0.dataset.jobId = jobId;
    let fails = 0;
    pollTimer = setInterval(async () => {
        try {
            const j = await fetchJSON('/api/job/' + jobId);
            fails = 0;
            const box = document.querySelector(selector);
            if (!box || box.classList.contains('hidden')) { clearInterval(pollTimer); unregisterPoll(jobId); return; }
            const map = { downloading: 'Mengunduh…', processing: 'Memproses…', done: 'Selesai', error: 'Gagal', queued: 'Antre' };
            box.querySelector('#progress-status').textContent = map[j.status] || j.status;
            box.querySelector('#progress-msg').textContent = j.message || '';
            box.querySelector('#progress-fill').style.width = Math.min(100, Math.max(0, j.progress || 0)) + '%';
            const pctBox = box.querySelector('#progress-pct');
            if (pctBox) pctBox.textContent = Math.round(j.progress || 0) + '%';
            const pt = box.querySelector('#progress-time');
            if (pt && dlStart) pt.textContent = fmtDur((Date.now() - dlStart) / 1000);
            if (j.status === 'done') {
                clearInterval(pollTimer);
                unregisterPoll(jobId);
                const totalT = fmtDur((Date.now() - dlStart) / 1000);
                box.querySelector('#progress-fill').style.width = '100%';
                box.classList.remove('fading', 'hidden');
                box.dataset.jobDone = '1';
                box.innerHTML = doneCardHtml(jobId, j, totalT);
                box.scrollIntoView({ behavior: 'smooth', block: 'center' });
                toast('MP3 siap diunduh! (' + totalT + ')');
            } else if (j.status === 'error') {
                clearInterval(pollTimer);
                unregisterPoll(jobId);
                box.innerHTML = `<div class="state-error"><b>Ups, unduhan gagal</b><p>${esc(j.error || '')}</p></div>`;
            }
        } catch (e) {
            // Satu kali gagal (jaringan sesaat) JANGAN mematikan polling —
            // cukup lewati siklus ini. Kalau gagal terus-terusan baru berhenti.
            fails++;
            if (fails >= 8) {
                clearInterval(pollTimer);
                unregisterPoll(jobId);
                const box = document.querySelector(selector);
                if (box) {
                    box.innerHTML = `<div class="state-error"><b>Koneksi terputus sesaat</b>
                        <p>Proses tetap berjalan di server. Muat ulang halaman untuk melihat status terbaru.</p></div>`;
                }
            }
        }
    }, 1200);
}

function renderCards(items, type) {
    const box = $('#music-results');
    const titles = { albums: 'Album', artists: 'Artis', playlists: 'Playlist' };
    box.innerHTML = `
        <div class="music-cards-head"><span>${titles[type] || 'Hasil'}</span><span class="muted" style="font-size:12px">${items.length} hasil</span></div>
        <div class="music-cards">
            ${items.map((it, idx) => {
                const isArtist = type === 'artists';
                const img = isArtist
                    ? `<img src="${esc(it.thumbnail || '')}" class="round" onerror="this.style.visibility='hidden'">`
                    : `<img src="${esc(it.thumbnail || '')}" onerror="this.style.visibility='hidden'">`;
                const sub = isArtist ? (it.subscribers || 'Artis') : (it.artist || it.author || '');
                const extra = !isArtist && type === 'albums' ? (it.year || '') : '';
                const cardTitle = isArtist ? (it.name || it.title) : it.title;
                return `<div class="mcard" onclick="musicOpenDetail('${type}', ${idx})">
                    <div class="mcard-img">${img}</div>
                    <span class="mcard-title no-translate">${esc(cardTitle)}</span>
                    <span class="mcard-sub no-translate">${esc(sub)}${extra ? ' · ' + esc(extra) : ''}</span>
                </div>`;
            }).join('')}
        </div>`;
    window['_music_' + type] = items;
}

function musicOpenDetail(type, idx) {
    const items = window['_music_' + type];
    const it = items && items[idx];
    if (!it || !it.id) return;
    const box = $('#music-results');
    showBusy(box, 'Memuat detail…');
    const url = type === 'albums' ? '/api/music-album/' : type === 'playlists' ? '/api/music-playlist/' : '/api/music-artist/';
    fetchJSON(url + encodeURIComponent(it.id)).then(d => {
        stopBusy();
        if (!d.ok) throw new Error(d.error || 'Gagal');
        if (d.type === 'artist') renderArtistDetail(d);
        else renderCollectionDetail(d);
    }).catch(e => {
        stopBusy();
        box.innerHTML = `<div class="state-error"><b>Ups, gagal memuat detail</b><p>${esc(e.message)}</p></div>`;
    });
}

function collectionBackBtn() {
    return `<button class="btn ghost" onclick="musicBack()">
        <svg class="ic" viewBox="0 0 24 24"><path d="M15 18l-6-6 6-6"/></svg> Kembali</button>`;
}

function renderCollectionDetail(d) {
    const box = $('#music-results');
    box.innerHTML = `
        <div class="collection-head">
            <img src="${esc(d.thumbnail || '')}" onerror="this.style.visibility='hidden'">
            <div class="col-info">
                <span class="col-type">${d.type === 'album' ? 'Album' : 'Playlist'}</span>
                <h2>${esc(d.title)}</h2>
                <p class="muted">${esc(d.artist || d.author || '')}${d.year ? ' · ' + esc(d.year) : ''} · ${d.tracks.length} lagu</p>
                <div class="actions" style="margin-top:8px">
                    <button class="btn primary" onclick="musicDownloadAll()">${IC.download} Unduh semua (${d.tracks.length} lagu)</button>
                    ${collectionBackBtn()}
                </div>
            </div>
        </div>
        <div class="track-table">
            <div class="track-row track-head">
                <span class="t-idx">#</span><span class="t-main">Judul</span>
                <span class="t-album">Artis</span><span class="t-dur">Durasi</span><span class="t-actions"></span>
            </div>
            ${d.tracks.map((t, i) => `
            <div class="track-row" onclick="playCollectionTrack(${i})">
                <span class="t-idx">${i + 1}</span>
                <span class="t-idx-play"><svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg></span>
                <span class="t-main"><span class="t-info"><span class="t-title no-translate">${esc(t.title)}</span></span></span>
                <span class="t-album no-translate">${esc(t.artist || '—')}</span>
                <span class="t-dur">${esc(t.duration_text || '—')}</span>
                <span class="t-actions" onclick="event.stopPropagation()">
                    <button class="btn mini" onclick="playCollectionTrack(${i})" title="Putar">${IC.play} Putar</button>
                    <button class="btn mini" onclick="downloadCollectionTrack('${esc(t.videoId)}', ${i}, 'mp3')" title="Unduh MP3">${IC.download} MP3</button>
                    <button class="btn mini primary" onclick="downloadCollectionTrack('${esc(t.videoId)}', ${i}, 'm4a')" title="Unduh M4A">${IC.download} M4A</button>
                </span>
            </div>`).join('')}
        </div>`;
    window._collection = d;
}

function playCollectionTrack(i) {
    const d = window._collection;
    const t = d && d.tracks[i];
    if (!t || !t.videoId) return;
    playSongAt({ videoId: t.videoId, title: t.title, artist: t.artist || d.title, thumbnail: d.thumbnail, duration_text: t.duration_text }, d.tracks, i);
}

function downloadCollectionTrack(videoId, i, mode) {
    const d = window._collection;
    const t = d && d.tracks[i];
    beginMusicDownload(videoId, t ? `${t.title} — ${t.artist || d.title}` : 'Lagu', mode || 'mp3');
}

function musicDownloadAll() {
    const d = window._collection;
    if (!d || !d.tracks.length) return;
    if (confirm(`Unduh ${d.tracks.length} lagu sebagai MP3? Unduhan berjalan berurutan.`)) {
        window._dlQueue = d.tracks.map(t => ({ videoId: t.videoId, title: `${t.title} — ${t.artist || d.title}` }));
        window._dlDone = [];
        musicQueueNext();
    }
}

function musicQueueNext() {
    if (!dlStart) dlStart = Date.now();
    const box = $('#music-results');
    const q = window._dlQueue || [];
    if (!q.length) {
        // semua selesai → tampilkan daftar file siap diunduh
        const done = window._dlDone || [];
        const rows = done.map(j => `
            <div class="track-row">
                <span class="t-main"><span class="t-info"><span class="t-title no-translate">${esc(j.title)}</span></span></span>
                <span class="t-dur">${esc(j.filename)}</span>
                <span class="t-actions">
                    <button class="btn mini primary" onclick="saveFile('${j.jobId}')">${IC.download} Simpan</button>
                </span>
            </div>`).join('');
        box.innerHTML = `
            <div class="music-cards-head"><span>Unduhan selesai (${done.length} file)</span></div>
            <div class="track-table">${rows}</div>
            ${collectionBackBtn()}`;
        toast(`Selesai! ${done.length} lagu siap diunduh.`);
        return;
    }
    const cur = q.shift();
    window._dlQueue = q;

    // buat kartu progress
    box.insertAdjacentHTML('beforeend', `
        <div class="progress-card" id="music-progress">
            <div class="progress-head">
                <span class="progress-title no-translate">${esc(cur.title)}</span>
                <span class="progress-status" id="progress-status">Antre</span>
                <span class="progress-time" id="progress-time">0:00</span>
                <button class="done-close card-close" onclick="dismissCard(this)" title="Tutup" aria-label="Tutup">
                    <svg viewBox="0 0 24 24"><path d="M18 6L6 18M6 6l12 12" style="fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round"/></svg>
                </button>
            </div>
            <div class="progress-pctwrap">
                <span class="progress-pct" id="progress-pct">0%</span>
                <div class="progress-track"><div class="progress-fill" id="progress-fill"></div></div>
            </div>
            <p class="progress-msg" id="progress-msg">Mengunduh (${window._dlDone.length + 1}/${window._dlDone.length + q.length + 1})…</p>
            <p class="progress-eta" id="progress-eta"></p>
        </div>`);
    $('#music-progress').scrollIntoView({ behavior: 'smooth', block: 'center' });

    fetchJSON('/api/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: 'https://www.youtube.com/watch?v=' + cur.videoId, mode: 'mp3', title: cur.title, resolution: '1080' }),
    }).then(res => {
        pollMusicQueue(res.job_id, cur.title);
    }).catch(e => {
        toast('Gagal memulai: ' + e.message, true);
        const p = $('#music-progress');
        if (p) p.remove();
        musicQueueNext();
    });
}

function pollMusicQueue(jobId, title) {
    clearInterval(pollTimer);
    registerPoll(jobId);
    const box0 = $('#music-progress');
    if (box0) box0.dataset.jobId = jobId;
    let fails = 0;
    pollTimer = setInterval(async () => {
        try {
            const j = await fetchJSON('/api/job/' + jobId);
            fails = 0;
            const box = $('#music-progress');
            if (!box || box.classList.contains('hidden')) { clearInterval(pollTimer); unregisterPoll(jobId); musicQueueNext(); return; }
            const map = { downloading: 'Mengunduh…', processing: 'Memproses…', done: 'Selesai', error: 'Gagal', queued: 'Antre' };
            box.querySelector('#progress-status').textContent = map[j.status] || j.status;
            box.querySelector('#progress-msg').textContent = j.message || '';
            box.querySelector('#progress-fill').style.width = Math.min(100, Math.max(0, j.progress || 0)) + '%';
            const pctBox2 = box.querySelector('#progress-pct');
            if (pctBox2) pctBox2.textContent = Math.round(j.progress || 0) + '%';
            const pt = box.querySelector('#progress-time');
            if (pt && dlStart) pt.textContent = fmtDur((Date.now() - dlStart) / 1000);
            const etaEl3 = box.querySelector('#progress-eta');
            if (etaEl3) {
                const m = (j.message || '').match(/sisa (\d+:\d{2})/);
                etaEl3.textContent = m ? 'Perkiraan selesai: ' + m[1] : '';
            }
            if (j.status === 'done') {
                clearInterval(pollTimer);
                unregisterPoll(jobId);
                window._dlDone.push({ jobId: jobId, title: title, filename: j.filename });
                box.remove();
                musicQueueNext();
            } else if (j.status === 'error') {
                clearInterval(pollTimer);
                unregisterPoll(jobId);
                box.remove();
                toast('Satu lagu gagal: ' + (j.error || '').slice(0, 120), true);
                musicQueueNext();
            }
        } catch (e) {
            // Gagal sesaat jangan memutus antrean — lewati siklus ini saja.
            fails++;
            if (fails >= 8) {
                clearInterval(pollTimer);
                unregisterPoll(jobId);
                const box = $('#music-progress');
                if (box) box.remove();
                toast('Koneksi terputus sesaat — lanjut ke lagu berikutnya.', true);
                musicQueueNext();
            }
        }
    }, 1200);
}

function renderArtistDetail(d) {
    const box = $('#music-results');
    box.innerHTML = `
        <div class="collection-head">
            <img src="${esc(d.thumbnail || '')}" class="round" onerror="this.style.visibility='hidden'">
            <div class="col-info">
                <span class="col-type">Artis</span>
                <h2>${esc(d.name)}</h2>
                <p class="muted">${esc(d.subscribers || '')}</p>
                <div class="actions" style="margin-top:8px">${collectionBackBtn()}</div>
            </div>
        </div>
        ${d.songs && d.songs.length ? `
        <h3 class="music-cards-head">Lagu terpopuler</h3>
        <div class="track-table">
            <div class="track-row track-head">
                <span class="t-idx">#</span><span class="t-main">Judul</span>
                <span class="t-album">Artis</span><span class="t-dur">Durasi</span><span class="t-actions"></span>
            </div>
            ${d.songs.map((t, i) => `
            <div class="track-row" onclick="playCollectionTrack(${i})">
                <span class="t-idx">${i + 1}</span>
                <span class="t-idx-play"><svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg></span>
                <span class="t-main"><span class="t-info"><span class="t-title no-translate">${esc(t.title)}</span></span></span>
                <span class="t-album no-translate">${esc(t.artist || '—')}</span>
                <span class="t-dur">${esc(t.duration_text || '—')}</span>
                <span class="t-actions" onclick="event.stopPropagation()">
                    <button class="btn mini" onclick="playCollectionTrack(${i})" title="Putar">${IC.play} Putar</button>
                    <button class="btn mini" onclick="downloadCollectionTrack('${esc(t.videoId)}', ${i}, 'mp3')" title="Unduh MP3">${IC.download} MP3</button>
                    <button class="btn mini primary" onclick="downloadCollectionTrack('${esc(t.videoId)}', ${i}, 'm4a')" title="Unduh M4A">${IC.download} M4A</button>
                </span>
            </div>`).join('')}
        </div>` : ''}
        ${d.albums && d.albums.length ? `
        <h3 class="music-cards-head">Album</h3>
        <div class="music-cards">
            ${d.albums.map(a => `
            <div class="mcard" onclick="musicOpenDetail('albums', ${window._artistAlbums.indexOf(a)})">
                <div class="mcard-img"><img src="${esc(a.thumbnail || '')}" onerror="this.style.visibility='hidden'"></div>
                <span class="mcard-title no-translate">${esc(a.title)}</span>
                <span class="mcard-sub no-translate">${esc(a.year || 'Album')}</span>
            </div>`).join('')}
        </div>` : ''}`;
    window._artistAlbums = d.albums || [];
    window._collection = { tracks: d.songs || [], title: d.name };
}

function musicBack() {
    const q = $('#music-q').value.trim();
    if (q) musicSearch();
    else $('#music-results').innerHTML = '';
}

/* ============================================================
   PLAYLIST — playlist musik per akun (Spotify-like)
   ============================================================ */
function songFromIndex(i) {
    return (window._musicSongs && window._musicSongs[i]) || null;
}

function openPlaylistPicker(i, ev) {
    ev = ev || window.event;
    if (ev) ev.stopPropagation();
    const s = songFromIndex(i);
    if (!s) return;
    // popover daftar playlist
    const old = document.getElementById('pl-picker');
    if (old) old.remove();
    const div = document.createElement('div');
    div.id = 'pl-picker';
    div.className = 'pl-picker';
    fetchJSON('/api/playlists').then(d => {
        const pls = d.playlists || [];
        div.innerHTML = `<div class="pl-picker-head">
                <b>Simpan ke playlist</b>
                <span class="pl-picker-song no-translate">${esc(s.title)}</span>
                <button class="pbtn q-x" onclick="document.getElementById('pl-picker').remove()">✕</button>
            </div>
            <div class="pl-picker-list">
                ${pls.length ? pls.map(p =>
                    `<button class="pl-opt" onclick="addToPlaylist(${p.id}, ${i}, this)">${esc(p.name)} <span class="pl-cnt">${p.cnt || 0}</span></button>`).join('')
                    : '<div class="pl-empty">Belum ada playlist — buat yang pertama di bawah!</div>'}
            </div>
            <div class="pl-picker-new">
                <input id="pl-new-name" placeholder="Nama playlist baru…" maxlength="60">
                <button class="btn primary mini" onclick="createAndAdd(${i})">Buat</button>
            </div>`;
    }).catch(e => {
        div.innerHTML = `<div class="pl-picker-head"><b>${esc(e.message)}</b>
            <button class="pbtn q-x" onclick="document.getElementById('pl-picker').remove()">✕</button></div>`;
    });
    document.body.appendChild(div);
    const r = (ev && ev.target && ev.target.getBoundingClientRect()) || { bottom: 80, right: 20 };
    div.style.position = 'fixed';
    div.style.right = '16px';
    div.style.bottom = '110px';
    div.style.zIndex = '95';
    div.style.width = 'min(320px, calc(100vw - 32px))';
}

async function createAndAdd(i) {
    const name = (document.getElementById('pl-new-name').value || '').trim();
    const s = songFromIndex(i);
    if (!name) { toast('Tulis nama playlist dulu', true); return; }
    if (!s) return;
    try {
        const d = await fetchJSON('/api/playlists', {
            method: 'POST', headers: authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ name }),
        });
        await addToPlaylist(d.id, i, null);
    } catch (e) {
        toast(e.message, true);
    }
}

async function addToPlaylist(pid, i, btn) {
    const s = songFromIndex(i);
    if (!s) return;
    try {
        await fetchJSON('/api/playlists/' + pid + '/items', {
            method: 'POST', headers: authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({
                video_id: s.videoId, title: s.title, artist: s.artist, thumbnail: s.thumbnail,
            }),
        });
        toast('Ditambahkan ke playlist 🎵');
        const pk = document.getElementById('pl-picker');
        if (pk) pk.remove();
    } catch (e) {
        toast(e.message, true);
    }
}

async function showMyPlaylists() {
    const box = $('#music-results');
    if (!_authToken) { toast('Login dulu untuk pakai playlist.', true); return; }
    showBusy(box, 'Memuat playlist…');
    try {
        const d = await fetchJSON('/api/playlists');
        stopBusy();
        const pls = d.playlists || [];
        box.innerHTML = `<div class="rec-head">
            <span class="rec-badge">🎵 Playlist Saya</span>
            <span class="rec-sub">${pls.length} playlist</span>
        </div>
        <div class="pl-create-row">
            <input id="pl-create-name" placeholder="Nama playlist baru…" maxlength="60">
            <button class="btn primary" onclick="createPlaylistFromRow()">Buat Playlist</button>
            <button class="btn ghost" onclick="musicBack()">Kembali</button>
        </div>
        ${pls.length
            ? `<div class="pl-grid">${pls.map(p => `
                <div class="pl-card" onclick="openPlaylist(${p.id})">
                    <div class="pl-card-ic">
                        <svg viewBox="0 0 24 24" class="ic"><path d="M9 18V5l12-2v13M9 18a3 3 0 11-6 0 3 3 0 016 0zm12-2a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
                    </div>
                    <span class="pl-card-name no-translate">${esc(p.name)}</span>
                    <span class="pl-card-sub">${p.cnt || 0} lagu</span>
                </div>`).join('')}</div>`
            : '<div class="music-empty"><p>Belum ada playlist. Buat yang pertama!</p></div>'}`;
    } catch (e) {
        stopBusy();
        box.innerHTML = `<div class="state-error"><b>Gagal memuat playlist</b><p>${esc(e.message)}</p></div>`;
    }
}

async function createPlaylistFromRow() {
    const name = (document.getElementById('pl-create-name').value || '').trim();
    if (!name) { toast('Tulis nama playlist dulu', true); return; }
    try {
        await fetchJSON('/api/playlists', {
            method: 'POST', headers: authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ name }),
        });
        toast('Playlist dibuat 🎵');
        showMyPlaylists();
    } catch (e) {
        toast(e.message, true);
    }
}

async function openPlaylist(pid) {
    const box = $('#music-results');
    showBusy(box, 'Memuat playlist…');
    try {
        const d = await fetchJSON('/api/playlists/' + pid);
        stopBusy();
        const pl = d.playlist || {};
        const items = d.items || [];
        box.innerHTML = `<div class="rec-head">
            <span class="rec-badge no-translate">🎵 ${esc(pl.name)}</span>
            <span class="rec-sub">${items.length} lagu</span>
        </div>
        <div class="actions" style="margin-bottom:12px">
            ${items.length ? `<button class="btn primary" onclick="playPlaylistItems()">${IC.play} Putar semua</button>` : ''}
            <button class="btn ghost" onclick="showMyPlaylists()">Kembali</button>
        </div>
        ${items.length
            ? `<div class="track-table">${items.map((it, i) => `
                <div class="track-row" onclick="playPlaylistAt(${i})">
                    <span class="t-idx">${i + 1}</span>
                    <span class="t-idx-play"><svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg></span>
                    <span class="t-main">
                        <img src="${esc(it.thumbnail || '')}" onerror="this.style.visibility='hidden'" alt="">
                        <span class="t-info">
                            <span class="t-title no-translate">${esc(it.title)}</span>
                            <span class="t-artist no-translate">${esc(it.artist || '')}</span>
                        </span>
                    </span>
                    <span class="t-actions" onclick="event.stopPropagation()">
                        <button class="btn mini" onclick="playPlaylistAt(${i})">${IC.play} Putar</button>
                        <button class="btn mini danger-ghost" onclick="removePlaylistItem(${pid}, ${it.id}, ${i})">Hapus</button>
                    </span>
                </div>`).join('')}</div>`
            : '<div class="music-empty"><p>Playlist kosong.</p></div>'}`;
        window._playlistItems = items;
    } catch (e) {
        stopBusy();
        box.innerHTML = `<div class="state-error"><b>Gagal memuat playlist</b><p>${esc(e.message)}</p></div>`;
    }
}

function playPlaylistAt(i) {
    const items = window._playlistItems || [];
    const s = items[i];
    if (!s) return;
    const mapped = items.map(it => ({ videoId: it.video_id, title: it.title, artist: it.artist, thumbnail: it.thumbnail }));
    playSongAt(mapped[i], mapped, i);
}

function playPlaylistItems() {
    const items = window._playlistItems || [];
    if (!items.length) return;
    const mapped = items.map(it => ({ videoId: it.video_id, title: it.title, artist: it.artist, thumbnail: it.thumbnail }));
    playSongAt(mapped[0], mapped, 0);
}

async function removePlaylistItem(pid, iid, i) {
    try {
        await fetchJSON('/api/playlists/' + pid + '/items/' + iid, { method: 'DELETE', headers: authHeaders() });
        toast('Dihapus dari playlist');
        openPlaylist(pid);
    } catch (e) {
        toast(e.message, true);
    }
}

/* ============================================================
   PEMUTAR MUSIK — putar langsung (stream), tanpa download
   ============================================================ */
let _player = { list: [], index: -1, shuffle: false };

/* ————— alur putar: unduh audio dulu (jalur download yang TERBUKTI jalan),
        lalu putar file MP3 lokal. Ini jauh lebih andal daripada stream proxy
        (yang resolve URL YouTube tiap request — lambat/gagal di IP datacenter). */
let _streamJobId = null;
let _streamTimer = null;

function cancelStreamJob() {
    if (_streamTimer) { clearInterval(_streamTimer); _streamTimer = null; }
    _streamJobId = null;
}

function playSongAt(song, list, idx) {
    if (!song || !song.videoId) { toast('Lagu ini tidak bisa diputar.', true); return; }
    _player.list = list || [];
    _player.index = (idx == null ? -1 : idx);

    const bar = $('#player-bar');
    bar.classList.remove('hidden');
    document.body.classList.add('has-player');
    $('#player-title').textContent = song.title || 'Lagu';
    $('#player-artist').textContent = song.artist || song.album || '';
    const th = $('#player-thumb');
    th.src = song.thumbnail || '';
    th.style.visibility = song.thumbnail ? '' : 'hidden';

    // matikan job stream lama & audio sebelumnya
    cancelStreamJob();
    const audio = $('#player-audio');
    clearTimeout(audio._watchdog);
    audio.pause();
    audio.removeAttribute('src');
    audio.load();
    audio._errShown = false;
    setPlayerUI('loading');
    playerShowMsg('Menyiapkan audio… 0%');

    prepareAndPlay(song);
}

async function prepareAndPlay(song) {
    try {
        const d = await fetchJSON('/api/music-prepare', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                video_id: song.videoId,
                title: (song.title || 'Lagu') + (song.artist ? ' — ' + song.artist : ''),
            }),
        });
        _streamJobId = d.job_id;
        pollStreamJob();
    } catch (e) {
        setPlayerUI('paused');
        playerShowMsg('Gagal menyiapkan audio. Coba unduh MP3-nya ya!');
    }
}

function pollStreamJob() {
    if (_streamTimer) clearInterval(_streamTimer);
    _streamTimer = setInterval(() => {
        if (!_streamJobId) return;
        fetchJSON('/api/job/' + _streamJobId).then(j => {
            if (!_streamJobId) return;   // sudah ganti lagu
            if (j.status === 'done') {
                clearInterval(_streamTimer); _streamTimer = null;
                const jid = _streamJobId;
                playPreparedAudio(jid);
            } else if (j.status === 'error') {
                clearInterval(_streamTimer); _streamTimer = null;
                setPlayerUI('paused');
                playerShowMsg(j.error || 'Gagal menyiapkan audio.');
                autoSkipIfAny();
            } else {
                const pct = Math.round(j.progress || 0);
                playerShowMsg('Menyiapkan audio… ' + pct + '%');
            }
        }).catch(() => { /* transien — biarkan polling lanjut */ });
    }, 1500);
}

function playPreparedAudio(jobId) {
    const audio = $('#player-audio');
    audio._errShown = false;
    audio.src = '/api/music-file/' + jobId;
    audio.load();
    const p = audio.play();
    if (p && p.catch) p.catch(() => { /* error ditangani via event error */ });
    clearTimeout(audio._watchdog);
    audio._watchdog = setTimeout(() => {
        if (!audio.paused || audio.readyState > 0) return;
        if (audio._errShown) return;
        audio._errShown = true;
        setPlayerUI('paused');
        playerShowMsg('Audio siap tapi tidak bisa diputar di perangkat ini.');
        autoSkipIfAny();
    }, 12000);
}

function autoSkipIfAny() {
    const list = _player.list;
    if (list && list.length > 1) {
        setTimeout(() => playerNext(true), 1500);
        toast('Lagu gagal — otomatis lewati ke berikutnya.', true);
    }
}

function playerShowMsg(text) {
    const el = $('#player-msg');
    if (!el) return;
    el.textContent = text;
    el.classList.remove('hidden');
}

function playerNext(skipAuto) {
    const list = _player.list;
    if (!list || !list.length) return;
    const n = list.length;
    let i = _player.index + 1;
    if (i >= n) i = 0;
    if (n === 1) { playerToggle(); return; }
    const s = list[i];
    if (s && s.videoId) playSongAt(s, list, i);
}

function playerPrev() {
    const list = _player.list;
    if (!list || !list.length) return;
    const audio = $('#player-audio');
    // kalau baru mulai (>3 detik) → kembali ke awal lagu; kalau sudah lama → lagu sebelumnya
    if (audio.currentTime > 3) {
        audio.currentTime = 0;
        return;
    }
    const n = list.length;
    const i = (_player.index - 1 + n) % n;
    const s = list[i];
    if (s && s.videoId) playSongAt(s, list, i);
}

function playerSeekInput(el) {
    const audio = $('#player-audio');
    if (!audio || !audio.duration || !isFinite(audio.duration)) return;
    audio.currentTime = (el.value / 1000) * audio.duration;
}

function playerClose() {
    const audio = $('#player-audio');
    clearTimeout(audio._watchdog);
    cancelStreamJob();
    audio.pause();
    audio.removeAttribute('src');
    audio.load();
    setPlayerUI('paused');
    $('#player-bar').classList.add('hidden');
    document.body.classList.remove('has-player');
    playerToggleLyrics(false);
    playerToggleQueue(false);
    _player.list = [];
    _player.index = -1;
}

function setPlayerUI(state) {
    const ic = $('#player-play-ic');
    const b = $('#player-play');
    if (!ic || !b) return;
    b.classList.toggle('loading', state === 'loading');
    b.disabled = state === 'loading';
    b.classList.toggle('playing', state === 'playing');
    const eq = $('#player-eq');
    if (eq) eq.classList.toggle('playing', state === 'playing');
    if (state === 'loading') {
        ic.innerHTML = '<svg class="spin" viewBox="0 0 24 24"><path d="M12 3a9 9 0 109 9" style="fill:none;stroke:currentColor;stroke-width:2.4;stroke-linecap:round"/></svg>';
    } else if (state === 'playing') {
        ic.innerHTML = IC.pause.replace('class="ic"', '');
    } else {
        ic.innerHTML = IC.play.replace('class="ic"', '');
    }
}

function playerToggle() {
    const audio = $('#player-audio');
    if (!audio.src) return;
    if (audio.paused) audio.play().catch(() => {});
    else audio.pause();
}

/* ————— LIRIK ————— */
let _lyrics = null;       // [{t: detik, l: baris}] untuk lirik sinkron
let _lyricsTimer = null;

function playerToggleLyrics(force) {
    const panel = $('#lyrics-panel');
    if (!panel) return;
    const want = (force === undefined) ? panel.classList.contains('hidden') : !!force;
    if (want) {
        panel.classList.remove('hidden');
        playerToggleQueue(false);
        loadLyrics();
    } else {
        panel.classList.add('hidden');
    }
}

async function loadLyrics() {
    const title = $('#player-title').textContent;
    const artist = $('#player-artist').textContent;
    const body = $('#lyrics-body');
    body.innerHTML = '<div class="music-empty">Memuat lirik…</div>';
    _lyrics = null;
    clearInterval(_lyricsTimer);
    try {
        const d = await fetchJSON('/api/music-lyrics?title=' + encodeURIComponent(title) +
                                  '&artist=' + encodeURIComponent(artist));
        if (!d.found || !d.lyrics) {
            body.innerHTML = '<div class="music-empty"><p>Lirik tidak ditemukan untuk lagu ini.</p></div>';
            return;
        }
        if (d.synced) {
            _lyrics = parseSyncedLyrics(d.lyrics);
        }
        renderLyrics(d.lyrics, d.synced);
    } catch (e) {
        body.innerHTML = '<div class="music-empty"><p>Gagal memuat lirik.</p></div>';
    }
}

function parseSyncedLyrics(text) {
    // format LRC: [mm:ss.xx] baris
    const out = [];
    for (const line of (text || '').split('\n')) {
        const m = line.match(/\[(\d+):(\d+(?:\.\d+)?)\](.*)/);
        if (m) {
            const t = parseInt(m[1], 10) * 60 + parseFloat(m[2]);
            out.push({ t: t, l: (m[3] || '').trim() });
        }
    }
    return out.sort((a, b) => a.t - b.t);
}

function renderLyrics(text, synced) {
    const body = $('#lyrics-body');
    if (!synced || !_lyrics || !_lyrics.length) {
        const lines = (text || '').split('\n').map(l => l.trim()).filter(Boolean);
        body.innerHTML = '<div class="lyrics-plain">' +
            lines.map(l => `<div class="ly-line">${esc(l)}</div>`).join('') + '</div>';
        return;
    }
    body.innerHTML = '<div class="lyrics-synced">' +
        _lyrics.map((x, i) => `<div class="ly-line" data-i="${i}">${esc(x.l || '♪')}</div>`).join('') +
        '</div>';
    clearInterval(_lyricsTimer);
    _lyricsTimer = setInterval(() => {
        const audio = $('#player-audio');
        const t = audio.currentTime || 0;
        let idx = -1;
        for (let i = _lyrics.length - 1; i >= 0; i--) {
            if (_lyrics[i].t <= t + 0.3) { idx = i; break; }
        }
        const lines = body.querySelectorAll('.ly-line');
        lines.forEach((el, i) => {
            el.classList.toggle('active', i === idx);
        });
        if (idx >= 0 && lines[idx]) {
            lines[idx].scrollIntoView({ block: 'center', behavior: 'smooth' });
        }
    }, 400);
}

/* ————— ANTREAN ————— */
function playerToggleQueue(force) {
    const panel = $('#queue-panel');
    if (!panel) return;
    const want = (force === undefined) ? panel.classList.contains('hidden') : !!force;
    if (want) {
        panel.classList.remove('hidden');
        playerToggleLyrics(false);
        renderQueue();
    } else {
        panel.classList.add('hidden');
    }
}

function renderQueue() {
    const list = $('#queue-list');
    const listArr = _player.list || [];
    const cur = _player.index;
    $('#queue-count').textContent = listArr.length + ' lagu';
    if (!listArr.length) {
        list.innerHTML = '<div class="music-empty">Antrean kosong — cari lagu lalu tekan Putar.</div>';
        return;
    }
    const rows = [];
    for (let i = 0; i < listArr.length; i++) {
        const s = listArr[i];
        if (!s) continue;
        rows.push(`<div class="q-row ${i === cur ? 'q-now' : ''}" onclick="playSongAt(_player.list[${i}], _player.list, ${i})">
            <img src="${esc(s.thumbnail || '')}" onerror="this.style.visibility='hidden'" alt="">
            <div class="q-info">
                <span class="q-title no-translate">${esc(s.title || 'Lagu')}</span>
                <span class="q-artist no-translate">${esc(s.artist || '')}</span>
            </div>
            <button class="pbtn q-x" onclick="event.stopPropagation(); playerQueueRemove(${i})" title="Hapus dari antrean" aria-label="Hapus">
                <svg viewBox="0 0 24 24"><path d="M18 6L6 18M6 6l12 12" style="fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round"/></svg>
            </button>
        </div>`);
    }
    list.innerHTML = rows.join('');
}

function playerQueueRemove(i) {
    const list = _player.list;
    if (!list) return;
    const removed = list.splice(i, 1)[0];
    if (i < _player.index) _player.index -= 1;
    renderQueue();
}

/* ————— pemasangan event pemutar (sekali) ————— */
(function setupPlayer() {
    const audio = $('#player-audio');
    const seek = $('#player-seek');

    audio.addEventListener('timeupdate', () => {
        if (!audio.duration || !isFinite(audio.duration)) return;
        seek.value = Math.round((audio.currentTime / audio.duration) * 1000);
        $('#player-cur').textContent = fmtDur(audio.currentTime);
        $('#player-dur').textContent = fmtDur(audio.duration);
    });
    audio.addEventListener('loadedmetadata', () => {
        $('#player-cur').textContent = '0:00';
        $('#player-dur').textContent = fmtDur(audio.duration);
    });
    audio.addEventListener('playing', () => {
        clearTimeout(audio._watchdog);
        $('#player-msg').classList.add('hidden');
        setPlayerUI('playing');
    });
    audio.addEventListener('play', () => {
        clearTimeout(audio._watchdog);
        setPlayerUI('playing');
    });
    audio.addEventListener('canplay', () => setPlayerUI('playing'));
    audio.addEventListener('pause', () => {
        if (audio.ended) return;
        setPlayerUI('paused');
    });
    audio.addEventListener('ended', () => playerNext(true));
    audio.addEventListener('error', () => {
        const audio2 = $('#player-audio');
        if (audio2._errShown) return;
        audio2._errShown = true;
        clearTimeout(audio2._watchdog);
        setPlayerUI('paused');
        playerShowMsg('Lagu tidak bisa diputar di perangkat ini. Coba unduh MP3-nya ya!');
        autoSkipIfAny();
    });
})();

/* ============================================================
   JAM REAL-TIME (deteksi zona waktu perangkat)
   ============================================================ */
function startClock() {
    const el = document.getElementById('live-clock');
    if (!el) return;
    let tz;
    try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (e) { tz = 'lokal'; }
    function tick() {
        const now = new Date();
        const hh = String(now.getHours()).padStart(2, '0');
        const mm = String(now.getMinutes()).padStart(2, '0');
        const ss = String(now.getSeconds()).padStart(2, '0');
        const hari = now.toLocaleDateString('id-ID', { weekday: 'short', day: 'numeric', month: 'short' });
        el.innerHTML = hh + ':' + mm + ':' + ss + '<small>' + hari + '</small>';
        el.title = 'Zona waktu: ' + tz;
    }
    tick();
    setInterval(tick, 1000);
}

/* ============================================================
   AUTH — akun web langsung + tamu (token di localStorage)
   ============================================================ */
let _authToken = localStorage.getItem('umd_token') || '';
let _authUser = localStorage.getItem('umd_user') || '';
let _authGuest = localStorage.getItem('umd_guest') === '1';

function authHeaders(extra) {
    const h = Object.assign({}, extra || {});
    if (_authToken) h['X-Auth-Token'] = _authToken;
    return h;
}

function refreshAuthUI() {
    const loginBtn = document.getElementById('uc-login');
    const userBox = document.getElementById('uc-user');
    const nameEl = document.getElementById('uc-name');
    if (_authToken && _authUser) {
        if (loginBtn) loginBtn.classList.add('hidden');
        if (userBox) userBox.classList.remove('hidden');
        if (nameEl) nameEl.textContent = _authUser + (_authGuest ? ' (tamu)' : '');
    } else {
        if (loginBtn) loginBtn.classList.remove('hidden');
        if (userBox) userBox.classList.add('hidden');
    }
    renderHistory();
}

function showLogin() {
    document.getElementById('login-overlay').classList.remove('hidden');
    document.getElementById('auth-err').textContent = '';
    loginTab('login');
    // TIDAK auto-focus — keyboard tidak boleh muncul sendiri.
}

function hideLogin() {
    document.getElementById('login-overlay').classList.add('hidden');
}

function loginTab(mode) {
    document.getElementById('lt-login').classList.toggle('active', mode === 'login');
    document.getElementById('lt-signup').classList.toggle('active', mode === 'signup');
    const btn = document.getElementById('auth-submit');
    btn.textContent = mode === 'login' ? 'Masuk' : 'Daftar';
    btn.dataset.mode = mode;
}

async function doAuth() {
    const mode = document.getElementById('auth-submit').dataset.mode || 'login';
    const username = document.getElementById('auth-user').value.trim();
    const password = document.getElementById('auth-pass').value;
    const errEl = document.getElementById('auth-err');
    errEl.textContent = '';
    if (!username || !password) { errEl.textContent = 'Isi username & password.'; return; }
    try {
        const res = await fetchJSON('/api/auth/' + mode, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password }),
        });
        _authToken = res.token;
        _authUser = res.username;
        _authGuest = !!res.is_guest;
        localStorage.setItem('umd_token', _authToken);
        localStorage.setItem('umd_user', _authUser);
        localStorage.setItem('umd_guest', _authGuest ? '1' : '0');
        hideLogin();
        refreshAuthUI();
        toast('Selamat datang, ' + _authUser + '!');
    } catch (e) {
        errEl.textContent = e.message;
    }
}

async function doGuest() {
    try {
        const res = await fetchJSON('/api/auth/guest', { method: 'POST' });
        _authToken = res.token;
        _authUser = res.username;
        _authGuest = true;
        localStorage.setItem('umd_token', _authToken);
        localStorage.setItem('umd_user', _authUser);
        localStorage.setItem('umd_guest', '1');
        hideLogin();
        refreshAuthUI();
        toast('Lanjut sebagai tamu: ' + _authUser);
    } catch (e) {
        toast('Gagal masuk tamu: ' + e.message, true);
    }
}

async function doLogout() {
    try { await fetchJSON('/api/auth/logout', { method: 'POST', headers: authHeaders() }); } catch (e) {}
    _authToken = ''; _authUser = ''; _authGuest = false;
    localStorage.removeItem('umd_token');
    localStorage.removeItem('umd_user');
    localStorage.removeItem('umd_guest');
    refreshAuthUI();
    toast('Sudah keluar. Sampai jumpa!');
}

async function checkAuth() {
    if (!_authToken) return;
    try {
        const d = await fetchJSON('/api/auth/me', { headers: authHeaders() });
        if (!d.authenticated) {
            _authToken = ''; _authUser = '';
            localStorage.removeItem('umd_token');
            localStorage.removeItem('umd_user');
        }
    } catch (e) { /* offline — biarkan */ }
    refreshAuthUI();
}

/* ============================================================
   RIWAYAT DOWNLOAD per akun
   ============================================================ */
async function renderHistory() {
    const box = document.getElementById('history-box');
    if (!box) return;
    if (!_authToken) {
        box.innerHTML = 'Login untuk melihat riwayat download-mu.';
        return;
    }
    try {
        const d = await fetchJSON('/api/history', { headers: authHeaders() });
        const rows = d.history || [];
        if (!rows.length) {
            box.innerHTML = 'Belum ada riwayat download.';
            return;
        }
        box.innerHTML = rows.map(h => `
            <div class="history-row">
                <span class="h-title">${esc(h.title || '(tanpa judul)')}</span>
                <span class="h-meta">${esc(h.platform || '')} · ${esc(h.mode || '')}</span>
                <span class="h-meta">${new Date(h.created * 1000).toLocaleString('id-ID', { dateStyle: 'short', timeStyle: 'short' })}</span>
            </div>`).join('');
    } catch (e) {
        box.innerHTML = 'Gagal memuat riwayat.';
    }
}

/* ============================================================
   SARAN PLATFORM (kotak saran global)
   ============================================================ */
async function loadPlatformRequests() {
    const box = document.getElementById('plat-req-list');
    if (!box) return;
    try {
        const d = await fetchJSON('/api/platform-requests');
        const rows = d.requests || [];
        box.innerHTML = rows.length
            ? rows.slice(0, 12).map(r =>
                `<div class="plat-req-row">
                    <span class="pr-name">${esc(r.platform)}</span>
                    <span>oleh ${esc(r.username)}</span>
                    <span class="muted">${new Date(r.created * 1000).toLocaleDateString('id-ID')}</span>
                </div>`).join('')
            : '<span class="muted">Belum ada saran. Jadilah yang pertama!</span>';
    } catch (e) { box.innerHTML = ''; }
}

/* ============================================================
   FEEDBACK BUG — lapor bug / saran
   ============================================================ */
async function submitFeedback() {
    const msg = (document.getElementById('fb-msg').value || '').trim();
    const status = document.getElementById('fb-status');
    if (!msg) { toast('Tulis pesan dulu ya!', true); return; }
    status.textContent = 'Mengirim…';
    try {
        // deteksi halaman aktif untuk konteks laporan
        let page = 'beranda';
        const views = ['home', 'music', 'manga', 'news', 'chat', 'about'];
        for (const v of views) {
            const el = document.getElementById('view-' + v);
            if (el && !el.classList.contains('hidden')) { page = v; break; }
        }
        const res = await fetchJSON('/api/feedback', {
            method: 'POST', headers: authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ message: msg, page: page }),
        });
        status.textContent = res.message || 'Terima kasih!';
        document.getElementById('fb-msg').value = '';
        loadFeedback();
        setTimeout(() => { status.textContent = ''; }, 4000);
    } catch (e) {
        status.textContent = '';
        toast(e.message, true);
    }
}

async function loadFeedback() {
    const box = document.getElementById('fb-list');
    if (!box) return;
    try {
        const d = await fetchJSON('/api/feedback');
        const items = d.items || [];
        if (!items.length) {
            box.innerHTML = '<div class="muted" style="font-size:12px">Belum ada laporan. Jadilah yang pertama! 👑</div>';
            return;
        }
        box.innerHTML = '<div class="muted" style="font-size:11px;margin-bottom:6px">Laporan terbaru:</div>' +
            items.slice(0, 8).map(f => `
            <div class="fb-row">
                <span class="fb-name no-translate">${esc(f.username)}</span>
                <span class="fb-time">${new Date(f.created * 1000).toLocaleString('id-ID', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}</span>
                <span class="fb-text">${esc(f.message)}</span>
            </div>`).join('');
    } catch (e) { /* abaikan */ }
}

async function submitPlatformRequest() {
    const input = document.getElementById('plat-req-input');
    const name = (input.value || '').trim();
    if (!name) { toast('Tulis nama platform dulu', true); return; }
    try {
        await fetchJSON('/api/platform-requests', {
            method: 'POST', headers: authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ platform: name }),
        });
        input.value = '';
        toast('Saran masuk ke kotak saran global!');
        loadPlatformRequests();
    } catch (e) {
        toast(e.message, true);
    }
}

/* ============================================================
   MANGA (MangaDex) — cari, filter genre, rekomendasi
   ============================================================ */
let _mangaTag = '';
let _mangaTagName = '';
let _mangaQ = '';
let _mangaLang = 'en';   // bahasa chapter: original / en / id
let _mangaMid = '';      // id manga yang sedang dibuka (untuk ganti bahasa)
let _mangaCurrent = null; // konteks manga yang dibuka (id/title/cover) untuk riwayat
let _mangaView = 'home'; // 'home' | 'search' | 'detail' | 'reader' — untuk refresh saat buka tab

function mangaCardHtml(m) {
    const tags = (m.tags || []).map(t => `<span class="mg-chip">${esc(t)}</span>`).join('');
    return `<div class="manga-card" onclick="mangaOpen('${m.id}')">
        <img src="/api/manga-img?url=${encodeURIComponent(m.cover || '')}" loading="lazy"
             onerror="this.style.visibility='hidden'">
        <div class="mc-body">
            <div class="mc-title">${esc(m.title)}</div>
            <div class="mc-sub">${esc(m.status || '')}${m.year ? ' · ' + m.year : ''}</div>
            ${tags ? `<div class="mc-genres">${tags}</div>` : ''}
        </div>
    </div>`;
}

async function mangaHistoryHtml() {
    // Baris "Lanjut Baca" — riwayat baca manga per akun (dipakai di
    // rekomendasi & hasil pencarian). Kembalikan '' kalau tidak ada.
    if (!_authToken) return '';
    try {
        const hd = await fetchJSON('/api/manga/history');
        const hist = hd.items || [];
        if (!hist.length) return '';
        return `<div class="rec-head" style="margin-top:4px">
                <span class="rec-badge">📖 Lanjut Baca</span>
                <span class="rec-sub">${hist.length} manga terakhir · klik untuk lanjut</span>
            </div>
            <div class="manga-grid manga-history-grid">` +
            hist.slice(0, 8).map(h => `
            <div class="manga-card" onclick="mangaOpen('${h.manga_id}')">
                ${h.cover ? `<img src="/api/manga-img?url=${encodeURIComponent(h.cover)}" loading="lazy" onerror="this.style.visibility='hidden'">` : ''}
                <div class="mc-body">
                    <div class="mc-title">${esc(h.title)}</div>
                    <div class="mc-sub">Chapter ${esc(h.chapter || '?')}${h.lang ? ' · ' + esc(h.lang) : ''}</div>
                </div>
            </div>`).join('') + `</div>`;
    } catch (e) { return ''; }
}

async function mangaRecommend() {
    _mangaView = 'home';
    const box = document.getElementById('manga-results');
    if (!box) return;
    showBusy(box, 'Memuat rekomendasi…');
    try {
        const d = await fetchJSON('/api/manga-recommend?tag=' + encodeURIComponent(_mangaTag));
        stopBusy();
        if (!d.results || !d.results.length) {
            box.innerHTML = `<div class="music-empty"><p>Belum ada rekomendasi.</p></div>`;
            return;
        }
        const label = _mangaTag ? 'Rekomendasi ' + _mangaTagName : '🔥 Rekomendasi untukmu';
        const histHtml = await mangaHistoryHtml();
        box.innerHTML = histHtml + `<div class="rec-head">
            <span class="rec-badge">${esc(label)}</span>
            <span class="rec-sub">${d.results.length} judul populer · klik untuk baca</span>
        </div><div class="manga-grid">` + d.results.map(mangaCardHtml).join('') + `</div>`;
    } catch (e) {
        stopBusy();
        box.innerHTML = `<div class="state-error"><b>Ups, gagal memuat rekomendasi</b><p>${esc(e.message)}</p></div>`;
    }
}

async function mangaSearch() {
    _mangaView = 'search';
    const q = document.getElementById('manga-q').value.trim();
    _mangaQ = q;
    const box = document.getElementById('manga-results');
    if (!q && !_mangaTag) { mangaRecommend(); return; }
    showBusy(box, q ? 'Mencari manga…' : 'Memuat hasil…');
    try {
        const d = await fetchJSON('/api/manga-search?q=' + encodeURIComponent(q) +
                                  '&tag=' + encodeURIComponent(_mangaTag));
        stopBusy();
        if (!d.results || !d.results.length) {
            box.innerHTML = `<div class="music-empty"><p>${q ? `Tidak ada manga untuk "${esc(q)}".` : 'Tidak ada hasil.'}</p></div>`;
            return;
        }
        const histHtml = await mangaHistoryHtml();
        box.innerHTML = histHtml + `<div class="rec-head">
            <span class="rec-badge">Hasil pencarian</span>
            <span class="rec-sub">${d.results.length} judul</span>
        </div><div class="manga-grid">` + d.results.map(mangaCardHtml).join('') + `</div>`;
    } catch (e) {
        stopBusy();
        box.innerHTML = `<div class="state-error"><b>Ups, gagal cari manga</b><p>${esc(e.message)}</p></div>`;
    }
}

async function mangaInitGenres() {
    const box = document.getElementById('manga-genres');
    if (!box) return;
    try {
        const d = await fetchJSON('/api/manga-genres');
        box.innerHTML = `<button class="pill active" data-k="" onclick="mangaSetTag('')">Semua</button>` +
            d.genres.map(g =>
                `<button class="pill" data-k="${esc(g.key)}" onclick="mangaSetTag('${esc(g.key)}', '${esc(g.name)}')">${esc(g.name)}</button>`).join('');
    } catch (e) { /* biarkan kosong */ }
}

function mangaSetTag(key, name) {
    _mangaTag = key || '';
    _mangaTagName = name || '';
    document.querySelectorAll('#manga-genres .pill').forEach(b =>
        b.classList.toggle('active', b.dataset.k === _mangaTag));
    const q = document.getElementById('manga-q').value.trim();
    _mangaQ = q;
    if (q) mangaSearch();
    else mangaRecommend();
}

async function mangaOpen(mid) {
    _mangaView = 'detail';
    const box = document.getElementById('manga-results');
    _mangaMid = mid;
    showBusy(box, 'Memuat detail manga…');
    try {
        const d = await fetchJSON('/api/manga/' + mid + '?lang=' + encodeURIComponent(_mangaLang));
        stopBusy();
        if (!d.ok) throw new Error(d.error);
        _mangaCurrent = { id: mid, title: d.title, cover: d.cover || '' };
        const origLabel = d.original_language
            ? 'Bahasa Asli (' + esc(String(d.original_language).toUpperCase()) + ')'
            : 'Bahasa Asli';
        box.innerHTML = `
            <div class="collection-head">
                ${d.cover ? `<img src="/api/manga-img?url=${encodeURIComponent(d.cover)}" onerror="this.style.visibility='hidden'">` : ''}
                <div class="col-info">
                    <span class="col-type">Manga · ${esc(d.status || '')}</span>
                    <h2>${esc(d.title)}</h2>
                    <p class="muted">${esc((d.description || '').slice(0, 220))}</p>
                    <div class="actions" style="margin-top:10px">
                        <button class="btn ghost" onclick="mangaBack()">
                            <svg class="ic" viewBox="0 0 24 24"><path d="M15 18l-6-6 6-6"/></svg> Kembali
                        </button>
                    </div>
                </div>
            </div>
            <div class="filter-pills manga-langs" aria-label="Pilih bahasa chapter">
                <button class="pill ${_mangaLang === 'original' ? 'active' : ''}" data-l="original" onclick="mangaSetLang('original')">${origLabel}</button>
                <button class="pill ${_mangaLang === 'en' ? 'active' : ''}" data-l="en" onclick="mangaSetLang('en')">English</button>
                <button class="pill ${_mangaLang === 'id' ? 'active' : ''}" data-l="id" onclick="mangaSetLang('id')">Indonesia</button>
            </div>
            <h3 class="music-cards-head">Chapter (${d.chapters.length})</h3>
            ${d.chapters.length
                ? `<div class="chapter-list">${d.chapters.slice(0, 80).map(c => `
                    <div class="chapter-row" onclick="mangaRead('${c.id}', '${esc(String(c.chapter || ''))}', '${esc(c.lang || '')}')">
                        <span class="cr-t">Chapter ${esc(c.chapter || '?')}${c.title ? ' — ' + esc(c.title) : ''}</span>
                        <span class="cr-s">${esc(c.lang || '')} · ${c.pages} halaman</span>
                    </div>`).join('')}</div>`
                : `<div class="music-empty"><p>Belum ada chapter dalam bahasa ini — coba pilih bahasa lain di atas ya!</p></div>`}`;
    } catch (e) {
        stopBusy();
        box.innerHTML = `<div class="state-error"><b>Gagal buka manga</b><p>${esc(e.message)}</p></div>`;
    }
}

function mangaSetLang(l) {
    _mangaLang = l || 'en';
    if (_mangaMid) mangaOpen(_mangaMid);
    else mangaRecommend();
}

function mangaBack() {
    const q = document.getElementById('manga-q').value.trim();
    if (q) mangaSearch();
    else mangaRecommend();
}

let _readerPages = [];
let _readerIdx = 0;

async function mangaRead(cid, chapter, lang) {
    _mangaView = 'reader';
    const box = document.getElementById('manga-results');
    showBusy(box, 'Memuat halaman chapter…');
    try {
        const d = await fetchJSON('/api/manga/read/' + cid);
        stopBusy();
        if (!d.ok || !d.pages || !d.pages.length) throw new Error(d.error || 'Chapter kosong');
        // catat riwayat baca (kalau login)
        if (_authToken && _mangaCurrent) {
            try {
                fetchJSON('/api/manga/history', {
                    method: 'POST', headers: authHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify({
                        manga_id: _mangaCurrent.id, title: _mangaCurrent.title,
                        cover: _mangaCurrent.cover, chapter: chapter || '?',
                        chapter_id: cid, lang: lang || _mangaLang || '',
                    }),
                }).catch(() => {});
            } catch (e) { /* abaikan */ }
        }
        _readerPages = d.pages;
        _readerIdx = 0;
        box.innerHTML = `
            <div class="reader-head">
                <button class="btn ghost" onclick="mangaReaderBack()">
                    <svg class="ic" viewBox="0 0 24 24"><path d="M15 18l-6-6 6-6"/></svg> Kembali
                </button>
                <span class="muted">Chapter ${esc(chapter || '')} · ${esc(lang || '')} · halaman <b id="rd-page">1</b>/${d.pages.length}</span>
                <button class="btn primary" onclick="mangaNextPage()">Berikutnya
                    <svg class="ic" viewBox="0 0 24 24"><path d="M9 6l6 6-6 6"/></svg>
                </button>
            </div>
            <div class="reader" id="reader-wrap">
                <img id="rd-img" src="/api/manga-img?url=${encodeURIComponent(d.pages[0].url)}" alt="halaman 1">
            </div>`;
        document.getElementById('rd-img').addEventListener('click', mangaNextPage);
    } catch (e) {
        stopBusy();
        box.innerHTML = `<div class="state-error"><b>Gagal buka chapter</b><p>${esc(e.message)}</p></div>`;
    }
}

function mangaNextPage() {
    if (_readerIdx + 1 < _readerPages.length) {
        _readerIdx++;
        const img = document.getElementById('rd-img');
        img.src = '/api/manga-img?url=' + encodeURIComponent(_readerPages[_readerIdx].url);
        document.getElementById('rd-page').textContent = (_readerIdx + 1);
        img.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } else {
        toast('Selesai — ini halaman terakhir.');
    }
}

function mangaReaderBack() {
    const q = document.getElementById('manga-q').value.trim();
    if (q) mangaSearch();
    else mangaRecommend();
}

/* ============================================================
   BERITA — live multi-sumber, cari, filter
   ============================================================ */
let _newsCat = '';
let _newsSrc = 'all';      // 'all' = gabungan semua sumber di kategori
let _newsItems = [];
let _newsUpdated = 0;
// Urutan kategori yang diinginkan (server mengembalikan urutan alfabetis)
const NEWS_CAT_ORDER = ['indonesia', 'internasional', 'teknologi', 'ekonomi', 'olahraga', 'hiburan'];

function timeAgo(ts) {
    if (!ts) return '';
    const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
    if (s < 60) return 'baru saja';
    if (s < 3600) return Math.floor(s / 60) + ' mnt lalu';
    if (s < 86400) return Math.floor(s / 3600) + ' jam lalu';
    return Math.floor(s / 86400) + ' hari lalu';
}

function newsCardHtml(n, respSource) {
    const img = n.img
        ? `<img class="nc-img" src="/api/news-img?url=${encodeURIComponent(n.img)}" loading="lazy" alt="" onerror="this.style.visibility='hidden'">`
        : '';
    const time = n.ts ? `<span class="nc-time">${timeAgo(n.ts)}</span>` : '';
    return `<a class="news-card" href="${esc(n.link)}" target="_blank" rel="noopener">
        ${img}
        <div class="nc-body">
            <div class="nc-t">${esc(n.title)}</div>
            ${n.desc ? `<div class="nc-d">${esc(n.desc)}</div>` : ''}
            <div class="nc-m"><span class="nc-src">${esc(n.source || respSource || '')}</span>${time}</div>
        </div>
    </a>`;
}

function updateNewsLive() {
    const el = document.getElementById('news-live');
    if (!el) return;
    const dot = '<span class="dot"></span>';
    el.innerHTML = _newsUpdated
        ? `${dot} Live — diperbarui ${timeAgo(_newsUpdated)} · ${_newsItems.length} berita`
        : `${dot} Live — berita terbaru dari banyak sumber`;
}

async function loadNewsSetup() {
    const catsBox = document.getElementById('news-cats');
    if (!catsBox) return;
    try {
        const d = await fetchJSON('/api/news-sources');
        const cats = Object.keys(d.categories).sort(
            (a, b) => NEWS_CAT_ORDER.indexOf(a) - NEWS_CAT_ORDER.indexOf(b));
        if (!cats.length) return;
        _newsCat = cats[0];
        catsBox.innerHTML = cats.map(c =>
            `<button class="pill ${c === _newsCat ? 'active' : ''}" data-c="${esc(c)}" onclick="newsSetCat('${esc(c)}')">${esc(d.categories[c].label)}</button>`).join('');
        renderNewsSources(d.categories);
        loadNews();
    } catch (e) { /* ignore */ }
}

function renderNewsSources(categories) {
    const srcBox = document.getElementById('news-srcs');
    const cat = categories[_newsCat];
    const list = (cat && cat.sources) || [];
    if (!list.length) return;
    let html = `<button class="pill ${_newsSrc === 'all' ? 'active' : ''}" data-s="all" onclick="newsSetSrc('all')">Semua</button>`;
    html += list.map(s =>
        `<button class="pill ${s.key === _newsSrc ? 'active' : ''}" data-s="${esc(s.key)}" onclick="newsSetSrc('${esc(s.key)}')">${esc(s.name)}</button>`).join('');
    srcBox.innerHTML = html;
}

function newsSetCat(cat) {
    _newsCat = cat;
    document.querySelectorAll('#news-cats .pill').forEach(b =>
        b.classList.toggle('active', b.dataset.c === cat));
    _newsSrc = 'all';
    // bersihkan kolom cari — biar tidak ada kata kunci sisa dari kategori lama
    const nq = document.getElementById('news-q');
    if (nq) nq.value = '';
    fetchJSON('/api/news-sources').then(d => {
        renderNewsSources(d.categories);
        loadNews();
    }).catch(() => loadNews());
}

function newsSetSrc(key) {
    _newsSrc = key || 'all';
    document.querySelectorAll('#news-srcs .pill').forEach(b =>
        b.classList.toggle('active', b.dataset.s === _newsSrc));
    loadNews();
}

function newsCurrentQuery() {
    const nq = document.getElementById('news-q');
    return nq ? (nq.value || '').trim() : '';
}

async function loadNews() {
    const box = document.getElementById('news-results');
    if (!box) return;
    const q = newsCurrentQuery();
    box.innerHTML = '<div class="music-empty">Memuat berita…</div>';
    try {
        // Kirim kata kunci ke server juga — kalau data belum termuat, pencarian
        // tetap bekerja (server memfilter judul/isi).
        const d = await fetchJSON('/api/news?source=' + encodeURIComponent(_newsSrc) +
                                  '&category=' + encodeURIComponent(_newsCat) +
                                  '&q=' + encodeURIComponent(q));
        const items = d.items || [];
        _newsItems = items;
        _newsUpdated = d.updated_at || Math.floor(Date.now() / 1000);
        if (!items.length) {
            box.innerHTML = `<div class="music-empty"><p>${q
                ? `Tidak ada berita yang cocok dengan "${esc(q)}". Coba kata kunci lain.`
                : 'Belum ada berita dari sumber ini.'}</p></div>`;
            updateNewsLive();
            return;
        }
        const respSource = d.source || '';
        box.innerHTML = `<div class="news-grid">` +
            items.map(n => newsCardHtml(n, respSource)).join('') + `</div>`;
        updateNewsLive();
    } catch (e) {
        box.innerHTML = `<div class="state-error"><b>Gagal muat berita</b><p>${esc(e.message)}</p></div>`;
        updateNewsLive();
    }
}

function newsRefresh() {
    loadNews();
    toast('Berita disegarkan');
}

function newsFilterNow() {
    // filter instan client-side saat mengetik di kolom cari; kalau tidak ada
    // item yang cocok di data ter-muat, minta server cari (debounce singkat).
    const q = (document.getElementById('news-q').value || '').trim();
    const box = document.getElementById('news-results');
    const ql = q.toLowerCase();
    const items = _newsItems.filter(n =>
        !ql || (n.title || '').toLowerCase().includes(ql) || (n.desc || '').toLowerCase().includes(ql));
    if (q && !items.length && _newsItems.length) {
        // tidak ada di data ter-muat → coba server
        clearTimeout(newsFilterNow._tm);
        newsFilterNow._tm = setTimeout(() => loadNews(), 450);
        box.innerHTML = '<div class="music-empty">Mencari di semua sumber…</div>';
        return;
    }
    if (!items.length) {
        box.innerHTML = `<div class="music-empty"><p>${q ? 'Tidak ada berita yang cocok dengan "' + esc(q) + '".' : 'Belum ada berita.'}</p></div>`;
        return;
    }
    const respSource = (items[0] && items[0].source) || '';
    box.innerHTML = `<div class="news-grid">` +
        items.map(n => newsCardHtml(n, respSource)).join('') + `</div>`;
}

function newsSearchEnter() {
    // Enter di kolom cari → paksa cari ke server (fresh load dengan kata kunci)
    loadNews();
}

/* ============================================================
   CHAT GLOBAL
   ============================================================ */
let _chatSince = 0;
let _chatTimer = null;

let _chatReplyId = 0;
let _chatReplyWho = '';
let _chatReplyText = '';
let _chatAttach = null;   // {url, type, name}

function chatAttachmentHtml(m) {
    // Lampiran: gambar inline / video player / file link
    if (!m.attach_url) return '';
    const t = m.attach_type || 'file';
    const nm = m.attach_name || 'file';
    if (t === 'image') {
        return `<div class="cm-attach cm-img"><img src="${esc(m.attach_url)}" loading="lazy" alt="${esc(nm)}" onclick="window.open('${esc(m.attach_url)}','_blank')"></div>`;
    }
    if (t === 'video') {
        return `<div class="cm-attach"><video src="${esc(m.attach_url)}" controls preload="metadata"></video></div>`;
    }
    if (t === 'audio') {
        return `<div class="cm-attach"><audio src="${esc(m.attach_url)}" controls preload="metadata"></audio></div>`;
    }
    return `<div class="cm-attach cm-file"><a href="${esc(m.attach_url)}" target="_blank" rel="noopener">
        <span class="cm-file-ic">📄</span><span class="cm-file-name">${esc(nm)}</span></a></div>`;
}

function chatReplyHtml(m) {
    if (!m.reply_to) return '';
    return `<div class="cm-reply" onclick="chatReplyTo(${esc(m.id)}, '${esc((m.reply_to.username || '').replace(/'/g, '\\\''))}', '${esc((m.reply_to.message || '').slice(0, 80).replace(/'/g, '\\\''))}')">
        <span class="cm-reply-who">${esc(m.reply_to.username)}</span>
        <span class="cm-reply-text">${esc(m.reply_to.message || '')}</span>
    </div>`;
}

async function chatLoad() {
    try {
        const d = await fetchJSON('/api/chat?since=' + _chatSince);
        const list = document.getElementById('chat-list');
        if (!list) return;
        const msgs = d.messages || [];
        if (msgs.length) {
            _chatSince = msgs[msgs.length - 1].id;
            for (const m of msgs) {
                const own = m.username === _authUser && _authUser;
                const div = document.createElement('div');
                div.className = 'chat-msg' + (own ? ' own' : '');
                div.dataset.id = m.id;
                div.innerHTML = `<div class="cm-head">
                        <span class="cm-user">${esc(m.username)}</span>
                        <span class="cm-time">${new Date(m.created * 1000).toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' })}</span>
                        <button class="cm-reply-btn" onclick="chatReplyTo(${esc(m.id)}, '${esc(m.username.replace(/'/g, '\\\''))}', '${esc((m.message || '').slice(0, 80).replace(/'/g, '\\\''))}')" title="Balas" aria-label="Balas">
                            <svg viewBox="0 0 24 24" class="ic"><path d="M10 9V5l-7 7 7 7v-4.1c5 0 8.5 1.6 11 5.1-1-5-4-10-11-11z"/></svg>
                        </button>
                    </div>
                    ${chatReplyHtml(m)}
                    ${chatAttachmentHtml(m)}
                    <div class="cm-text">${esc(m.message || '')}</div>`;
                list.appendChild(div);
            }
            list.scrollTop = list.scrollHeight;
        }
    } catch (e) { /* abaikan */ }
}

function chatReplyTo(id, who, text) {
    _chatReplyId = id;
    _chatReplyWho = who;
    _chatReplyText = text || '';
    const bar = document.getElementById('chat-replybar');
    bar.classList.remove('hidden');
    document.getElementById('chat-reply-who').textContent = who;
    document.getElementById('chat-reply-text').textContent = _chatReplyText || '(pesan)';
}

function chatCancelReply() {
    _chatReplyId = 0;
    _chatReplyWho = '';
    _chatReplyText = '';
    document.getElementById('chat-replybar').classList.add('hidden');
}

function chatPickFile() {
    if (!_authToken) { toast('Login dulu untuk kirim lampiran.', true); return; }
    document.getElementById('chat-file-input').click();
}

function chatClearAttach() {
    _chatAttach = null;
    document.getElementById('chat-attach').classList.add('hidden');
    document.getElementById('chat-file-input').value = '';
}

async function chatSend() {
    const input = document.getElementById('chat-input');
    const msg = (input.value || '').trim();
    if (!msg && !_chatAttach) return;
    try {
        const body = { message: msg };
        if (_chatReplyId) body.parent_id = _chatReplyId;
        if (_chatAttach) {
            body.attach_url = _chatAttach.url;
            body.attach_type = _chatAttach.type;
            body.attach_name = _chatAttach.name;
        }
        await fetchJSON('/api/chat', {
            method: 'POST', headers: authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(body),
        });
        input.value = '';
        chatCancelReply();
        chatClearAttach();
        chatLoad();
    } catch (e) {
        toast(e.message, true);
    }
}

/* ————— upload lampiran chat ————— */
(function setupChatUpload() {
    const fi = document.getElementById('chat-file-input');
    if (!fi) return;
    fi.addEventListener('change', async () => {
        const f = fi.files && fi.files[0];
        if (!f) return;
        if (f.size > 25 * 1024 * 1024) { toast('File terlalu besar (maks 25 MB).', true); fi.value = ''; return; }
        const fd = new FormData();
        fd.append('file', f);
        try {
            const d = await fetchJSON('/api/chat/upload', { method: 'POST', body: fd });
            _chatAttach = { url: d.url, type: d.type, name: d.name };
            const bar = document.getElementById('chat-attach');
            bar.classList.remove('hidden');
            document.getElementById('chat-attach-name').textContent = d.name + (d.type === 'image' ? ' 🖼️' : d.type === 'video' ? ' 🎬' : ' 📎');
            toast('Lampiran siap dikirim');
        } catch (e) {
            toast(e.message, true);
            fi.value = '';
        }
    });
})();

/* ---------- Boot ---------- */
init();
startClock();
checkAuth().then(() => {
    // Login overlay: tampil otomatis kalau belum ada akun/tamu tersimpan
    if (!_authToken) showLogin();
});
loadNewsSetup();
loadPlatformRequests();
loadFeedback();
mangaInitGenres().then(() => mangaRecommend());
// Berita live: refresh otomatis tiap 2 menit saat tab Berita sedang terbuka,
// dan perbarui teks "Live — diperbarui …" tiap 30 detik.
setInterval(() => {
    const vn = document.getElementById('view-news');
    if (vn && !vn.classList.contains('hidden')) loadNews();
}, 120000);
setInterval(() => updateNewsLive(), 30000);
(function chatInit() {
    chatLoad();
    _chatTimer = setInterval(chatLoad, 4000);
    const ci = document.getElementById('chat-input');
    if (ci) ci.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') chatSend(); });
    const au = document.getElementById('auth-user');
    const ap = document.getElementById('auth-pass');
    if (au) au.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') doAuth(); });
    if (ap) ap.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') doAuth(); });
})();
