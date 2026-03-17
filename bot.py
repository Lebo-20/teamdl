import os
import json
import asyncio
import html
import telegram # type: ignore
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto # type: ignore
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes # type: ignore
from config import BOT_TOKEN, TEMP_DIR, ALLOWED_USERS, TELEGRAM_MAX_SIZE, TIMEOUT_DL # type: ignore
import parsers # type: ignore
import downloader # type: ignore
from typing import Any

# Kamus penyimpanan session per user (sementara di memory)
user_sessions = {}

def make_progress_bar(current: int, total: int, width: int = 20) -> str:
    if total <= 0:
        return f"[{'░' * width}] 0%"
    filled = int(width * current / total)
    bar = "█" * filled + "░" * (width - filled)
    pct = int(100 * current / total)
    return f"[{bar}] {pct}%"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if ALLOWED_USERS and user_id not in ALLOWED_USERS:
        await update.message.reply_text("Maaf, Anda tidak diizinkan menggunakan bot ini.")
        return
        
    await update.message.reply_text("Halo! Kirimkan file JSON dari platform drama (DotDrama, DraamaBox, DramaWave, dll) untuk mulai mendownload.")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if ALLOWED_USERS and user_id not in ALLOWED_USERS:
        return
        
    doc = update.message.document
    if not doc.file_name.endswith('.json'):
        await update.message.reply_text("❌ Mohon kirimkan file berformat JSON.")
        return

    # Buat temp folder
    os.makedirs(TEMP_DIR, exist_ok=True)
    
    file_path = os.path.join(TEMP_DIR, doc.file_name)
    file_obj = await context.bot.get_file(doc.file_id)
    await file_obj.download_to_drive(file_path)

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        source_type = parsers.detect_source(data)
        if source_type == "unknown":
            await update.message.reply_text("❌ Format JSON tidak dikenali. File dari platform apa ini?")
            return
            
        # Parse data
        drama_info = parsers.parse_json_data(data, source_type, doc.file_name)
        
        # Simpan state untuk didownload
        session_id = f"{user_id}_{update.message.message_id}"
        
        # Folder temp khusus sesi ini (untuk multi-user isolation)
        session_dir = os.path.join(TEMP_DIR, session_id)
        os.makedirs(session_dir, exist_ok=True)
        
        user_sessions[session_id] = {
            "drama_info": drama_info,
            "source": source_type,
            "session_dir": session_dir,
            "filename": doc.file_name,
            "downloaded": [],
            "failed": []
        }
        
        # Kirim info drama
        title_esc = html.escape(drama_info['title'])
        text = f"🎬 <b>{title_esc}</b>\n\n"
        if drama_info.get('sinopsis'):
            syn_raw = drama_info['sinopsis']
            syn_short = syn_raw[:800] + ('...' if len(syn_raw) > 800 else '')
            text += f"📖 <b>Sinopsis:</b>\n<i>{html.escape(syn_short)}</i>\n\n"
            
        if drama_info.get('tags'):
            text += f"🏷️ <b>Tags:</b> {html.escape(str(drama_info['tags']))}\n"
            
        text += f"📺 <b>Total Episode:</b> {drama_info['total_ep']} episode\n"
        text += f"📦 <b>Platform:</b> {html.escape(source_type.capitalize())}\n\n"
        text += f"Lanjut download {drama_info['total_ep']} episode?"

        keyboard = [
            [
                InlineKeyboardButton("✅ Ya, Download", callback_data=f"dl_{session_id}"),
                InlineKeyboardButton("❌ Batal", callback_data=f"cancel_{session_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if drama_info.get('cover'):
            await update.message.reply_photo(photo=drama_info['cover'], caption=text, reply_markup=reply_markup, parse_mode='HTML')
        else:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')

    except Exception as e:
        await update.message.reply_text(f"❌ Terjadi kesalahan saat memproses JSON: {str(e)}")
    finally:
        # Bersihkan file .json setelah dibaca (opsional, bisa simpan dulu)
        if os.path.exists(file_path):
            os.remove(file_path)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data.startswith("cancel_"):
        await query.edit_message_caption("❌ Proses dibatalkan.") if query.message.caption else await query.edit_message_text("❌ Proses dibatalkan.")
        session_id = data.split("cancel_")[1]
        user_sessions.pop(session_id, None)
        return
        
    elif data.startswith("dl_"):
        session_id = data.split("dl_")[1]
        session = user_sessions.get(session_id)
        if not session:
            await query.edit_message_caption("⚠️ Sesi habis. Kirim file JSON ulang.") if query.message.caption else await query.edit_message_text("⚠️ Sesi habis. Kirim file JSON ulang.")
            return
            
        drama_info = session.get('drama_info', {}) # type: ignore
        total = drama_info.get('total_ep', 0) # type: ignore
        title = str(drama_info.get('title', 'Unknown')) # type: ignore
        
        status_text = (
            f"⬇️ <b>PROSES DOWNLOAD</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 <b>Drama:</b> {html.escape(title)}\n"
            f"📺 <b>Total:</b> {total} episode\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏳ Menyiapkan download...\n"
        )
        
        try:
            if query.message.caption:
                await query.edit_message_caption(status_text, parse_mode='HTML')
            else:
                await query.edit_message_text(status_text, parse_mode='HTML')
        except Exception as e:
            print(f"Error edit message start: {e}")
            
        success_count = 0
        failed_count = 0
        failed_eps = []
        
        # Mulai loop download per episode
        episodes_list: Any = drama_info.get('episodes', []) # type: ignore
        for idx, ep in enumerate(episodes_list): # type: ignore
            current_num = idx + 1
            ep_num = ep.get('num', current_num) # type: ignore
            
            # Format status text untuk episode ini
            status_msg = f"Mendownload..."
            progress_bar = make_progress_bar(current_num, total)
            
            status_text = (
                f"⬇️ <b>PROSES DOWNLOAD</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 <b>Drama:</b> {html.escape(title)}\n"
                f"📺 <b>Total:</b> {total} episode\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"⏳ <b>Episode {current_num}/{total}</b> — {html.escape(status_msg)}\n"
                f"{progress_bar}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ Selesai : {success_count}\n"
                f"❌ Gagal   : {failed_count}\n"
            )
            
            try:
                if query.message.caption:
                    await query.edit_message_caption(status_text, parse_mode='HTML')
                else:
                    await query.edit_message_text(status_text, parse_mode='HTML')
            except Exception as e:
                # Ignore edit errors (misal: message not modified karena text sama)
                pass

            url = ep.get('url')
            source = session['source'] # type: ignore
            
            # Cek status expired atau locked khusus FlikReels
            if source == "flikreels":
                import time
                timeout = ep.get("hls_timeout", 0) # type: ignore
                if timeout > 0 and time.time() > timeout:
                    failed_count += 1
                    failed_eps.append(f"EP{ep_num} (Expired)")
                    session['failed'].append(ep) # type: ignore
                    continue
                if ep.get("is_lock") == 1 and not url: # type: ignore
                    failed_count += 1
                    failed_eps.append(f"EP{ep_num} (Terkunci/Perlu token)")
                    session['failed'].append(ep) # type: ignore
                    continue
                    
            if not url:
                failed_count += 1
                failed_eps.append(f"EP{ep_num} (No URL)")
                session['failed'].append(ep) # type: ignore
                continue
                
            # Tentukan tipe download berdasarkan platform/URL
            safe_title = "".join([c for c in title if c.isalpha() or c.isdigit() or c==' ']).rstrip()
            ep_filename = f"{safe_title} - EP{ep_num:02d}.mp4"
            output_path = os.path.join(session['session_dir'], ep_filename) # type: ignore
            
            success = False
            
            # Download logika
            if source == "vigloo":
                # Butuh yt-dlp dengan cookies
                cookies = ep.get('cookies', {}) # type: ignore
                headers = {}
                if cookies:
                    cookie_str = "; ".join([f"{k}={v}" for k, v in cookies.items()])
                    headers["Cookie"] = cookie_str
                success = await downloader.download_video_ytdlp(url, output_path, headers) # type: ignore
            elif source in ["flikreels", "dramaflickreels"]:
                success = await downloader.download_video_ytdlp(url, output_path) # type: ignore
                if not success:
                    success = await downloader.download_file(url, output_path) # type: ignore
            elif ".m3u8" in url or source in ["dramawave_info", "dramawave_direct", "freereels", "goodshort", "meloshort", "stardust"]: # type: ignore
                success = await downloader.download_video_ytdlp(url, output_path) # type: ignore
                if not success:
                    success = await downloader.download_video_ffmpeg(url, output_path) # type: ignore
            else:
                # File MP4 biasa (draamabox, poincinta, dotdrama)
                if source in ["draamabox", "draamabox_list"]:
                     # Seringkali memblokir request aiohttp sederhana, gunakan yt-dlp duluan
                     success = await downloader.download_video_ytdlp(url, output_path) # type: ignore
                     if not success:
                         success = await downloader.download_file(url, output_path) # type: ignore
                else:
                    success = await downloader.download_file(url, output_path) # type: ignore
                
            # Proses Subtitle jika ada
            sub_url = ep.get('subtitle') # type: ignore
            if success and sub_url:
                sub_path = os.path.join(session['session_dir'], f"temp_sub_{ep_num}.srt") # type: ignore
                sub_success = await downloader.download_file(sub_url, sub_path) # type: ignore
                if sub_success:
                    new_output = await downloader.mux_subtitle(output_path, sub_path, "mp4") # type: ignore
                    if new_output:
                        # Hapus raw video dan subtitle, pakai yang subbed
                        if os.path.exists(output_path): os.remove(output_path)
                        if os.path.exists(sub_path): os.remove(sub_path)
                        output_path = new_output
            
            if success:
                success_count += 1
                session['downloaded'].append(output_path) # type: ignore
            else:
                failed_count += 1
                failed_eps.append(f"EP{ep_num}")
                session['failed'].append(ep) # type: ignore
                
            # Beri jeda kecil antar episode agar tidak spam Telegram API
            await asyncio.sleep(1)

        # Laporan Hasil Download Selesai
        if failed_eps:
            limit = 15
            if len(failed_eps) > limit:
                failed_text = f"\n⚠️ Episode gagal: {', '.join(failed_eps[:limit])} (+{len(failed_eps)-limit} lainnya)" # type: ignore
            else:
                failed_text = f"\n⚠️ Episode gagal: {', '.join(failed_eps)}"
        else:
            failed_text = ""
        
        final_text = (
            f"⬇️ <b>DOWNLOAD SELESAI</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 <b>Drama:</b> {html.escape(title)}\n"
            f"📺 <b>Total:</b> {total} episode\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Berhasil : {success_count} episode\n"
            f"❌ Gagal    : {failed_count} episode\n"
            f"━━━━━━━━━━━━━━━━━━━━{html.escape(failed_text)}\n"
            f"\nPilih format upload:"
        )

        keyboard = [
            [
                InlineKeyboardButton("📦 Upload MKV", callback_data=f"up_mkv_{session_id}"),
                InlineKeyboardButton("🎬 Upload MP4", callback_data=f"up_mp4_{session_id}")
            ],
            [InlineKeyboardButton("❌ Batal Upload", callback_data=f"cancel_{session_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            if query.message.caption: # type: ignore
                await query.edit_message_caption(final_text, reply_markup=reply_markup, parse_mode='HTML') # type: ignore
            else:
                await query.edit_message_text(final_text, reply_markup=reply_markup, parse_mode='HTML') # type: ignore
        except Exception as e:
            print(f"Error edit final text: {e}")

    elif data.startswith("up_"):
        # Format: up_mkv_SESSIONID or up_mp4_SESSIONID
        parts = data.split("_", 2)
        if len(parts) < 3:
            return
            
        target_format = parts[1] # "mkv" or "mp4"
        session_id = parts[2]
        session = user_sessions.get(session_id)
        
        if not session:
            await query.edit_message_caption("⚠️ Sesi habis.") if query.message.caption else await query.edit_message_text("⚠️ Sesi habis.") # type: ignore
            return
            
        downloaded_files = session.get('downloaded', []) # type: ignore
        if not downloaded_files:
            await query.edit_message_caption("❌ Tidak ada file yang berhasil didownload untuk diupload.") if query.message.caption else await query.edit_message_text("❌ Tidak ada file yang berhasil didownload untuk diupload.") # type: ignore
            return
            
        total_upload = len(downloaded_files)
        title = session.get('drama_info', {}).get('title', 'Unknown') # type: ignore
        
        status_text = (
            f"⬆️ <b>PROSES UPLOAD</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 <b>Drama:</b> {html.escape(title)}\n"
            f"📺 <b>Format:</b> {html.escape(target_format.upper())}\n"
            f"📺 <b>Total:</b> {total_upload} file\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏳ Menyiapkan upload...\n"
        )
        
        try:
            if query.message.caption: # type: ignore
                await query.edit_message_caption(status_text, parse_mode='HTML') # type: ignore
            else:
                await query.edit_message_text(status_text, parse_mode='HTML') # type: ignore
        except Exception:
            pass
            
        uploaded_count = 0
        failed_upload = 0
        
        # Urutkan file berdasarkan nama untuk memastikan urutan episode benar
        if isinstance(downloaded_files, list):
            downloaded_files.sort()
        
        for idx, filepath in enumerate(downloaded_files):
            current_num = idx + 1
            filename = os.path.basename(filepath)
            
            # Ekstrak nomor episode jika memungkinkan, jika tidak pakai urutan
            import re
            ep_match = re.search(r'EP(\d+)', filename)
            ep_str = f"EP{ep_match.group(1)}" if ep_match else f"EP{current_num:02d}"
            
            progress_bar = make_progress_bar(current_num, total_upload)
            status_text = (
                f"⬆️ <b>PROSES UPLOAD</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 <b>Drama:</b> {html.escape(title)}\n"
                f"📺 <b>Format:</b> {html.escape(target_format.upper())}\n"
                f"📺 <b>Total:</b> {total_upload} file\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"⏳ Mengupload {html.escape(ep_str)} ({current_num}/{total_upload})...\n"
                f"{progress_bar}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ Terupload : {uploaded_count}\n"
                f"❌ Gagal     : {failed_upload}\n"
            )
            
            try:
                if query.message.caption: # type: ignore
                    await query.edit_message_caption(status_text, parse_mode='HTML') # type: ignore
                else:
                    await query.edit_message_text(status_text, parse_mode='HTML') # type: ignore
            except Exception:
                pass
                
            upload_path = filepath
            
            # Konversi format jika perlu
            if not filepath.endswith(f".{target_format}"):
                converted_path = filepath.rsplit('.', 1)[0] + f".{target_format}"
                cmd = ["ffmpeg", "-y", "-i", filepath, "-c", "copy", converted_path]
                try:
                    import subprocess
                    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    upload_path = converted_path
                except Exception as e:
                    print(f"Error convert {filepath}: {e}")
                    # Jika gagal konversi, tetep upload file asli
            
            # Upload document
            caption = f"📺 <b>{html.escape(title)}</b> | {ep_str} dari {total_upload}"
            
            try:
                # Cek ukuran file
                file_size = os.path.getsize(upload_path)
                if file_size > TELEGRAM_MAX_SIZE:
                    failed_upload += 1
                    continue
                    
                with open(upload_path, 'rb') as doc:
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id, # type: ignore
                        document=doc,
                        caption=caption,
                        parse_mode='HTML',
                        write_timeout=TIMEOUT_DL,
                        read_timeout=TIMEOUT_DL,
                        connect_timeout=TIMEOUT_DL
                    )
                uploaded_count += 1
            except Exception as e:
                print(f"Gagal upload {upload_path}: {e}")
                failed_upload += 1
            finally:
                # Cleanup file
                try:
                    if os.path.exists(upload_path):
                        os.remove(upload_path)
                    if upload_path != filepath and os.path.exists(filepath):
                        os.remove(filepath)
                except Exception:
                    pass
            
            await asyncio.sleep(1) # Jeda Telegram API

        # Status Akhir Upload
        final_text = (
            f"✅ <b>PROSES SELESAI!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 <b>Drama:</b> {html.escape(title)}\n"
            f"📺 <b>Format:</b> {html.escape(target_format.upper())}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⬆️ Total File    : {total_upload}\n"
            f"✅ Berhasil      : {uploaded_count}\n"
            f"❌ Gagal Upload  : {failed_upload}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎉 Semua proses telah selesai!"
        )
        
        try:
            if query.message.caption:
                await query.edit_message_caption(final_text, parse_mode='HTML')
            else:
                await query.edit_message_text(final_text, parse_mode='HTML')
        except Exception:
            pass
            
        # Hapus session
        user_sessions.pop(session_id, None)
        
        # Hapus folder temp sesi
        try:
            import shutil
            s_dir = session.get('session_dir')
            if s_dir and isinstance(s_dir, str) and os.path.exists(s_dir):
                shutil.rmtree(s_dir)
        except Exception:
            pass

def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("Silakan edit config.py dan isi BOT_TOKEN!")
        return
        
    app = ApplicationBuilder().token(BOT_TOKEN).concurrent_updates(True).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.FileExtension("json"), handle_document))
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    print("Bot is running...")
    app.run_polling()

if __name__ == '__main__':
    main()
