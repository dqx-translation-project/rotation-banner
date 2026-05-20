# DQX Rotation Banner Translator

Automatically fetches the current rotation banners from the [Dragon Quest X Hiroba](https://hiroba.dqx.jp/sc/rotationbanner) site, translates the Japanese text to English using Claude's vision API, and publishes the results as a GitHub Pages slideshow.

## How it works

1. Scrapes the current 13-banner rotation from the DQX Hiroba site every 6 hours
2. Downloads any banners not yet translated
3. Sends each image to Claude (vision) to OCR and translate the Japanese text
4. Overlays the English translation directly on the image
5. Publishes the translated banners to GitHub Pages as a rotating slideshow
6. Each banner links to its original announcement page on Hiroba

Banners already translated are skipped on subsequent runs. If a banner is removed from the rotation, it is removed from the live site but kept in `translated/` for reference.

## Setup

### Requirements

- Python 3.13+
- An [Anthropic API key](https://console.anthropic.com)

### Local usage

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

$env:ANTHROPIC_API_KEY = "sk-ant-..."
python translate.py
```

Use `--dry-run` to verify banner scraping without downloading or translating anything. Use `--force` to re-translate banners already in `translated/`.

### GitHub Actions

The workflow runs automatically every 6 hours. To enable it:

1. Add your Anthropic API key as a repository secret named `ANTHROPIC_API_KEY`
2. Enable GitHub Pages in repo Settings → Pages → Deploy from branch `main`, folder `/docs`

Translated images are auto-committed to `main` with `[skip ci]` to prevent a loop. GitHub Pages rebuilds automatically on each push.

## Repository layout

```
translated/     # all successfully translated banner images (permanent)
docs/
  live/         # current rotation images served by GitHub Pages
  index.html    # auto-generated slideshow (rebuilt each run)
  css/
  js/
fonts/          # DejaVuSans-Bold.ttf used for text overlay
work/           # temp download directory (gitignored)
translate.py    # main pipeline script
```
