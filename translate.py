import argparse
import base64
import io
import json
import os
import re
import shutil
import sys
from pathlib import Path

import anthropic
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont

BANNER_PAGE_URL = "https://hiroba.dqx.jp/sc/rotationbanner"
BANNER_BASE_URL = "https://cache.hiroba.dqx.jp/dq_resource/rotationbanner/"

ROOT = Path(__file__).parent
WORK_DIR = ROOT / "work"
TRANSLATED_DIR = ROOT / "translated"
DOCS_DIR = ROOT / "docs"
LIVE_DIR = DOCS_DIR / "live"
_bold = ROOT / "fonts" / "DejaVuSans-Bold.ttf"
_regular = ROOT / "fonts" / "DejaVuSans.ttf"
FONT_PATH = _bold if _bold.exists() else _regular
CSS_PATH = DOCS_DIR / "css" / "banner.css"
JS_PATH = DOCS_DIR / "js" / "slideshow.js"

SYSTEM_PROMPT = """\
You are an OCR and translation assistant. When given a banner image from a Japanese video game website, identify all Japanese text visible in the image and translate it to English.

Return your response as a JSON object with this exact structure:
{
  "blocks": [
    {
      "original": "original Japanese text",
      "translated": "English translation",
      "bbox": [x1, y1, x2, y2]
    }
  ]
}

Where bbox is the bounding box of the text region in pixels: [left, top, right, bottom].
If there is no Japanese text in the image, return {"blocks": []}.
Return only the JSON object with no additional text, markdown, or code fences."""

BANNER_CSS = """\
* {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
}

body {
    background: #1a1a1a;
    display: flex;
    justify-content: center;
    align-items: center;
    min-height: 100vh;
}

.slideshow-container {
    max-width: 728px;
    width: 100%;
}

.slides-wrapper {
    position: relative;
}

.slide {
    display: none;
}

.slide.active {
    display: block;
}

.slide img {
    width: 100%;
    height: auto;
    display: block;
}

.dots-container {
    text-align: center;
    padding: 10px 0;
    background: #111;
}

.dot {
    display: inline-block;
    width: 20px;
    height: 8px;
    background: #555;
    margin: 0 4px;
    cursor: pointer;
    transition: background 0.3s;
}

.dot.active,
.dot:hover {
    background: #ccc;
}
"""

SLIDESHOW_JS = """\
(function () {
    var INTERVAL = 5000;
    var current = 0;
    var timer;

    var slides = document.querySelectorAll('.slide');
    var dots = document.querySelectorAll('.dot');

    if (slides.length === 0) return;

    function showSlide(n) {
        slides[current].classList.remove('active');
        dots[current].classList.remove('active');
        current = ((n % slides.length) + slides.length) % slides.length;
        slides[current].classList.add('active');
        dots[current].classList.add('active');
    }

    function startTimer() {
        timer = setInterval(function () { showSlide(current + 1); }, INTERVAL);
    }

    function resetTimer() {
        clearInterval(timer);
        startTimer();
    }

    dots.forEach(function (dot) {
        dot.addEventListener('click', function () {
            showSlide(parseInt(dot.getAttribute('data-index'), 10));
            resetTimer();
        });
    });

    startTimer();
}());
"""


def scrape_banners(session: requests.Session) -> list[tuple[str, str]]:
    resp = session.get(BANNER_PAGE_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    seen: set[str] = set()
    banners: list[tuple[str, str]] = []
    for li in soup.select("li.slide"):
        img = li.find("img")
        if not img:
            continue
        m = re.search(r'banner_rotation_\d{8}_\d{3}\.jpg', img.get("src", ""))
        if not m:
            continue
        filename = m.group(0)
        if filename in seen:
            continue
        seen.add(filename)
        link = ""
        a = li.find("a")
        if a:
            lm = re.search(r"link=(https?://[^']+)", a.get("href", ""))
            if lm:
                link = lm.group(1)
        banners.append((filename, link))
    return banners


def download_image(session: requests.Session, url: str, dest_dir: Path) -> Path | None:
    filename = url.rsplit("/", 1)[-1]
    dest = dest_dir / filename
    if dest.exists():
        return dest
    resp = session.get(url, stream=True, timeout=30)
    if resp.status_code != 200:
        print(f"  warn: download failed {url} ({resp.status_code})")
        return None
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
    return dest


def claude_ocr_translate(image_path: Path, client: anthropic.Anthropic) -> list[dict]:
    with Image.open(image_path) as img:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=92)
        image_data = base64.standard_b64encode(buf.getvalue()).decode("utf-8")

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": "Please identify and translate all Japanese text in this banner image.",
                    },
                ],
            }
        ],
    )

    raw = response.content[0].text.strip()
    # strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    data = json.loads(raw)
    blocks = []
    for item in data.get("blocks", []):
        bbox = item.get("bbox", [])
        if len(bbox) != 4:
            continue
        x1, y1, x2, y2 = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
        blocks.append({
            "text": item.get("original", ""),
            "translated_text": item.get("translated", ""),
            "bbox": (x1, y1, x2, y2),
        })
    return blocks



