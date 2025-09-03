import asyncio
import requests
from pathlib import Path
from playwright.async_api import async_playwright
import subprocess
import re

def download_with_requests(url, filename=None, chunk_size=1024*1024):
    """Download direct .mp4 via requests with progress"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/117.0.0.0 Safari/537.36",
        "Referer": url,
        "Accept": "video/mp4,video/*;q=0.9,*/*;q=0.8",
    }
    if filename is None:
        filename = url.split("/")[-1]
    filepath = Path(filename).resolve()
    try:
        with requests.get(url, headers=headers, stream=True, timeout=15) as response:
            response.raise_for_status()
            total_size = int(response.headers.get("content-length", 0))
            downloaded = 0
            with open(filepath, "wb") as f:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        percent = downloaded * 100 / total_size if total_size else 0
                        print(f"\rProgress: {percent:.2f}%", end="")
        print("\nDownload completed ✅")
        return filepath
    except Exception as e:
        print(f"\nError downloading video: {e}")
        return None

def download_m3u8(m3u8_url, filename):
    """Download HLS stream via ffmpeg"""
    filename = Path(filename).with_suffix(".mp4").resolve()
    print(f"Downloading HLS stream via ffmpeg to {filename}")
    cmd = ["ffmpeg", "-y", "-i", m3u8_url, "-c", "copy", str(filename)]
    subprocess.run(cmd)
    print("Download completed ✅")

async def capture_video_url(page_url):
    """
    Use headed Playwright to capture .mp4 or .m3u8 URL,
    automatically handle popups/consent/play buttons
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        video_url = None

        async def handle_route(route):
            nonlocal video_url
            url = route.request.url
            if re.search(r"\.mp4|\.m3u8", url):
                if not video_url:  # take first matching URL
                    video_url = url
            await route.continue_()

        page = await context.new_page()
        await context.route("**/*", handle_route)
        await page.goto(page_url)
        print("Page loaded. Attempting to close popups automatically...")

        # Auto-click common popup/consent/play buttons
        popup_selectors = [
            'button:has-text("Accept")',
            'button:has-text("I Agree")',
            'button:has-text("Close")',
            'button:has-text("Play")',
        ]
        for selector in popup_selectors:
            try:
                element = await page.query_selector(selector)
                if element:
                    await element.click()
                    print(f"Clicked popup: {selector}")
            except:
                pass

        print("Waiting for video requests to fire...")
        await asyncio.sleep(20)  # Wait for network requests
        await browser.close()
        return video_url

if __name__ == "__main__":
    page_url = input("Enter the video page URL: ").strip()
    if not page_url:
        print("No URL provided.")
        exit()

    print("Opening Chrome to capture video URL...")
    video_url = asyncio.run(capture_video_url(page_url))
    if not video_url:
        print("Failed to capture .mp4 or .m3u8 URL automatically.")
        exit()

    print(f"Found video URL: {video_url}")

    if video_url.endswith(".m3u8"):
        title = Path(page_url.split("/")[-1]).stem
        download_m3u8(video_url, f"{title}.mp4")
    else:
        filename = Path(video_url.split("/")[-1]).name
        download_with_requests(video_url, filename=filename)
