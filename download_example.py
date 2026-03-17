import subprocess

def download_video_from_json(item_json):
    # 1. Persiapkan Nama Judul File (Membersihkan karakter ilegal di Windows)
    raw_title = item_json.get("chapter_title", "Video_Tidak_Dikenal")
    safe_title = "".join(c for c in raw_title if c.isalpha() or c.isdigit() or c in ' -_').strip()
    
    # 2. Prioritaskan URL MP4 (origin_down_url), fallback ke HLS m3u8 (hls_url)
    origin_url = item_json.get("origin_down_url")
    hls_url = item_json.get("hls_url")
    
    target_url = origin_url if origin_url else hls_url
    
    if not target_url:
        print(f"Error: Tidak ada link download untuk {safe_title}")
        return False

    print(f"Mulai mendownload: {safe_title}")
    print(f"Tipe URL: {'MP4 (Direct)' if target_url == origin_url else 'HLS (.m3u8)'}")
    
    # 3. Parameter yt-dlp Anti-Error
    cmd = [
        "yt-dlp",
        "--no-warnings",
        
        # Penanganan Format & Output (Solusi: Fixed output name tapi >1 download stream)
        "-o", f"{safe_title}.%(ext)s",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        
        # Penanganan Jaringan & Error Timeout (Solusi: HTTP 408 & Fragment Error)
        "--retries", "10",
        "--fragment-retries", "10",
        "--retry-sleep", "5",
        "--socket-timeout", "60",
        
        # Optimasi Kecepatan
        "--concurrent-fragments", "16",
        "--buffer-size", "1M",
        "--no-playlist",
        
        target_url
    ]
    
    # 4. Eksekusi Subprocess
    try:
        subprocess.run(cmd, check=True)
        print(f"✅ Selesai: {safe_title}.mp4")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Gagal mendownload {safe_title}. Error code: {e.returncode}")
        return False

if __name__ == "__main__":
    # Contoh Penggunaan JSON
    sample_data = {
        "chapter_title": "Kebangkitan Gadis Jelek - EP.1",
        "hls_url": "https://example.com/playlist.m3u8",
        "origin_down_url": "https://example.com/video_direct.mp4"
    }
    
    download_video_from_json(sample_data)
