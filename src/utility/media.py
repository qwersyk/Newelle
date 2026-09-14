import os
import base64
from .message_chunk import get_message_chunks
import fnmatch
import mimetypes
import re
import gettext
import hashlib
import tempfile
from pathlib import Path
from urllib.parse import urlparse

_ = gettext.gettext


def file_matches_patterns(path: str, patterns: list[str]) -> bool:
    mime_type = mimetypes.guess_type(path)[0] or ""
    return any(fnmatch.fnmatch(path.lower(), pattern.lower()) for pattern in patterns if pattern != "plaintext") or (
        "plaintext" in patterns and (mime_type.startswith("text/") or path.lower().endswith(".conf"))
    )


def prepare_file_message(message: str) -> str:
    """Apply explicit attachment routing without changing saved history."""
    def replace(block):
        lang, body = block.group(1), block.group(2)
        if lang == "file_direct":
            return f"```file\n{body}\n```"
        if lang == "file_rag":
            names = ", ".join(os.path.basename(path.strip()) for path in body.splitlines() if path.strip())
            return f"[Documents provided through retrieval: {names}]"
        return block.group(0)
    return re.sub(r"```(\w*)[^\S\n]*\n(.*?)\n```", replace, message, flags=re.DOTALL)


def get_file_base64(file_path):
    """Encode an attachment without treating unknown MIME types as JPEG."""
    if file_path.startswith("data:"):
        return file_path
    mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    with open(file_path, "rb") as attachment:
        encoded = base64.b64encode(attachment.read()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def save_api_attachment(data: str, filename: str = "attachment", remote: bool = False) -> str:
    """Materialize API attachments in persistent app storage for saved chats.

    Never interpret API-supplied strings as local filesystem paths. Content
    hashes deduplicate attachments when clients resend the full history.
    """
    from gi.repository import GLib
    if not isinstance(filename, str):
        raise TypeError(_("Attachment filename must be a string"))
    if data.startswith("data:"):
        header, encoded = data.split(",", 1)
        if not header.endswith(";base64"):
            raise ValueError(_("Attachments must use base64 data URLs"))
        mime_type = header[5:].split(";", 1)[0]
        raw = base64.b64decode(encoded, validate=True)
    elif remote and urlparse(data).scheme in ("http", "https"):
        import requests
        response = requests.get(data, timeout=60)
        response.raise_for_status()
        raw = response.content
        mime_type = response.headers.get("Content-Type", "").split(";", 1)[0]
        filename = Path(urlparse(data).path).name or filename
    elif not remote:
        raw = base64.b64decode(data, validate=True)
        mime_type = mimetypes.guess_type(filename)[0]
    else:
        raise ValueError(_("Attachment URLs must use HTTP, HTTPS or base64 data URLs"))
    # Only use the basename; filenames from clients must not escape storage.
    filename = _safe_attachment_name(filename)
    if not Path(filename).suffix:
        filename += mimetypes.guess_extension(mime_type or "") or ".bin"
    directory = Path(GLib.get_user_data_dir()) / "Newelle" / "api-attachments" / hashlib.sha256(raw).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / filename
    if not target.exists():
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as temporary:
            temporary.write(raw)
        os.replace(temporary.name, target)
    return str(target)


def _safe_attachment_name(filename: str) -> str:
    import re
    name = re.sub(r"[^\w. -]", "_", os.path.basename(filename))[:200]
    return name if name not in ("", ".", "..") else "attachment"


def video_frame_content(file_path: str, max_frames: int = 16) -> list:
    """Sample a video's visual content for APIs accepting images only."""
    import json
    import shutil
    import subprocess
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise ValueError(_("Video frame sampling requires ffmpeg and ffprobe"))
    if file_path.startswith(("data:", "http://", "https://")):
        file_path = save_api_attachment(file_path, "video", remote=True)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", file_path],
        capture_output=True, check=True, timeout=60,
    )
    duration = float(json.loads(probe.stdout)["format"]["duration"])
    interval = max(duration / max_frames, 1)
    with tempfile.TemporaryDirectory(prefix="newelle-video-") as directory:
        subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-i", file_path,
             "-vf", f"fps=1/{interval}:start_time=0:round=up,scale=768:768:force_original_aspect_ratio=decrease",
             "-frames:v", str(max_frames), os.path.join(directory, "%03d.jpg")],
            capture_output=True, check=True, timeout=120,
        )
        frames = sorted(Path(directory).glob("*.jpg"))
        if not frames:
            raise ValueError(_("No video frames could be decoded"))
        content = [{"type": "text", "text": "Video frames in chronological order (audio is not included):"}]
        for frame in frames:
            content.append({"type": "image_url", "image_url": {"url": get_file_base64(str(frame))}})
        return content

