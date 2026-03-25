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
    elif "shorttv.live" in url: referer = "https://shorttv.live/"
    
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
    elif "shorttv.live" in m3u8_url: referer = "https://shorttv.live/"
    
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
    # Deteksi Referer & Origin secara Dinamis
    parsed_u = urllib.parse.urlparse(url)
    domain = parsed_u.netloc
    origin = f"{parsed_u.scheme}://{domain}"
    
    # Custom referer untuk platform tertentu
    referer = origin + "/"
    if "short-cdn.com" in domain or "fast-cdn.com" in domain or "dramabox" in url:
        referer = "https://www.dramabox.com/"
    elif "vividshort.com" in url:
        referer = "https://www.vividshort.com/"
    elif "shorttv.live" in url:
        referer = "https://www.shorttv.live/"
        
    # Gunakan User-Agent Mobile agar lebih lancar
    ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    
    cmd = [
        "yt-dlp", "--no-warnings", 
        "--user-agent", ua, 
        "--referer", referer,
        "--no-check-certificate",
        "--add-header", f"Origin: {origin}",
        "--add-header", "Accept: */*",
        "--add-header", "Accept-Language: en-US,en;q=0.9",
        "--add-header", "Sec-Fetch-Mode: cors",
        "--add-header", "Sec-Fetch-Site: cross-site",
        "--ignore-config",
        "--no-playlist",
        "--concurrent-fragments", "16",
        "--buffer-size", "1M",
        "--retries", "5",
        "--external-downloader", "aria2c", 
        "--external-downloader-args", "aria2c:-x 16 -s 16 -k 1M",
        "-f", "bestvideo+bestaudio/best",
        "--merge-output-format", "mp4"
    ]
    
    # Tambahkan impersonate jika yt-dlp modern (opsional)
    # cmd.extend(["--impersonate", "chrome"])
    
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

async def burn_subtitle(video_path: str, sub_path: str) -> Optional[str]:
    """Hardsub subtitle ke video (Re-encoding) dengan efisiensi tinggi (720p)."""
    output_path = video_path.replace(".mp4", "_hardsub.mp4")
    
    # Style: FontName,FontSize,PrimaryColour,OutlineColour,Outline,Bold,MarginV
    # Warna: &H00FFFFFF (Putih), &H00000000 (Hitam)
    style = "FontName=Standard Symbols PS,FontSize=10,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=1,Bold=1,MarginV=90"
    
    # Filter subtitles + scaling 720p
    # Gunakan subtitles filter dengan auto-detection format (srt/ass/vtt)
    # Catatan: Path subtitle harus di-escape jika mengandung karakter aneh
    filter_complex = f"subtitles='{sub_path}':force_style='{style}',scale=1280:720"
    
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", filter_complex,
        "-c:v", "libx264", 
        "-preset", "veryfast", # Kecepatan tinggi (veryfast)
        "-crf", "23",          # Kualitas seimbang (crf 23)
        "-c:a", "aac", 
        "-b:a", "128k",        # Audio efisien (128k)
        output_path
    ]
    
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        await process.communicate()
        return output_path if process.returncode == 0 else None
    except Exception as e:
        print(f"Hardsub Error: {e}")
        return None

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

async def merge_videos(video_list: list[str], output_path: str) -> bool:
    """Gabungkan daftar video menjadi satu file menggunakan FFmpeg concat demuxer."""
    if not video_list: return False
    
    # Buat file list untuk concat
    list_path = output_path.replace(".mp4", "_list.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for v in video_list:
            # escaping path for ffmpeg concat list
            abs_v = os.path.abspath(v).replace("\\", "/")
            f.write(f"file '{abs_v}'\n")
            
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", list_path, "-c", "copy", output_path
    ]
    
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        await process.communicate()
        if os.path.exists(list_path): os.remove(list_path)
        return process.returncode == 0 and os.path.exists(output_path)
    except Exception as e:
        print(f"Merge Error: {e}")
        if os.path.exists(list_path): os.remove(list_path)
        return False

async def extract_thumbnail(video_path: str, thumb_path: str) -> bool:
    """Ekstrak thumbnail dari video menggunakan FFmpeg."""
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-ss", "00:00:02", # Ambil detik ke-2
        "-vframes", "1",
        "-vf", "scale=320:-1", # Lebar 320px
        thumb_path
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        await process.communicate()
        return process.returncode == 0 and os.path.exists(thumb_path)
    except Exception:
        return False

async def get_video_info(video_path: str) -> dict:
    """Ambil informasi durasi, lebar, dan tinggi video menggunakan ffprobe."""
    import json
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration:stream=duration,width,height",
        "-of", "json", video_path
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, _ = await process.communicate()
        data = json.loads(stdout)
        
        # 1. Cari durasi di Format
        duration_raw = data.get("format", {}).get("duration")
        
        # 2. Cari di Streams jika tidak ada di format
        streams = data.get("streams", [])
        if duration_raw is None and streams:
            duration_raw = streams[0].get("duration")
            
        duration = float(duration_raw) if duration_raw else 0
        
        info = {
            "duration": int(duration),
            "width": 0,
            "height": 0
        }
        
        for s in streams:
            if "width" in s and "height" in s and s.get("width") and s.get("height"):
                info["width"] = int(s["width"])
                info["height"] = int(s["height"])
                break
                
        # Jika durasi masih 0, kembalikan default kecil agar tidak error 0:00
        if info["duration"] < 1: info["duration"] = 1
            
        return info
    except Exception as e:
        print(f"FFprobe error: {e}")
        return {"duration": 1, "width": 0, "height": 0}
