import os

# Telegram API & Bot Token (Dapatkan dari my.telegram.org & @BotFather)
API_ID = 30653860   # Ganti dengan API ID dari my.telegram.org
API_HASH = "98e0a87077d4fc642ce183dfd7f46a19"  # Ganti dengan API Hash dari my.telegram.org
BOT_TOKEN = "8687157077:AAGhlvyD6y6JMSQ5hbsYpOruUNSKF_ukaSA"

# Folder sementara untuk download file
TEMP_DIR = "C:/tmp/drama_bot/" if os.name == 'nt' else "/tmp/drama_bot/"

# Setting default
PREFERRED_QUALITY = "720p"
MAX_RETRY = 3
TIMEOUT_DL = 600
STATUS_UPDATE_INTERVAL = 2
TELEGRAM_MAX_SIZE = 2000 * 1024 * 1024  # 2GB Limit API
ALLOWED_USERS = []  # Kosongkan jika semua user boleh akses
OWNER_ID = 5888747846  # User ID Telegram Anda untuk fitur /update 

# Concurrency & Speed
MAX_CONCURRENT_DOWNLOADS = 3  # Jumlah download episode sekaligus per session
WORKERS = 10  # Jumlah worker telegram untuk menangani banyak user sekaligus

# Proxy (Opsional, gunakan jika bot sering timeout atau diblokir)
HTTP_PROXY = "" # Contoh: "http://user:pass@host:port"

# Telegram Local Bot API Server (Wajib jika ingin upload > 50MB hingga 2GB)
# Contoh: "http://127.0.0.1:8081" (Kosongkan jika pakai server standar Telegram)
LOCAL_BOT_API_URL = "" 
