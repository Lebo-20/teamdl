import os
import json
import asyncio
import html
import sys
import subprocess
import shutil
import urllib.parse
import re
from typing import Any

# Telethon Imports
from telethon import TelegramClient, events, Button
import config as config_file
from config import (
    BOT_TOKEN, API_ID, API_HASH, ALLOWED_USERS, TELEGRAM_MAX_SIZE, 
    TIMEOUT_DL, MAX_CONCURRENT_DOWNLOADS, WORKERS, HTTP_PROXY, 
    TEMP_DIR, USE_ARIA2
) # type: ignore

import parsers # type: ignore
import downloader # type: ignore

# Ensure TEMP_DIR exists
os.makedirs(TEMP_DIR, exist_ok=True)

# Client Setup
client = TelegramClient('bot_session', API_ID, API_HASH, proxy=HTTP_PROXY).start(bot_token=BOT_TOKEN)

# Kamus penyimpanan session per user (sementara di memory)
user_sessions: dict[str, dict[str, Any]] = {}

def make_progress_bar(current: int, total: int, width: int = 20) -> str:
    if total <= 0:
        return f"[{'░' * width}] 0%"
    filled = int(width * current / total)
    bar = "█" * filled + "░" * (width - filled)
    pct = int(100 * current / total)
    return f"[{bar}] {pct}%"

@client.on(events.NewMessage(pattern='/start'))
async def start(event):
    user_id = event.sender_id
    if ALLOWED_USERS and user_id not in ALLOWED_USERS:
        await event.respond("Maaf, Anda tidak diizinkan menggunakan bot ini.")
        return
    await event.respond("Halo! Kirimkan file JSON drama untuk mulai mendownload.")

@client.on(events.NewMessage(func=lambda e: e.document and e.document.mime_type == 'application/json'))
async def handle_document(event):
    user_id = event.sender_id
    if ALLOWED_USERS and user_id not in ALLOWED_USERS:
        return
        
    doc = event.document
    filename = event.file.name
    if not filename.endswith('.json'):
        await event.respond("❌ Mohon kirimkan file berformat JSON.")
        return

    # Download JSON
    os.makedirs(TEMP_DIR, exist_ok=True)
    file_path = os.path.join(TEMP_DIR, filename)
    await event.download_media(file=file_path)

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        source_type = parsers.detect_source(data)
        if source_type == "unknown":
            await event.respond("❌ Format JSON tidak dikenali.")
            return
            
        drama_info = parsers.parse_json_data(data, source_type, filename)
        session_id = f"{user_id}_{event.id}"
        session_dir = os.path.join(TEMP_DIR, session_id)
        os.makedirs(session_dir, exist_ok=True)
        
        user_sessions[session_id] = {
            "drama_info": drama_info,
            "source": source_type,
            "session_dir": session_dir,
            "downloaded": [],
            "failed_list": [],
            "format": "MP4"
        }
        
        text = (
            f"🎬 <b>{html.escape(drama_info['title'])}</b>\n"
            f"──────────────────────────\n"
        )
        
        if drama_info.get('sinopsis'):
            text += f"📖 <b>Sinopsis:</b>\n<i>{html.escape(drama_info['sinopsis'][:400])}{'...' if len(drama_info['sinopsis']) > 400 else ''}</i>\n\n"
            
        if drama_info.get('tags'):
            text += f"🏷️ <b>Tags:</b> {html.escape(drama_info['tags'])}\n"
            
        text += (
            f"📺 <b>Total:</b> {drama_info['total_ep']} episode\n"
            f"📦 <b>Platform:</b> {html.escape(source_type.capitalize())}\n"
            f"──────────────────────────\n"
            f"Lanjut download semua episode?"
        )

        buttons = [
            [
                Button.inline("🎬 Softsub (MKV/MP4)", data=f"sub_soft_{session_id}"),
                Button.inline("🎞️ Hardsub (Burn-in)", data=f"sub_hard_{session_id}")
            ],
            [Button.inline("❌ Batal", data=f"cancel_{session_id}")]
        ]
        
        # Kirim Detail Drama (dengan Cover sebagai Foto jika ada)
        cover_path = None
        if drama_info.get('cover'):
            temp_cover = os.path.join(session_dir, "cover.jpg")
            if await downloader.download_file(drama_info['cover'], temp_cover):
                cover_path = temp_cover

        try:
            if cover_path:
                await event.respond(text, file=cover_path, buttons=buttons, parse_mode='html')
                if os.path.exists(cover_path): os.remove(cover_path)
            else:
                await event.respond(text, buttons=buttons, parse_mode='html')
        except Exception as img_err:
            print(f"Image Send Error: {img_err}")
            # Fallback ke teks saja jika gambar gagal diproses/dikirim
            await event.respond(text, buttons=buttons, parse_mode='html')
            if cover_path and os.path.exists(cover_path): os.remove(cover_path)

    except Exception as e:
        await event.respond(f"❌ Error: {str(e)}")
    finally:
        if os.path.exists(file_path): os.remove(file_path)

