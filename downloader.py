import os
import subprocess
import aiohttp # type: ignore
import asyncio
import urllib.parse
from typing import Dict, Optional

async def download_file(url: str, output_path: str, headers: Optional[Dict[str, str]] = None) -> bool:
    """Download file biasa (subtitles, mp4 direct)."""
    # Deteksi referer otomatis
    referer = "https://www.google.com/"
    if "mydramawave.com" in url: referer = "https://www.mydramawave.com/"
    elif "vividshort.com" in url: referer = "https://vividshort.com/"
    elif "farsunpteltd.com" in url: referer = "https://pages.farsunpteltd.com/"
    
    parsed = urllib.parse.urlparse(referer)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    
    default_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": referer,
        "Origin": origin,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "cross-site"
    }
    
    if headers:
        default_headers.update(headers)
        
    try:
        async with aiohttp.ClientSession(headers=default_headers) as session:
            async with session.get(url, timeout=300) as response:
                response.raise_for_status()
                with open(output_path, 'wb') as f:
                    while True:
                        chunk = await response.content.read(8192)
                        if not chunk:
                            break
                        f.write(chunk)
        return True
    except Exception as e:
        print(f"Error download {url}: {e}")
        return False

async def download_aria2(url: str, output_path: str, headers: Optional[Dict[str, str]] = None) -> bool:
    """Download file menggunakan aria2c untuk kecepatan maksimal."""
    # Deteksi referer otomatis
    referer = "https://www.google.com/"
    if "mydramawave.com" in url: referer = "https://www.mydramawave.com/"
    elif "vividshort.com" in url: referer = "https://vividshort.com/"
    elif "farsunpteltd.com" in url: referer = "https://pages.farsunpteltd.com/"
    
    dir_name = os.path.dirname(output_path)
    file_name = os.path.basename(output_path)

    cmd = [
        "aria2c", 
        "--console-log-level=warn",
        "-x", "16", 
        "-s", "16", 
        "-k", "1M",
        "--dir", dir_name,
        "--out", file_name,
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "--referer", referer,
        url
    ]

    if headers:
        for k, v in headers.items():
            cmd.extend(["--header", f"{k}: {v}"])
            
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        await process.communicate()
        
        # Cek apakah file benar-benar ada dan ukurannya masuk akal
        if process.returncode == 0 and os.path.exists(output_path):
            if os.path.getsize(output_path) > 1024 * 512: # Minimal 512KB (bukan playlist m3u8)
                return True
            else:
                print(f"Warning: File too small ({os.path.getsize(output_path)} bytes), possible playlist or error page.")
                if os.path.exists(output_path): os.remove(output_path)
        return False
    except Exception as e:
        print(f"Aria2 error: {e}")
        return False

async def download_video_ffmpeg(m3u8_url: str, output_path: str, headers: dict | None = None) -> bool:
    """Download video dari m3u8 menggunakan ffmpeg (Async)."""
    # Deteksi referer otomatis
    referer = "https://www.google.com/"
    if "mydramawave.com" in m3u8_url: referer = "https://www.mydramawave.com/"
    elif "vividshort.com" in m3u8_url: referer = "https://vividshort.com/"
    elif "farsunpteltd.com" in m3u8_url: referer = "https://pages.farsunpteltd.com/"
    
    cmd = ["ffmpeg", "-y"]
    
    final_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": referer
    }
    if headers:
        final_headers.update(headers)
        
    header_str = "".join([f"{k}: {v}\r\n" for k, v in final_headers.items()])
    cmd.extend(["-headers", header_str])
        
    # Optimasi: threads 0 (auto), copy streaming
    cmd.extend([
        "-threads", "0",
        "-i", m3u8_url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        output_path
    ])
    
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        await process.communicate()
        
        if process.returncode == 0 and os.path.exists(output_path):
            if os.path.getsize(output_path) > 1024 * 1024: # Minimal 1MB untuk video
                return True
            else:
                print(f"Warning: ffmpeg output too small ({os.path.getsize(output_path)} bytes).")
                if os.path.exists(output_path): os.remove(output_path)
        return False
    except Exception as e:
        print(f"FFmpeg async error: {e}")
        return False

async def download_video_ytdlp(url: str, output_path: str, headers: dict | None = None) -> bool:
    """Download video menggunakan yt-dlp dengan optimasi kecepatan (Async)."""
    referer = "https://www.google.com/"
    if "mydramawave.com" in url: referer = "https://www.mydramawave.com/"
    elif "vividshort.com" in url: referer = "https://vividshort.com/"
    elif "farsunpteltd.com" in url: referer = "https://pages.farsunpteltd.com/"
    
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    
    # Deteksi Origin
    parsed_url = urllib.parse.urlparse(url)
    origin = f"{parsed_url.scheme}://{parsed_url.netloc}"

    cmd = [
        "yt-dlp", "--no-warnings", "-q", 
        "--user-agent", ua, 
        "--referer", referer,
        "--add-header", f"Origin: {origin}",
        "--no-check-certificate",
        "--ignore-config",
        "--no-playlist",
        "--concurrent-fragments", "16",
        "--buffer-size", "1M",
        "--retries", "3",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4"
    ]
    
    # Tambahkan impersonate jika yt-dlp modern (meniru sidik jari browser Chrome)
    cmd.extend(["--impersonate", "chrome"])
    
    if headers:
        for k, v in headers.items():
            cmd.extend(["--add-header", f"{k}: {v}"])
        
    cmd.extend(["-o", output_path, url])
    
    try:
        process = await asyncio.create_subprocess_exec(*cmd)
        await process.communicate()
        
        if process.returncode == 0 and os.path.exists(output_path):
            if os.path.getsize(output_path) > 1024 * 1024: # Minimal 1MB
                return True
            else:
                print(f"Warning: yt-dlp output too small ({os.path.getsize(output_path)} bytes).")
                if os.path.exists(output_path): os.remove(output_path)
        return False
    except Exception as e:
        print(f"yt-dlp async error: {e}")
        return False

async def mux_subtitle(video_path: str, sub_path: str, output_ext: str) -> str:
    """Mux subtitle softsub ke video (Async)."""
    output_path = video_path.replace(".mp4", f"_subbed.{output_ext}")
    
    # Dasar perintah
    cmd = ["ffmpeg", "-y", "-threads", "0", "-i", video_path, "-i", sub_path]
    
    if output_ext == "mkv":
        cmd.extend([
            "-map", "0", "-map", "1",
            "-c", "copy", "-c:s", "srt",
            "-metadata:s:s:0", "language=ind",
            "-metadata:s:s:0", "title=Indonesia"
        ])
    else:  # mp4
        cmd.extend([
            "-map", "0", "-map", "1",
            "-c", "copy", "-c:s", "mov_text",
            "-metadata:s:s:0", "language=ind"
        ])
    
    cmd.append(output_path)
    
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        await process.communicate()
        return output_path if process.returncode == 0 else ""
    except Exception as e:
        print(f"Mux async error: {e}")
        return ""