def encode_image_base64(file_path):
    if file_path.startswith("http"):
        import requests 
        response = requests.get(file_path)
        file_path = "/tmp/" + file_path.split("/")[-1]
        with open(file_path, "wb") as f:
            f.write(response.content)

    mime_types = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.webp': 'image/webp',
        '.mp4': 'video/mp4',
        '.avi': 'video/x-msvideo',
        '.mov': 'video/quicktime'
    }

    ext = os.path.splitext(file_path)[1].lower()
    mime_type = mime_types.get(ext, 'image/jpeg')

    with open(file_path, "rb") as file:
        encoded = base64.b64encode(file.read()).decode("utf-8")

    return f"data:{mime_type};base64,{encoded}"


def get_image_base64(image_str: str):
    """
    Get image string as base64 string, starting with data:/image/jpeg;base64,

    Args:
        image_str: content of the image codeblock 

    Returns:
       base64 encoded image 
    """
    if not image_str.startswith("data:image/"):
        image = encode_image_base64(image_str)
        return image
    else:
        return image_str

def get_image_path(image_str: str):
    """
    Get image string as image path

    Args:
        image_str: content of the image codeblock 

    Returns:
       image path 
    """
    if image_str.startswith("data:image/"):
        header_end = image_str.index(",")
        mime_type = image_str[len("data:"):header_end]
        ext_map = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
        }
        ext = ext_map.get(mime_type, ".jpg")
        raw_data = base64.b64decode(image_str[header_end + 1:])
        saved_image = "/tmp/" + image_str[header_end + 1:][:30] + ext
        with open(saved_image, "wb") as f:
            f.write(raw_data)
        return saved_image
    return image_str

def extract_image(message: str) -> tuple[str | None, str]:
    """
    Extract image from message

    Args:
        message: message string

    Returns:
        tuple[str, str]: image and text, if no image, image is None 
    """
    img = None
    if message.startswith("```image"):
        img = message.split("\n")[1]
        text = message.split("\n")[3:]                    
        text = "\n".join(text)
    else:
        text = message
    return img, text

def extract_video(message: str) -> tuple[str | None, str]:
    """
    Extract video from message

    Args:
        message: message string

    Returns:
        tuple[str, str]: image and text, if no image, image is None 
    """
    img = None
    if message.startswith("```video"):
        img = message.split("\n")[1]
        text = message.split("\n")[3:]                    
        text = "\n".join(text)
    else:
        text = message
    return img, text

def extract_file(message: str) -> tuple[str | None, str]:
    """
    Extract file from message

    Args:
        message: message string

    Returns:
        tuple[str, str]: file and text, if no file, file is None 
    """
    file = None
    if message.startswith("```file"):
        file = message.split("\n")[1]
        text = message.split("\n")[3:]                    
        text = "\n".join(text)
    else:
        text = message
    return file, text

