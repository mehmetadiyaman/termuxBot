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
echo "[4/4] Termux:Widget kısayolları ayarlanıyor..."
mkdir -p ~/.shortcuts/tasks

# Botu Başlat kısayolu
cat << 'EOF' > ~/.shortcuts/Botu_Baslat.sh
#!/bin/bash
cd ~/termuxBot
clear
echo "🚀 Viewpoints Bot Başlatılıyor..."
python bot.py
EOF

# Hesap Menüsü kısayolu
cat << 'EOF' > ~/.shortcuts/Hesap_Menusu.sh
#!/bin/bash
cd ~/termuxBot
clear
python accounts.py
EOF

# İzinleri ver
chmod +x ~/.shortcuts/*.sh

echo ""
echo "================================================="
echo "✅ Kurulum Başarıyla Tamamlandı!"
echo "================================================="
echo "Ana ekranınıza 'Termux:Widget' ekleyerek"
echo "'Botu_Baslat' butonuna tıklayabilirsiniz."
echo "================================================="
