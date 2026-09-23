#!/bin/bash

echo "================================================="
echo "🚀 Viewpoints Bot Kurulumuna Hoş Geldiniz! 🚀"
echo "================================================="
echo ""
echo "[1/4] Gerekli paketler kontrol ediliyor..."
pkg update -y
pkg install -y python tsu curl ncurses-utils git

echo ""
echo "[2/4] Hedef klasör oluşturuluyor..."
mkdir -p ~/termuxBot
cd ~/termuxBot

echo ""
echo "[3/4] Dosyalar indiriliyor (Git Clone)..."
# Eğer daha önce indirilmişse silip baştan çekiyoruz
cd ~
rm -rf termuxBot
git clone https://github.com/mehmetadiyaman/termuxBot.git termuxBot
cd termuxBot

echo ""
echo "================================================="
echo "✅ Kurulum Başarıyla Tamamlandı!"
echo "================================================="
echo "Bot başlatılıyor..."
echo ""

python bot.py
