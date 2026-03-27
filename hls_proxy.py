import os
import aiohttp
from aiohttp import web
import urllib.parse
import re
import asyncio

class HLSProxy:
    def __init__(self, host="0.0.0.0", port=8001):
        self.host = host
        self.port = port
        self.app = web.Application()
        self.app.router.add_get('/proxy', self.handle_proxy)
        self.runner = None
        self.site = None

    async def start(self):
        """Start the HLS Proxy server."""
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()
        print(f"✅ HLS Proxy Running on http://{self.host}:{self.port}")

    async def stop(self):
        """Stop the HLS Proxy server."""
        if self.runner:
            await self.runner.cleanup()

    async def handle_proxy(self, request):
        """The main proxy handler for m3u8 and ts segments."""
        target_url = request.query.get('url')
        if not target_url:
            return web.json_response({"error": "URL parameter required"}, status=400)

        # Decode URL
        target_url = urllib.parse.unquote(target_url)
        parsed_target = urllib.parse.urlparse(target_url)
        
        # Deteksi Platform untuk Header Spesifik
        is_dramabox = "dramaboxdb.com" in parsed_target.hostname or "dramabox" in target_url
        
        if is_dramabox:
            # DramaBox CDN requires Dalvik UA and specific headers
            ua = "Dalvik/2.1.0 (Linux; U; Android 13; M2101K7AG Build/TKQ1.221013.002)"
            headers = {
                "User-Agent": ua,
                "Referer": "https://dramaboxdb.com/",
                "Origin": "https://dramaboxdb.com",
                "Accept": "*/*",
                "Accept-Encoding": "identity",
                "Connection": "keep-alive"
            }
        else:
            # Default UA for other platforms
            ua = "Mozilla/5.0 (Linux; Android 13; Redmi Note 5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36"
            headers = {
                "User-Agent": ua,
                "Accept": "*/*",
                "Accept-Encoding": "identity",
                "Connection": "keep-alive"
            }

        # Handle range headers if provided by player/downloader
        if request.headers.get('Range'):
            headers['Range'] = request.headers['Range']

        # Disable SSL verification for maximum compatibility with proxy worker domains
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            try:
                async with session.get(target_url, headers=headers, timeout=60) as response:
                    content_type = response.headers.get('Content-Type', '')
                    
                    # Case 1: m3u8 playlist -> Rewrite every line to pass through this proxy
                    if '.m3u8' in target_url or 'application/vnd.apple.mpegurl' in content_type:
                        body_encoded = await response.read()
                        try:
                            body = body_encoded.decode('utf-8')
                        except:
                            body = body_encoded.decode('latin-1', errors='replace')
                        
                        # Determine base path for relative URLs
                        parsed_target = urllib.parse.urlparse(target_url)
                        # Extract folder path
                        last_slash = target_url.rsplit('/', 1)[0]
                        base_folder = last_slash + '/' if '/' in target_url else target_url
                        
                        # Current proxy address
                        # Using request.host to be dynamic (ip or domain)
                        current_host = request.host
                        proxy_base = f"http://{current_host}/proxy?url="
                        
                        new_lines = []
                        for line in body.split('\n'):
                            line = line.strip()
                            if not line or line.startswith('#'):
                                # Handle URI in #EXT-X-KEY or #EXT-X-MAP
                                if line.startswith('#EXT-X-KEY') or line.startswith('#EXT-X-MAP'):
                                    uri_match = re.search(r'URI="([^"]+)"', line)
                                    if uri_match:
                                        old_uri = uri_match.group(1)
                                        if not old_uri.startswith('http'):
                                            new_uri = urllib.parse.urljoin(base_folder, old_uri)
                                        else:
                                            new_uri = old_uri
                                        line = line.replace(old_uri, f"{proxy_base}{urllib.parse.quote(new_uri)}")
                                
                                new_lines.append(line)
                                continue
                            
                            # Rewrite segment URL
                            if line.startswith('http'):
                                final_url = line
                            else:
                                final_url = urllib.parse.urljoin(base_folder, line)
                                
                            new_lines.append(f"{proxy_base}{urllib.parse.quote(final_url)}")
                        
                        return web.Response(text='\n'.join(new_lines), content_type='application/vnd.apple.mpegurl')
                    
                    # Case 2: TS segments or other binary data -> Stream it directly
                    else:
                        # Prepare dynamic headers for binary data
                        res_headers = {
                            'Content-Type': content_type,
                            'Access-Control-Allow-Origin': '*'
                        }
                        # Pass through important headers
                        for h in ['Content-Length', 'Content-Range', 'Accept-Ranges']:
                            if h in response.headers:
                                res_headers[h] = response.headers[h]

                        res = web.StreamResponse(status=response.status, headers=res_headers)
                        await res.prepare(request)
                        
                        async for chunk in response.content.iter_chunked(1024 * 128):
                            await res.write(chunk)
                        return res
                        
            except Exception as e:
                print(f"HLS Proxy Error: {e} for URL: {target_url}")
                return web.json_response({"error": str(e)}, status=502)

# Global Instance
hls_proxy = HLSProxy()
