import os
import json
import asyncio
import html
import sys
import subprocess
import telegram # type: ignore
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup # type: ignore
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes # type: ignore
from typing import Any
import shutil

# --- CONFIGURATION LOADING ---
# Mencoba memuat dari config.py, jika gagal gunakan Environment Variables
try:
    import config as config_file
    BOT_TOKEN = getattr(config_file, 'BOT_TOKEN', "")
    ALLOWED_USERS: list[int] = getattr(config_file, 'ALLOWED_USERS', [])
    TELEGRAM_MAX_SIZE: int = getattr(config_file, 'TELEGRAM_MAX_SIZE', 2000 * 1024 * 1024)
    TIMEOUT_DL: int = getattr(config_file, 'TIMEOUT_DL', 600)
    MAX_CONCURRENT_DOWNLOADS: int = getattr(config_file, 'MAX_CONCURRENT_DOWNLOADS', 3)
    WORKERS: int = getattr(config_file, 'WORKERS', 10)
    HTTP_PROXY: str = getattr(config_file, 'HTTP_PROXY', "")
    TEMP_DIR_CONFIG: str = getattr(config_file, 'TEMP_DIR', "")
    LOCAL_BOT_API_URL: str = getattr(config_file, 'LOCAL_BOT_API_URL', "")
except (ImportError, ModuleNotFoundError):
    # Fallback ke Environment Variables jika config.py tidak ada
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    allowed_raw = os.getenv("ALLOWED_USERS", "")
    ALLOWED_USERS = [int(i) for i in allowed_raw.split(",") if i] if allowed_raw else []
    TELEGRAM_MAX_SIZE = int(os.getenv("TELEGRAM_MAX_SIZE", 2147483648))
    TIMEOUT_DL = int(os.getenv("TIMEOUT_DL", 600))
    MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", 3))
    WORKERS = int(os.getenv("WORKERS", 10))
    HTTP_PROXY = os.getenv("HTTP_PROXY", "")
    TEMP_DIR_CONFIG = os.getenv("TEMP_DIR", "")
    LOCAL_BOT_API_URL = os.getenv("LOCAL_BOT_API_URL", "")
    print("⚠️ config.py tidak ditemukan di server, menggunakan Environment Variables (jika ada).")

# Folder penyimpanan project-local
TEMP_DIR = TEMP_DIR_CONFIG if TEMP_DIR_CONFIG else os.path.join(os.getcwd(), "downloads")
os.makedirs(TEMP_DIR, exist_ok=True)

# Kamus penyimpanan session per user (sementara di memory)
user_sessions: dict[str, dict[str, Any]] = {}

