import os
import json
import asyncio
import html
import sys
import subprocess
import telegram # type: ignore
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto # type: ignore
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes # type: ignore
import config as config_file
from config import BOT_TOKEN, TEMP_DIR, ALLOWED_USERS, TELEGRAM_MAX_SIZE, TIMEOUT_DL, MAX_CONCURRENT_DOWNLOADS, WORKERS # type: ignore
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

async def cleanup_all_temp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Membersihkan seluruh folder TEMP_DIR."""
    user_id = update.effective_user.id # type: ignore
    owner_id = getattr(config_file, 'OWNER_ID', 0)
    
    if owner_id != 0 and user_id != owner_id:
        await update.message.reply_text("❌ Fitur ini hanya untuk Owner.") # type: ignore
        return

    import shutil
    try:
        if os.path.exists(TEMP_DIR):
            shutil.rmtree(TEMP_DIR)
            os.makedirs(TEMP_DIR, exist_ok=True)
            await update.message.reply_text("✅ Seluruh folder temp dan session telah dibersihkan.") # type: ignore
        else:
            await update.message.reply_text("ℹ️ Folder temp sudah kosong.") # type: ignore
        
        # Bersihkan juga session di memory
        user_sessions.clear()
    except Exception as e:
        await update.message.reply_text(f"❌ Gagal membersihkan temp: {str(e)}") # type: ignore

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data.startswith("cancel_"):
        await query.edit_message_caption("❌ Proses dibatalkan.") if query.message.caption else await query.edit_message_text("❌ Proses dibatalkan.")
        session_id = data.split("cancel_")[1]
        session = user_sessions.pop(session_id, None)
        if session:
            try:
                import shutil
                s_dir = session.get('session_dir')
                if s_dir and os.path.exists(s_dir):
                    shutil.rmtree(s_dir)
            except Exception:
                pass
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
        
        # Mulai download paralel per episode
        episodes_list: Any = drama_info.get('episodes', []) # type: ignore
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
        
        async def download_task(idx, ep):
            nonlocal success_count, failed_count
            current_num = idx + 1
            ep_num = ep.get('num', current_num)
            url = ep.get('url')
            source = session['source']
            
            async with semaphore:
                # Cek expired (FlikReels)
                if source == "flikreels":
                    import time
                    timeout = ep.get("hls_timeout", 0)
                    if timeout > 0 and time.time() > timeout:
                        failed_count += 1
                        failed_eps.append(f"EP{ep_num} (Exp)")
                        return
                    if ep.get("is_lock") == 1 and not url:
                        failed_count += 1
                        failed_eps.append(f"EP{ep_num} (Lock)")
                        return

                if not url:
                    failed_count += 1
                    failed_eps.append(f"EP{ep_num} (NoUrl)")
                    return

                safe_title = "".join([c for c in title if c.isalpha() or c.isdigit() or c==' ']).rstrip()
                ep_filename = f"{safe_title} - EP{ep_num:02d}.mp4"
                output_path = os.path.join(session['session_dir'], ep_filename)
                
                success = False
                # Download logika
                if source == "vigloo":
                    cookies = ep.get('cookies', {})
                    headers = {"Cookie": "; ".join([f"{k}={v}" for k, v in cookies.items()])} if cookies else {}
                    success = await downloader.download_video_ytdlp(url, output_path, headers)
                elif source in ["flikreels", "dramaflickreels"]:
                    success = await downloader.download_video_ytdlp(url, output_path)
                    if not success: success = await downloader.download_aria2(url, output_path)
                elif ".m3u8" in url or source in ["dramawave_info", "dramawave_direct", "freereels", "goodshort", "meloshort", "stardust"]:
                    success = await downloader.download_video_ytdlp(url, output_path)
                    if not success: success = await downloader.download_video_ffmpeg(url, output_path)
                else:
                    if source in ["draamabox", "draamabox_list"]:
                        success = await downloader.download_video_ytdlp(url, output_path)
                        if not success: success = await downloader.download_aria2(url, output_path)
                    else:
                        success = await downloader.download_aria2(url, output_path)
                
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

                if success:
                    success_count += 1
                    session['downloaded'].append(output_path)
                else:
                    failed_count += 1
                    failed_eps.append(f"EP{ep_num}")

        # Task untuk update status message
        async def update_status_loop():
            while (success_count + failed_count) < total:
                progress_bar = make_progress_bar(success_count + failed_count, total)
                status_text = (
                    f"⬇️ <b>PROSES DOWNLOAD (ARIA2)</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📦 <b>Drama:</b> {html.escape(title)}\n"
                    f"📺 <b>Total:</b> {total} episode\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"⏳ <b>Progress:</b> {success_count + failed_count}/{total}\n"
                    f"{progress_bar}\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"✅ Selesai : {success_count}\n"
                    f"❌ Gagal   : {failed_count}\n"
                )
                try:
                    if query.message.caption: await query.edit_message_caption(status_text, parse_mode='HTML')
                    else: await query.edit_message_text(status_text, parse_mode='HTML')
                except Exception: pass
                await asyncio.sleep(3)

        # Jalankan task paralel
        tasks = [download_task(idx, ep) for idx, ep in enumerate(episodes_list)]
        status_task = asyncio.create_task(update_status_loop())
        await asyncio.gather(*tasks)
        status_task.cancel()

        # Laporan Hasil Download Selesai
        if failed_eps:
            limit = 15
            failed_text = f"\n⚠️ Gagal: {', '.join(failed_eps[:limit])}" + ("..." if len(failed_eps) > limit else "")
        else:
            failed_text = ""
        
        final_text = (
            f"⬇️ <b>DOWNLOAD SELESAI (ARIA2)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 <b>Drama:</b> {html.escape(title)}\n"
            f"📺 <b>Total:</b> {total} episode\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Berhasil : {success_count}\n"
            f"❌ Gagal    : {failed_count}\n"
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
            if query.message.caption:
                await query.edit_message_caption(final_text, reply_markup=reply_markup, parse_mode='HTML')
            else:
                await query.edit_message_text(final_text, reply_markup=reply_markup, parse_mode='HTML')
        except Exception: pass

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

async def update_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Update bot dari GitHub dan restart."""
    user_id = update.effective_user.id # type: ignore
    owner_id = getattr(config_file, 'OWNER_ID', 0)
    
    if owner_id != 0 and user_id != owner_id:
        await update.message.reply_text("❌ Fitur ini hanya untuk Owner.") # type: ignore
        return

    msg = await update.message.reply_text("🔄 Memulai update dari GitHub...") # type: ignore
    
    try:
        # Jalankan git pull
        process = await asyncio.create_subprocess_exec(
            "git", "pull",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        result_out = stdout.decode().strip()
        result_err = stderr.decode().strip()
        
        if "Already up to date." in result_out:
            await msg.edit_text("✅ Bot sudah berada di versi terbaru.")
            return
            
        await msg.edit_text(f"✅ Update berhasil!\n\n<b>Log:</b>\n<code>{html.escape(result_out)}</code>\n\n🔄 Restarting...", parse_mode='HTML')
        
        # Restart process
        os.execv(sys.executable, [sys.executable] + sys.argv)
        
    except Exception as e:
        await msg.edit_text(f"❌ Gagal update: {str(e)}")

async def restart_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Restart bot secara manual."""
    user_id = update.effective_user.id # type: ignore
    owner_id = getattr(config_file, 'OWNER_ID', 0)
    
    if owner_id != 0 and user_id != owner_id:
        await update.message.reply_text("❌ Fitur ini hanya untuk Owner.") # type: ignore
        return

    await update.message.reply_text("🔄 Restarting...") # type: ignore
    os.execv(sys.executable, [sys.executable] + sys.argv)

def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("Silakan edit config.py dan isi BOT_TOKEN!")
        return
        
    app = ApplicationBuilder().token(BOT_TOKEN).concurrent_updates(True).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("update", update_bot))
    app.add_handler(CommandHandler("restart", restart_bot))
    app.add_handler(CommandHandler("cleartmp", cleanup_all_temp))
    app.add_handler(MessageHandler(filters.Document.FileExtension("json"), handle_document))
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    print("Bot is running...")
    app.run_polling(read_timeout=60, write_timeout=60, connect_timeout=60, pool_timeout=60)

if __name__ == '__main__':
    main()