def render_overlay(image: Image.Image, blocks: list[dict]) -> Image.Image:
    img = image.copy().convert("RGB")

    if not FONT_PATH.exists():
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default()
        for block in blocks:
            text = block.get("translated_text", "")
            if text:
                x0, y0, _, _ = block["bbox"]
                draw.text((x0, y0), text, fill=(255, 255, 255), font=font)
        return img

    # render text at 2x on a transparent layer, then downscale for crisp edges
    scale = 2
    w, h = img.size
    overlay = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for block in blocks:
        text = block.get("translated_text", "")
        if not text:
            continue
        x0, y0, x1, y1 = block["bbox"]
        bbox_w = max(x1 - x0, 1)
        cx = (x0 + x1) // 2
        cy = (y0 + y1) // 2

        font = None
        for size in range(20, 7, -1):
            f = ImageFont.truetype(str(FONT_PATH), size * scale)
            try:
                text_w = f.getlength(text)
            except AttributeError:
                bb = f.getbbox(text)
                text_w = bb[2] - bb[0] if bb else size * scale * len(text)
            if text_w <= bbox_w * scale * 0.95:
                font = f
                break
        if font is None:
            font = ImageFont.truetype(str(FONT_PATH), 8 * scale)

        draw.text(
            (cx * scale, cy * scale), text,
            fill=(255, 255, 255, 255), font=font, anchor="mm",
            stroke_width=2, stroke_fill=(0, 0, 0, 255),
        )

    overlay = overlay.resize((w, h), Image.LANCZOS)
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    return img


def process_image(image_path: Path, client: anthropic.Anthropic) -> bool:
    dest = TRANSLATED_DIR / image_path.name
    try:
        img = Image.open(image_path)
        blocks = claude_ocr_translate(image_path, client)
        if not blocks:
            print(f"  warn: no translatable text found in {image_path.name}")
            return False

        result = render_overlay(img, blocks)
        result.save(dest, "JPEG", quality=92)
        print(f"  ok: {image_path.name} ({len(blocks)} text blocks)")
        return True
    except anthropic.APIError as e:
        print(f"  error: Claude API error for {image_path.name}: {e}")
        raise
    except Exception as e:
        print(f"  error: failed to process {image_path.name}: {e}")
        return False


def sync_live(banners: list[tuple[str, str]]) -> None:
    translated_set = {f.name for f in TRANSLATED_DIR.iterdir() if f.suffix == ".jpg"}
    live_candidates = {fn for fn, _ in banners if fn in translated_set}
    links = {fn: link for fn, link in banners if fn in live_candidates}

    for filename in live_candidates:
        dest = LIVE_DIR / filename
        src = TRANSLATED_DIR / filename
        if not dest.exists() or src.stat().st_mtime > dest.stat().st_mtime:
            print(f"  live: updating {filename}")
            shutil.copy2(src, dest)

    for f in list(LIVE_DIR.iterdir()):
        if f.suffix == ".jpg" and f.name not in live_candidates:
            print(f"  live: removing {f.name} (no longer in rotation)")
            f.unlink()

    links_path = LIVE_DIR / "links.json"
    with open(links_path, "w", newline="\n", encoding="utf-8") as f:
        json.dump(links, f, ensure_ascii=False, indent=2)


