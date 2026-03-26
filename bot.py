import os
import json
import asyncio
import html
import sys
import subprocess
import shutil
import urllib.parse
import re
import time
from datetime import timedelta
from typing import Any

# Telethon Imports
from telethon import TelegramClient, events, Button
from telethon.tl.types import DocumentAttributeVideo
import config as config_file

# Safely import optional settings
def get_config(key, default=None):
    return getattr(config_file, key, default)

# Mandatory imports
from config import BOT_TOKEN, API_ID, API_HASH, ALLOWED_USERS, TELEGRAM_MAX_SIZE

# Optional/Defaulted imports
TIMEOUT_DL = get_config('TIMEOUT_DL', 3600)
MAX_CONCURRENT_DOWNLOADS = get_config('MAX_CONCURRENT_DOWNLOADS', 5)
WORKERS = get_config('WORKERS', 8)
HTTP_PROXY = get_config('HTTP_PROXY', None)
TEMP_DIR = get_config('TEMP_DIR', './downloads/')
USE_ARIA2 = get_config('USE_ARIA2', True)
BACKUP_CHANNEL_ID = get_config('BACKUP_CHANNEL_ID', None)

import parsers # type: ignore
import downloader # type: ignore

# Ensure TEMP_DIR exists
os.makedirs(TEMP_DIR, exist_ok=True)

# Client Setup
client = TelegramClient('bot_session', API_ID, API_HASH, proxy=HTTP_PROXY).start(bot_token=BOT_TOKEN)

user_sessions: dict[str, dict[str, Any]] = {}

# Panel Monitoring Global (untuk live update)
panel_messages: dict[int, Any] = {} # {chat_id: message_obj}

def make_progress_bar(current: int, total: int, width: int = 20) -> str:
    if total <= 0:
        return f"[{'░' * width}] 0%"
    filled = int(width * current / total)
    bar = "█" * filled + "░" * (width - filled)
    pct = int(100 * current / total)
    return f"[{bar}] {pct}%"

async def send_and_backup(chat_id, *args, **kwargs):
    """Kirim file ke user DAN otomatis copy ke channel backup."""
    msg = await client.send_file(chat_id, *args, **kwargs)
    
    # Ambil ID Backup (pastikan Integer)
    backup_id = get_config('BACKUP_CHANNEL_ID')
    if backup_id:
        try:
            backup_id = int(backup_id)
        except (ValueError, TypeError):
            backup_id = None

    if msg and backup_id and int(chat_id) != backup_id:
        try:
            # Gunakan file=msg.media untuk Fast Copy (tanpa upload ulang)
            # send_message dengan file= seringkali lebih stabil untuk copy media antar peer
            await client.send_message(
                backup_id, 
                file=msg.media, 
                caption=msg.message,
                parse_mode='html'
            )
        except Exception as e:
            print(f"❌ [BACKUP ERROR] Gagal mengirim ke channel {backup_id}: {e}")
            print(f"Tips: Pastikan bot sudah jadi Admin di channel backup dan punya izin kirim pesan.")
    return msg