def make_progress_bar(current: int, total: int, width: int = 20) -> str:
    if total <= 0:
        return f"[{'░' * width}] 0%"
    filled = int(width * current / total)
    bar = "█" * filled + "░" * (width - filled)
    pct = int(100 * current / total)
    return f"[{bar}] {pct}%"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user: return
    user_id = update.effective_user.id
    if ALLOWED_USERS and user_id not in ALLOWED_USERS:
        await update.message.reply_text("Maaf, Anda tidak diizinkan menggunakan bot ini.") # type: ignore
        return
    await update.message.reply_text("Halo! Kirimkan file JSON drama untuk mulai mendownload.") # type: ignore

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message or not update.message.document: return
    user_id = update.effective_user.id
    if ALLOWED_USERS and user_id not in ALLOWED_USERS: return
        
    doc = update.message.document
    if not doc.file_name or not doc.file_name.endswith('.json'):
        await update.message.reply_text("❌ Mohon kirimkan file berformat JSON.")
        return

    # Download JSON
    os.makedirs(TEMP_DIR, exist_ok=True)
    file_path = os.path.join(TEMP_DIR, doc.file_name)
    file_obj = await context.bot.get_file(doc.file_id)
    await file_obj.download_to_drive(file_path)

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        import parsers # type: ignore
        source_type = parsers.detect_source(data)
        if source_type == "unknown":
            await update.message.reply_text("❌ Format JSON tidak dikenali.")
            return
            
        drama_info = parsers.parse_json_data(data, source_type, doc.file_name)
        session_id = f"{user_id}_{update.message.message_id}"
        session_dir = os.path.join(TEMP_DIR, session_id)
        os.makedirs(session_dir, exist_ok=True)
        
        user_sessions[session_id] = {
            "drama_info": drama_info,
            "source": source_type,
            "session_dir": session_dir,
            "downloaded": [],
            "failed_list": []
        }
        
        text = f"🎬 <b>{html.escape(drama_info['title'])}</b>\n\n"
        text += f"📺 <b>Total:</b> {drama_info['total_ep']} episode\n"
        text += f"📦 <b>Platform:</b> {html.escape(source_type.capitalize())}\n\n"
        text += f"Lanjut download {drama_info['total_ep']} episode?"

        keyboard = [[
            InlineKeyboardButton("✅ Ya, Download", callback_data=f"dl_{session_id}"),
            InlineKeyboardButton("❌ Batal", callback_data=f"cancel_{session_id}")
        ]]
        
        if drama_info.get('cover'):
            await update.message.reply_photo(photo=drama_info['cover'], caption=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        else:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

    except Exception as e:
        await update.message.reply_text(f"❌ Error JSON: {str(e)}")
    finally:
        if os.path.exists(file_path): os.remove(file_path)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data or not query.message: return
    await query.answer()
    
    data = query.data
    if data.startswith("cancel_"):
        session_id = data.split("cancel_")[1]
        session = user_sessions.pop(session_id, None)
        if session:
            shutil.rmtree(session['session_dir'], ignore_errors=True)
        
        msg = "❌ Proses dibatalkan."
        if query.message.caption: await query.edit_message_caption(msg)
        else: await query.edit_message_text(msg)
        return
        
    elif data.startswith("dl_"):
        session_id = data.split("dl_")[1]
        session = user_sessions.get(session_id)
        if not session:
            await query.edit_message_text("⚠️ Sesi habis.")
            return
            
        drama_info = session['drama_info']
        total = drama_info['total_ep']
        title = drama_info['title']
        
        # Variabel progress dengan list agar mutable di inner function
        progress = [0, 0] # [success, failed]
        
        async def download_task(idx, ep, semaphore):
            current_num = idx + 1
            ep_num = ep.get('num', current_num)
            url = ep.get('url')
            
            async with semaphore:
                if not url:
                    progress[1] += 1
                    session['failed_list'].append(f"EP{ep_num}(NoURL)")
                    return

                import downloader # type: ignore
                safe_title = "".join([c for c in title if c.isalnum() or c==' ']).strip()
                ep_filename = f"{safe_title} - EP{ep_num:02d}.mp4"
                output_path = os.path.join(session['session_dir'], ep_filename)
                
                success = False
                source = session['source']
                
                try:
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
                except Exception as e:
                    print(f"Error download EP{ep_num}: {e}")
                    success = False

                if success:
                    progress[0] += 1
                    session['downloaded'].append(output_path)
                else:
                    progress[1] += 1
                    session['failed_list'].append(f"EP{ep_num}")

        async def update_status_loop():
            while (progress[0] + progress[1]) < total:
                done = progress[0] + progress[1]
                text = (
                    f"⬇️ <b>PROSES DOWNLOAD</b>\n"
                    f"📦 <b>Drama:</b> {html.escape(title)}\n"
                    f"📺 <b>Progress:</b> {done}/{total}\n"
                    f"{make_progress_bar(done, total)}\n"
                    f"✅ Selesai: {progress[0]} | ❌ Gagal: {progress[1]}"
                )
                try:
                    if query.message.caption: await query.edit_message_caption(text, parse_mode='HTML') # type: ignore
                    else: await query.edit_message_text(text, parse_mode='HTML')
                except Exception: pass
                await asyncio.sleep(4)

        sem = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
        tasks = [download_task(idx, ep, sem) for idx, ep in enumerate(drama_info['episodes'])]
        status_task = asyncio.create_task(update_status_loop())
        await asyncio.gather(*tasks)
        status_task.cancel()

        # Hasil Akhir
        failed_text = f"\n⚠️ Gagal: {', '.join(session['failed_list'][:10])}" if session['failed_list'] else ""
        final_text = (
            f"⬇️ <b>DOWNLOAD SELESAI</b>\n"
            f"📦 <b>Drama:</b> {html.escape(title)}\n"
            f"✅ Berhasil: {progress[0]} | ❌ Gagal: {progress[1]}{html.escape(failed_text)}\n"
            f"\nPilih format upload:"
        )

        keyboard = [[
            InlineKeyboardButton("📦 Upload MKV", callback_data=f"up_mkv_{session_id}"),
            InlineKeyboardButton("🎬 Upload MP4", callback_data=f"up_mp4_{session_id}")
        ], [InlineKeyboardButton("❌ Batal Upload", callback_data=f"cancel_{session_id}")]]
        
        try:
            if query.message.caption: await query.edit_message_caption(final_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
            else: await query.edit_message_text(final_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        except Exception: pass

    elif data.startswith("up_"):
        parts = data.split("_", 2)
        target_format = parts[1]
        session_id = parts[2]
        session = user_sessions.get(session_id)
        if not session: return

        files = session['downloaded']
        if not files: return
        files.sort()

        uploaded = 0
        failed_up = 0
        title = session['drama_info']['title']

        for idx, filepath in enumerate(files):
            current = idx + 1
            
            # Progress update
            text = (
                f"⬆️ <b>PROSES UPLOAD</b>\n"
                f"📦 <b>Drama:</b> {html.escape(title)}\n"
                f"📺 <b>Upload:</b> {current}/{len(files)}\n"
                f"{make_progress_bar(current, len(files))}\n"
                f"✅ Berhasil: {uploaded} | ❌ Gagal: {failed_up}"
            )
            try:
                if query.message.caption: await query.edit_message_caption(text, parse_mode='HTML')
                else: await query.edit_message_text(text, parse_mode='HTML')
            except Exception: pass

            upload_path = filepath
            # Conversion
            if not filepath.endswith(f".{target_format}"):
                converted = filepath.rsplit('.', 1)[0] + f".{target_format}"
                try:
                    subprocess.run(["ffmpeg", "-y", "-i", filepath, "-c", "copy", converted], 
                                 check=True, capture_output=True)
                    upload_path = converted
                except Exception as e:
                    print(f"FFmpeg Error: {e}")

            # Send to Telegram
            try:
                if not os.path.exists(upload_path):
                    raise FileNotFoundError(f"File not found: {upload_path}")

                with open(upload_path, 'rb') as f:
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id, # type: ignore
                        document=f,
                        caption=f"📺 {html.escape(title)} - Episode {current}",
                        parse_mode='HTML',
                        write_timeout=600
                    )
                uploaded += 1
            except Exception as e:
                print(f"Upload Failure for {upload_path}: {str(e)}")
                failed_up += 1
            finally:
                if os.path.exists(upload_path): os.remove(upload_path)
                if upload_path != filepath and os.path.exists(filepath): os.remove(filepath)
            
            await asyncio.sleep(1)

        # Hasil Akhir - Kirim pesan BARU (bukan edit)
        drama_info = session.get('drama_info', {})
        synopsis = drama_info.get('sinopsis') or drama_info.get('summary') or drama_info.get('description') or 'Tidak ada sinopsis.'
        syn_short = str(synopsis)[:500] + ('...' if len(str(synopsis)) > 500 else '')
        
        # Fallback variables jika data kosong
        rep_title = str(drama_info.get('title', title or "Unknown Drama")).strip()
        rep_format = str(target_format).upper()
        
        report = (
            f"✅ <b>PROSES SELESAI!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 <b>Drama:</b> {html.escape(rep_title)}\n"
            f"📺 <b>Format:</b> {html.escape(rep_format)}\n"
            f"📖 <b>Sinopsis:</b>\n<i>{html.escape(syn_short)}</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⬆️ <b>Total File</b>    : {len(files)}\n"
            f"✅ <b>Berhasil</b>      : {uploaded}\n"
            f"❌ <b>Gagal Upload</b>  : {failed_up}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎉 Semua proses telah selesai!"
        )
        
        try:
            # Kirim pesan baru
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=report,
                parse_mode='HTML'
            )
            
            # Hapus pesan status progress terakhir agar tidak menumpuk
            if query.message:
                await query.message.delete()
        except Exception as e:
            print(f"Error sending final report: {e}")
            # Fallback jika hapus gagal (misal expired), coba edit saja
            try:
                if query.message.caption: await query.edit_message_caption(report, parse_mode='HTML')
                else: await query.edit_message_text(report, parse_mode='HTML')
            except: pass
        
        user_sessions.pop(session_id, None)
        shutil.rmtree(session['session_dir'], ignore_errors=True)

async def update_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message: return
    user_id = update.effective_user.id
    if user_id != getattr(config_file if 'config_file' in globals() else None, 'OWNER_ID', 0): return
    msg = await update.message.reply_text("🔄 Updating...")
    try:
        subprocess.run(["git", "pull"], check=True)
        await msg.edit_text("✅ Updated! Restarting...")
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e: await msg.edit_text(f"❌ Failed: {e}")

async def restart_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message: return
    if update.effective_user.id != getattr(config_file if 'config_file' in globals() else None, 'OWNER_ID', 0): return
    await update.message.reply_text("🔄 Restarting...")
    os.execv(sys.executable, [sys.executable] + sys.argv)

from telegram.request import HTTPXRequest

def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN tidak ditemukan! Isi di config.py atau Environment Variable.")
        return
    
    # Request setup for stability
    t_request = HTTPXRequest(connect_timeout=60, read_timeout=60, write_timeout=600)
    if HTTP_PROXY: t_request = HTTPXRequest(connect_timeout=60, proxy_url=HTTP_PROXY, write_timeout=600)
        
    app_builder = ApplicationBuilder().token(BOT_TOKEN).concurrent_updates(True).request(t_request)
    if LOCAL_BOT_API_URL:
        # Menghubungkan ke Local Bot API Server untuk upload > 50MB
        local_url = LOCAL_BOT_API_URL.rstrip('/')
        app_builder.base_url(f"{local_url}/bot")
        app_builder.local_mode(True)
        print(f"🚀 Running in Local Bot API mode: {local_url}")
    
    app = app_builder.build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("update", update_bot))
    app.add_handler(CommandHandler("restart", restart_bot))
    app.add_handler(MessageHandler(filters.Document.FileExtension("json"), handle_document))
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    print("Bot is running... (Menunggu koneksi)")
    
    for attempt in range(5):
        try:
            app.run_polling(drop_pending_updates=True)
            break
        except Exception as e:
            if "Conflict" in str(e) or "Timed out" in str(e):
                print(f"⚠️ Retry {attempt+1}/5: {e}")
                import time
                time.sleep(10)
            else:
                print(f"❌ Error: {e}")
                break

if __name__ == '__main__':
    main()
