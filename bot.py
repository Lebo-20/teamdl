import os
import json
import asyncio
import html
import sys
import subprocess
import shutil
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
                Button.inline("✅ Ya, Download", data=f"dl_{session_id}"),
                Button.inline("❌ Batal", data=f"cancel_{session_id}")
            ]
        ]
        
        # Kirim Detail Drama (dengan Cover sebagai Foto jika ada)
        cover_path = None
        if drama_info.get('cover'):
            temp_cover = os.path.join(session_dir, "cover.jpg")
            if await downloader.download_file(drama_info['cover'], temp_cover):
                cover_path = temp_cover

        if cover_path:
            await event.respond(text, file=cover_path, buttons=buttons, parse_mode='html')
            if os.path.exists(cover_path): os.remove(cover_path)
        else:
            await event.respond(text, buttons=buttons, parse_mode='html')

    except Exception as e:
        await event.respond(f"❌ Error JSON: {str(e)}")
    finally:
        if os.path.exists(file_path): os.remove(file_path)

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
        
    elif data.startswith("dl_"):
        session_id = data.split("dl_")[1]
        session = user_sessions.get(session_id)
        if not session:
            await event.edit("⚠️ Sesi habis.")
            return
            
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
                ep_filename = f"{safe_title} - EP{ep_num:02d}.mp4"
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
        files.sort()

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
                    subprocess.run(["ffmpeg", "-y", "-i", filepath, "-c", "copy", converted], 
                                 check=True, capture_output=True)
                    upload_path = converted
                except Exception as e:
                    print(f"FFmpeg Error: {e}")

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

@client.on(events.NewMessage(pattern='/update', from_users=getattr(config_file, 'OWNER_ID', 0)))
async def update_bot(event):
    msg = await event.respond("🔄 Updating...")
    try:
        subprocess.run(["git", "pull"], check=True)
        await msg.edit("✅ Updated! Restarting...")
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        await msg.edit(f"❌ Failed: {e}")

@client.on(events.NewMessage(pattern='/restart', from_users=getattr(config_file, 'OWNER_ID', 0)))
async def restart_bot(event):
    await event.respond("🔄 Restarting...")
    os.execv(sys.executable, [sys.executable] + sys.argv)

print("Telethon Bot is running... (Press Ctrl+C to stop)")
client.run_until_disconnected()
