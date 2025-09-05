import asyncio
import requests
from pathlib import Path
from playwright.async_api import async_playwright
import subprocess
import re
from urllib.parse import urlparse, unquote
import m3u8

def sanitize_filename(filename: str, fallback_prefix="video") -> str:
    """Remove invalid characters for Windows filenames and add fallback if needed."""
    cleaned = re.sub(r'[\\/*?:"<>|]', "_", filename).strip()
    if not cleaned or cleaned.startswith("."):
        cleaned = f"{fallback_prefix}.mp4"
    return cleaned

def unique_filename(base: str) -> str:
    """Ensure filename is unique by appending counter if needed."""
    base_path = Path(base)
    if not base_path.exists():
        return str(base_path)
    stem, suffix = base_path.stem, base_path.suffix
    counter = 1
    while True:
        new_name = f"{stem}_{counter}{suffix}"
        new_path = base_path.with_name(new_name)
        if not new_path.exists():
            return str(new_path)
        counter += 1

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
        parsed = urlparse(url)
        filename = unquote(Path(parsed.path).name)
    filename = sanitize_filename(filename)
    filename = unique_filename(filename)
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
        print(f"\nDownload completed ✅ Saved as: {filepath.name}")
        return filepath
    except Exception as e:
        print(f"\nError downloading video: {e}")
        return None

def download_m3u8(m3u8_url, filename):
    """Download HLS stream via ffmpeg"""
    filename = sanitize_filename(filename)
    filename = unique_filename(filename)
    filename = Path(filename).with_suffix(".mp4").resolve()

    # Get highest resolution from m3u8 master playlist
    try:
        r = requests.get(m3u8_url)
        master = m3u8.loads(r.text)
        if master.is_variant:
            best = max(master.playlists, key=lambda p: (p.stream_info.resolution or (0,0)))
            m3u8_url = best.uri
            print(f"Selected highest resolution stream: {best.stream_info.resolution}")
    except Exception as e:
        print(f"Could not parse m3u8 for resolutions: {e}")

    print(f"Downloading HLS stream via ffmpeg to {filename}")
    cmd = ["ffmpeg", "-y", "-i", m3u8_url, "-c", "copy", str(filename)]
    subprocess.run(cmd)
    print("Download completed ✅")

def get_video_duration(url):
    """Get video duration in seconds using ffprobe"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "format=duration", "-of",
             "default=noprint_wrappers=1:nokey=1", url],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            return float(result.stdout.strip())
    except:
        pass
    return None

async def capture_video_url(page_url):
    """
    Use headed Playwright to capture all .mp4 or .m3u8 URLs,
    fetch page title for better filenames,
    and automatically handle popups/consent/play buttons
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        video_urls = []
        page_title = None

        async def handle_route(route):
            url = route.request.url
            if re.search(r"\.mp4|\.m3u8", url):
                if url not in video_urls:
                    video_urls.append(url)
            await route.continue_()

        page = await context.new_page()
        await context.route("**/*", handle_route)
        await page.goto(page_url)
        print("Page loaded. Attempting to close popups automatically...")

        # Grab page title
        try:
            page_title = await page.title()
            if page_title:
                page_title = sanitize_filename(page_title.strip()) + ".mp4"
        except:
            pass

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
        await asyncio.sleep(25)
        await browser.close()
        return video_urls, page_title

if __name__ == "__main__":
    page_url = input("Enter the video page URL: ").strip()
    if not page_url:
        print("No URL provided.")
        exit()

    print("Opening Chrome to capture video URLs...")
    video_urls, page_title = asyncio.run(capture_video_url(page_url))
    if not video_urls:
        print("Failed to capture video URLs automatically.")
        exit()

    # Gather info for user
    videos_info = []
    for idx, url in enumerate(video_urls, 1):
        duration = None
        if url.endswith(".mp4"):
            duration = get_video_duration(url)
        elif url.endswith(".m3u8"):
            # Try to get duration from m3u8 playlist
            try:
                r = requests.get(url)
                m = m3u8.loads(r.text)
                duration = sum([seg.duration for seg in m.segments]) if m.segments else None
            except:
                pass
        videos_info.append((idx, url, duration))

    # Show list to user
    print("\nAvailable videos found:")
    for idx, url, duration in videos_info:
        dur_str = f"{int(duration//60)}m {int(duration%60)}s" if duration else "Unknown"
        print(f"{idx}. {url} (Duration: {dur_str})")

    # Ask user which video to download
    selection = input("\nEnter the number of the video to download (comma separated for multiple): ")
    selected_indices = set(int(s.strip()) for s in selection.split(",") if s.strip().isdigit())

    for idx, url, _ in videos_info:
        if idx in selected_indices:
            if url.endswith(".m3u8"):
                title = page_title or f"video_{idx}"
                download_m3u8(url, f"{title}.mp4")
            else:
                parsed = urlparse(url)
                filename = unquote(Path(parsed.path).name)
                if filename.lower() in ("video.mp4", "file.mp4", ""):
                    filename = page_title or f"video_{idx}.mp4"
                download_with_requests(url, filename=filename)