async def panel_update_loop():
    """Background task untuk memperbarui status di panel monitoring secara otomatis (Live Update)."""
    while True:
        if not panel_messages:
            await asyncio.sleep(5)
            continue
            
        active_count = 0
        text = "📊 <b>LIVE MONITORING PANEL</b>\n──────────────────────────\n"
        
        # Hitung data terbaru dari semua user
        for sid, sess in list(user_sessions.items()):
            ls = sess.get("live_status")
            if not ls: continue
            
            active_count += 1
            drama = sess.get("drama_info", {}).get("title", "Unknown")
            user_id = sid.split("_")[0]
            op_type = ls.get('type', 'PROSES')
            
            text += (
                f"👤 <b>UID:</b> <code>{user_id}</code> | ⚙️ <b>{op_type}</b>\n"
                f"📦 <b>Drama:</b> <i>{html.escape(drama[:40])}</i>\n"
                f"📺 <b>Prog:</b> {ls['done']}/{ls['total']} ({ls['pct']}%)\n"
                f"⏳ <b>ETA:</b> {ls['eta']} | ⏱️ <b>Dur:</b> {ls['elapsed']}\n"
                f"──────────────────────────\n"
            )
            
        if active_count == 0:
            text += "📭 <b>Tidak ada proses aktif saat ini.</b>"
        else:
            text += f"🚀 <b>Total Proses Aktif:</b> {active_count}\n"
            text += f"🔄 <i>Auto-update tiap 5 detik...</i>"

        # Update semua panel yang terdaftar (biasanya hanya 1 milik Owner)
        for chat_id, msg in list(panel_messages.items()):
            try:
                await msg.edit(text, parse_mode='html')
            except Exception:
                # Jika pesan sudah dihapus user atau error lainnya
                panel_messages.pop(chat_id, None)
                
        await asyncio.sleep(5)

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

@client.on(events.NewMessage(func=lambda e: e.document and e.document.mime_type != 'application/json'))
async def handle_any_file_hint(event):
    user_id = event.sender_id
    if ALLOWED_USERS and user_id not in ALLOWED_USERS:
        return
        
    session_id = f"merge_{user_id}"
    if session_id not in user_sessions:
        user_sessions[session_id] = {"files": [], "session_dir": os.path.join(TEMP_DIR, session_id)}
        os.makedirs(user_sessions[session_id]["session_dir"], exist_ok=True)
    
    # Simpan info file (untuk didownload nanti saat merge diklik)
    # Kita tidak mendownload sekarang agar hemat storage jika user hanya ingin rename
    user_sessions[session_id]["files"].append(event.message.id)
    
    count = len(user_sessions[session_id]["files"])
    text = (
        "👆 <b>Balas (reply)</b> ke pesan file ini dengan nama baru untuk mengganti nama.\n\n"
        f"📦 Terdeteksi {count} file dalam antrean merge."
    )
    
    buttons = None
    if count >= 2:
        buttons = [
            [Button.inline(f"🎬 Gabungkan {count} Video", data=f"do_merge_{user_id}")],
            [Button.inline("❌ Bersihkan Antrean", data=f"clear_merge_{user_id}")]
        ]
        
    await event.respond(text, buttons=buttons, parse_mode='html')

