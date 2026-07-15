# Maintainer: Nyarch Linux Team

pkgname=nyarchassistant
pkgver=1.4.5
pkgrel=1
pkgdesc="Nyarch Assistant - Your ultimate Waifu AI Assistant (Newelle Fork)"
arch=("any")
url="https://github.com/NyarchLinux/NyarchAssistant"
license=('GPL-3.0-or-later')

depends=(
  libadwaita
  gtksourceview5
  vte4
  webkitgtk-6.0
  dconf
  python
  python-gobject
  python-requests
  python-requests-toolbelt
  python-pillow
  python-beautifulsoup4
  python-pydantic
  python-pydub
  python-matplotlib
)

optdepends=(
  # STT
  "python-openwakeword: wakeword detection"
  "python-vosk: offline STT"
  "python-openai-whisper: Whisper STT"
  "python-speechrecognition: Sphinx/Google/Vosk STT"
  "pulseaudio: audio input"
  # TTS
  "python-edge-tts: Edge TTS"
  "python-gtts: Google TTS"
  "python-voicevox-client: Voicevox TTS (Japanese)"
  "python-elevenlabs: ElevenLabs TTS"
  "python-kokoro-onnx: Kokoro TTS engine"
  "python-soundfile: Kokoro audio backend"
  "espeak-ng: eSpeak TTS"
  # LLM
  "python-openai: OpenAI-compatible LLM handlers"
  "python-ollama: Ollama LLM handler"
  "python-google-genai: Google Gemini handler"
  "python-anthropic: Claude handler"
  # Embeddings / RAG
  "python-wordllama: local embedding model (lightweight)"
  "python-model2vec: local embedding model (multilingual)"
  "python-faiss: vector store for RAG/memory"
  "python-llama-index-core: LlamaIndex RAG framework"
  "python-llama-index-readers-file: document reader for RAG"
  # Web search
  "python-duckduckgo-search: DuckDuckGo search"
  # Telegram
  "python-telegram-bot: Telegram bot interface"
  # Avatar
  "python-livepng: Live2D PNG avatar"
  # Other
  "python-pyaudio: audio recording/calls"
  "python-scikit-learn: ML features"
  "python-expandvars: config variable expansion"
  "python-tiktoken: token counting"
  "python-pylatexenc: LaTeX rendering"
  "python-lxml: advanced XML/HTML parsing"
  "python-lxml-html-clean: HTML sanitization"
  "python-newspaper: article extraction"
  "python-mcp: MCP integration"
  "python-markdownify: markdown conversion"
  "python-webrtcvad: VAD (alternative)"
  "python-pysilero-vad: VAD (PySilero)"
  "python-docx2txt: DOCX document reader"
  "python-tldextract: URL parsing"
  "python-feedparser: RSS parsing"
  "python-cssselect: HTML parsing"
  "python-httpx: MCP OAuth"
  "ollama: Ollama auto-serve"
  "portaudio: audio backend"
  "glib-networking: TLS/network for GLib"
  "gsettings-desktop-schemas: GSettings schemas"
  "adwaita-icon-theme: icon theme"
)

makedepends=("meson" "ninja" "pkg-config" "git" "desktop-file-utils" "glib2")

noextract=('pack.tar.xz')

source=(
  "nyarchassistant-$pkgver.tar.gz::$url/archive/$pkgver.tar.gz"
  "https://github.com/NyarchLinux/live2d-lipsync-viewer/releases/download/0.5/pack.tar.xz"
  "arch-chan.png::https://nyarchlinux.moe/acchan.png"
)

_srcdir="NyarchAssistant-$pkgver"
b2sums=('SKIP'
        'SKIP'
        'SKIP')

build() {
  arch-meson "$srcdir/$_srcdir" build
  meson compile -C build
}

package() {
  meson install -C build --destdir "$pkgdir"

  chmod +x "$pkgdir/usr/bin/nyarchassistant"

  install -Dm644 "$srcdir/$_srcdir/README.md" -t "$pkgdir/usr/share/doc/$pkgname"
  install -Dm644 "$srcdir/$_srcdir/COPYING"   -t "$pkgdir/usr/share/licenses/$pkgname"

  _live2d_web="$pkgdir/usr/share/nyarchassistant/data/live2d/web"
  mkdir -p "$_live2d_web"
  tar -xJf "$srcdir/pack.tar.xz" -C "$_live2d_web" --no-same-owner
  cp "$srcdir/arch-chan.png" "$_live2d_web/arch-chan.png"
}
