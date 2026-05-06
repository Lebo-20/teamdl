from telethon.sync import TelegramClient
import config
import asyncio

async def main():
    client = TelegramClient('test_session', config.API_ID, config.API_HASH)
    await client.start(bot_token=config.BOT_TOKEN)
    await client.send_message(6337959812, "🔔 **Bot Testing:** Saya sedang aktif sekarang. Silakan coba kirim file JSON Anda lagi!")
    await client.disconnect()

if __name__ == "__main__":
    import os
    # We need to be careful about session file if bot is running. 
    # But test_session is different.
    asyncio.run(main())