def get_file_icon(file_name: str) -> str:
    """
    Determine the appropriate icon for a file based on its extension.

    Args:
        file_name: name of the file (with or without path)

    Returns:
        freedesktop.org icon name for the file type
    """
    if '.' not in file_name:
        return "text-x-generic"

    extension = file_name.lower().split('.')[-1]

    # Image files
    image_extensions = ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'tiff', 'tif', 'svg', 'webp', 'ico', 'xpm']
    if extension in image_extensions:
        return "image-x-generic"

    # Video files
    video_extensions = ['mp4', 'avi', 'mkv', 'mov', 'wmv', 'flv', 'webm', 'm4v', '3gp', 'ogv']
    if extension in video_extensions:
        return "video-x-generic"

    # Audio files
    audio_extensions = ['mp3', 'wav', 'flac', 'ogg', 'aac', 'm4a', 'wma', 'opus']
    if extension in audio_extensions:
        return "audio-x-generic"

    # Document files
    if extension == 'pdf':
        return "application-pdf"

    doc_extensions = ['doc', 'docx', 'odt', 'rtf']
    if extension in doc_extensions:
        return "x-office-document"

    spreadsheet_extensions = ['xls', 'xlsx', 'ods', 'csv']
    if extension in spreadsheet_extensions:
        return "x-office-spreadsheet"

    presentation_extensions = ['ppt', 'pptx', 'odp']
    if extension in presentation_extensions:
        return "x-office-presentation"

    # Archive files
    archive_extensions = ['zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'xz', 'deb', 'rpm']
    if extension in archive_extensions:
        return "package-x-generic"

    # Code files
    code_extensions = ['py', 'js', 'html', 'css', 'cpp', 'c', 'h', 'java', 'php', 'rb', 'go', 'rs']
    if extension in code_extensions:
        return "text-x-script"

    # Web files
    if extension in ['html', 'htm']:
        return "text-html"

    if extension in ['css']:
        return "text-css"

    # Configuration files
    config_extensions = ['conf', 'cfg', 'ini', 'json', 'xml', 'yaml', 'yml', 'toml']
    if extension in config_extensions:
        return "text-x-generic-template"

    # Executable files
    executable_extensions = ['exe', 'msi', 'deb', 'rpm', 'appimage', 'flatpak', 'snap']
    if extension in executable_extensions:
        return "application-x-executable"

    # Script files
    script_extensions = ['sh', 'bash', 'zsh', 'fish', 'bat', 'cmd', 'ps1']
    if extension in script_extensions:
        return "text-x-script"

    # Text files
    text_extensions = ['txt', 'md', 'rst', 'log', 'readme']
    if extension in text_extensions:
        return "text-x-generic"

    # Font files
    font_extensions = ['ttf', 'otf', 'woff', 'woff2', 'eot']
    if extension in font_extensions:
        return "font-x-generic"

    # Default fallback
    return "text-x-generic"


def extract_supported_files(history: list, supported_extensions: list, blacklist_formats: list = [], include_automatic: bool = True) -> list[str]:
    """
    Extract supported files from message history, excluding blacklisted formats.
    If 'plaintext' is in supported_extensions, files identified as text/* MIME type are also included.

    Args:
        history: message history
        supported_extensions: list of supported file extensions (can include 'plaintext')
        blacklist_formats: list of file formats to exclude (optional)
        include_automatic: include legacy file blocks; explicit RAG blocks
            always participate and bypass the model's supported-file blacklist.

    Returns:
        list[str]: list of supported files
    """
    documents = []

    for message in history:
        if message.get("User") not in (None, "User", "File"):
            continue
        chunks = get_message_chunks(message.get("Message", "")) # Use .get for safety

        for chunk in chunks:
            if chunk.type == "codeblock" and (chunk.lang == "file_rag" or (chunk.lang == "file" and include_automatic)):
                files = chunk.text.split("\n")
                for file in files:
                    file = file.strip()
                    if not file or file.startswith("#"):
                        continue

                    if file_matches_patterns(file, supported_extensions):
                        if chunk.lang != "file_rag" and file_matches_patterns(file, blacklist_formats):
                            continue 
                        documents.append("file:" + file) 

    return documents


def chat_contains_vision(history: list[dict]) -> bool:
    """Return whether a chat contains an image or video attachment."""
    for message in history:
        chunks = get_message_chunks(message.get("Message", ""))
        if any(
            chunk.type == "codeblock" and chunk.lang.lower() in ("image", "video")
            for chunk in chunks
        ):
            return True
    return False


def extract_audio(message: str) -> tuple[str | None, str]:
    """Read a recorded audio attachment without exposing its path as text."""
    match = re.search(r"```audio\n([^\n]+)\n```\n?", message)
    if match is None:
        return None, message
    return match.group(1), message[:match.start()] + message[match.end():]


def audio_text(message: str) -> str:
    path, text = extract_audio(message)
    return (text.strip() or _("[Audio message]")) if path else message


def audio_history_text(history: list) -> list:
    return [{**m, "Message": audio_text(m.get("Message", ""))} for m in history]


def prepare_audio_message(message: str, supported: bool) -> str:
    """Send recordings to audio models and STT text to text-only models."""
    path, text = extract_audio(message)
    if path and not supported:
        if not text.strip():
            raise ValueError(_("The recording must be transcribed before sending it to this model."))
        return text
    return message
