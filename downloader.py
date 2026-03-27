import os
import subprocess
import aiohttp
import asyncio
import urllib.parse
from typing import Dict, Optional, List, Any
import random
import time
import config
from proxy_manager import proxy_manager

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36"
]

async def download_file(url: str, output_path: str, headers: Optional[Dict[str, str]] = None) -> bool:
    """Download file biasa (subtitles, mp4 direct)."""
    # Deteksi referer otomatis
    referer = "https://www.google.com/"
    if "mydramawave.com" in url: referer = "https://www.mydramawave.com/"
    elif "vividshort.com" in url: referer = "https://vividshort.com/"
    elif "farsunpteltd.com" in url: referer = "https://pages.farsunpteltd.com/"
    elif "shorttv.live" in url: referer = "https://shorttv.live/"
    elif "netshort.com" in url: referer = "https://www.netshort.com/"
    elif "reelshort.com" in url or "crazymaplestudios.com" in url: referer = "https://www.reelshort.com/"
    elif "vigloo.com" in url: referer = "https://www.vigloo.com/"
    elif "rishort.workers.dev" in url: referer = "https://hls-proxy.rishort.workers.dev/"
    elif "flickreels.com" in url: referer = "https://www.flickreels.com/"
    
    # Pilih User-Agent Random
    ua = random.choice(USER_AGENTS)
    
    parsed = urllib.parse.urlparse(referer)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    
    default_headers = {
        "User-Agent": ua,
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
        # Default proxy dari config
        proxy = getattr(config, 'HTTP_PROXY', None)
        
        async with aiohttp.ClientSession(headers=default_headers) as session:
            async with session.get(url, timeout=300, proxy=proxy) as response:
                if response.status == 200:
                    with open(output_path, 'wb') as f:
                        while True:
                            chunk = await response.content.read(8192)
                            if not chunk: break
                            f.write(chunk)
                    return True
                    
        # Fallback to Auto Proxy
        if getattr(config, 'USE_AUTO_PROXY', False):
            print("Download failed, retrying with free proxy...")
            for i in range(3): # Coba 3 kali dengan proxy berbeda
                # Tambahkan delay random (1-3 detik) agar tidak terdeteksi bot flood
                await asyncio.sleep(random.uniform(1.0, 3.0))
                
                auto_proxy = await proxy_manager.get_random_proxy()
                if not auto_proxy: break
                
                # Ganti UA lagi untuk setiap retry
                retry_headers = default_headers.copy()
                retry_headers["User-Agent"] = random.choice(USER_AGENTS)
                
                print(f"Attempting with Proxy: {auto_proxy}")
                try:
                    async with aiohttp.ClientSession(headers=retry_headers) as session:
                        async with session.get(url, timeout=120, proxy=auto_proxy) as response:
                            if response.status == 200:
                                with open(output_path, 'wb') as f:
                                    while True:
                                        chunk = await response.content.read(8192)
                                        if not chunk: break
                                        f.write(chunk)
                                return True
                except: pass
        return False
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
    elif "netshort.com" in url: referer = "https://www.netshort.com/"
    elif "reelshort.com" in url or "crazymaplestudios.com" in url: referer = "https://www.reelshort.com/"
    elif "vigloo.com" in url: referer = "https://www.vigloo.com/"
    elif "rishort.workers.dev" in url: referer = "https://hls-proxy.rishort.workers.dev/"
    
    dir_name = os.path.dirname(output_path)
    file_name = os.path.basename(output_path)

    base_cmd = [
        "aria2c", 
        "--console-log-level=warn",
        "-x", "16", 
        "-s", "16", 
        "-k", "1M",
        "--dir", dir_name,
        "--out", file_name,
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "--referer", referer,
    ]

    if headers:
        for k, v in headers.items():
            base_cmd.extend(["--header", f"{k}: {v}"])
            
    base_cmd.append(url)

    # Function to execute aria2c and check result
    async def execute_aria2(cmd_list):
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd_list,
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

    # Initial attempt with configured proxy
    current_cmd = list(base_cmd)
    proxy = getattr(config, 'HTTP_PROXY', None)
    if proxy:
        current_cmd.insert(-1, f"--all-proxy={proxy}") # Insert before URL
    
    if await execute_aria2(current_cmd):
        return True

    # Fallback to Auto Proxy
    if getattr(config, 'USE_AUTO_PROXY', False):
        print("Aria2 download failed, retrying with free proxy...")
        for i in range(3): # Coba 3 kali dengan proxy berbeda
            auto_proxy = await proxy_manager.get_random_proxy()
            if not auto_proxy: break
            print(f"Attempting Aria2 with Proxy: {auto_proxy}")
            
            proxy_cmd = list(base_cmd)
            proxy_cmd.insert(-1, f"--all-proxy={auto_proxy}") # Insert before URL
            
            if await execute_aria2(proxy_cmd):
                return True
    
    return False

async def download_video_ffmpeg(m3u8_url: str, output_path: str, headers: dict | None = None) -> bool:
    """Download video dari m3u8 menggunakan ffmpeg (Async)."""
    # Deteksi referer otomatis
    referer = "https://www.google.com/"
    if "mydramawave.com" in m3u8_url: referer = "https://www.mydramawave.com/"
    elif "vividshort.com" in m3u8_url: referer = "https://vividshort.com/"
    elif "farsunpteltd.com" in m3u8_url: referer = "https://pages.farsunpteltd.com/"
    elif "shorttv.live" in m3u8_url: referer = "https://shorttv.live/"
    elif "netshort.com" in m3u8_url: referer = "https://www.netshort.com/"
    elif "reelshort.com" in m3u8_url or "crazymaplestudios.com" in m3u8_url: referer = "https://www.reelshort.com/"
    elif "vigloo.com" in m3u8_url: referer = "https://www.vigloo.com/"
    elif "rishort.workers.dev" in m3u8_url: referer = "https://hls-proxy.rishort.workers.dev/"
    
    cmd = ["ffmpeg", "-y"]
    
    final_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": referer
    }
    if headers:
        final_headers.update(headers)
        
    header_str = "".join([f"{k}: {v}\r\n" for k, v in final_headers.items()])
    cmd.extend(["-headers", header_str])
        
    # Proxy support for FFmpeg
    proxy = getattr(config, 'HTTP_PROXY', None)
    if proxy:
        cmd.extend(["-http_proxy", proxy])
        
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

