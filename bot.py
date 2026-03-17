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
except (ImportError, ModuleNotFoundError):
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    allowed_raw = os.getenv("ALLOWED_USERS", "")
    ALLOWED_USERS = [int(i) for i in allowed_raw.split(",") if i] if allowed_raw else []
    TELEGRAM_MAX_SIZE = int(os.getenv("TELEGRAM_MAX_SIZE", 2147483648))
    TIMEOUT_DL = int(os.getenv("TIMEOUT_DL", 600))
    MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", 3))
    WORKERS = int(os.getenv("WORKERS", 10))
    HTTP_PROXY = os.getenv("HTTP_PROXY", "")
    TEMP_DIR_CONFIG = os.getenv("TEMP_DIR", "")

TEMP_DIR = TEMP_DIR_CONFIG if TEMP_DIR_CONFIG else os.path.join(os.getcwd(), "downloads")
os.makedirs(TEMP_DIR, exist_ok=True)

user_sessions: dict[str, dict[str, Any]] = {}

def make_progress_bar(current: int, total: int, width: int = 20) -> str:
    if total <= 0: return f"[{'░' * width}] 0%"
    filled = int(width * current / total)
    bar = "█" * filled + "░" * (width - filled)
    pct = int(100 * current / total)
    return f"[{bar}] {pct}%"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user: return
    user_id = update.effective_user.id
    if ALLOWED_USERS and user_id not in ALLOWED_USERS:
        await update.message.reply_text("Maaf, Anda tidak diizinkan.") # type: ignore
        return
    await update.message.reply_text("Halo! Kirimkan file JSON drama untuk mulai.") # type: ignore

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message or not update.message.document: return
    doc = update.message.document
    if not doc.file_name or not doc.file_name.endswith('.json'):
        await update.message.reply_text("❌ Mohon kirimkan file berformat JSON.")
        return

    os.makedirs(TEMP_DIR, exist_ok=True)
    file_path = os.path.join(TEMP_DIR, doc.file_name)
    file_obj = await context.bot.get_file(doc.file_id)
    await file_obj.download_to_drive(file_path)

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        import parsers # type: ignore
        source_type = parsers.detect_source(data)
        drama_info = parsers.parse_json_data(data, source_type, doc.file_name)
        session_id = f"{update.effective_user.id}_{update.message.message_id}"
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
        text += f"Lanjut download?"

        keyboard = [[
            InlineKeyboardButton("✅ Ya, Download", callback_data=f"dl_{session_id}"),
            InlineKeyboardButton("❌ Batal", callback_data=f"cancel_{session_id}")
        ]]
        
        if drama_info.get('cover'):
            await update.message.reply_photo(photo=drama_info['cover'], caption=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        else:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    except Exception as e: await update.message.reply_text(f"❌ Error JSON: {str(e)}")
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
        if session: shutil.rmtree(session['session_dir'], ignore_errors=True)
        await query.edit_message_caption("❌ Proses dibatalkan.") if query.message.caption else await query.edit_message_text("❌ Proses dibatalkan.")
        
    elif data.startswith("dl_"):
        session_id = data.split("dl_")[1]
        session = user_sessions.get(session_id)
        if not session: return await query.edit_message_text("⚠️ Sesi habis.")
            
        drama_info = session['drama_info']
        total = drama_info['total_ep']
        title = drama_info['title']
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
                
                try:
                    source = session['source']
                    if source == "vigloo":
                        cookies = ep.get('cookies', {})
                        headers = {"Cookie": "; ".join([f"{k}={v}" for k, v in cookies.items()])} if cookies else {}
                        success = await downloader.download_video_ytdlp(url, output_path, headers)
                    else:
                        success = await downloader.download_video_ytdlp(url, output_path)
                    
                    if success:
                        progress[0] += 1
                        session['downloaded'].append(output_path)
                    else:
                        progress[1] += 1
                        session['failed_list'].append(f"EP{ep_num}")
                except Exception:
                    progress[1] += 1
                    session['failed_list'].append(f"EP{ep_num}")

        async def update_status_loop():
            while (progress[0] + progress[1]) < total:
                done = progress[0] + progress[1]
                text = (f"⬇️ <b>DOWNLOAD:</b> {html.escape(title)}\n"
                        f"📺 Progress: {done}/{total}\n"
                        f"{make_progress_bar(done, total)}\n"
                        f"✅ Selesai: {progress[0]} | ❌ Gagal: {progress[1]}")
                try:
                    await query.edit_message_caption(text, parse_mode='HTML') if query.message.caption else await query.edit_message_text(text, parse_mode='HTML')
                except Exception: pass
                await asyncio.sleep(4)

        sem = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
        tasks = [download_task(idx, ep, sem) for idx, ep in enumerate(drama_info['episodes'])]
        status_task = asyncio.create_task(update_status_loop())
        await asyncio.gather(*tasks)
        status_task.cancel()

        final_text = (f"⬇️ <b>DOWNLOAD SELESAI</b>\n"
                      f"📦 {html.escape(title)}\n"
                      f"✅ Berhasil: {progress[0]} | ❌ Gagal: {progress[1]}\n\nPilih format upload:")
        keyboard = [[InlineKeyboardButton("📦 MKV", callback_data=f"up_mkv_{session_id}"), 
                    InlineKeyboardButton("🎬 MP4", callback_data=f"up_mp4_{session_id}")],
                   [InlineKeyboardButton("❌ Batal Upload", callback_data=f"cancel_{session_id}")]]
        try:
            await query.edit_message_caption(final_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML') if query.message.caption else await query.edit_message_text(final_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        except Exception: pass

    elif data.startswith("up_"):
        parts = data.split("_", 2)
        target_format, session_id = parts[1].lower(), parts[2]
        # JALANKAN DI BACKGROUND AGAR BOT BISA MERESPON USER LAIN
        asyncio.create_task(process_upload_task(update, context, query, session_id, target_format))

async def process_upload_task(update: Update, context: ContextTypes.DEFAULT_TYPE, query: Any, session_id: str, target_format: str):
    session = user_sessions.get(session_id)
    if not session: return
    
    files = session['downloaded']
    if not files: return
    files.sort()
    
    title = session['drama_info']['title']
    uploaded, failed_up = 0, 0
    chat_id = update.effective_chat.id # type: ignore

    for idx, filepath in enumerate(files):
        current = idx + 1
        progress_text = (f"⬆️ <b>PROSES UPLOAD</b>\n"
                        f"📦 {html.escape(title)}\n"
                        f"📺 Format: {target_format.upper()}\n"
                        f"🚀 File: {current}/{len(files)}\n"
                        f"{make_progress_bar(current, len(files))}\n"
                        f"✅ Berhasil: {uploaded}")
        try:
            await query.edit_message_caption(progress_text, parse_mode='HTML') if query.message.caption else await query.edit_message_text(progress_text, parse_mode='HTML')
        except Exception: pass

        upload_path = filepath
        if not filepath.lower().endswith(f".{target_format}"):
            converted = filepath.rsplit('.', 1)[0] + f".{target_format}"
            try:
                if os.path.exists(converted): os.remove(converted) 
                
                # NON-BLOCKING FFMPEG (Agar bot tidak membeku)
                proc = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-y", "-i", filepath, "-c", "copy", converted,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                await proc.wait()
                
                if os.path.exists(converted) and os.path.getsize(converted) > 1000:
                    upload_path = converted
            except Exception: pass

        try:
            with open(upload_path, 'rb') as f:
                await context.bot.send_document(
                    chat_id=chat_id, 
                    document=f,
                    filename=os.path.basename(upload_path),
                    caption=f"📺 {html.escape(title)} - Episode {current}",
                    parse_mode='HTML', 
                    write_timeout=600
                )
            uploaded += 1
        except Exception: failed_up += 1
        finally:
            if os.path.exists(upload_path): os.remove(upload_path)
            if upload_path != filepath and os.path.exists(filepath): os.remove(filepath)
        await asyncio.sleep(1)

    drama_info = session.get('drama_info', {})
    synopsis = drama_info.get('sinopsis', 'Tidak ada sinopsis.')
    syn_short = synopsis[:500] + ('...' if len(synopsis) > 500 else '')
    
    report = (f"✅ <b>PROSES SELESAI!</b>\n"
              f"━━━━━━━━━━━━━━━━━━━━\n"
              f"📦 <b>{html.escape(title)}</b>\n"
              f"📺 Format: {target_format.upper()}\n"
              f"📖 Sinopsis:\n<i>{html.escape(syn_short)}</i>\n"
              f"━━━━━━━━━━━━━━━━━━━━\n"
              f"✅ Berhasil: {uploaded} | ❌ Gagal: {failed_up}\n"
              f"🎉 Selesai!")

    try:
        finish_msg = f"✅ <b>Selesai mengirim {uploaded} file ke Telegram.</b>\nLaporan ada di bawah 👇"
        await query.edit_message_caption(finish_msg, parse_mode='HTML') if query.message.caption else await query.edit_message_text(finish_msg, parse_mode='HTML')
        await context.bot.send_message(chat_id=chat_id, text=report, parse_mode='HTML')
    except Exception: pass
    
    user_sessions.pop(session_id, None)
    shutil.rmtree(session['session_dir'], ignore_errors=True)

# --- RESTART & UPDATE ---
async def restart_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user: return
    try:
        if update.effective_user.id != getattr(config_file, 'OWNER_ID', 0): return
    except: return
    await update.message.reply_text("🔄 Restarting...") # type: ignore
    os.execv(sys.executable, [sys.executable] + sys.argv)

async def update_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message: return
    try:
        if update.effective_user.id != getattr(config_file, 'OWNER_ID', 0): return
    except: return
    msg = await update.message.reply_text("🔄 Updating...")
    try:
        subprocess.run(["git", "pull"], check=True)
        await msg.edit_text("✅ Updated! Restarting...")
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e: await msg.edit_text(f"❌ Failed: {e}")

from telegram.request import HTTPXRequest
def main():
    if not BOT_TOKEN: return print("❌ BOT_TOKEN KOSONG!")
    t_request = HTTPXRequest(connect_timeout=30, read_timeout=30)
    app = ApplicationBuilder().token(BOT_TOKEN).concurrent_updates(True).request(t_request).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("restart", restart_bot))
    app.add_handler(CommandHandler("update", update_bot))
    app.add_handler(MessageHandler(filters.Document.FileExtension("json"), handle_document))
    app.add_handler(CallbackQueryHandler(handle_callback))
    print("Bot is running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__': main()