def generate_index_html() -> None:
    images = sorted(f.name for f in LIVE_DIR.iterdir() if f.suffix == ".jpg")

    links_path = LIVE_DIR / "links.json"
    links: dict[str, str] = {}
    if links_path.exists():
        with open(links_path, encoding="utf-8") as f:
            links = json.load(f)

    if not images:
        body = '  <p style="color:#fff;text-align:center;padding:2em">No translated banners yet.</p>\n'
        script = ""
    else:
        slide_lines = []
        dot_lines = []
        for i, name in enumerate(images):
            active = " active" if i == 0 else ""
            img_tag = f'<img src="live/{name}" alt="Banner {i + 1}">'
            link = links.get(name, "")
            if link:
                inner = f'<a href="{link}" target="_blank" rel="noopener">{img_tag}</a>'
            else:
                inner = img_tag
            slide_lines.append(f'      <div class="slide{active}">{inner}</div>')
            dot_lines.append(f'      <span class="dot{active}" data-index="{i}"></span>')
        body = (
            '  <div class="slideshow-container">\n'
            '    <div class="slides-wrapper">\n'
            + "\n".join(slide_lines) + "\n"
            + '    </div>\n'
            + '    <div class="dots-container">\n'
            + "\n".join(dot_lines) + "\n"
            + '    </div>\n'
            + '  </div>\n'
        )
        script = '  <script src="js/slideshow.js"></script>\n'

    html = (
        '<!DOCTYPE html>\n'
        '<html lang="en">\n'
        '<head>\n'
        '  <meta charset="UTF-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        '  <title>DQX Rotation Banners (EN)</title>\n'
        '  <link rel="stylesheet" href="css/banner.css">\n'
        '</head>\n'
        '<body>\n'
        + body
        + script
        + '</body>\n'
        '</html>\n'
    )

    with open(DOCS_DIR / "index.html", "w", newline="\n", encoding="utf-8") as f:
        f.write(html)


def write_static_assets() -> None:
    CSS_PATH.parent.mkdir(parents=True, exist_ok=True)
    JS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CSS_PATH.exists():
        with open(CSS_PATH, "w", newline="\n", encoding="utf-8") as f:
            f.write(BANNER_CSS)
    if not JS_PATH.exists():
        with open(JS_PATH, "w", newline="\n", encoding="utf-8") as f:
            f.write(SLIDESHOW_JS)


def main() -> None:
    parser = argparse.ArgumentParser(description="DQX rotation banner OCR+translation pipeline")
    parser.add_argument("--dry-run", action="store_true", help="print actions without writing files")
    parser.add_argument("--force", action="store_true", help="re-translate images already in translated/")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("error: ANTHROPIC_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)

    for d in (WORK_DIR, TRANSLATED_DIR, LIVE_DIR):
        d.mkdir(parents=True, exist_ok=True)

    write_static_assets()

    if not FONT_PATH.exists():
        print(f"warn: font not found at {FONT_PATH} — overlay text will use bitmap fallback")

    client = anthropic.Anthropic(api_key=api_key)

    print("fetching rotation banner page...")
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (compatible; dqx-banner-bot/1.0)"

    try:
        banners = scrape_banners(session)
    except Exception as e:
        print(f"error: failed to scrape banner page: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"found {len(banners)} banners in rotation:")
    for filename, link in banners:
        print(f"  {filename}" + (f" -> {link}" if link else ""))

    if args.dry_run:
        print("\n[dry-run] skipping download, translation, and sync")
        return

    translated_set = {f.name for f in TRANSLATED_DIR.iterdir() if f.suffix == ".jpg"}

    success = 0
    skipped = 0
    failed = 0

    for filename, _ in banners:
        if filename in translated_set and not args.force:
            print(f"skip: {filename} (already translated)")
            skipped += 1
            continue

        url = BANNER_BASE_URL + filename
        print(f"downloading: {filename}")
        work_path = download_image(session, url, WORK_DIR)
        if work_path is None:
            failed += 1
            continue

        print(f"processing: {filename}")
        try:
            ok = process_image(work_path, client)
        except anthropic.APIError as e:
            print(f"error: Claude API error, stopping: {e}", file=sys.stderr)
            failed += 1
            break
        if ok:
            success += 1
        else:
            failed += 1

    print("\nsyncing live/...")
    sync_live(banners)

    print("generating index.html...")
    generate_index_html()

    print(f"\ndone: {success} translated, {skipped} skipped, {failed} failed")


if __name__ == "__main__":
    main()