async def download_video_ytdlp(url: str, output_path: str, headers: dict | None = None, lang: str = "all") -> bool:
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
    elif "netshort.com" in url:
        referer = "https://www.netshort.com/"
    elif "reelshort.com" in url or "crazymaplestudios.com" in url:
        referer = "https://www.reelshort.com/"
    elif "vigloo.com" in url:
        referer = "https://www.vigloo.com/"
    elif "rishort.workers.dev" in url:
        referer = "https://hls-proxy.rishort.workers.dev/"
    elif "flickreels.com" in url:
        referer = "https://www.flickreels.com/"
    elif "onfilom.com" in url:
        referer = "https://www.shorttv.live/"
        
    # Gunakan User-Agent Mobile agar lebih lancar, Desktop untuk Worker Proxy
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    if "vigloo.com" in url or "reelshort.com" in url or "flickreels.com" in url:
        ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    
    base_cmd = [
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
        "--retries", "10",
        "--embed-subs",
        "--sub-format", "srt/vtt/best",
        "--convert-subs", "srt",
        "--merge-output-format", "mkv"
    ]
    
    # Subtitle selection
    if lang == "none":
        base_cmd.extend(["--no-write-subs"])
    elif lang == "all":
        base_cmd.extend(["--write-subs", "--sub-langs", "id.*,ind.*,en.*,all"])
    else:
        # Match specific language (e.g. ind, eng)
        base_cmd.extend(["--write-subs", "--sub-langs", f"{lang}.*"])
    
    # Disable aria2c for proxies and specific domains to improve stability
    is_worker = "workers.dev" in domain or "rishort" in url
    if not is_worker:
        base_cmd.extend([
            "--external-downloader", "aria2c", 
            "--external-downloader-args", "aria2c:-x 16 -s 16 -k 1M"
        ])
    
    base_cmd.extend(["-f", "bestvideo+bestaudio/best"])
    
    # Tambahkan impersonate jika yt-dlp modern (opsional)
    # base_cmd.extend(["--impersonate", "chrome"])
    
    if headers:
        for k, v in headers.items():
            base_cmd.extend(["--add-header", f"{k}: {v}"])
    
    base_cmd.extend(["-o", output_path, url])

    # Function to execute yt-dlp and check result
    async def execute_ytdlp(cmd_list):
        try:
            process = await asyncio.create_subprocess_exec(*cmd_list)
            await process.communicate()
            
            # Cek apakah file benar-benar ada dan ukurannya masuk akal
            if os.path.exists(output_path):
                if os.path.getsize(output_path) > 1024 * 512: # Minimal 512KB
                    return True
                else:
                    print(f"Warning: yt-dlp output too small ({os.path.getsize(output_path)} bytes).")
                    if os.path.exists(output_path): os.remove(output_path)
            return False
        except Exception as e:
            print(f"YT-DLP async error: {e}")
            return False

    # Initial attempt with configured proxy
    current_cmd = list(base_cmd)
    proxy = getattr(config, 'HTTP_PROXY', None)
    if proxy:
        current_cmd.insert(-2, "--proxy") # Insert before -o
        current_cmd.insert(-2, proxy)
    
    if await execute_ytdlp(current_cmd):
        return True
            
    # Jika gagal & USE_AUTO_PROXY aktif, coba lagi dengan proxy random
    if getattr(config, 'USE_AUTO_PROXY', False):
        print("YTDLP failed, retrying with auto-proxy...")
        for i in range(2): # Coba 2 kali saja agar tidak kelamaan
            # Delay random 2-5 detik
            await asyncio.sleep(random.uniform(2.0, 5.0))
            
            auto_proxy = await proxy_manager.get_random_proxy()
            if not auto_proxy: break
            
            # Ganti User-Agent random
            new_ua = random.choice(USER_AGENTS)
            
            # Copy cmd & ganti proxy-nya
            proxy_cmd = list(base_cmd)
            # Update UA di command list
            for idx, val in enumerate(proxy_cmd):
                if val == "--user-agent":
                    proxy_cmd[idx+1] = new_ua
            
            # Sisipkan --proxy ke list command
            proxy_cmd.insert(-2, "--proxy") # Insert before -o
            proxy_cmd.insert(-2, auto_proxy)
            
            print(f"Retrying YTDLP with Proxy: {auto_proxy} UA: {new_ua}")
            if await execute_ytdlp(proxy_cmd):
                return True

    # FINAL FALLBACK: Local HLS Proxy (Khusus m3u8)
    if ".m3u8" in url or "m3u8" in url.lower():
        print("Trying final fallback with local HLS Proxy...")
        proxy_video_url = f"http://localhost:8001/proxy?url={urllib.parse.quote(url)}"
        
        # Gunakan command asli (tanpa proxy lama) tapi ganti URL-nya
        final_cmd = list(base_cmd)
        # Update URL di command list
        for idx, val in enumerate(final_cmd):
            if val == url:
                final_cmd[idx] = proxy_video_url
                break
        else:
            # Fallback jika tidak ketemu di list
            final_cmd[-1] = proxy_video_url
            
        print(f"Retrying with Local HLS Proxy: {proxy_video_url}")
        if await execute_ytdlp(final_cmd):
            return True
                
    return False

