import os
import base64
from .message_chunk import get_message_chunks
import fnmatch
import mimetypes

def encode_image_base64(file_path):
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
    if not image_str.startswith("data:image/jpeg;base64,"):
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
    if image_str.startswith("data:image/jpeg;base64,"):
        raw_data = base64.b64decode(image_str[len("data:image/jpeg;base64,"):])
        saved_image = "/tmp/" + image_str[len("data:image/jpeg;base64,"):][30:] + ".jpg"
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

def extract_supported_files(history: list, supported_extensions: list, blacklist_formats: list = []) -> list[str]:
    """
    Extract supported files from message history, excluding blacklisted formats.
    If 'plaintext' is in supported_extensions, files identified as text/* MIME type are also included.

    Args:
        history: message history
        supported_extensions: list of supported file extensions (can include 'plaintext')
        blacklist_formats: list of file formats to exclude (optional)

    Returns:
        list[str]: list of supported files
    """
    documents = []
    plaintext_supported = "plaintext" in supported_extensions
    if plaintext_supported:
        supported_extensions.append(".conf")

    for message in history:
        chunks = get_message_chunks(message.get("Message", "")) # Use .get for safety

        for chunk in chunks:
            if chunk.type == "codeblock" and chunk.lang == "file":
                files = chunk.text.split("\n")
                for file in files:
                    file = file.strip()
                    if not file or file.startswith("#"):
                        continue

                    is_supported = False

                    if any(fnmatch.fnmatch(file.lower(), pattern.lower()) for pattern in supported_extensions if pattern != "plaintext"):
                         is_supported = True

                    if not is_supported and plaintext_supported:
                        mime_type, _ = mimetypes.guess_type(file)
                        if mime_type and mime_type.startswith('text/'):
                            is_supported = True 

                    if is_supported:
                        if any(fnmatch.fnmatch(file.lower(), pattern.lower()) for pattern in blacklist_formats):
                            continue 
                        documents.append("file:" + file) 

    return documents
