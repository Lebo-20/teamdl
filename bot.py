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
import math
from datetime import timedelta
from typing import Any

# Telethon Imports
from telethon import TelegramClient, events, Button
from telethon.tl.types import DocumentAttributeVideo
import config as config_file

# Local Modules
import parsers
import downloader
from vigloo_api import vigloo_api # New import

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
            # Di Telethon send_message tidak pake caption jika mengirim media melalui file=
            # Sebaiknya gunakan send_file dengan file=msg.media
            await client.send_file(
                backup_id, 
                file=msg.media, 
                caption=msg.message,
                force_document=True,
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

@client.on(events.NewMessage(func=lambda e: e.document))
async def handle_document(event):
    user_id = event.sender_id
    if ALLOWED_USERS and user_id not in ALLOWED_USERS:
        return
        
    doc = event.document
    filename = event.file.name
    if not (filename.endswith('.json') or filename.endswith('.m3u8')):
        # Kita hanya proses JSON atau M3U8 di sini
        return

    # Download File
    os.makedirs(TEMP_DIR, exist_ok=True)
    file_path = os.path.join(TEMP_DIR, filename)
    await event.download_media(file=file_path)

    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        await event.respond("❌ File kosong atau gagal didownload.")
        return

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        source_type = "unknown"
        drama_info = None
        
        # Coba parse sebagai JSON dulu
        try:
            data = json.loads(content)
            source_type = parsers.detect_source(data)
            if source_type != "unknown":
                drama_info = parsers.parse_json_data(data, source_type, filename)
        except json.JSONDecodeError:
            pass
            
        # Jika bukan JSON atau tidak dikenal, coba parse sebagai M3U8
        if source_type == "unknown" and "#EXTM3U" in content:
            source_type = "m3u8_raw"
            drama_info = parsers.parse_m3u8_content(content, filename)
            
        if not drama_info or source_type == "unknown":
            await event.respond("❌ Format file tidak dikenali (Bukan JSON Drama atau M3U8 valid).")
            if os.path.exists(file_path): os.remove(file_path)
            return

        # Setup Session
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
            f"📦 <b>Platform:</b> {html.escape(source_type.upper())}\n"
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
        
        # Kirim Detail Drama
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
        await event.respond(f"❌ <b>Error:</b> {html.escape(str(e))}", parse_mode='html')
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

@client.on(events.NewMessage(pattern=r'^/vigloo(\s+|$)(.*)'))
async def handle_vigloo_search(event):
    user_id = event.sender_id
    if ALLOWED_USERS and user_id not in ALLOWED_USERS:
        return
        
    query = event.pattern_match.group(2).strip()
    if not query:
        await event.respond("❌ Gunakan: `/vigloo Judul Drama` untuk mencari drama.")
        return

    msg = await event.respond(f"🔍 <b>Mencari:</b> <code>{html.escape(query)}</code>...", parse_mode='html')
    
    try:
        # Jika query adalah 8 digit angka, coba anggap sebagai ID Drama langsung
        if query.isdigit() and len(query) >= 8:
            await show_vigloo_drama_detail(event, query, user_id, msg)
            return

        results = await vigloo_api.search(query)
        if results is None:
            await msg.edit("❌ <b>Gagal mengakses API Vigloo.</b>\nPastikan <code>VIGLOO_TOKEN</code> di <code>config.py</code> sudah benar dan tidak expired.", parse_mode='html')
            return
            
        if not results:
            await msg.edit("❌ Drama tidak ditemukan. Coba gunakan judul dalam bahasa Inggris atau ID Drama (8 digit).")
            return

        text = f"🎯 <b>Hasil Pencarian Vigloo:</b>\n"
        buttons = []
        for i, res in enumerate(results[:8]): # Ambil 8 hasil teratas
            title = res.get('name', 'Unknown')
            program_id = res.get('id')
            text += f"{i+1}. <b>{html.escape(title)}</b>\n"
            buttons.append([Button.inline(f"🎬 {title[:20]}...", data=f"vigloo_view_{program_id}")])

        await msg.edit(text, buttons=buttons, parse_mode='html')
    except Exception as e:
        await msg.edit(f"❌ Error API: {e}")

@client.on(events.NewMessage(pattern=r'^/(l|ytdlleech)(\s+|$)'))
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
        
        # Step 1: Check available subtitles
        await summary_msg.edit(f"🔍 <b>Mengecek subtitle untuk:</b>\n<code>{html.escape(url[:100])}...</code>", parse_mode='html')
        
        try:
            import subprocess as _sub
            # Gunakan yt-dlp --list-subs untuk deteksi cepat
            # Kita hanya ambil output kodenya saja
            proc = _sub.run(["yt-dlp", "--list-subs", "--skip-download", url], capture_output=True, text=True, timeout=30)
            output = proc.stdout
            
            # Simple parsing for languages
            # Format biasanya: "eng      unknown_video" atau "ind      vtt"
            langs = []
            lines = output.split('\n')
            started = False
            for line in lines:
                if "Language Formats" in line:
                    started = True
                    continue
                if started and line.strip():
                    parts = line.split()
                    if parts:
                        lang_code = parts[0]
                        if lang_code not in ["Language", "Formats"] and len(lang_code) <= 10:
                            langs.append(lang_code)

            # Store in session for callback
            batch_id = f"batch_{user_id}_{event.id}"
            if batch_id not in user_sessions:
                user_sessions[batch_id] = {
                    "total": total,
                    "success": 0,
                    "failed": 0
                }

            session_id = f"link_{user_id}_{event.id}_{idx}"
            user_sessions[session_id] = {
                "batch_id": batch_id,
                "url": url,
                "langs": langs,
                "current": current,
                "total": total,
                "summary_msg_id": summary_msg.id,
                "chat_id": event.chat_id,
                "reply_to": event.id
            }

            if langs:
                # Batasi jumlah tombol biar tidak kepanjangan (max 10)
                display_langs = sorted(list(set(langs)))[:12]
                buttons = []
                row = []
                for l in display_langs:
                    row.append(Button.inline(f"🌐 {l}", data=f"dl_link_{session_id}_{l}"))
                    if len(row) == 3:
                        buttons.append(row)
                        row = []
                if row: buttons.append(row)
                buttons.append([Button.inline("📦 ALL SUBS", data=f"dl_link_{session_id}_all")])
                buttons.append([Button.inline("🚫 NO SUBS", data=f"dl_link_{session_id}_none")])

                await event.respond(
                    f"🎯 <b>SUBTITLE DITEMUKAN</b>\n"
                    f"Silakan pilih bahasa subtitle yang ingin di-embed ke dalam video (Softsub MKV):\n\n"
                    f"🔗 <code>{html.escape(url[:100])}...</code>",
                    buttons=buttons,
                    parse_mode='html',
                    reply_to=event.id
                )
                continue # Tunggu user klik tombol
            else:
                # Tidak ada sub, langsung download
                success = await perform_link_download(event.chat_id, url, current, total, summary_msg, event.id, "none")
                # Update batch
                user_sessions[batch_id]["success" if success else "failed"] += 1
                if user_sessions[batch_id]["success"] + user_sessions[batch_id]["failed"] == total:
                    # Report Final
                    await event.respond(f"✅ <b>Download Selesai!</b>\nBerhasil: {user_sessions[batch_id]['success']}\nGagal: {user_sessions[batch_id]['failed']}", parse_mode='html')
                    user_sessions.pop(batch_id, None)

        except Exception as e:
            print(f"Error checking subs for {url}: {e}")
            # Fallback ke download biasa
            success = await perform_link_download(event.chat_id, url, current, total, summary_msg, event.id, "all")
            # Update batch logic here too if needed

    # Final summary update (jika tidak ada link yang butuh konfirmasi)
    # Note: flow akan berlanjut di callback handler
    pass

async def perform_link_download(chat_id, url, current, total, summary_msg, reply_to, lang_choice):
    """Fungsi pembantu untuk mengeksekusi download link."""
    user_id = chat_id 
    session_id = f"dl_temp_{int(time.time())}"
    session_dir = os.path.join(TEMP_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)
    
    # Simple filename extraction
    parsed_url = urllib.parse.urlparse(url)
    path = parsed_url.path
    filename_orig = os.path.basename(path)
    
    if not filename_orig or '.' not in filename_orig or len(filename_orig) < 4:
        filename = f"video_{current}.mkv"
    else:
        name_part = filename_orig.rsplit('.', 1)[0]
        filename = f"{name_part}_{current}.mkv"
        
    output_path = os.path.join(session_dir, filename)
    
    error_msg = ""
    try:
        # Download logic dengan pilihan bahasa
        success = await downloader.download_video_ytdlp(url, output_path, lang=lang_choice)
        if not success:
            success = await downloader.download_video_ffmpeg(url, output_path)
            if not success: error_msg = "Download via YTDLP & FFmpeg failed."
            
        if success and os.path.exists(output_path):
            display_url = url if len(url) < 100 else url[:100] + "..."
            await send_and_backup(
                chat_id,
                output_path,
                caption=f"📺 Video ({current}/{total}):\n<code>{html.escape(display_url)}</code>\n\n✅ Subtitle: {lang_choice}",
                force_document=True,
                supports_streaming=True,
                reply_to=reply_to,
                parse_mode='html'
            )
            return True
        return False
    except Exception as e:
        error_msg = str(e)
        # Kirim error ke user
        try: await client.send_message(chat_id, f"❌ <b>Gagal Download:</b>\n<code>{html.escape(url[:100])}...</code>\n\n📌 <b>Error:</b>\n<code>{html.escape(error_msg)}</code>", parse_mode='html')
        except: pass
        return False
    finally:
        shutil.rmtree(session_dir, ignore_errors=True)

@client.on(events.CallbackQuery(pattern=r'^dl_link_'))
async def handle_link_sub_callback(event):
    # Pattern: dl_link_{session_id}_{lang}
    data = event.data.decode()
    parts = data.split('_')
    # session_id = link_{user_id}_{event_id}_{idx}
    # Di sini kita butuh index yang tepat
    # parts: [dl, link, link, user, eid, idx, lang] -> ini bakal kepanjangan
    # Mari kita gunakan index yang aman
    lang = parts[-1]
    # Gabungkan sisa parts untuk dapet session_id: link_{user_id}_{event_id}_{idx}
    session_id = "_".join(parts[2:-1])
    
    session = user_sessions.get(session_id)
    if not session:
        await event.edit("⚠️ Sesi habis atau expired.")
        return
        
    url = session['url']
    current = session['current']
    total = session['total']
    summary_msg_id = session['summary_msg_id']
    chat_id = session['chat_id']
    reply_to = session['reply_to']
    
    await event.edit(f"🎬 <b>Memulai Download...</b>\nBahasa: <code>{lang}</code>\n📦 Link: {current}/{total}", parse_mode='html')
    
    # Cari summary message
    summary_msg = await client.get_messages(chat_id, ids=summary_msg_id)
    
    # Download
    success = await perform_link_download(chat_id, url, current, total, summary_msg, reply_to, lang)
    
    batch_id = session.get('batch_id')
    if batch_id and batch_id in user_sessions:
        user_sessions[batch_id]["success" if success else "failed"] += 1
        
        # Cek apakah sudah semua diproses
        batch = user_sessions[batch_id]
        if batch["success"] + batch["failed"] == batch["total"]:
            # Report Final
            final_text = (
                f"✅ <b>DOWNLOAD SELESAI</b>\n"
                f"──────────────────────────\n"
                f"📋 <b>Total Link</b>  : {batch['total']}\n"
                f"✅ <b>Berhasil</b>    : {batch['success']}\n"
                f"❌ <b>Gagal</b>       : {batch['failed']}\n"
                f"──────────────────────────"
            )
            await client.send_message(chat_id, final_text, parse_mode='html')
            user_sessions.pop(batch_id, None)

    if success:
        await event.edit(f"✅ <b>Selesai!</b> Video dikirim di bawah.", parse_mode='html')
    else:
        await event.edit(f"❌ <b>Gagal Download.</b> Silakan cek log atau pesan error di bawah.", parse_mode='html')
        
    # Hapus sesi link
    user_sessions.pop(session_id, None)
    await summary_msg.edit(final_text, parse_mode='html')

async def show_vigloo_drama_detail(event, program_id, user_id, msg_to_edit=None):
    """Helper shared function untuk menampilkan detail drama Vigloo."""
    if msg_to_edit:
        msg = await msg_to_edit.edit("⏳ <b>Mengambil detail drama...</b>", parse_mode='html')
    else:
        # Biasanya dari CallbackQuery
        msg = await event.edit("⏳ <b>Mengambil detail drama...</b>", parse_mode='html')
        
    detail = await vigloo_api.get_drama_detail(program_id)
    if not detail:
        await msg.edit("❌ Gagal mengambil detail drama.")
        return

    # Gunakan ID unik untuk sesi agar tidak bentrok
    session_id = f"vg_{user_id}_{int(time.time())}"
    user_sessions[session_id] = {
        "drama_info": detail,
        "source": f"vigloo_api",
        "session_dir": os.path.join(TEMP_DIR, session_id),
        "downloaded": [],
        "failed_list": [],
        "format": "MP4"
    }
    os.makedirs(user_sessions[session_id]["session_dir"], exist_ok=True)

    text = (
        f"🎬 <b>{html.escape(detail['title'])}</b>\n"
        f"──────────────────────────\n"
        f"📖 <b>Sinopsis:</b>\n<i>{html.escape(detail['sinopsis'][:400])}{'...' if len(detail['sinopsis']) > 400 else ''}</i>\n\n"
        f"📺 <b>Total:</b> {detail['total_ep']} episode\n"
        f"📦 <b>Platform:</b> VIGLOO API (captain.sapimu.au)\n"
        f"──────────────────────────\n"
        f"Lanjut download semua episode?"
    )
    
    buttons = [
        [
            Button.inline("🎬 Softsub (MKV)", data=f"sub_soft_{session_id}"),
            Button.inline("🎞️ Hardsub (Wait)", data=f"sub_hard_{session_id}")
        ],
        [Button.inline("❌ Batal", data=f"cancel_{session_id}")]
    ]

    try:
        if detail.get('cover'):
            temp_cover = os.path.join(user_sessions[session_id]["session_dir"], "cover.jpg")
            if await downloader.download_file(detail['cover'], temp_cover):
                # Saat kirim file, kita harus delete pesan lama jika itu callback
                if not msg_to_edit:
                    await event.delete()
                else:
                    await msg.delete()
                    
                await client.send_file(event.chat_id, temp_cover, caption=text, buttons=buttons, parse_mode='html')
                if os.path.exists(temp_cover): os.remove(temp_cover)
                return
    except Exception as e:
        print(f"Error sending cover: {e}")

    await msg.edit(text, buttons=buttons, parse_mode='html')

@client.on(events.CallbackQuery)
async def handle_callback(event):
    user_id = event.sender_id
    data = event.data.decode('utf-8')
    
    if data.startswith("vigloo_view_"):
        program_id = data.split("vigloo_view_")[1]
        await show_vigloo_drama_detail(event, program_id, user_id)

    elif data.startswith("do_merge_"):
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
                if "live_status" in session:
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
                elif source == "vigloo_api":
                    # Fetch URL on-the-fly dari API (agar token tidak expired)
                    data = await vigloo_api.get_stream_url(ep.get('seasonId'), ep_num)
                    if data:
                        v_url = data.get('url')
                        cookies = data.get('cookies', {})
                        headers = {"Cookie": "; ".join([f"{k}={v}" for k, v in cookies.items()])} if cookies else {}
                        success = await downloader.download_video_ytdlp(v_url, output_path, headers)
                    else:
                        success = False
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
