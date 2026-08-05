#!/bin/bash
# ============================================================
# push.sh — Push ke GitHub TANPA diminta token/username lagi
#
# Cara pakai:
#   bash push.sh "pesan commit"
#
# Ciri baru:
#   • SELALU bikin commit baru (--allow-empty) → tidak akan pernah
#     muncul "Everything up-to-date". Setiap jalan = pasti ke-push.
#   • Kalau folder ini ZIP LAMA (tanpa penanda "Versi UI"), tampil
#     peringatan sebelum push.
#   • Di akhir, nunjukin nomor versi UI yang ada di folder ini.
# ============================================================

set -e

REPO_URL="https://github.com/ahsansdr11-spec/Nna.git"
USER_NAME="ahsansdr11-spec"
USER_EMAIL="ahsansdr11@gmail.com"

cd "$(dirname "$0")"

# 0) Cek apakah ini kode TERBARU (penanda "Versi UI" ada di index.html)
UI_MARK=$(grep -c "Versi UI" static/index.html 2>/dev/null || echo 0)
UI_NUM=$(grep -o 'UI_VERSION = [0-9]*' static/app.js 2>/dev/null | grep -o '[0-9]*' || echo "?")
if [ "$UI_MARK" = "0" ]; then
    echo "⚠️  PERINGATAN: folder ini TIDAK punya penanda \"Versi UI\"."
    echo "   Artinya ini ZIP LAMA — download ZIP terbaru & unzip ulang dulu,"
    echo "   kalau tidak Railway tetap dapat kode lama."
    echo ""
fi

# 1) Aktifkan penyimpan kredensial (kalau belum)
git config --global credential.helper store || true

# 2) Setup repo kalau folder ini belum repo git (baru di-unzip = git hilang)
if [ ! -d .git ]; then
    echo ">> Membuat repo git baru..."
    git init -q
    git config user.name "$USER_NAME"
    git config user.email "$USER_EMAIL"
    git branch -M main
    git remote add origin "$REPO_URL"
else
    echo ">> Repo git sudah ada — pakai yang lama"
    git remote set-url origin "$REPO_URL"
fi

# 3) Commit SELALU (--allow-empty): walau isi sama, tetap jadi commit baru
git add .
git commit --allow-empty -m "${1:-update}" -q || true

# 4) Push FORCE — dijamin update, tidak akan "Everything up-to-date"
echo ">> Push ke GitHub (force)..."
echo "   (Push pertama: isi Username = $USER_NAME, Password = token GitHub-mu)"
git push -u origin main --force

echo ""
echo "==============================================================="
echo "  ✅ Push selesai!"
echo "  Versi UI di folder ini : $UI_NUM"
echo "  Tunggu 1-2 menit, buka situs → scroll bawah → cek 'Versi UI'"
echo "  Push berikutnya tinggal: bash push.sh \"pesan\""
echo "==============================================================="
