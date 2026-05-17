#!/bin/bash

set -e

echo "🔧 Устанавливаем зависимости..."
sudo apt update
sudo apt install -y make build-essential libssl-dev zlib1g-dev \
libbz2-dev libreadline-dev libsqlite3-dev curl libncursesw5-dev \
xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev \
wget

echo "⬇️ Загружаем Python 3.10.13..."
cd /tmp
wget https://www.python.org/ftp/python/3.10.13/Python-3.10.13.tgz
tar -xf Python-3.10.13.tgz
cd Python-3.10.13

echo "⚙️ Сборка Python..."
./configure --enable-optimizations --prefix=/opt/python310
make -j$(nproc)

echo "📦 Установка в /opt/python310..."
sudo make altinstall

echo "🧪 Создаём виртуальное окружение ~/sgarin310..."
/opt/python310/bin/python3.10 -m venv ~/sgarin310

echo "✅ Готово! Активируйте окружение:"
echo "source ~/sgarin310/bin/activate"