async def burn_subtitle(video_path: str, sub_path: str) -> Optional[str]:
    """Hardsub subtitle ke video (Re-encoding) dengan efisiensi tinggi (720p)."""
    output_path = video_path.replace(".mp4", "_hardsub.mp4")
    
    # Style: FontName,FontSize,PrimaryColour,OutlineColour,Outline,Bold,MarginV
    # Warna: &H00FFFFFF (Putih), &H00000000 (Hitam)
    style = "FontName=Standard Symbols PS,FontSize=10,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=1,Bold=1,MarginV=90"
    
    # Filter subtitles + scaling 720x1280 (Portrait untuk Short Drama)
    filter_complex = f"subtitles='{sub_path}':force_style='{style}',scale=720:1280"
    
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

async def merge_videos(video_list: list[str], output_path: str, progress_callback=None) -> bool:
    """Gabungkan daftar video menjadi satu file menggunakan metode intermediate TS agar durasi akurat."""
    if not video_list: return False
    
    temp_ts_files = []
    session_dir = os.path.dirname(output_path)
    total_parts = len(video_list)
    
    try:
        # 1. Konversi setiap MP4 ke TS (Lossless & Cepat)
        for idx, v in enumerate(video_list):
            if progress_callback:
                await progress_callback(idx, total_parts, phase="CONVERTING")
                
            ts_path = os.path.join(session_dir, f"temp_merge_{idx}.ts")
            
            # Deteksi codec untuk bitstream filter yang tepat
            v_info = await get_video_info(v)
            v_codec = v_info.get("codec", "h264")
            bsf = "h264_mp4toannexb"
            if "hevc" in v_codec or "h265" in v_codec:
                bsf = "hevc_mp4toannexb"
            
            # Gunakan bitstream filter untuk h264/hevc agar kompatibel
            # Tambahkan -sn agar abaikan subtitle saat konversi ke TS (mencegah error)
            cmd_ts = [
                "ffmpeg", "-y", "-i", v,
                "-map", "0:v:0", "-map", "0:a:0", # Ambil video & audio pertama saja
                "-c", "copy", "-bsf:v", bsf, 
                "-sn", # Skip subtitle agar tidak error saat ke mpegts
                "-f", "mpegts", ts_path
            ]
            process = await asyncio.create_subprocess_exec(
                *cmd_ts,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            await process.communicate()
            if os.path.exists(ts_path):
                temp_ts_files.append(ts_path)

        if not temp_ts_files: return False

        if progress_callback:
            await progress_callback(total_parts, total_parts, phase="MERGING")

        # 2. Gabungkan file TS menggunakan protokol concat
        concat_str = "concat:" + "|".join(temp_ts_files)
        cmd_merge = [
            "ffmpeg", "-y", "-i", concat_str,
            "-c", "copy", "-bsf:a", "aac_adtstoasc", 
            "-movflags", "+faststart", # Supaya durasi terhitung benar & cepat diload
            output_path
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd_merge,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        await process.communicate()
        
        return process.returncode == 0 and os.path.exists(output_path)
    except Exception as e:
        print(f"Merge Error: {e}")
        return False
    finally:
        # Bersihkan file TS sementara
        for ts in temp_ts_files:
            if os.path.exists(ts): os.remove(ts)

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
        "ffprobe", "-v", "error", "-show_entries", "format=duration:stream=duration,width,height,codec_name,codec_type",
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
        
        # 4. Deteksi Codec
        codec = "h264"
        for s in streams:
            if s.get("codec_type") == "video":
                codec = s.get("codec_name", "h264")
                break
                
        info = {
            "duration": int(duration),
            "width": 0,
            "height": 0,
            "codec": codec
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