@client.on(events.NewMessage(pattern=r'^/l(\s+|$)'))
async def handle_link_command(event):
    user_id = event.sender_id
    if ALLOWED_USERS and user_id not in ALLOWED_USERS:
        return
        
    text = event.message.text
    # Extract links from the message
    links = re.findall(r'https?://[^\s\n]+', text)
    
    if not links:
        await event.respond("❌ Mohon sertakan link video (m3u8/mp4).")
        return

    # Limit to 100 links
    if len(links) > 100:
        await event.respond(f"⚠️ Terlalu banyak link. Maksimal 100 link per perintah. Hanya 100 link pertama yang akan diproses.")
        links = links[:100]

    total = len(links)
    summary_msg = await event.respond(f"🚀 Memulai download {total} link...")
    
    success_count = 0
    fail_count = 0
    
    # Process each link (supporting multiple links per command)
    for idx, url in enumerate(links):
        current = idx + 1
        try:
            await summary_msg.edit(
                f"📥 <b>PROSES LINK {current}/{total}</b>\n"
                f"🔗 <code>{html.escape(url)}</code>\n\n"
                f"✅ Berhasil: {success_count} | ❌ Gagal: {fail_count}\n"
                f"{make_progress_bar(current, total)}",
                parse_mode='html'
            )
        except Exception: pass
        
        # Unique session dir for each link
        batch_session_id = f"{user_id}_{event.id}_{idx}"
        session_dir = os.path.join(TEMP_DIR, batch_session_id)
        os.makedirs(session_dir, exist_ok=True)
        
        # Simple filename extraction
        parsed_url = urllib.parse.urlparse(url)
        path = parsed_url.path
        filename_orig = os.path.basename(path)
        
        if not filename_orig or '.' not in filename_orig or len(filename_orig) < 4:
            filename = f"video_{current}.mp4"
        else:
            # Tetap gunakan nama asli tapi pastikan extension mp4 dan amankan dari tabrakan
            name_part = filename_orig.rsplit('.', 1)[0]
            filename = f"{name_part}_{current}.mp4"
            
        output_path = os.path.join(session_dir, filename)
        
        try:
            # Download logic
            success = await downloader.download_video_ytdlp(url, output_path)
            if not success:
                success = await downloader.download_video_ffmpeg(url, output_path)
                
            if success and os.path.exists(output_path):
                # Upload
                await client.send_file(
                    event.chat_id,
                    output_path,
                    caption=f"📺 Video ({current}/{total}):\n<code>{html.escape(url)}</code>",
                    force_document=True,
                    supports_streaming=True,
                    reply_to=event.id,
                    parse_mode='html'
                )
                success_count += 1
            else:
                fail_count += 1
        except Exception as e:
            print(f"Error download link {url}: {e}")
            fail_count += 1
        finally:
            shutil.rmtree(session_dir, ignore_errors=True)
            
    # Final report
    final_text = (
        f"✅ <b>DOWNLOAD SELESAI</b>\n"
        f"──────────────────────────\n"
        f"📋 <b>Total Link</b>  : {total}\n"
        f"✅ <b>Berhasil</b>    : {success_count}\n"
        f"❌ <b>Gagal</b>       : {fail_count}\n"
        f"──────────────────────────"
    )
    await summary_msg.edit(final_text, parse_mode='html')

