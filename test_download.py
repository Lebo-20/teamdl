import asyncio
import json
import os
import parsers
import downloader

async def main():
    json_path = "c:/teamdl/poincinta_jodoh_memanggil.json"
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    source_type = parsers.detect_source(data)
    print(f"Detected Platform: {source_type}")
    
    drama_info = parsers.parse_json_data(data, source_type)
    print(f"Title: {drama_info['title']}")
    
    episodes = drama_info['episodes']
    if not episodes:
        print("No episodes found.")
        return
    
    ep = episodes[0]
    print(f"Downloading Episode {ep['num']}...")
    
    output_path = f"c:/teamdl/test_download_ep{ep['num']}.mp4"
    success = await downloader.download_video_ytdlp(ep['url'], output_path)
    
    if success:
        print(f"✅ Success! Saved to: {output_path}")
    else:
        print("❌ Download failed.")

if __name__ == "__main__":
    asyncio.run(main())