@client.on(events.NewMessage(func=lambda e: not e.text.startswith('/') and e.is_reply))
async def handle_rename_reply(event):
    user_id = event.sender_id
    if ALLOWED_USERS and user_id not in ALLOWED_USERS:
        return
        
    reply_msg = await event.get_reply_message()
    if not reply_msg or not reply_msg.document or reply_msg.document.mime_type == 'application/json':
        return # Hanya proses reply ke file (bukan JSON)

    new_name = event.text.strip()
    # Amankan nama file (izinkan spasi, titik, dash, underscore)
    new_name = "".join([c for c in new_name if c.isalnum() or c in " ._- ()"]).strip()
    if not new_name:
        return

    orig_filename = reply_msg.file.name or "file"
    # Jika tidak ada ekstensi, gunakan ekstensi asli
    if '.' not in new_name:
        ext = orig_filename.rsplit('.', 1)[-1] if '.' in orig_filename else "mp4"
        new_name += f".{ext}"
        
    session_id = f"rename_{user_id}_{event.id}"
    session_dir = os.path.join(TEMP_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)
    
    msg = await event.respond(f"⏳ <b>Memproses Rename:</b> <code>{html.escape(new_name)}</code>...", parse_mode='html')
    
    file_path = os.path.join(session_dir, orig_filename)
    
    try:
        # Register session for monitoring
        user_sessions[session_id] = {
            "drama_info": {"title": f"Rename: {new_name}"},
            "live_status": {"done": 0, "total": 1, "pct": 0, "eta": "...", "elapsed": "0:00:00", "type": "RENAME"}
        }
        start_time_rename = time.time()

        # Download Progress
        async def dl_progress(current, total):
            elapsed = int(time.time() - start_time_rename)
            user_sessions[session_id]["live_status"].update({
                "done": current, "total": total, 
                "pct": int((current/total)*100) if total > 0 else 0,
                "elapsed": str(timedelta(seconds=elapsed))
            })
            
            pct = (current / total) * 100
            if int(pct) % 15 == 0 or current == total:
                try:
                    await msg.edit(f"📥 <b>Downloading...</b>\n{make_progress_bar(current, total)}\n"
                                  f"📦 <code>{html.escape(orig_filename)}</code>", parse_mode='html')
                except Exception: pass
        
        await reply_msg.download_media(file=file_path, progress_callback=dl_progress)
        
        new_path = os.path.join(session_dir, new_name)
        os.rename(file_path, new_path)
        
        # Ekstrak thumbnail & info video
        thumb_path = os.path.join(session_dir, "thumb.jpg")
        has_thumb = await downloader.extract_thumbnail(new_path, thumb_path)
        v_info = await downloader.get_video_info(new_path)
        
        # Progress Bar untuk Upload
        async def up_progress(current, total):
            pct = (current / total) * 100
            if int(pct) % 15 == 0 or current == total:
                try:
                    await msg.edit(f"📤 <b>Uploading...</b>\n{make_progress_bar(current, total)}\n"
                                  f"📁 <code>{html.escape(new_name)}</code>", parse_mode='html')
                except Exception: pass

        await send_and_backup(
            event.chat_id,
            new_path,
            caption=f"📁 <b>{html.escape(new_name)}</b>",
            thumb=thumb_path if has_thumb else None,
            supports_streaming=True,
            force_document=False,
            parse_mode='html',
            reply_to=reply_msg.id,
            attributes=[DocumentAttributeVideo(
                duration=v_info["duration"],
                w=v_info["width"],
                h=v_info["height"],
                supports_streaming=True
            )],
            progress_callback=up_progress
        )
        
        await msg.delete()
    except Exception as e:
        await msg.edit(f"❌ Gagal: {str(e)}")
    finally:
        user_sessions.pop(session_id, None)
        shutil.rmtree(session_dir, ignore_errors=True)

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
        
        batch_session_id = f"batch_{user_id}_{event.id}"
        if batch_session_id not in user_sessions:
            user_sessions[batch_session_id] = {
                "drama_info": {"title": f"Batch Links ({total} items)"},
                "live_status": {"done": 0, "total": total, "pct": 0, "eta": "...", "elapsed": "0:00:00", "type": "BATCH"}
            }
        start_time_batch = time.time()

        # Unique session dir for each link
        item_session_id = f"{user_id}_{event.id}_{idx}"
        session_dir = os.path.join(TEMP_DIR, item_session_id)
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
        
        error_msg = ""
        try:
            # Download logic
            success = await downloader.download_video_ytdlp(url, output_path)
            if not success:
                success = await downloader.download_video_ffmpeg(url, output_path)
                if not success: error_msg = "Download via YTDLP & FFmpeg failed."
                
            if success and os.path.exists(output_path):
                # Upload
                await send_and_backup(
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
            error_msg = str(e)
            print(f"Error download link {url}: {e}")
            fail_count += 1
        finally:
            if error_msg:
                # Send error details to user for debugging
                try: await event.respond(f"❌ <b>Gagal Download ({current}):</b>\n<code>{html.escape(url[:100])}...</code>\n\n📌 <b>Pesan Error:</b>\n<code>{html.escape(error_msg)}</code>", parse_mode='html')
                except: pass
                
            # Update status for panel
            elapsed = int(time.time() - start_time_batch)
            user_sessions[batch_session_id]["live_status"].update({
                "done": idx + 1,
                "pct": int(((idx + 1) / total) * 100),
                "elapsed": str(timedelta(seconds=elapsed))
            })
            shutil.rmtree(session_dir, ignore_errors=True)
            
    # Final report
    user_sessions.pop(batch_session_id, None)
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
    
    if data.startswith("do_merge_"):
        user_id = data.split("do_merge_")[1]
        session_id = f"merge_{user_id}"
        session = user_sessions.get(session_id)
        if not session or not session["files"]:
            await event.answer("⚠️ Antrean kosong.", alert=True)
            return

        msg = await event.edit("⏳ <b>Memulai proses penggabungan...</b>\nMendownload semua file dalam antrean.", parse_mode='html')
        
        file_paths = []
        try:
            for idx, msg_id in enumerate(session["files"]):
                # Ambil pesan asli
                target_msg = await client.get_messages(event.chat_id, ids=msg_id)
                if not target_msg or not target_msg.document: continue
                
                filename = target_msg.file.name or f"part_{idx}.mp4"
                path = os.path.join(session["session_dir"], f"{idx}_{filename}")
                
                await msg.edit(f"📥 <b>Downloading part {idx+1}/{len(session['files'])}...</b>", parse_mode='html')
                await target_msg.download_media(file=path)
                file_paths.append(path)

            if len(file_paths) < 2:
                await msg.edit("❌ Minimal butuh 2 file valid untuk digabungkan.")
                return

            output_name = "Merged_Video.mp4"
            output_path = os.path.join(session["session_dir"], output_name)
            
            # progress callback untuk merge
            async def merge_progress(curr, total, phase):
                pct = int((curr / total) * 100) if total > 0 else 0
                if phase == "MERGING": pct = 99 # Hampir selesai saat fase gabung
                
                await msg.edit(f"⚙️ <b>Proses Penggabungan:</b>\n{make_progress_bar(curr, total)}\n"
                              f"📂 Fase: <code>{phase}</code>", parse_mode='html')
                
                # Update panel monitoring
                session["live_status"].update({
                    "done": curr, "total": total, "pct": pct, "type": f"MERGE_{phase}"
                })

            success = await downloader.merge_videos(file_paths, output_path, progress_callback=merge_progress)
            
            if success:
                # Ekstrak info
                thumb_path = os.path.join(session["session_dir"], "thumb.jpg")
                has_thumb = await downloader.extract_thumbnail(output_path, thumb_path)
                v_info = await downloader.get_video_info(output_path)
                
                # Upload Progress
                async def up_progress_merge(curr, total):
                    pct = int((curr / total) * 100)
                    try:
                        await msg.edit(f"📤 <b>Uploading Merged Video...</b>\n{make_progress_bar(curr, total)}", parse_mode='html')
                    except: pass
                    session["live_status"].update({
                        "done": curr, "total": total, "pct": pct, "type": "UPLOAD_MERGE"
                    })

                final_msg = await send_and_backup(
                    event.chat_id,
                    output_path,
                    caption=f"✅ <b>Berhasil Menggabungkan {len(file_paths)} File!</b>\n\n📁 Nama: <code>{output_name}</code>\n\n👆 <b>Balas (reply)</b> ke pesan ini dengan nama baru jika ingin mengubah namanya.",
                    thumb=thumb_path if has_thumb else None,
                    attributes=[DocumentAttributeVideo(
                        duration=v_info["duration"],
                        w=v_info["width"],
                        h=v_info["height"],
                        supports_streaming=True
                    )],
                    supports_streaming=True,
                    parse_mode='html',
                    progress_callback=up_progress_merge
                )
                # Sesi merge selesai
                await msg.delete()
                user_sessions.pop(session_id, None)
                shutil.rmtree(session["session_dir"], ignore_errors=True)
            else:
                await msg.edit("❌ Gagal menggabungkan video.")
        except Exception as e:
            await msg.edit(f"❌ Error saat merge: {e}")
            
    elif data.startswith("clear_merge_"):
        user_id = data.split("clear_merge_")[1]
        session_id = f"merge_{user_id}"
        session = user_sessions.pop(session_id, None)
        if session:
            shutil.rmtree(session["session_dir"], ignore_errors=True)
        await event.edit("✅ Antrean merge telah dibersihkan.")

    elif data.startswith("cancel_"):
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

    elif data.startswith("merge_"):
        session_id = data.split("merge_")[1]
        session = user_sessions.get(session_id)
        if not session:
            await event.edit("⚠️ Sesi habis.")
            return
            
        files = session['downloaded']
        if not files:
            await event.edit("⚠️ Tidak ada file untuk digabungkan.")
            return
            
        await event.edit("⏳ <b>Sedang menggabungkan semua episode...</b>\nMohon tunggu, ini mungkin memakan waktu.", parse_mode='html')
        
        # Natural Sort
        import re
        def natural_sort_key(s):
            return [int(text) if text.isdigit() else text.lower() for text in re.split('([0-9]+)', s)]
        files.sort(key=natural_sort_key)
        
        title = session['drama_info']['title']
        output_name = f"{title}_Full_Movie.mp4"
        output_path = os.path.join(session['session_dir'], output_name)
        
        async def merge_progress_full(curr, total, phase):
            pct = int((curr / total) * 100) if total > 0 else 0
            if phase == "MERGING": pct = 99
            
            try:
                await event.edit(f"⚙️ <b>Menggabungkan Episodes ({len(files)} eps):</b>\n{make_progress_bar(curr, total)}\n"
                                f"📂 Fase: <code>{phase}</code>", parse_mode='html')
            except: pass
            
            session["live_status"].update({
                "done": curr, "total": total, "pct": pct, "type": f"FULL_MERGE_{phase}"
            })

        success = await downloader.merge_videos(files, output_path, progress_callback=merge_progress_full)
        
        if success:
            # Upload Progress
            async def up_progress_full(curr, total):
                pct = int((curr / total) * 100)
                try:
                    await event.edit(f"⬆️ <b>Uploading Full Movie...</b>\n{make_progress_bar(curr, total)}", parse_mode='html')
                except: pass
                session["live_status"].update({
                    "done": curr, "total": total, "pct": pct, "type": "UPLOAD_FULL"
                })

            try:
                await send_and_backup(
                    event.chat_id,
                    output_path,
                    caption=f"🎬 <b>{html.escape(title)}</b>\nFull Episodes Merged ✅",
                    parse_mode='html',
                    force_document=True,
                    supports_streaming=True,
                    progress_callback=up_progress_full
                )
                # Cleanup setelah berhasil kirim full movie
                user_sessions.pop(session_id, None)
                shutil.rmtree(session['session_dir'], ignore_errors=True)
                await event.respond("✅ Proses penggabungan dan pengiriman selesai!")
            except Exception as e:
                await event.respond(f"❌ Gagal mengirim file gabungan: {e}")
        else:
            await event.edit("❌ Gagal menggabungkan video. Pastikan semua episode terdownload dengan benar.")

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
        up_start_time = time.time()

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

            # Update Live Status for Panel
            up_elapsed = int(time.time() - up_start_time)
            up_pct = int((current / len(files)) * 100)
            up_eta_sec = int((up_elapsed / current) * (len(files) - current)) if current > 0 else 0
            
            session["live_status"] = {
                "done": current,
                "total": len(files),
                "pct": up_pct,
                "eta": str(timedelta(seconds=up_eta_sec)),
                "elapsed": str(timedelta(seconds=up_elapsed)),
                "type": "UPLOAD"
            }

            try:
                if not os.path.exists(upload_path):
                    raise FileNotFoundError(f"File not found: {upload_path}")

                # Telethon upload 
                await send_and_backup(
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

        # Hapus status monitoring setelah upload selesai
        session.pop("live_status", None)

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
                sub_fmt = ep.get('sub_format', '').lower() if ep.get('sub_format') else ''
                
                if success and sub_url:
                    # Deteksi format subtitle dari URL atau Parser (vtt atau srt)
                    is_vtt = "vtt" in sub_fmt or ".vtt" in sub_url.lower() or "mime_type=text_plain" in sub_url
                    is_hls_sub = ".m3u8" in sub_url.lower()
                    
                    raw_sub_ext = ".vtt" if is_vtt else ".srt"
                    if is_hls_sub: raw_sub_ext = ".srt" # Langsung ke srt via ffmpeg
                    
                    raw_sub_path = os.path.join(session['session_dir'], f"temp_sub_raw_{ep_num}{raw_sub_ext}")
                    sub_path = os.path.join(session['session_dir'], f"temp_sub_{ep_num}.srt")
                    
                    sub_downloaded = False
                    if is_hls_sub:
                        # Gunakan ffmpeg untuk download HLS subtitle langsung ke srt
                        import asyncio as _asyncio
                        import subprocess as _subprocess
                        conv_proc = await _asyncio.create_subprocess_exec(
                            "ffmpeg", "-y", "-i", sub_url, sub_path,
                            stdout=_subprocess.DEVNULL,
                            stderr=_subprocess.DEVNULL
                        )
                        await conv_proc.communicate()
                        if os.path.exists(sub_path):
                            sub_downloaded = True
                            is_vtt = False # Sudah srt via ffmpeg
                    else:
                        if await downloader.download_file(sub_url, raw_sub_path):
                            sub_downloaded = True
                    
                    if sub_downloaded:
                        # Konversi VTT ke SRT jika perlu (FFmpeg bisa handle ini)
                        if is_vtt:
                            import asyncio as _asyncio
                            import subprocess as _subprocess
                            conv_proc = await _asyncio.create_subprocess_exec(
                                "ffmpeg", "-y", "-i", raw_sub_path, sub_path,
                                stdout=_subprocess.DEVNULL,
                                stderr=_subprocess.DEVNULL
                            )
                            await conv_proc.communicate()
                            if os.path.exists(raw_sub_path): os.remove(raw_sub_path)
                            if not os.path.exists(sub_path):
                                print(f"VTT->SRT conversion failed for EP{ep_num}")
                                sub_path = None
                        else:
                            # Format sudah SRT, langsung pakai
                            os.rename(raw_sub_path, sub_path)
                        
                        if sub_path and os.path.exists(sub_path):
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

    start_time = time.time()
    async def update_status_loop():
        while (counts["success"] + counts["failed"]) < total:
            done = counts["success"] + counts["failed"]
            elapsed = int(time.time() - start_time)
            elapsed_str = str(timedelta(seconds=elapsed))
            
            eta_str = "--:--:--"
            pct = 0
            if done > 0:
                avg_time = elapsed / done
                eta_sec = int(avg_time * (total - done))
                eta_str = str(timedelta(seconds=eta_sec))
                pct = int((done / total) * 100)

            # Update Session for /panel
            session["live_status"] = {
                "done": done,
                "total": total,
                "pct": pct,
                "eta": eta_str,
                "elapsed": elapsed_str,
                "type": "DOWNLOAD"
            }

            text = (
                f"⬇️ <b>PROSES DOWNLOAD</b>\n"
                f"📦 <b>Drama:</b> {html.escape(title)}\n"
                f"📺 <b>Progress:</b> {done}/{total} | ⏳ <b>ETA:</b> {eta_str}\n"
                f"{make_progress_bar(done, total)}\n"
                f"✅ Selesai: {counts['success']} | ❌ Gagal: {counts['failed']}\n"
                f"⏱️ <b>Sudah Berjalan:</b> {elapsed_str}"
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
    
    # Hapus status dari panel segera setelah download selesai
    session.pop("live_status", None)
    
    total_time = int(time.time() - start_time)
    duration_str = str(timedelta(seconds=total_time))

    failed_text = f"\n⚠️ Gagal: {', '.join(session['failed_list'][:10])}" if session['failed_list'] else ""
    final_text = (
        f"⬇️ <b>DOWNLOAD SELESAI</b>\n"
        f"📦 <b>Drama:</b> {html.escape(title)}\n"
        f"✅ Berhasil: {counts['success']} | ❌ Gagal: {counts['failed']}{html.escape(failed_text)}\n"
        f"⏱️ <b>Total Waktu:</b> {duration_str}\n"
        f"\nPilih format upload:"
    )

    buttons = [
        [
            Button.inline("📦 Upload MKV", data=f"up_mkv_{session_id}"),
            Button.inline("🎬 Upload MP4", data=f"up_mp4_{session_id}")
        ],
        [Button.inline("🎞️ Gabungkan Semua (Beta)", data=f"merge_{session_id}")],
        [Button.inline("❌ Batal Upload", data=f"cancel_{session_id}")]
    ]
    await event.edit(final_text, buttons=buttons, parse_mode='html')



@client.on(events.NewMessage(pattern=r'^/(menu|help)(\s+|$)'))
async def help_menu(event):
    user_id = event.sender_id
    is_owner = (user_id == getattr(config_file, 'OWNER_ID', 0))
    
    owner_text = ""
    if is_owner:
        owner_text = (
            "\n👑 <b>OWNER COMMANDS:</b>\n"
            "├ `/panel` - Monitor proses aktif\n"
            "├ `/update` - Update bot dari GitHub\n"
            "├ `/restart` - Restart bot langsung\n"
            "└ `/id` - Cek ID Telegram Anda\n"
        )
        
    text = (
        "🤖 <b>TEAMDL DOWNLOADER BOT MENU</b>\n"
        "──────────────────────────\n"
        "📖 <b>PANDUAN PENGGUNAAN:</b>\n"
        "\n"
        "1️⃣ <b>Download Drama:</b>\n"
        "   Kirimkan file JSON drama (MinuteDrama, Shorten, Loklok, dll).\n"
        "   Bot akan otomatis mendeteksi dan memberi opsi download.\n\n"
        "2️⃣ <b>Download via Link:</b>\n"
        "   Gunakan perintah `/l <link>`.\n"
        "   Contoh: `/l https://site.com/video.mp4` atau m3u8.\n\n"
        "3️⃣ <b>Ganti Nama (Rename):</b>\n"
        "   Kirim file video ke bot, lalu <b>balas (reply)</b>\n"
        "   pada video tersebut dengan nama baru yang diinginkan.\n\n"
        "4️⃣ <b>Gabungkan (Merge):</b>\n"
        "   Kirim 2 file video atau lebih. Bot akan menawarkan\n"
        "   tombol <i>'Gabungkan'</i> setelah Anda mengirim file ke-2.\n"
        "──────────────────────────\n"
        f"{owner_text}"
        "🚀 <i>Bot ini didesain untuk kemudahan download drama.</i>"
    )
    
    await event.respond(text, parse_mode='html')

@client.on(events.NewMessage(pattern=r'^/panel(\s+|$)'))
async def monitoring_panel(event):
    owner_id = getattr(config_file, 'OWNER_ID', 0)
    if event.sender_id != owner_id:
        await event.respond("❌ Maaf, perintah ini hanya untuk Owner bot.")
        return
        
    msg = await event.respond("⏳ <b>Menyiapkan Live Monitoring Panel...</b>", parse_mode='html')
    # Daftarkan pesan ini untuk di-update otomatis oleh panel_update_loop
    panel_messages[event.chat_id] = msg

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
client.loop.create_task(panel_update_loop())
client.run_until_disconnected()