@client.on(events.CallbackQuery)
async def handle_callback(event):
    user_id = event.sender_id
    data = event.data.decode('utf-8')
    
    if data.startswith("cancel_"):
        session_id = data.split("cancel_")[1]
        session = user_sessions.pop(session_id, None)
        if session:
            shutil.rmtree(session['session_dir'], ignore_errors=True)
        await event.edit("❌ Proses dibatalkan.")
        
    elif data.startswith("sub_"):
        parts = data.split("_", 2)
        sub_type = parts[1] # 'soft' or 'hard'
        session_id = parts[2]
        session = user_sessions.get(session_id)
        if not session:
            await event.edit("⚠️ Sesi habis.")
            return
            
        session['sub_type'] = sub_type
        # After choosing sub type, automatically start download
        # Redirect to 'dl_' logic or call it directly. 
        # For simplicity, we trigger the download here
        await handle_callback_download(event, session_id)

    elif data.startswith("dl_"):
        session_id = data.split("dl_")[1]
        await handle_callback_download(event, session_id)

    elif data.startswith("up_"):
        parts = data.split("_", 2)
        target_format = parts[1].upper()
        session_id = parts[2]
        session = user_sessions.get(session_id)
        if not session: return

        session['format'] = target_format
        files = session['downloaded']
        if not files:
            await event.edit("⚠️ Tidak ada file untuk diupload.")
            return
        # Natural Sort agar urutan upload pas (Ep1, Ep2, dst... bukan Ep1, Ep10, Ep2)
        import re
        def natural_sort_key(s):
            return [int(text) if text.isdigit() else text.lower() for text in re.split('([0-9]+)', s)]
        
        files.sort(key=natural_sort_key)

        uploaded = 0
        failed_up = 0
        title = session['drama_info']['title']

        for idx, filepath in enumerate(files):
            current = idx + 1
            text = (
                f"⬆️ <b>PROSES UPLOAD</b>\n"
                f"📦 <b>Drama:</b> {html.escape(title)}\n"
                f"📺 <b>Upload:</b> {current}/{len(files)}\n"
                f"{make_progress_bar(current, len(files))}\n"
                f"✅ Berhasil: {uploaded} | ❌ Gagal: {failed_up}"
            )
            try:
                await event.edit(text, parse_mode='html')
            except Exception: pass

            upload_path = filepath
            if not filepath.endswith(f".{target_format}"):
                converted = filepath.rsplit('.', 1)[0] + f".{target_format}"
                if os.path.exists(converted): os.remove(converted)
                try:
                    # Pastikan menyalin semua stream (video, audio, subtitle) dengan -map 0
                    # Gunakan codec subtitle yang tepat sesuai format target
                    sub_codec = "srt" if target_format == "MKV" else "mov_text"
                    cmd = ["ffmpeg", "-y", "-i", filepath, "-map", "0", "-c", "copy", "-c:s", sub_codec, converted]
                    subprocess.run(cmd, check=True, capture_output=True)
                    upload_path = converted
                except Exception as e:
                    print(f"FFmpeg Convert Error: {e}")
                    # Jika gagal (mungkin stream tidak kompatibel), coba copy biasa tanpa -map 0
                    try:
                        subprocess.run(["ffmpeg", "-y", "-i", filepath, "-c", "copy", converted], 
                                     check=True, capture_output=True)
                        upload_path = converted
                    except: pass

            try:
                if not os.path.exists(upload_path):
                    raise FileNotFoundError(f"File not found: {upload_path}")

                # Telethon upload 
                await client.send_file(
                    event.chat_id,
                    upload_path,
                    caption=f"📺 {html.escape(title)} - Episode {current}",
                    parse_mode='html',
                    force_document=True,
                    supports_streaming=True
                )
                uploaded += 1
            except Exception as e:
                print(f"Upload Failure: {str(e)}")
                failed_up += 1
            finally:
                if os.path.exists(upload_path): os.remove(upload_path)
                if upload_path != filepath and os.path.exists(filepath): os.remove(filepath)
            
            await asyncio.sleep(2)

        # FINAL REPORT CANTIK
        drama_info = session['drama_info']
        sinopsis = drama_info.get('sinopsis', 'Tidak ada sinopsis.')
        report = (
            f"✅ <b>PROSES SELESAI!</b>\n"
            f"──────────────────────────\n"
            f"📦 <b>Drama:</b> {html.escape(title)}\n"
            f"🎬 <b>Format:</b> {session['format']}\n"
            f"📖 <b>Sinopsis:</b>\n<i>{html.escape(sinopsis)}</i>\n"
            f"──────────────────────────\n"
            f"⬆️ <b>Total File</b>    : {len(files)}\n"
            f"✅ <b>Berhasil</b>      : {uploaded}\n"
            f"❌ <b>Gagal Upload</b>  : {failed_up}\n"
            f"──────────────────────────\n"
            f"🥳 Semua proses telah selesai! 🎉"
        )
        await event.respond(report, parse_mode='html')
        user_sessions.pop(session_id, None)
        shutil.rmtree(session['session_dir'], ignore_errors=True)

async def handle_callback_download(event, session_id):
    session = user_sessions.get(session_id)
    if not session: return
            
    drama_info = session['drama_info']
    total = drama_info['total_ep']
    title = drama_info['title']
    counts = {"success": 0, "failed": 0}
    
    async def download_task(idx, ep, semaphore):
        current_num = idx + 1
        ep_num = ep.get('num', current_num)
        url = ep.get('url')
        
        async with semaphore:
            if not url:
                counts["failed"] += 1
                session['failed_list'].append(f"EP{ep_num}(NoURL)")
                return

            safe_title = "".join([c for c in title if c.isalnum() or c==' ']).strip()
            ep_filename = f"{safe_title} Ep{ep_num}.mp4"
            output_path = os.path.join(session['session_dir'], ep_filename)
            
            success = False
            source = session['source']
            
            try:
                # MENGUTAMAKAN YT-DLP UNTUK KESTABILAN
                if source == "vigloo":
                    cookies = ep.get('cookies', {})
                    headers = {"Cookie": "; ".join([f"{k}={v}" for k, v in cookies.items()])} if cookies else {}
                    success = await downloader.download_video_ytdlp(url, output_path, headers)
                else:
                    success = await downloader.download_video_ytdlp(url, output_path)
                    
                    # Aria2 hanya untuk file direct (bukan m3u8)
                    if not success and USE_ARIA2 and ".m3u8" not in url: 
                        success = await downloader.download_aria2(url, output_path)
                        
                    # ffmpeg sebagai last resort untuk m3u8
                    if not success: 
                        success = await downloader.download_video_ffmpeg(url, output_path)
                
                # Subtitle
                sub_url = ep.get('subtitle')
                if success and sub_url:
                    sub_path = os.path.join(session['session_dir'], f"temp_sub_{ep_num}.srt")
                    if await downloader.download_file(sub_url, sub_path):
                        if session.get('sub_type') == 'hard':
                            # Hardsub (Burn-in)
                            new_out = await downloader.burn_subtitle(output_path, sub_path)
                        else:
                            # Softsub (Muxing)
                            new_out = await downloader.mux_subtitle(output_path, sub_path, "mp4")
                            
                        if new_out:
                            if os.path.exists(output_path): os.remove(output_path)
                            if os.path.exists(sub_path): os.remove(sub_path)
                            output_path = new_out
            except Exception as e:
                print(f"Error download EP{ep_num}: {e}")
                success = False

            if success:
                counts["success"] += 1
                session['downloaded'].append(output_path)
            else:
                counts["failed"] += 1
                session['failed_list'].append(f"EP{ep_num}")

    async def update_status_loop():
        while (counts["success"] + counts["failed"]) < total:
            done = counts["success"] + counts["failed"]
            text = (
                f"⬇️ <b>PROSES DOWNLOAD</b>\n"
                f"📦 <b>Drama:</b> {html.escape(title)}\n"
                f"📺 <b>Progress:</b> {done}/{total}\n"
                f"{make_progress_bar(done, total)}\n"
                f"✅ Selesai: {counts['success']} | ❌ Gagal: {counts['failed']}"
            )
            try:
                await event.edit(text, parse_mode='html')
            except Exception: pass
            await asyncio.sleep(4)

    sem = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
    tasks = [download_task(idx, ep, sem) for idx, ep in enumerate(drama_info['episodes'])]
    status_task = asyncio.create_task(update_status_loop())
    await asyncio.gather(*tasks)
    status_task.cancel()

    failed_text = f"\n⚠️ Gagal: {', '.join(session['failed_list'][:10])}" if session['failed_list'] else ""
    final_text = (
        f"⬇️ <b>DOWNLOAD SELESAI</b>\n"
        f"📦 <b>Drama:</b> {html.escape(title)}\n"
        f"✅ Berhasil: {counts['success']} | ❌ Gagal: {counts['failed']}{html.escape(failed_text)}\n"
        f"\nPilih format upload:"
    )

    buttons = [
        [
            Button.inline("📦 Upload MKV", data=f"up_mkv_{session_id}"),
            Button.inline("🎬 Upload MP4", data=f"up_mp4_{session_id}")
        ],
        [Button.inline("❌ Batal Upload", data=f"cancel_{session_id}")]
    ]
    await event.edit(final_text, buttons=buttons, parse_mode='html')



@client.on(events.NewMessage(pattern='/id'))
async def get_id(event):
    await event.respond(f"ID Anda: `{event.sender_id}`\n\nMasukkan ID di atas ke dalam `OWNER_ID` di file `config.py` agar bisa menggunakan perintah update.")

@client.on(events.NewMessage(pattern='/update'))
async def update_bot(event):
    owner_id = getattr(config_file, 'OWNER_ID', 0)
    if event.sender_id != owner_id:
        return # Abaikan jika bukan owner
        
    msg = await event.respond("🔄 Menarik update terbaru dari GitHub...")
    try:
        # Gunakan reset --hard agar tidak error saat ada konflik file lokal
        subprocess.run(["git", "fetch", "--all"], check=True)
        subprocess.run(["git", "reset", "--hard", "origin/main"], check=True)
        
        await msg.edit("✅ Update berhasil! Memulai ulang bot...")
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        await msg.edit(f"❌ Update Gagal: {str(e)}")

@client.on(events.NewMessage(pattern='/restart'))
async def restart_bot(event):
    owner_id = getattr(config_file, 'OWNER_ID', 0)
    if event.sender_id != owner_id:
        return
    await event.respond("🔄 Restarting...")
    os.execv(sys.executable, [sys.executable] + sys.argv)

print("Telethon Bot is running... (Press Ctrl+C to stop)")
client.run_until_disconnected()
