from .image_generator import ImageGeneratorHandler
from ...handlers.extra_settings import ExtraSettings
from ...utility.system import can_escape_sandbox, is_flatpak, get_spawn_command, has_backend, detect_cuda_version
from ...utility.media import get_image_path
from ...utility.background_process import BackgroundProcess
from ...utility.build_process import BuildCancelled, BuildProcess
from ...utility.download_manager import (
    DownloadCancelled,
    DownloadKind,
    current_download_task,
    get_download_manager,
)
from ...tools import Tool, ToolResult
from ...handlers import ErrorSeverity
from ...ui.model_library import (
    LibraryModel,
    ModelLibraryWindow,
)
from ...utility.model_icons import get_model_icon
from ...ui.build_dependency_warning import BuildDependencyWarning
from gettext import gettext as _
import subprocess
import os
import platform
import threading
import shutil
import zipfile
import tempfile
import time
import socket
import json
import glob
from gi.repository import Gtk, Adw, GLib, Gdk
import requests

# Model Library
SD_MODELS = [
    {
        "id": "sd-v1-5",
        "family": "sd",
        "display": "Stable Diffusion 1.5",
        "description": "Classic SD 1.5 from RunwayML. ~4 GB, single file. Great for LoRAs and fast iteration.",
        "tags": ["sd", "text2image", "safetensors", "4GB"],
        "files": [
            {"role": "diffusion", "url": "https://huggingface.co/runwayml/stable-diffusion-v1-5/resolve/main/v1-5-pruned-emaonly.safetensors", "filename": "v1-5-pruned-emaonly.safetensors", "shared": False},
        ],
        "cli_extra": [],
    },
    {
        "id": "sdxl-base-1.0",
        "family": "sdxl",
        "display": "SDXL Base 1.0",
        "description": "Stable Diffusion XL base 1.0 with fixed VAE. ~6.5 GB, native 1024x1024 generation.",
        "tags": ["sdxl", "text2image", "safetensors", "6GB"],
        "files": [
            {"role": "diffusion", "url": "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors", "filename": "sd_xl_base_1.0.safetensors", "shared": False},
            {"role": "vae", "url": "https://huggingface.co/madebyollin/sdxl-vae-fp16-fix/resolve/main/sdxl_vae.safetensors", "filename": "sdxl_vae.safetensors", "shared": True},
        ],
        "cli_extra": ["--vae-tiling"],
    },
    {
        "id": "sd3-medium",
        "family": "sd3",
        "display": "Stable Diffusion 3 Medium",
        "description": "SD3 Medium with bundled CLIPs and T5XXL. ~5 GB, single file.",
        "tags": ["sd3", "text2image", "safetensors", "5GB"],
        "files": [
            {"role": "diffusion", "url": "https://code.ixdev.cn/hf-mirrors/stable-diffusion-3-medium/-/raw/main/sd3_medium_incl_clips_t5xxlfp16.safetensors", "filename": "sd3_medium_incl_clips_t5xxlfp16.safetensors", "shared": False},
        ],
        "cli_extra": [],
    },
    {
        "id": "sd3.5-large",
        "family": "sd3",
        "display": "Stable Diffusion 3.5 Large",
        "description": "SD3.5 Large with separate CLIP-L, CLIP-G and T5XXL text encoders. ~13 GB total.",
        "tags": ["sd3", "text2image", "safetensors", "13GB"],
        "files": [
            {"role": "diffusion", "url": "https://code.ixdev.cn/hf-mirrors/stable-diffusion-3.5-large/-/raw/main/sd3.5_large.safetensors", "filename": "sd3.5_large.safetensors", "shared": False},
            {"role": "clip_l", "url": "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors", "filename": "clip_l.safetensors", "shared": True},
            {"role": "t5xxl", "url": "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp16.safetensors", "filename": "t5xxl_fp16.safetensors", "shared": True},
        ],
        "cli_extra": ["--clip-on-cpu"],
    },
    {
        "id": "flux1-dev-q8_0",
        "family": "flux",
        "display": "FLUX.1-dev Q8_0",
        "description": "FLUX.1-dev 12B Q8_0 (high quality). ~12 GB diffusion, ~12 GB T5XXL. Best on 16+ GB VRAM.",
        "tags": ["flux", "text2image", "gguf", "q8_0", "12GB"],
        "files": [
            {"role": "diffusion", "url": "https://huggingface.co/leejet/FLUX.1-dev-gguf/resolve/main/flux1-dev-q8_0.gguf", "filename": "flux1-dev-q8_0.gguf", "shared": False},
            {"role": "vae", "url": "https://code.ixdev.cn/hf-mirrors/FLUX.1-dev/-/raw/main/ae.safetensors", "filename": "ae.safetensors", "shared": True},
            {"role": "clip_l", "url": "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors", "filename": "clip_l.safetensors", "shared": True},
            {"role": "t5xxl", "url": "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp16.safetensors", "filename": "t5xxl_fp16.safetensors", "shared": True},
        ],
        "cli_extra": ["--diffusion-fa", "--offload-to-cpu", "--clip-on-cpu"],
    },
    {
        "id": "flux1-dev-q4_0",
        "family": "flux",
        "display": "FLUX.1-dev Q4_0",
        "description": "FLUX.1-dev 12B Q4_0 (smaller). ~6.5 GB diffusion. Fits on 8 GB VRAM with offload.",
        "tags": ["flux", "text2image", "gguf", "q4_0", "6GB"],
        "files": [
            {"role": "diffusion", "url": "https://huggingface.co/leejet/FLUX.1-dev-gguf/resolve/main/flux1-dev-q4_0.gguf", "filename": "flux1-dev-q4_0.gguf", "shared": False},
            {"role": "vae", "url": "https://code.ixdev.cn/hf-mirrors/FLUX.1-dev/-/raw/main/ae.safetensors", "filename": "ae.safetensors", "shared": True},
            {"role": "clip_l", "url": "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors", "filename": "clip_l.safetensors", "shared": True},
            {"role": "t5xxl", "url": "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp16.safetensors", "filename": "t5xxl_fp16.safetensors", "shared": True},
        ],
        "cli_extra": ["--diffusion-fa", "--offload-to-cpu", "--clip-on-cpu"],
    },
    {
        "id": "flux1-schnell-q8_0",
        "family": "flux",
        "display": "FLUX.1-schnell Q8_0",
        "description": "FLUX.1-schnell 12B Q8_0, distilled for 4-step generation. Apache 2.0 licensed.",
        "tags": ["flux", "text2image", "gguf", "q8_0", "schnell", "12GB"],
        "files": [
            {"role": "diffusion", "url": "https://huggingface.co/leejet/FLUX.1-schnell-gguf/resolve/main/flux1-schnell-q8_0.gguf", "filename": "flux1-schnell-q8_0.gguf", "shared": False},
            {"role": "vae", "url": "https://code.ixdev.cn/hf-mirrors/FLUX.1-schnell/-/raw/main/ae.safetensors", "filename": "ae.safetensors", "shared": True},
            {"role": "clip_l", "url": "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors", "filename": "clip_l.safetensors", "shared": True},
            {"role": "t5xxl", "url": "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp16.safetensors", "filename": "t5xxl_fp16.safetensors", "shared": True},
        ],
        "cli_extra": ["--diffusion-fa", "--offload-to-cpu", "--clip-on-cpu"],
    },
    {
        "id": "flux2-dev-q4_k_s",
        "family": "flux2",
        "display": "FLUX.2-dev Q4_K_S",
        "description": "FLUX.2-dev with Mistral-Small-3.2 24B LLM text encoder. ~19 GB diffusion, ~14 GB LLM.",
        "tags": ["flux2", "text2image", "gguf", "q4_k_s", "32GB"],
        "files": [
            {"role": "diffusion", "url": "https://huggingface.co/city96/FLUX.2-dev-gguf/resolve/main/flux2-dev-Q4_K_S.gguf", "filename": "flux2-dev-Q4_K_S.gguf", "shared": False},
            {"role": "vae", "url": "https://huggingface.co/Comfy-Org/flux2-klein-4B/resolve/main/split_files/vae/flux2-vae.safetensors", "filename": "flux2_ae.safetensors", "shared": True},
            {"role": "llm", "url": "https://huggingface.co/unsloth/Mistral-Small-3.2-24B-Instruct-2506-GGUF/resolve/main/Mistral-Small-3.2-24B-Instruct-2506-Q4_K_M.gguf", "filename": "Mistral-Small-3.2-24B-Instruct-2506-Q4_K_M.gguf", "shared": False},
        ],
        "cli_extra": ["--diffusion-fa", "--offload-to-cpu"],
    },
    {
        "id": "flux2-klein-4b",
        "family": "flux2",
        "display": "FLUX.2-klein-4B Q4_0",
        "description": "FLUX.2-klein-4B with Qwen3-4B text encoder. ~2.5 GB diffusion, ~2.5 GB LLM. Fits on 8 GB VRAM.",
        "tags": ["flux2", "text2image", "gguf", "q4_0", "klein", "5GB"],
        "files": [
            {"role": "diffusion", "url": "https://huggingface.co/leejet/FLUX.2-klein-4B-GGUF/resolve/main/flux-2-klein-4b-Q4_0.gguf", "filename": "flux-2-klein-4b-Q4_0.gguf", "shared": False},
            {"role": "vae", "url": "https://huggingface.co/Comfy-Org/flux2-klein-4B/resolve/main/split_files/vae/flux2-vae.safetensors", "filename": "flux2_ae.safetensors", "shared": True},
            {"role": "llm", "url": "https://huggingface.co/unsloth/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf", "filename": "Qwen3-4B-Q4_K_M.gguf", "shared": False},
        ],
        "cli_extra": ["--diffusion-fa", "--offload-to-cpu"],
    },
    {
        "id": "flux2-klein-9b",
        "family": "flux2",
        "display": "FLUX.2-klein-9B Q4_0",
        "description": "FLUX.2-klein-9B with Qwen3-8B text encoder. ~5.5 GB diffusion, ~5 GB LLM. Fits on 12 GB VRAM.",
        "tags": ["flux2", "text2image", "gguf", "q4_0", "klein", "10GB"],
        "files": [
            {"role": "diffusion", "url": "https://huggingface.co/leejet/FLUX.2-klein-9B-GGUF/resolve/main/flux-2-klein-9b-Q4_0.gguf", "filename": "flux-2-klein-9b-Q4_0.gguf", "shared": False},
            {"role": "vae", "url": "https://huggingface.co/Comfy-Org/flux2-klein-4B/resolve/main/split_files/vae/flux2-vae.safetensors", "filename": "flux2_ae.safetensors", "shared": True},
            {"role": "llm", "url": "https://huggingface.co/unsloth/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf", "filename": "Qwen3-8B-Q4_K_M.gguf", "shared": False},
        ],
        "cli_extra": ["--diffusion-fa", "--offload-to-cpu"],
    },
    {
        "id": "flux1-kontext-dev-q4_k_m",
        "family": "kontext",
        "display": "FLUX.1-Kontext-dev Q4_K_M",
        "description": "FLUX.1-Kontext-dev image edit model. Requires a reference image. ~6.9 GB diffusion Q4_K_M.",
        "tags": ["flux", "kontext", "image-edit", "gguf", "q4_k_m", "12GB"],
        "files": [
            {"role": "diffusion", "url": "https://huggingface.co/QuantStack/FLUX.1-Kontext-dev-GGUF/resolve/main/flux1-kontext-dev-Q4_K_M.gguf", "filename": "flux1-kontext-dev-Q4_K_M.gguf", "shared": False},
            {"role": "vae", "url": "https://code.ixdev.cn/hf-mirrors/FLUX.1-dev/-/raw/main/ae.safetensors", "filename": "ae.safetensors", "shared": True},
            {"role": "clip_l", "url": "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors", "filename": "clip_l.safetensors", "shared": True},
            {"role": "t5xxl", "url": "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp16.safetensors", "filename": "t5xxl_fp16.safetensors", "shared": True},
        ],
        "cli_extra": ["--diffusion-fa", "--offload-to-cpu", "--clip-on-cpu"],
    },
    {
        "id": "chroma-v40-q8_0",
        "family": "chroma",
        "display": "Chroma v40 Q8_0",
        "description": "Chroma unlocked v40 8.9B Q8_0. ~8.9 GB diffusion, ~9.5 GB T5XXL. Apache 2.0.",
        "tags": ["chroma", "text2image", "gguf", "q8_0", "18GB"],
        "files": [
            {"role": "diffusion", "url": "https://huggingface.co/silveroxides/Chroma-GGUF/resolve/main/chroma-unlocked-v40/chroma-unlocked-v40-Q8_0.gguf", "filename": "chroma-unlocked-v40-Q8_0.gguf", "shared": False},
            {"role": "vae", "url": "https://code.ixdev.cn/hf-mirrors/FLUX.1-dev/-/raw/main/ae.safetensors", "filename": "ae.safetensors", "shared": True},
            {"role": "t5xxl", "url": "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp16.safetensors", "filename": "t5xxl_fp16.safetensors", "shared": True},
        ],
        "cli_extra": ["--diffusion-fa", "--offload-to-cpu", "--clip-on-cpu", "--chroma-disable-dit-mask"],
    },
    {
        "id": "qwen-image-q4_k_m",
        "family": "qwen_image",
        "display": "Qwen Image Q4_K_M",
        "description": "Qwen Image with Qwen2.5-VL-7B text encoder. ~13 GB diffusion, ~4.5 GB LLM. Strong text rendering.",
        "tags": ["qwen", "text2image", "gguf", "q4_k_m", "18GB"],
        "files": [
            {"role": "diffusion", "url": "https://huggingface.co/QuantStack/Qwen-Image-GGUF/resolve/main/Qwen_Image-Q4_K_M.gguf", "filename": "Qwen_Image-Q4_K_M.gguf", "shared": False},
            {"role": "vae", "url": "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors", "filename": "qwen_image_vae.safetensors", "shared": True},
            {"role": "llm", "url": "https://huggingface.co/mradermacher/Qwen2.5-VL-7B-Instruct-GGUF/resolve/main/Qwen2.5-VL-7B-Instruct.Q4_K_M.gguf", "filename": "Qwen2.5-VL-7B-Instruct.Q4_K_M.gguf", "shared": False},
        ],
        "cli_extra": ["--diffusion-fa", "--offload-to-cpu", "--flow-shift", "3"],
    },
    {
        "id": "qwen-image-edit-q8_0",
        "family": "qwen_image_edit",
        "display": "Qwen Image Edit Q8_0",
        "description": "Qwen Image Edit. Takes a reference image plus an instruction prompt. Shares the Qwen Image VAE and LLM. Requires --diffusion-model and -r flags at runtime.",
        "tags": ["qwen", "image-edit", "gguf", "q8_0", "18GB"],
        "files": [
            {"role": "diffusion", "url": "https://huggingface.co/QuantStack/Qwen-Image-Edit-GGUF/resolve/main/Qwen_Image_Edit-Q8_0.gguf", "filename": "Qwen_Image_Edit-Q8_0.gguf", "shared": False},
            {"role": "vae", "url": "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors", "filename": "qwen_image_vae.safetensors", "shared": True},
            {"role": "llm", "url": "https://huggingface.co/mradermacher/Qwen2.5-VL-7B-Instruct-GGUF/resolve/main/Qwen2.5-VL-7B-Instruct.Q4_K_M.gguf", "filename": "Qwen2.5-VL-7B-Instruct.Q4_K_M.gguf", "shared": True},
        ],
        "cli_extra": ["--diffusion-fa", "--offload-to-cpu", "--flow-shift", "3"],
    },
    {
        "id": "qwen-image-edit-2509-q4_k_s",
        "family": "qwen_image_edit",
        "display": "Qwen Image Edit 2509 Q4_K_S",
        "description": "Qwen Image Edit 2509. Adds --llm_vision (vision projector) for stronger instruction following. Needs an additional ~1 GB mmproj file.",
        "tags": ["qwen", "image-edit", "gguf", "q4_k_s", "2509", "18GB"],
        "files": [
            {"role": "diffusion", "url": "https://huggingface.co/QuantStack/Qwen-Image-Edit-2509-GGUF/resolve/main/Qwen-Image-Edit-2509-Q4_K_S.gguf", "filename": "Qwen-Image-Edit-2509-Q4_K_S.gguf", "shared": False},
            {"role": "vae", "url": "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors", "filename": "qwen_image_vae.safetensors", "shared": True},
            {"role": "llm", "url": "https://huggingface.co/mradermacher/Qwen2.5-VL-7B-Instruct-GGUF/resolve/main/Qwen2.5-VL-7B-Instruct.Q8_0.gguf", "filename": "Qwen2.5-VL-7B-Instruct.Q8_0.gguf", "shared": False},
            {"role": "llm_vision", "url": "https://huggingface.co/mradermacher/Qwen2.5-VL-7B-Instruct-GGUF/resolve/main/Qwen2.5-VL-7B-Instruct.mmproj-Q8_0.gguf", "filename": "Qwen2.5-VL-7B-Instruct.mmproj-Q8_0.gguf", "shared": False},
        ],
        "cli_extra": ["--diffusion-fa", "--offload-to-cpu", "--flow-shift", "3"],
    },
    {
        "id": "qwen-image-edit-2511-q4_k_m",
        "family": "qwen_image_edit",
        "display": "Qwen Image Edit 2511 Q4_K_M",
        "description": "Qwen Image Edit 2511. Uses a safetensors LLM and requires --qwen-image-zero-cond-t (set automatically).",
        "tags": ["qwen", "image-edit", "gguf", "q4_k_m", "2511", "20GB"],
        "files": [
            {"role": "diffusion", "url": "https://huggingface.co/unsloth/Qwen-Image-Edit-2511-GGUF/resolve/main/qwen-image-edit-2511-Q4_K_M.gguf", "filename": "qwen-image-edit-2511-Q4_K_M.gguf", "shared": False},
            {"role": "vae", "url": "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors", "filename": "qwen_image_vae.safetensors", "shared": True},
            {"role": "llm", "url": "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b.safetensors", "filename": "qwen_2.5_vl_7b.safetensors", "shared": False},
        ],
        "cli_extra": ["--diffusion-fa", "--offload-to-cpu", "--flow-shift", "3", "--qwen-image-zero-cond-t"],
    },
    {
        "id": "z-image-turbo-q4_k",
        "family": "z_image",
        "display": "Z-Image Turbo Q4_K",
        "description": "Z-Image Turbo distilled 8-step model. ~3.9 GB diffusion, ~2.5 GB Qwen3-4B. Fits on 8 GB VRAM.",
        "tags": ["z-image", "turbo", "text2image", "gguf", "q4_k", "6GB"],
        "files": [
            {"role": "diffusion", "url": "https://huggingface.co/leejet/Z-Image-Turbo-GGUF/resolve/main/z_image_turbo-Q4_K.gguf", "filename": "z_image_turbo-Q4_K.gguf", "shared": False},
            {"role": "vae", "url": "https://code.ixdev.cn/hf-mirrors/FLUX.1-schnell/-/raw/main/ae.safetensors", "filename": "ae.safetensors", "shared": True},
            {"role": "llm", "url": "https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf", "filename": "Qwen3-4B-Instruct-2507-Q4_K_M.gguf", "shared": False},
        ],
        "cli_extra": ["--diffusion-fa", "--offload-to-cpu"],
    },
    {
        "id": "ltx-2.3-22b-dev-ud-q4_k_m",
        "family": "ltx2",
        "display": "LTX-2.3 22B dev Q4_K_M (video)",
        "description": "LTX-2.3 video model with gemma-3-12b text encoder. ~10 GB diffusion, ~7 GB LLM, ~5 GB VAE.",
        "tags": ["ltx2", "video", "t2v", "i2v", "gguf", "q4_k_m", "25GB"],
        "files": [
            {"role": "diffusion", "url": "https://huggingface.co/unsloth/LTX-2.3-GGUF/resolve/main/ltx-2.3-22b-dev-UD-Q4_K_M.gguf", "filename": "ltx-2.3-22b-dev-UD-Q4_K_M.gguf", "shared": False},
            {"role": "vae", "url": "https://huggingface.co/unsloth/LTX-2.3-GGUF/resolve/main/vae/ltx-2.3-22b-dev_video_vae.safetensors", "filename": "ltx-2.3-22b-dev_video_vae.safetensors", "shared": True},
            {"role": "audio_vae", "url": "https://huggingface.co/unsloth/LTX-2.3-GGUF/resolve/main/vae/ltx-2.3-22b-dev_audio_vae.safetensors", "filename": "ltx-2.3-22b-dev_audio_vae.safetensors", "shared": True},
            {"role": "llm", "url": "https://huggingface.co/unsloth/gemma-3-12b-it-GGUF/resolve/main/gemma-3-12b-it-Q4_K_M.gguf", "filename": "gemma-3-12b-it-Q4_K_M.gguf", "shared": False},
            {"role": "embeddings", "url": "https://huggingface.co/unsloth/LTX-2.3-GGUF/resolve/main/text_encoders/ltx-2.3-22b-dev_embeddings_connectors.safetensors", "filename": "ltx-2.3-22b-dev_embeddings_connectors.safetensors", "shared": True},
        ],
        "cli_extra": ["--diffusion-fa", "--offload-to-cpu"],
    },
    {
        "id": "ovis-image-q4_0",
        "family": "ovis_image",
        "display": "Ovis-Image 7B Q4_0",
        "description": "Ovis-Image 7B text-to-image with Ovis 2.5 text encoder. ~4.2 GB diffusion, ~5 GB LLM.",
        "tags": ["ovis", "text2image", "gguf", "q4_0", "9GB"],
        "files": [
            {"role": "diffusion", "url": "https://huggingface.co/leejet/Ovis-Image-7B-GGUF/resolve/main/ovis_image-Q4_0.gguf", "filename": "ovis_image-Q4_0.gguf", "shared": False},
            {"role": "vae", "url": "https://code.ixdev.cn/hf-mirrors/FLUX.1-schnell/-/raw/main/ae.safetensors", "filename": "ae.safetensors", "shared": True},
            {"role": "llm", "url": "https://huggingface.co/Comfy-Org/Ovis-Image/resolve/main/split_files/text_encoders/ovis_2.5.safetensors", "filename": "ovis_2.5.safetensors", "shared": False},
        ],
        "cli_extra": ["--diffusion-fa", "--offload-to-cpu"],
    },
    {
        "id": "anima-preview-q4_k_m",
        "family": "anima",
        "display": "Anima preview Q4_K_M",
        "description": "Anima preview 3B Q4_K_M. ~1.4 GB diffusion, ~0.5 GB Qwen3-0.6B. Very lightweight.",
        "tags": ["anima", "text2image", "gguf", "q4_k_m", "2GB"],
        "files": [
            {"role": "diffusion", "url": "https://huggingface.co/Bedovyy/Anima-GGUF/resolve/main/anima-preview-Q4_K_M.gguf", "filename": "anima-preview-Q4_K_M.gguf", "shared": False},
            {"role": "vae", "url": "https://huggingface.co/circlestone-labs/Anima/resolve/main/split_files/vae/qwen_image_vae.safetensors", "filename": "qwen_image_vae.safetensors", "shared": True},
            {"role": "llm", "url": "https://huggingface.co/mradermacher/Qwen3-0.6B-Base-GGUF/resolve/main/Qwen3-0.6B-Base.Q4_K_M.gguf", "filename": "Qwen3-0.6B-Base.Q4_K_M.gguf", "shared": False},
        ],
        "cli_extra": ["--diffusion-fa", "--offload-to-cpu"],
    },
]


class StableDiffusionCPPHandler(ImageGeneratorHandler):
    """Local image generation using stable-diffusion.cpp.

    Supports downloading prebuilt binaries or building from source
    with hardware acceleration (CUDA, Vulkan, ROCm).
    """

    key = "stablediffusioncpp"
    schema_key = "image-generator-settings"

    RELEASE_API_URL = "https://api.github.com/repos/leejet/stable-diffusion.cpp/releases"
    REPO_URL = "https://github.com/leejet/stable-diffusion.cpp.git"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.sd_cpp_path = os.path.join(self.path, "stable-diffusion.cpp")
        self.sd_binary_path = os.path.join(self.sd_cpp_path, "build", "bin", "sd-cli")
        self.sd_server_binary_path = os.path.join(self.sd_cpp_path, "build", "bin", "sd-server")
        self.model_folder = os.path.join(self.path, "sd_models")
        self.shared_folder = os.path.join(self.model_folder, "_shared")
        self.lora_folder = os.path.join(self.path, "sd_lora")
        self._installing = False
        self._server = BackgroundProcess("sd-server")
        self._server.register_atexit()
        self._build_process = BuildProcess("stable-diffusion.cpp-build")
        self._build_process.register_atexit()
        self.downloading = {}

        for folder in (self.model_folder, self.shared_folder, self.lora_folder):
            if not os.path.exists(folder):
                try:
                    os.makedirs(folder)
                except Exception:
                    pass

    def get_extra_settings(self) -> list:
        settings = []

        # Sync special settings with the currently selected model. When the user
        # picks a library variant the VAE/LLM/CLIP/T5XXL/etc fields are filled
        # in from the variant manifest; when they pick a custom (loose) file
        # those fields are cleared.
        self._sync_special_settings_with_model()

        # Model selection
        model_list = self._get_model_list()
        settings.append(
            ExtraSettings.ComboSetting(
                "model",
                "Model",
                "Stable Diffusion model to use",
                model_list,
                model_list[0][1] if len(model_list) > 0 else "",
                refresh=lambda button: self._get_model_list(True),
                folder=self.model_folder,
                update_settings=True,
            )
        )

        settings.append(
            ExtraSettings.ButtonSetting(
                "library",
                "Model Library",
                "Browse and download curated models (SD, SDXL, SD3, FLUX, Kontext, Chroma, Qwen, Z-Image, LTX-2, Ovis, Anima, ...)",
                self.open_model_library,
                label="Model Library",
            )
        )

        settings.append(
            ExtraSettings.EntrySetting(
                "custom_models_dir",
                "Custom Models Directory",
                "Additional directory to scan for model files (.safetensors, .ckpt, .gguf). Leave empty to disable.",
                "",
                update_settings=True,
            )
        )

        # Installation
        if not self._is_binary_installed():
            settings.append(
                ExtraSettings.ButtonSetting(
                    "install",
                    "Install StableDiffusionCPP",
                    "Download prebuilt binaries or build from source with hardware acceleration",
                    self.show_install_dialog,
                    label="Install",
                )
            )
        else:
            settings.append(
                ExtraSettings.ToggleSetting(
                    "gpu_acceleration",
                    "Hardware Acceleration",
                    "Enable hardware acceleration (requires GPU backend)",
                    False,
                )
            )
            if is_flatpak():
                settings.append(
                    ExtraSettings.ToggleSetting(
                        "use_system_sd",
                        "Use System sd-cli",
                        "Use system-installed sd-cli instead of built-in (requires sd-cli on host and sandbox escape)",
                        False,
                    )
                )
            settings.append(
                ExtraSettings.ButtonSetting(
                    "reinstall",
                    "Reinstall",
                    "Rebuild or re-download stable-diffusion.cpp",
                    self.show_install_dialog,
                    label="Reinstall",
                )
            )

            # Server mode toggle (only if binary is installed)
            settings.append(
                ExtraSettings.ToggleSetting(
                    "use_server",
                    "Use sd-server",
                    "Use the HTTP server (sd-server) instead of the CLI (sd-cli) for image generation. The server is faster for multiple generations but uses more memory.",
                    False,
                )
            )
            settings.append(
                ExtraSettings.SpinSetting(
                    "server_port",
                    "Server Port",
                    "Port for the sd-server HTTP server",
                    17860, 1024, 65535, 1, 1, 0,
                )
            )

        # Generation settings
        settings.append(
            ExtraSettings.NestedSetting(
                "generation_settings",
                "Generation Settings",
                "Configure image generation parameters",
                [
                    ExtraSettings.SpinSetting(
                        "width", "Width", "Image width in pixels",
                        512, 64, 2048, 8, 64, 0,
                    ),
                    ExtraSettings.SpinSetting(
                        "height", "Height", "Image height in pixels",
                        512, 64, 2048, 8, 64, 0,
                    ),
                    ExtraSettings.SpinSetting(
                        "steps", "Steps", "Number of sampling steps",
                        20, 1, 150, 1, 10, 0,
                    ),
                    ExtraSettings.EntrySetting(
                        "cfg_scale", "CFG Scale", "Unconditional guidance scale",
                        "7.0",
                    ),
                    ExtraSettings.EntrySetting(
                        "seed", "Seed", "RNG seed (-1 for random)",
                        "-1",
                    ),
                    ExtraSettings.ComboSetting(
                        "sampling_method",
                        "Sampling Method",
                        "Sampler to use for generation",
                        ["euler", "euler_a", "heun", "dpm2", "dpm++2m", "dpm++2mv2", "lcm"],
                        "euler_a",
                    ),
                    ExtraSettings.MultilineEntrySetting(
                        "positive_prompt_template",
                        "Positive Prompt Template",
                        "Template for positive prompt. [input] will be replaced with the user prompt.",
                        "[input]",
                    ),
                    ExtraSettings.MultilineEntrySetting(
                        "negative_prompt_template",
                        "Negative Prompt Template",
                        "Template for negative prompt. [input] will be replaced with the positive prompt.",
                        "",
                    ),
                    ExtraSettings.SpinSetting(
                        "clip_skip",
                        "CLIP Skip",
                        "Ignore last layers of CLIP network; 1 ignores none, 2 ignores one layer. <= 0 uses model default (1 for SD1.x, 2 for SD2.x)",
                        -1, -1, 12, 1, 1, 0,
                    ),
                ],
            )
        )

        # LoRA settings
        settings.append(
            ExtraSettings.NestedSetting(
                "lora_settings",
                "LoRA Settings",
                "Configure LoRA adapters. Place LoRA files in the folder below and reference them in your prompt with <lora:filename:multiplier>.",
                [
                    ExtraSettings.ToggleSetting(
                        "enable_lora",
                        "Enable LoRA",
                        "Enable LoRA support by passing --lora-model-dir to sd-cli/sd-server",
                        False,
                        folder=self.lora_folder,
                    ),
                    ExtraSettings.EntrySetting(
                        "lora_folder_path",
                        "LoRA Folder",
                        "Directory containing LoRA weights (.safetensors, .ckpt)",
                        "",
                        update_settings=True,
                    ),
                ],
            )
        )

        # Image editing (Qwen Image Edit). The toggle is always shown; the
        # nested configuration only appears when the toggle is on, so we
        # re-emit get_extra_settings() on toggle change (update_settings=True)
        # and conditionally append the nested block.
        self._sync_edit_settings_with_model()
        settings.append(
            ExtraSettings.ToggleSetting(
                "enable_image_editing",
                "Enable Image Editing",
                "Enable the Qwen Image Edit model and the edit_image tool. Requires a downloaded Qwen Image Edit variant from the Model Library (and, for the 2509 variant, a vision projector file).",
                False,
                update_settings=True,
            )
        )
        if self.get_setting("enable_image_editing", False, False):
            edit_model_list = self._get_model_list(family="qwen_image_edit")
            settings.append(
                ExtraSettings.NestedSetting(
                    "image_editing_settings",
                    "Image Editing Settings",
                    "Configure the Qwen Image Edit model, its text encoders and editing-specific overrides.",
                    [
                        ExtraSettings.ComboSetting(
                            "edit_model",
                            "Edit Model",
                            "Stable Diffusion model to use for image editing. Pick a downloaded Qwen Image Edit variant or a custom file.",
                            edit_model_list,
                            edit_model_list[0][1] if len(edit_model_list) > 0 else "",
                            folder=self.model_folder,
                            update_settings=True,
                        ),
                        ExtraSettings.EntrySetting(
                            "edit_vae_path",
                            "Edit VAE",
                            "Path to the VAE used by Qwen Image Edit (--vae). Leave empty to use the model default or the variant manifest.",
                            "",
                            folder=self.model_folder,
                        ),
                        ExtraSettings.EntrySetting(
                            "edit_llm_path",
                            "Edit LLM (Qwen 2.5 VL)",
                            "Path to the LLM text encoder (--llm) used by the Qwen Image Edit model.",
                            "",
                            folder=self.model_folder,
                        ),
                        ExtraSettings.EntrySetting(
                            "edit_llm_vision_path",
                            "Edit LLM Vision Projector (2509 only)",
                            "Path to the vision projector file (--llm_vision) used by the Qwen Image Edit 2509 variant.",
                            "",
                            folder=self.model_folder,
                        ),
                        ExtraSettings.ToggleSetting(
                            "edit_qwen_image_zero_cond_t",
                            "Edit: Qwen Image Zero Cond T (2511)",
                            "Enable zero_cond_t for Qwen Image Edit (--qwen-image-zero-cond-t). Required for the 2511 variant for good results.",
                            False,
                        ),
                        ExtraSettings.MultilineEntrySetting(
                            "edit_extra_cli_args",
                            "Edit Extra CLI Arguments",
                            "Additional command-line arguments passed verbatim to sd-cli for image editing. One per line.",
                            "",
                        ),
                    ],
                )
            )

        # Advanced settings
        settings.append(
            ExtraSettings.NestedSetting(
                "advanced_settings",
                "Advanced Settings",
                "Override VAE, LLM and text encoder paths and tune low-VRAM / model-specific CLI flags.",
                [
                    ExtraSettings.EntrySetting(
                        "vae_path",
                        "VAE",
                        "Path to standalone VAE model (overrides --vae). Leave empty to use the model default or the variant manifest.",
                        "",
                        folder=self.model_folder,
                    ),
                    ExtraSettings.EntrySetting(
                        "llm_path",
                        "LLM (Qwen / Mistral / Gemma / Ovis)",
                        "Path to the LLM text encoder (--llm) used by Qwen Image, FLUX.2, Z-Image, LTX-2, Ovis, Anima.",
                        "",
                        folder=self.model_folder,
                    ),
                    ExtraSettings.EntrySetting(
                        "clip_l_path",
                        "CLIP-L",
                        "Path to the CLIP-L text encoder (--clip_l) used by SD3.5, FLUX.1, Kontext.",
                        "",
                        folder=self.model_folder,
                    ),
                    ExtraSettings.EntrySetting(
                        "clip_g_path",
                        "CLIP-G",
                        "Path to the CLIP-G text encoder (--clip_g) used by SD3.5.",
                        "",
                        folder=self.model_folder,
                    ),
                    ExtraSettings.EntrySetting(
                        "t5xxl_path",
                        "T5XXL",
                        "Path to the T5XXL text encoder (--t5xxl) used by FLUX.1, Kontext, Chroma, SD3.5.",
                        "",
                        folder=self.model_folder,
                    ),
                    ExtraSettings.EntrySetting(
                        "video_vae_path",
                        "Video VAE",
                        "Path to the video VAE (used as --vae for LTX-2).",
                        "",
                        folder=self.model_folder,
                    ),
                    ExtraSettings.EntrySetting(
                        "audio_vae_path",
                        "Audio VAE",
                        "Path to the audio VAE (--audio-vae) used by LTX-2.",
                        "",
                        folder=self.model_folder,
                    ),
                    ExtraSettings.EntrySetting(
                        "embeddings_connectors_path",
                        "Embeddings Connectors",
                        "Path to the embeddings connectors safetensors (--embeddings-connectors) used by LTX-2.",
                        "",
                        folder=self.model_folder,
                    ),
                    ExtraSettings.ToggleSetting(
                        "offload_to_cpu",
                        "Offload to CPU",
                        "Place weights in RAM and load them into VRAM on demand (--offload-to-cpu).",
                        False,
                    ),
                    ExtraSettings.ToggleSetting(
                        "diffusion_fa",
                        "Diffusion Flash Attention",
                        "Use flash attention in the diffusion model (--diffusion-fa).",
                        False,
                    ),
                    ExtraSettings.ToggleSetting(
                        "vae_tiling",
                        "VAE Tiling",
                        "Process VAE in tiles to reduce memory usage (--vae-tiling).",
                        False,
                    ),
                    ExtraSettings.ToggleSetting(
                        "clip_on_cpu",
                        "Keep CLIP on CPU",
                        "Keep CLIP text encoders in CPU memory (--clip-on-cpu).",
                        False,
                    ),
                    ExtraSettings.ToggleSetting(
                        "vae_on_cpu",
                        "Keep VAE on CPU",
                        "Keep VAE in CPU memory (--vae-on-cpu).",
                        False,
                    ),
                    ExtraSettings.ToggleSetting(
                        "chroma_disable_dit_mask",
                        "Chroma: Disable DiT Mask",
                        "Disable DiT mask for Chroma (--chroma-disable-dit-mask).",
                        False,
                    ),
                    ExtraSettings.ToggleSetting(
                        "chroma_enable_t5_mask",
                        "Chroma: Enable T5 Mask",
                        "Enable T5 mask for Chroma (--chroma-enable-t5-mask).",
                        False,
                    ),
                    ExtraSettings.ToggleSetting(
                        "qwen_image_zero_cond_t",
                        "Qwen Image: Zero Cond T",
                        "Enable zero_cond_t for Qwen Image (--qwen-image-zero-cond-t).",
                        False,
                    ),
                    ExtraSettings.ScaleSetting(
                        "flow_shift",
                        "Flow Shift",
                        "Shift value for Flow models (e.g. 3 for Qwen Image). 0 = auto.",
                        0.0, 0.0, 10.0, 2,
                    ),
                    ExtraSettings.ComboSetting(
                        "prediction",
                        "Prediction Type",
                        "Override the model's prediction type (--prediction).",
                        ["auto", "eps", "v", "edm_v", "sd3_flow", "flux_flow", "flux2_flow"],
                        "auto",
                    ),
                    ExtraSettings.ComboSetting(
                        "cache_mode",
                        "Cache Mode",
                        "Caching method for faster inference (--cache-mode).",
                        ["none", "easycache", "ucache", "dbcache", "spectrum"],
                        "none",
                    ),
                    ExtraSettings.ComboSetting(
                        "rng",
                        "RNG",
                        "Random number generator backend (--rng). 'cuda' matches A1111, 'cpu' matches ComfyUI.",
                        ["cuda", "cpu", "std_default"],
                        "cuda",
                    ),
                    ExtraSettings.MultilineEntrySetting(
                        "extra_cli_args",
                        "Extra CLI Arguments",
                        "Additional command-line arguments passed verbatim to sd-cli / sd-server. One per line, e.g. '--vae-tile-size 64x64'.",
                        "",
                    ),
                ],
            )
        )

        return settings

    def _get_model_list(self, update=False, family=None):
        """Get available model files in the model folder.

        Library-installed variants are listed first with their display name,
        followed by loose model files at the root of the model folder, and
        finally any files found in the user-configured custom models directory.

        Files that are part of any installed variant's manifest (such as a
        variant's VAE, LLM, T5XXL, etc.) are intentionally hidden from the
        list, since the variant is already represented by its diffusion entry.

        Args:
            update: If True, refresh the settings UI after collecting the list.
            family: If set, only include library variants whose ``family``
                matches this string and only loose files with a matching
                variant name. Used by the image-editing model picker to
                restrict the list to ``qwen_image_edit`` variants.
        """
        model_list = []
        seen = set()

        variant_owned_paths = self._variant_owned_paths()

        for entry in SD_MODELS:
            if family is not None and entry.get("family") != family:
                continue
            manifest_path = self._manifest_path(entry["id"])
            if os.path.exists(manifest_path):
                try:
                    with open(manifest_path, "r") as f:
                        manifest = json.load(f)
                except Exception:
                    continue
                diffusion_path = manifest.get("files", {}).get("diffusion")
                if not diffusion_path or not os.path.exists(diffusion_path):
                    continue
                relative_path = os.path.relpath(diffusion_path, self.model_folder)
                model_list.append((entry["display"], relative_path))
                seen.add(entry["display"])

        for root, dirs, files in os.walk(self.model_folder):
            dirs[:] = [d for d in dirs if d != "_shared" and not d.startswith(".")]
            for file in files:
                if file.endswith((".safetensors", ".ckpt", ".pth", ".pt", ".gguf")):
                    file_path = os.path.join(root, file)
                    abs_path = os.path.abspath(file_path)
                    if abs_path in variant_owned_paths:
                        continue
                    file_name = os.path.splitext(file)[0]
                    relative_path = os.path.relpath(file_path, self.model_folder)
                    display_name = file_name
                    if display_name in seen:
                        display_name = f"{file_name} (custom)"
                    seen.add(display_name)
                    model_list.append((display_name, relative_path))

        custom_dir = self.get_setting("custom_models_dir", False, "") or ""
        if custom_dir:
            custom_dir = os.path.expanduser(custom_dir)
            if os.path.isdir(custom_dir) and os.path.abspath(custom_dir) != os.path.abspath(self.model_folder):
                for root, _, files in os.walk(custom_dir):
                    for file in files:
                        if file.endswith((".safetensors", ".ckpt", ".pth", ".pt", ".gguf")):
                            abs_path = os.path.abspath(os.path.join(root, file))
                            if abs_path in variant_owned_paths:
                                continue
                            file_name = os.path.splitext(file)[0]
                            display_name = file_name
                            if display_name in seen:
                                display_name = f"{file_name} (custom)"
                            seen.add(display_name)
                            model_list.append((display_name, abs_path))

        if update:
            self.settings_update()

        return tuple(model_list)

    def _variant_owned_paths(self) -> set:
        """Return the set of absolute file paths that belong to any installed
        variant's manifest. Used to hide auxiliary files (VAE, LLM, T5XXL, ...)
        from the model list."""
        paths = set()
        for entry in SD_MODELS:
            manifest = self._read_manifest(entry["id"])
            if not manifest:
                continue
            for path in (manifest.get("files", {}) or {}).values():
                if not path:
                    continue
                try:
                    paths.add(os.path.abspath(path))
                except Exception:
                    pass
        return paths

    def _sync_special_settings_with_model(self):
        """Auto-populate or clear the advanced special settings based on the
        currently selected model.

        - If the model matches an installed variant's manifest, fill in the
          VAE / LLM / CLIP / T5XXL / audio-VAE / embeddings paths from the
          manifest so the user doesn't have to.
        - If the model is a custom (loose) file and the previously synced
          model was a variant, clear those special settings so stale values
          from a previous variant don't bleed through.
        - If both the previous and the current model are custom, leave the
          settings alone so the user's manual edits are preserved.

        The sync runs at most once per model change, tracked via the
        ``_last_synced_model`` setting which is persisted across sessions.
        """
        try:
            current_model = self.get_setting("model", False, "") or ""
        except Exception:
            current_model = ""
        try:
            last_synced = self.get_setting("_last_synced_model", False, "") or ""
        except Exception:
            last_synced = ""
        if current_model == last_synced:
            return

        special_settings = [
            "vae_path",
            "llm_path",
            "clip_l_path",
            "clip_g_path",
            "t5xxl_path",
            "audio_vae_path",
            "embeddings_connectors_path",
        ]

        current_is_variant = self._variant_for_model_path(current_model) is not None
        previous_was_variant = (
            last_synced != "" and self._variant_for_model_path(last_synced) is not None
        )

        if current_is_variant:
            _, manifest = self._variant_for_model_path(current_model)
            files = manifest.get("files", {}) or {}
            for role, key in (
                ("vae", "vae_path"),
                ("llm", "llm_path"),
                ("clip_l", "clip_l_path"),
                ("clip_g", "clip_g_path"),
                ("t5xxl", "t5xxl_path"),
                ("audio_vae", "audio_vae_path"),
                ("embeddings", "embeddings_connectors_path"),
            ):
                value = files.get(role) or ""
                try:
                    self.set_setting(key, value)
                except Exception:
                    pass
        elif previous_was_variant:
            for key in special_settings:
                try:
                    self.set_setting(key, "")
                except Exception:
                    pass

        try:
            self.set_setting("_last_synced_model", current_model)
        except Exception:
            pass

    def _sync_edit_settings_with_model(self):
        """Auto-populate or clear the image-editing settings based on the
        currently selected ``edit_model``.

        Mirrors :meth:`_sync_special_settings_with_model` for the edit
        pipeline: when the user picks a ``qwen_image_edit`` library variant
        the VAE / LLM / llm_vision paths and the ``--qwen-image-zero-cond-t``
        toggle are filled in from the variant manifest; when the user picks a
        custom (loose) file the previously synced values are cleared so they
        don't bleed across models. If both the previous and current model are
        custom, the user's manual edits are preserved.

        The sync runs at most once per model change, tracked via the
        ``_last_synced_edit_model`` setting which is persisted across sessions.
        """
        try:
            current_model = self.get_setting("edit_model", False, "") or ""
        except Exception:
            current_model = ""
        try:
            last_synced = self.get_setting("_last_synced_edit_model", False, "") or ""
        except Exception:
            last_synced = ""
        if current_model == last_synced:
            return

        edit_settings = [
            "edit_vae_path",
            "edit_llm_path",
            "edit_llm_vision_path",
        ]

        current_is_variant = self._variant_for_model_path(current_model) is not None
        previous_was_variant = (
            last_synced != "" and self._variant_for_model_path(last_synced) is not None
        )

        if current_is_variant:
            _, manifest = self._variant_for_model_path(current_model)
            files = manifest.get("files", {}) or {}
            cli_extra = manifest.get("cli_extra", []) or []
            for role, key in (
                ("vae", "edit_vae_path"),
                ("llm", "edit_llm_path"),
                ("llm_vision", "edit_llm_vision_path"),
            ):
                value = files.get(role) or ""
                try:
                    self.set_setting(key, value)
                except Exception:
                    pass
            try:
                self.set_setting(
                    "edit_qwen_image_zero_cond_t",
                    "--qwen-image-zero-cond-t" in cli_extra,
                )
            except Exception:
                pass
        elif previous_was_variant:
            for key in edit_settings:
                try:
                    self.set_setting(key, "")
                except Exception:
                    pass
            try:
                self.set_setting("edit_qwen_image_zero_cond_t", False)
            except Exception:
                pass

        try:
            self.set_setting("_last_synced_edit_model", current_model)
        except Exception:
            pass


    def _get_lora_dir(self) -> str:
        """Return the LoRA folder path from settings, or the default."""
        path = self.get_setting("lora_folder_path", True, "")
        if path:
            path = os.path.expanduser(path)
            return path
        return self.lora_folder

    def _is_lora_enabled(self) -> bool:
        """Check if LoRA support is enabled."""
        return self.get_setting("enable_lora", False, False)

    def _resolve_model_path(self, value: str) -> str:
        """Resolve a model setting value to an absolute path."""
        if not value:
            return ""
        if os.path.isabs(value):
            return value
        return os.path.join(self.model_folder, value)

    def _model_arg(self, model_path: str) -> list:
        """Return the appropriate CLI flag for the given model file path.

        stable-diffusion.cpp has two ways to load the diffusion model:
        - ``-m`` / ``--model`` (``model_path``) loads tensors *without* adding
          a prefix. Use this for bundled safetensors files (SD/SDXL) whose
          tensors already carry the correct naming (``model.diffusion_model.*``,
          ``conditioner.embedders.*``, ``first_stage_model.*``).
        - ``--diffusion-model`` (``diffusion_model_path``) loads tensors and
          adds the ``model.diffusion_model.`` prefix to every tensor that does
          not already start with it. Use this for separate diffusion-only
          files (typically GGUFs) whose tensors have no prefix.

        Heuristic: pass ``--diffusion-model`` for ``.gguf`` files (so the
        prefix is added) and ``-m`` for everything else (safetensors, torch
        checkpoints, …).
        """
        if not model_path:
            return ["-m", ""]
        if model_path.lower().endswith(".gguf"):
            return ["--diffusion-model", model_path]
        return ["-m", model_path]

    # ── Model library ──────────────────────────────────────────────

    def _manifest_path(self, variant_id: str) -> str:
        """Return the manifest path for a given library variant."""
        return os.path.join(self.model_folder, variant_id, ".sd_model.json")

    def _read_manifest(self, variant_id: str):
        """Read a variant manifest from disk, or return None if missing/invalid."""
        manifest_path = self._manifest_path(variant_id)
        if not os.path.exists(manifest_path):
            return None
        try:
            with open(manifest_path, "r") as f:
                return json.load(f)
        except Exception:
            return None

    def _variant_for_model_path(self, model_path: str):
        """Find the library entry whose installed diffusion path matches the given
        absolute or relative model path, or None if it's a loose file."""
        if not model_path:
            return None
        abs_path = self._resolve_model_path(model_path)
        for entry in SD_MODELS:
            manifest = self._read_manifest(entry["id"])
            if not manifest:
                continue
            if manifest.get("files", {}).get("diffusion") == abs_path:
                return entry, manifest
        return None

    def _build_advanced_args(self, variant_manifest=None, edit_mode=False):
        """Build a list of additional CLI args from advanced settings and the
        optional variant manifest.

        Variant-provided defaults are used when the user has not overridden
        the corresponding path setting.

        Args:
            variant_manifest: The manifest dict of the currently selected
                variant. Used for variant-provided defaults.
            edit_mode: When True, the setting keys used for the encoder paths
                and zero-cond-t flag are read from the ``edit_*`` settings
                (used by the Qwen Image Edit pipeline), and ``--llm_vision``
                is emitted when an ``llm_vision`` role is present in the
                manifest or in the user settings.
        """
        args = []

        def resolve_path(setting_key, manifest_role):
            value = self.get_setting(setting_key, True, "") or ""
            if value and os.path.exists(os.path.expanduser(value)):
                return os.path.expanduser(value)
            if variant_manifest is not None:
                files = variant_manifest.get("files", {}) or {}
                p = files.get(manifest_role)
                if p and os.path.exists(p):
                    return p
            return ""

        def add_file_arg(flag, setting_key, manifest_role):
            path = resolve_path(setting_key, manifest_role)
            if path:
                args.extend([flag, path])

        vae_key = "edit_vae_path" if edit_mode else "vae_path"
        llm_key = "edit_llm_path" if edit_mode else "llm_path"
        add_file_arg("--vae", vae_key, "vae")
        add_file_arg("--llm", llm_key, "llm")
        add_file_arg("--clip_l", "clip_l_path", "clip_l")
        add_file_arg("--clip_g", "clip_g_path", "clip_g")
        add_file_arg("--t5xxl", "t5xxl_path", "t5xxl")
        add_file_arg("--audio-vae", "audio_vae_path", "audio_vae")
        add_file_arg("--embeddings-connectors", "embeddings_connectors_path", "embeddings")
        if edit_mode:
            add_file_arg("--llm_vision", "edit_llm_vision_path", "llm_vision")

        if self.get_setting("offload_to_cpu", False, False):
            args.append("--offload-to-cpu")
        if self.get_setting("diffusion_fa", False, False):
            args.append("--diffusion-fa")
        if self.get_setting("vae_tiling", False, False):
            args.append("--vae-tiling")
        if self.get_setting("clip_on_cpu", False, False):
            args.append("--clip-on-cpu")
        if self.get_setting("vae_on_cpu", False, False):
            args.append("--vae-on-cpu")
        if self.get_setting("chroma_disable_dit_mask", False, False):
            args.append("--chroma-disable-dit-mask")
        if self.get_setting("chroma_enable_t5_mask", False, False):
            args.append("--chroma-enable-t5-mask")
        zero_cond_setting = "edit_qwen_image_zero_cond_t" if edit_mode else "qwen_image_zero_cond_t"
        if self.get_setting(zero_cond_setting, False, False):
            args.append("--qwen-image-zero-cond-t")

        flow_shift = self.get_setting("flow_shift", True, 0.0)
        try:
            flow_shift = float(flow_shift)
        except (TypeError, ValueError):
            flow_shift = 0.0
        if flow_shift > 0:
            args.extend(["--flow-shift", str(flow_shift)])

        prediction = self.get_setting("prediction", True, "auto")
        if prediction and prediction != "auto":
            args.extend(["--prediction", str(prediction)])

        cache_mode = self.get_setting("cache_mode", True, "none")
        if cache_mode and cache_mode != "none":
            args.extend(["--cache-mode", str(cache_mode)])

        rng = self.get_setting("rng", True, "cuda")
        if rng and rng != "cuda":
            args.extend(["--rng", str(rng)])

        extra_cli_key = "edit_extra_cli_args" if edit_mode else "extra_cli_args"
        extra_cli = self.get_setting(extra_cli_key, True, "") or ""
        for line in extra_cli.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            args.extend(line.split())

        if variant_manifest is not None:
            for extra in variant_manifest.get("cli_extra", []) or []:
                if extra not in args:
                    args.append(extra)

        return args

    # Model library integration (used by ModelLibraryWindow)
    def fetch_models(self):
        """Return the catalog of installable models for the library window."""
        models = []
        for entry in SD_MODELS:
            description = entry["description"]
            tags = list(entry.get("tags", []))
            icon_name, icon_color = get_model_icon(
                entry["display"],
                tags,
            )
            models.append(LibraryModel(
                id=entry["id"],
                name=entry["display"],
                description=description,
                tags=tags,
                is_pinned=False,
                is_installed=self.model_installed(entry["id"]),
                icon_name=icon_name,
                icon_color=icon_color,
            ))
        return models

    def model_installed(self, model: str) -> bool:
        """Check whether a library variant is fully installed."""
        if model in self.downloading and self.downloading[model].get("progress", 1.0) < 1.0:
            return False
        return os.path.exists(self._manifest_path(model))

    def get_percentage(self, model: str) -> float:
        """Return the current download progress (0.0 - 1.0) for a model variant."""
        if model in self.downloading:
            return self.downloading[model].get("progress", 0.0)
        if self.model_installed(model):
            return 1.0
        return 0.0

    def install_model(self, model_id: str):
        """Toggle install / uninstall of a library variant."""
        if model_id in self.downloading and self.downloading[model_id].get("progress", 0.0) < 1.0:
            return
        if self.model_installed(model_id):
            self._uninstall_variant(model_id)
            GLib.idle_add(self.settings_update)
            return

        self.downloading[model_id] = {"status": True, "progress": 0.0, "files_done": 0, "files_total": 0}
        GLib.idle_add(self.settings_update)
        self._download_variant(model_id)

    def _download_variant(self, model_id: str):
        """Download every file for a variant, with progress reporting."""
        entry = next((e for e in SD_MODELS if e["id"] == model_id), None)
        if entry is None:
            if model_id in self.downloading:
                del self.downloading[model_id]
            GLib.idle_add(self.settings_update)
            return

        try:
            files = entry.get("files", []) or []
            total = len(files)
            self.downloading[model_id]["files_total"] = total
            self.downloading[model_id]["files_done"] = 0
            self.downloading[model_id]["progress"] = 0.0

            variant_dir = os.path.join(self.model_folder, model_id)
            os.makedirs(variant_dir, exist_ok=True)

            resolved_files = []
            for fdef in files:
                filename = fdef["filename"]
                if fdef.get("shared"):
                    dest = os.path.join(self.shared_folder, filename)
                else:
                    dest = os.path.join(variant_dir, filename)
                resolved_files.append({**fdef, "dest": dest})

            for idx, fdef in enumerate(resolved_files):
                self.downloading[model_id]["current_file"] = fdef["filename"]
                self.downloading[model_id]["files_done"] = idx
                if os.path.exists(fdef["dest"]) and os.path.getsize(fdef["dest"]) > 0:
                    self._advance_progress(model_id, idx, total, 1.0)
                    continue
                self._download_file(fdef["url"], fdef["dest"], model_id, idx, total)

            manifest = {
                "id": entry["id"],
                "family": entry.get("family", ""),
                "display": entry.get("display", ""),
                "description": entry.get("description", ""),
                "cli_extra": list(entry.get("cli_extra", []) or []),
                "files": {},
            }
            for fdef in resolved_files:
                manifest["files"][fdef["role"]] = fdef["dest"]

            with open(self._manifest_path(model_id), "w") as f:
                json.dump(manifest, f, indent=2)

            self.downloading[model_id]["progress"] = 1.0
            self.downloading[model_id]["status"] = False
        except Exception as e:
            print(f"Failed to install {model_id}: {e}")
            import traceback
            traceback.print_exc()
            self.downloading.pop(model_id, None)
            raise
        finally:
            if model_id in self.downloading:
                self.downloading[model_id]["status"] = False
            GLib.idle_add(self.settings_update)
            GLib.timeout_add(2000, self._cleanup_download_entry, model_id)

    def _advance_progress(self, model_id, idx, total, file_progress):
        if model_id not in self.downloading:
            return
        per_file = 1.0 / max(total, 1)
        self.downloading[model_id]["progress"] = min((idx + file_progress) * per_file, 1.0)

    def _download_file(self, url, dest, model_id, idx, total):
        tmp_path = dest + ".part"
        task = current_download_task()
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            with requests.get(url, stream=True, timeout=300) as resp:
                resp.raise_for_status()
                total_bytes = int(resp.headers.get("content-length", 0))
                downloaded = 0
                first_chunk = None
                if task is not None:
                    task.update(
                        phase=_("Downloading {name}").format(name=os.path.basename(dest)),
                        reset_progress=True,
                        cancellable=True,
                    )
                with open(tmp_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1024 * 256):
                        if not chunk:
                            continue
                        if task is not None:
                            task.check_cancelled()
                        if first_chunk is None:
                            first_chunk = chunk
                        f.write(chunk)
                        downloaded += len(chunk)
                        file_progress = downloaded / total_bytes if total_bytes > 0 else 0.0
                        self._advance_progress(model_id, idx, total, file_progress)
                        if task is not None:
                            task.update(
                                fraction=file_progress if total_bytes > 0 else None,
                                transferred_bytes=downloaded,
                                total_bytes=total_bytes if total_bytes > 0 else None,
                            )
            self._validate_downloaded_file(tmp_path, dest, first_chunk)
            if task is not None:
                task.update(cancellable=False, phase=_("Finalizing model file"))
            os.replace(tmp_path, dest)
            self._advance_progress(model_id, idx, total, 1.0)
        except Exception:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
            raise

    def _validate_downloaded_file(self, tmp_path, dest, first_chunk):
        """Verify the downloaded file is a real model file and not an HTML
        error page or empty response. Raises a descriptive exception on failure."""
        if first_chunk is None:
            raise RuntimeError(f"Download produced no data for {os.path.basename(dest)}")

        sample = first_chunk[:16] if len(first_chunk) >= 16 else first_chunk
        # Detect HTML (login pages, error pages)
        head = sample.lstrip().lower()
        if head.startswith(b"<!doctype html") or head.startswith(b"<html") or sample.startswith(b"<?xml"):
            raise RuntimeError(
                f"Downloaded file for {os.path.basename(dest)} is an HTML page, "
                "not a model file. The URL may be gated, moved, or behind a login. "
                "Check the URL or the model's repository on Hugging Face."
            )
        # Detect empty / very small payloads (anything that doesn't even look like a
        # binary model header)
        try:
            file_size = os.path.getsize(tmp_path)
        except OSError:
            file_size = 0
        if file_size < 1024:
            raise RuntimeError(
                f"Downloaded file for {os.path.basename(dest)} is too small "
                f"({file_size} bytes). The URL may be broken or returning an error."
            )
        # Detect known model formats by magic header
        # - GGUF: 'GGUF' (0x46475547) at offset 0
        # - Safetensors: little-endian uint64 of JSON header length at offset 0
        is_gguf = sample[:4] == b"GGUF"
        is_safetensors = False
        if len(sample) >= 8:
            try:
                header_len = int.from_bytes(sample[:8], "little")
                # Safetensors header is reasonable JSON, should be 50-1MB
                if 16 < header_len < 1024 * 1024:
                    is_safetensors = True
            except Exception:
                pass
        if not (is_gguf or is_safetensors):
            raise RuntimeError(
                f"Downloaded file for {os.path.basename(dest)} has an unknown header "
                f"({sample[:8]!r}). Expected GGUF or safetensors format."
            )

    def _cleanup_download_entry(self, model_id):
        if model_id in self.downloading and self.downloading[model_id].get("progress", 0.0) >= 1.0:
            try:
                del self.downloading[model_id]
            except KeyError:
                pass
            self.settings_update()
        return False

    def _uninstall_variant(self, model_id):
        """Remove an installed variant. Shared files are kept if used by other variants."""
        manifest = self._read_manifest(model_id)
        variant_dir = os.path.join(self.model_folder, model_id)

        shared_refs = {}
        for other in SD_MODELS:
            if other["id"] == model_id:
                continue
            other_manifest = self._read_manifest(other["id"])
            if not other_manifest:
                continue
            for role, path in (other_manifest.get("files", {}) or {}).items():
                shared_refs[os.path.abspath(path)] = shared_refs.get(os.path.abspath(path), 0) + 1

        if manifest:
            for role, path in (manifest.get("files", {}) or {}).items():
                try:
                    if os.path.commonpath([os.path.abspath(path), os.path.abspath(self.shared_folder)]) == os.path.abspath(self.shared_folder):
                        abs_path = os.path.abspath(path)
                        if shared_refs.get(abs_path, 0) == 0:
                            try:
                                os.remove(abs_path)
                            except FileNotFoundError:
                                pass
                except ValueError:
                    pass

        if os.path.exists(variant_dir):
            shutil.rmtree(variant_dir, ignore_errors=True)

    def open_model_library(self, button):
        try:
            root = button.get_root()
        except Exception:
            root = None
        win = ModelLibraryWindow(self, root)
        win.present()

    def _is_binary_installed(self) -> bool:
        """Check if the sd-cli binary is installed and executable."""
        if os.path.exists(self.sd_binary_path) and os.access(self.sd_binary_path, os.X_OK):
            return True
        if os.path.exists(self.sd_server_binary_path) and os.access(self.sd_server_binary_path, os.X_OK):
            return True
        # Also check system PATH when use_system_sd is enabled
        if is_flatpak() and self.get_setting("use_system_sd", False, False):
            return shutil.which("sd-cli") is not None
        return False

    def _get_binary_path(self) -> str:
        """Get the path to the sd-cli binary."""
        use_system = is_flatpak() and self.get_setting("use_system_sd", False, False)
        if use_system:
            return "sd-cli"
        if self.get_setting("gpu_acceleration", False, False) and self._is_binary_installed():
            return self.sd_binary_path
        # Fall back to system binary
        return "sd-cli"

    # ── Server mode ─────────────────────────────────────────────────

    def _get_server_binary_path(self) -> str:
        """Get the path to the sd-server binary."""
        use_system = is_flatpak() and self.get_setting("use_system_sd", False, False)
        if use_system:
            return "sd-server"
        if self.get_setting("gpu_acceleration", False, False) and os.path.exists(self.sd_server_binary_path):
            return self.sd_server_binary_path
        return "sd-server"

    def _get_server_port(self) -> int:
        """Get the configured server port."""
        return self.get_setting("server_port", True, 17860)

    def _is_server_running(self) -> bool:
        """Check if the sd-server is already running on the configured port."""
        port = self._get_server_port()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                result = s.connect_ex(("127.0.0.1", port))
                return result == 0
        except Exception:
            return False

    def _start_server(self) -> None:
        """Start the sd-server process if not already running."""
        if self._server.is_running:
            return
        if self._is_server_running():
            return

        binary = self._get_server_binary_path()

        # Validate that the binary exists
        if binary == self.sd_server_binary_path and not os.path.exists(binary):
            raise FileNotFoundError(
                f"sd-server binary not found at {binary}. "
                "Please reinstall stable-diffusion.cpp or disable server mode."
            )
        if binary == "sd-server" and not shutil.which("sd-server"):
            raise FileNotFoundError(
                "sd-server not found on system PATH. "
                "Please install it or disable server mode."
            )

        model = self._resolve_model_path(self.get_setting("model"))
        port = self._get_server_port()
        variant = self._variant_for_model_path(self.get_setting("model"))
        variant_manifest = variant[1] if variant else None

        cmd = [
            binary,
            *self._model_arg(model),
            "--listen-port", str(port),
        ]

        if self._is_lora_enabled():
            cmd.extend(["--lora-model-dir", self._get_lora_dir()])

        clip_skip = self.get_setting("clip_skip", True, -1)
        if clip_skip > 0:
            cmd.extend(["--clip-skip", str(int(clip_skip))])

        cmd.extend(self._build_advanced_args(variant_manifest))

        use_system = is_flatpak() and self.get_setting("use_system_sd", False, False)
        use_spawn = is_flatpak() and (use_system or (os.path.exists(self.sd_server_binary_path) and self.get_setting("gpu_acceleration", False, False)))

        # Capture stderr so a startup failure can be reported to the user.
        start_kwargs = {"stderr": subprocess.PIPE}
        if binary == self.sd_server_binary_path:
            bin_dir = os.path.dirname(binary)
            if use_spawn:
                cmd = get_spawn_command() + [f"--env=LD_LIBRARY_PATH={bin_dir}"] + cmd
            else:
                env = os.environ.copy()
                existing = env.get("LD_LIBRARY_PATH", "")
                env["LD_LIBRARY_PATH"] = bin_dir if not existing else f"{bin_dir}:{existing}"
                start_kwargs["env"] = env
        elif use_spawn:
            cmd = get_spawn_command() + cmd

        proc = self._server.start(cmd, **start_kwargs)

        # Wait for the server to be ready
        timeout = 120
        start = time.time()
        while time.time() - start < timeout:
            # Check if the process died prematurely
            if proc.poll() is not None:
                returncode = proc.returncode
                stderr_output = self._server.get_output(which="stderr").strip()
                self._server.stop()
                error_msg = f"sd-server exited with code {returncode}"
                if stderr_output:
                    error_msg += f": {stderr_output}"
                raise RuntimeError(error_msg)
            if self._is_server_running():
                return
            time.sleep(0.5)

        raise RuntimeError("sd-server failed to start within 120 seconds")

    def _stop_server(self) -> None:
        """Stop the sd-server process."""
        self._server.stop()

    def destroy(self):
        """Stop the sd-server on shutdown."""
        self._stop_server()

    def _generate_via_server(self, prompt: str, output_file: str) -> str:
        """Generate an image using the sd-server HTTP API.

        Posts to /sdapi/v1/txt2img and decodes the base64 image response.
        """
        port = self._get_server_port()
        url = f"http://127.0.0.1:{port}/sdapi/v1/txt2img"

        width = self.get_setting("width", True, 512)
        height = self.get_setting("height", True, 512)
        steps = self.get_setting("steps", True, 20)
        cfg_scale = self.get_setting("cfg_scale", True, 7.0)
        seed = self.get_setting("seed", True, -1)
        sampling_method = self.get_setting("sampling_method", True, "euler_a")
        clip_skip = self.get_setting("clip_skip", True, -1)
        negative_prompt_template = self.get_setting("negative_prompt_template", True, "")
        positive_prompt_template = self.get_setting("positive_prompt_template", True, "[input]")

        prompt = positive_prompt_template.replace("[input]", prompt)

        negative_prompt = ""
        if negative_prompt_template:
            negative_prompt = negative_prompt_template.replace("[input]", prompt)

        payload = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "width": int(width),
            "height": int(height),
            "steps": int(steps),
            "cfg_scale": float(cfg_scale),
            "seed": int(seed),
            "sampler_name": str(sampling_method),
        }

        if clip_skip > 0:
            payload["clip_skip"] = int(clip_skip)

        try:
            resp = requests.post(url, json=payload, timeout=600)
            resp.raise_for_status()
            data = resp.json()

            images = data.get("images", [])
            if not images:
                raise RuntimeError("sd-server returned no images")

            import base64
            image_bytes = base64.b64decode(images[0])
            with open(output_file, "wb") as f:
                f.write(image_bytes)

            return output_file

        except requests.exceptions.ConnectionError:
            raise RuntimeError(f"Could not connect to sd-server on port {port}. Is it running?")
        except requests.exceptions.Timeout:
            raise TimeoutError("Image generation timed out after 10 minutes")
        except Exception as e:
            raise RuntimeError(f"sd-server generation failed: {e}")

    # ── Image generation ────────────────────────────────────────────

    def generate_image(self, prompt: str, msg_uuid: str, output_file: str = None) -> str:
        """Generate an image using stable-diffusion.cpp.

        Args:
            prompt: The text prompt for image generation
            msg_uuid: Unique message identifier
            output_file: Path to save the generated image

        Returns:
            str: Local file path to the generated image
        """
        if output_file is None:
            output_file = os.path.join(self.cache_dir, f"{msg_uuid}.png")

        # Use server mode if enabled
        if self.get_setting("use_server", False, False):
            self._start_server()
            return self._generate_via_server(prompt, output_file)

        model = self._resolve_model_path(self.get_setting("model"))
        if not model or not os.path.exists(model):
            raise FileNotFoundError(f"Model not found: {model}")

        binary = self._get_binary_path()
        width = self.get_setting("width", True, 512)
        height = self.get_setting("height", True, 512)
        steps = self.get_setting("steps", True, 20)
        cfg_scale = self.get_setting("cfg_scale", True, 7.0)
        seed = self.get_setting("seed", True, -1)
        sampling_method = self.get_setting("sampling_method", True, "euler_a")
        clip_skip = self.get_setting("clip_skip", True, -1)
        negative_prompt_template = self.get_setting("negative_prompt_template", True, "")
        positive_prompt_template = self.get_setting("positive_prompt_template", True, "[input]")

        variant = self._variant_for_model_path(self.get_setting("model"))
        variant_manifest = variant[1] if variant else None

        prompt = positive_prompt_template.replace("[input]", prompt)

        if negative_prompt_template:
            negative_prompt = negative_prompt_template.replace("[input]", prompt)

        cmd = [
            binary,
            *self._model_arg(model),
            "-p", prompt,
            "-o", output_file,
            "-W", str(int(width)),
            "-H", str(int(height)),
            "--steps", str(int(steps)),
            "--cfg-scale", str(float(cfg_scale)),
            "-s", str(int(seed)),
            "--sampling-method", str(sampling_method),
        ]

        if clip_skip > 0:
            cmd.extend(["--clip-skip", str(int(clip_skip))])

        if self._is_lora_enabled():
            cmd.extend(["--lora-model-dir", self._get_lora_dir()])

        cmd.extend(self._build_advanced_args(variant_manifest))

        if negative_prompt_template:
            cmd.extend(["-n", negative_prompt])

        # Use flatpak-spawn for built binaries in Flatpak, and also for system binary
        use_system = is_flatpak() and self.get_setting("use_system_sd", False, False)
        use_spawn = is_flatpak() and (use_system or (self._is_binary_installed() and self.get_setting("gpu_acceleration", False, False)))
        if use_spawn:
            cmd = get_spawn_command() + cmd

        env = os.environ.copy()
        # Add the binary's directory to LD_LIBRARY_PATH so it finds libstable-diffusion.so
        if binary == self.sd_binary_path:
            bin_dir = os.path.dirname(binary)
            if use_spawn:
                # flatpak-spawn doesn't inherit env vars; pass via --env= flags
                cmd = cmd[:1] + [f"--env=LD_LIBRARY_PATH={bin_dir}"] + cmd[1:]
            else:
                existing = env.get("LD_LIBRARY_PATH", "")
                env["LD_LIBRARY_PATH"] = bin_dir if not existing else f"{bin_dir}:{existing}"

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env)
            if result.returncode != 0:
                print(f"sd-cli error: {result.stderr}")
                raise RuntimeError(f"sd-cli failed: {result.stderr}")

            if not os.path.exists(output_file):
                # sd-cli may append .png automatically
                alt_output = output_file
                if not alt_output.endswith(".png"):
                    alt_output = output_file + ".png"
                if os.path.exists(alt_output):
                    return alt_output
                raise FileNotFoundError(f"Output file not created: {output_file}")

            return output_file

        except subprocess.TimeoutExpired:
            raise TimeoutError("Image generation timed out after 10 minutes")
        except Exception as e:
            raise RuntimeError(f"Image generation failed: {e}")

    # ── Tools ───────────────────────────────────────────────────────

    def get_tools(self) -> list:
        """Return the list of tools exposed by this handler.

        The base class returns a single ``generate_image`` tool. We add a
        second ``edit_image`` tool when the user has enabled image editing
        in the handler's settings. The tool registry is rebuilt whenever
        a setting on this handler changes, so toggling image editing has
        an immediate effect on the tool set the LLM sees.
        """
        from ...ui.widgets.image_generator import ImageGeneratorWidget
        # Use closures so the Tool instance picks up the right bound methods
        # even though get_tools() is called multiple times.
        tools = [Tool(
            "generate_image",
            "Generate an image from a text prompt. Use detailed, descriptive prompts with English words separated by commas.",
            self._generate_image_tool,
            title="Generate Image",
            restore_func=self._restore_image_tool,
            icon_name="insert-image-symbolic",
        )]

        if self.get_setting("enable_image_editing", False, False):
            def _edit_image_tool(prompt: str, reference_image: str, msg_uuid=None):
                return self._edit_image_tool(prompt, reference_image, msg_uuid)

            def _edit_image_restore(msg_uuid, prompt):
                return self._edit_image_restore(msg_uuid, prompt)

            tools.append(Tool(
                "edit_image",
                (
                    "Edit an existing image with a text instruction. The "
                    "'reference_image' argument must be the path (or data: URI) "
                    "of the input image — typically the value inside a ```image``` "
                    "codeblock in the most recent user message. Requires the "
                    "'Enable Image Editing' toggle in stable-diffusion.cpp settings."
                ),
                _edit_image_tool,
                title="Edit Image",
                restore_func=_edit_image_restore,
                icon_name="image-edit-symbolic",
                default_on=True,
                tools_group="Image Generation",
                schema={
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": (
                                "The editing instruction, e.g. "
                                "'change the background to a sunset'."
                            ),
                        },
                        "reference_image": {
                            "type": "string",
                            "description": (
                                "Absolute path or data: URI of the image to edit. "
                                "Copy it from the ```image``` block in the most "
                                "recent user message."
                            ),
                        },
                    },
                    "required": ["prompt", "reference_image"],
                },
            ))
        return tools

    def _edit_image_tool(self, prompt: str, reference_image: str, msg_uuid=None):
        """Tool function for the edit_image tool.

        Validates the configuration, normalizes the reference image, builds
        a widget, kicks off the edit in a background thread and returns a
        ``ToolResult`` with the widget attached.
        """
        from ...ui.widgets.image_generator import ImageGeneratorWidget

        result = ToolResult()

        if not self.get_setting("enable_image_editing", False, False):
            result.set_output(
                "Image editing is disabled. Enable the 'Enable Image Editing' "
                "toggle in the stable-diffusion.cpp handler settings."
            )
            return result

        if not self._is_binary_installed():
            result.set_output(
                "stable-diffusion.cpp is not installed. Please install it from "
                "the Model Library before using image editing."
            )
            return result

        edit_model_value = self.get_setting("edit_model", True, "") or ""
        if not edit_model_value:
            result.set_output(
                "No image editing model is configured. Pick a Qwen Image Edit "
                "variant in 'Image Editing Settings' (downloadable from the Model "
                "Library)."
            )
            return result

        edit_model_path = self._resolve_model_path(edit_model_value)
        if not os.path.exists(edit_model_path):
            result.set_output(
                f"Image editing model not found at {edit_model_path}. "
                "Re-download it from the Model Library or pick a different model."
            )
            return result

        # Normalize data: URIs into a local file we control.
        try:
            ref_path = get_image_path(reference_image or "")
        except Exception as e:
            result.set_output(f"Invalid reference_image argument: {e}")
            return result
        if not ref_path or not os.path.exists(ref_path):
            result.set_output(
                "Could not find the reference image. Pass the absolute path "
                "of the image (or a data: URI) in the 'reference_image' argument."
            )
            return result

        # Persist the reference image into our cache so it survives even if
        # /tmp is cleaned up. Use msg_uuid for a stable, unique name.
        try:
            ext = os.path.splitext(ref_path)[1].lower() or ".png"
            if ext not in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
                ext = ".png"
            cached_ref = os.path.join(self.cache_dir, f"edit_input_{msg_uuid}{ext}")
            shutil.copyfile(ref_path, cached_ref)
            ref_path = cached_ref
        except Exception as e:
            print(f"Failed to cache reference image: {e}")

        widget = ImageGeneratorWidget(width=400, height=400)
        widget.set_prompt(prompt)
        result.set_widget(widget)
        self._edit_and_display(prompt, ref_path, widget, msg_uuid)
        return result

    def _edit_image_restore(self, msg_uuid, prompt):
        """Restore function for the edit_image tool.

        Rebuilds the widget from the cached output PNG so the chat can be
        reloaded and still show the edited image.
        """
        from ...ui.widgets.image_generator import ImageGeneratorWidget
        widget = ImageGeneratorWidget(width=400, height=400)
        widget.set_prompt(prompt)
        cached_path = self.cache_path_for(msg_uuid)
        if os.path.exists(cached_path):
            widget.set_image_from_path(cached_path)
        return ToolResult(widget=widget)

    def _edit_and_display(
        self,
        prompt: str,
        reference_image: str,
        widget,
        msg_uuid: str,
    ):
        """Run image editing in a background thread and update the widget when done.

        Mirrors the base class's ``generate_and_display`` but tailored for the
        edit pipeline: it always invokes sd-cli (not sd-server), uses the
        edit-specific settings, and passes the reference image via ``-r``.
        """
        output_path = self.cache_path_for(msg_uuid)

        def edit():
            try:
                image_source = self.edit_image(
                    prompt, reference_image, msg_uuid, output_file=output_path
                )
                if image_source and image_source.startswith(("http://", "https://")):
                    image_source = self._download_image(image_source, output_path)
            except Exception as e:
                print(f"Image editing failed: {e}")
                image_source = None
            GLib.idle_add(self._set_image_on_widget, widget, image_source, msg_uuid)

        threading.Thread(target=edit).start()

    def edit_image(
        self,
        prompt: str,
        reference_image: str,
        msg_uuid: str,
        output_file: str = None,
    ) -> str:
        """Edit an image using stable-diffusion.cpp's Qwen Image Edit pipeline.

        Always runs ``sd-cli`` (the sd-server HTTP API has no documented image
        editing endpoint), reads the edit settings from the handler, and
        assembles a command line that includes ``-r <reference_image>``,
        the edit model via ``--diffusion-model``, the configured
        ``--vae`` / ``--llm`` / ``--llm_vision`` paths, the
        ``--qwen-image-zero-cond-t`` flag when enabled, and the standard
        generation parameters.

        Args:
            prompt: The editing instruction.
            reference_image: Absolute path to the input image.
            msg_uuid: Unique message identifier for caching.
            output_file: Optional local file path to save the edited image.

        Returns:
            str: Local file path to the edited image.
        """
        from ...ui.widgets.image_generator import ImageGeneratorWidget  # noqa: F401  (kept for consistency)

        if output_file is None:
            output_file = os.path.join(self.cache_dir, f"{msg_uuid}.png")

        if not self._is_binary_installed():
            raise FileNotFoundError(
                "stable-diffusion.cpp is not installed. Please install it first."
            )

        edit_model_setting = self.get_setting("edit_model", True, "") or ""
        if not edit_model_setting:
            raise ValueError(
                "No image editing model is configured. Pick a Qwen Image Edit "
                "variant in 'Image Editing Settings'."
            )
        edit_model = self._resolve_model_path(edit_model_setting)
        if not os.path.exists(edit_model):
            raise FileNotFoundError(f"Image editing model not found: {edit_model}")

        if not reference_image or not os.path.exists(reference_image):
            raise FileNotFoundError(f"Reference image not found: {reference_image}")

        binary = self._get_binary_path()
        width = self.get_setting("width", True, 512)
        height = self.get_setting("height", True, 512)
        steps = self.get_setting("steps", True, 20)
        cfg_scale = self.get_setting("cfg_scale", True, 7.0)
        seed = self.get_setting("seed", True, -1)
        sampling_method = self.get_setting("sampling_method", True, "euler_a")
        clip_skip = self.get_setting("clip_skip", True, -1)
        positive_prompt_template = self.get_setting("positive_prompt_template", True, "[input]")
        negative_prompt_template = self.get_setting("negative_prompt_template", True, "")

        variant = self._variant_for_model_path(edit_model_setting)
        variant_manifest = variant[1] if variant else None

        full_prompt = positive_prompt_template.replace("[input]", prompt)
        negative_prompt = ""
        if negative_prompt_template:
            negative_prompt = negative_prompt_template.replace("[input]", full_prompt)

        cmd = [
            binary,
            *self._model_arg(edit_model),
            "-p", full_prompt,
            "-o", output_file,
            "-W", str(int(width)),
            "-H", str(int(height)),
            "--steps", str(int(steps)),
            "--cfg-scale", str(float(cfg_scale)),
            "-s", str(int(seed)),
            "--sampling-method", str(sampling_method),
            "-r", reference_image,
        ]

        if clip_skip > 0:
            cmd.extend(["--clip-skip", str(int(clip_skip))])

        if self._is_lora_enabled():
            cmd.extend(["--lora-model-dir", self._get_lora_dir()])

        cmd.extend(self._build_advanced_args(variant_manifest, edit_mode=True))

        if negative_prompt_template:
            cmd.extend(["-n", negative_prompt])

        # Edit always uses CLI (sd-server has no documented image editing
        # endpoint). Apply the same Flatpak/LD_LIBRARY_PATH plumbing as
        # generate_image() above.
        use_system = is_flatpak() and self.get_setting("use_system_sd", False, False)
        use_spawn = is_flatpak() and (
            use_system
            or (self._is_binary_installed() and self.get_setting("gpu_acceleration", False, False))
        )
        if use_spawn:
            cmd = get_spawn_command() + cmd

        env = os.environ.copy()
        if binary == self.sd_binary_path:
            bin_dir = os.path.dirname(binary)
            if use_spawn:
                cmd = cmd[:1] + [f"--env=LD_LIBRARY_PATH={bin_dir}"] + cmd[1:]
            else:
                existing = env.get("LD_LIBRARY_PATH", "")
                env["LD_LIBRARY_PATH"] = bin_dir if not existing else f"{bin_dir}:{existing}"

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600, env=env
            )
            if result.returncode != 0:
                print(f"sd-cli edit error: {result.stderr}")
                raise RuntimeError(
                    f"sd-cli image editing failed: {result.stderr}"
                )

            if not os.path.exists(output_file):
                alt_output = output_file
                if not alt_output.endswith(".png"):
                    alt_output = output_file + ".png"
                if os.path.exists(alt_output):
                    return alt_output
                raise FileNotFoundError(f"Output file not created: {output_file}")

            return output_file

        except subprocess.TimeoutExpired:
            raise TimeoutError("Image editing timed out after 10 minutes")
        except Exception as e:
            raise RuntimeError(f"Image editing failed: {e}")

    # ── Installation dialog ────────────────────────────────────────────

    def show_install_dialog(self, button):
        win = Adw.Window(title="Install stable-diffusion.cpp")
        self._build_window = win
        win.set_default_size(700, 760)
        win.set_modal(True)
        try:
            root = button.get_root()
            if root:
                win.set_transient_for(root)
        except Exception:
            pass

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        win.set_content(main_box)

        dots = Adw.CarouselIndicatorDots()
        dots.set_margin_top(12)
        main_box.append(dots)

        content = Adw.Carousel()
        content.set_allow_mouse_drag(False)
        content.set_allow_scroll_wheel(False)
        content.set_hexpand(True)
        content.set_vexpand(True)
        dots.set_carousel(content)
        main_box.append(content)

        # ── Page 0: Choose Method ──────────────────────────────────────

        page0 = Adw.StatusPage(
            title="Choose Installation Method",
            description="How would you like to install stable-diffusion.cpp?",
            icon_name="system-software-install-symbolic",
        )
        page0_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, hexpand=True, vexpand=True)
        page0_box.set_margin_start(24)
        page0_box.set_margin_end(24)
        page0_box.set_valign(Gtk.Align.CENTER)

        cards_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20, homogeneous=True)
        cards_box.set_halign(Gtk.Align.CENTER)

        # Left card: Compile
        compile_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        compile_card.add_css_class("card")
        compile_card.set_margin_top(8)
        compile_card.set_margin_bottom(8)
        compile_card.set_margin_start(12)
        compile_card.set_margin_end(12)

        compile_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        compile_inner.set_margin_top(16)
        compile_inner.set_margin_bottom(16)
        compile_inner.set_margin_start(16)
        compile_inner.set_margin_end(16)
        compile_card.append(compile_inner)

        compile_icon = Gtk.Image.new_from_icon_name("tools-symbolic")
        compile_icon.set_pixel_size(48)
        compile_icon.set_halign(Gtk.Align.CENTER)
        compile_inner.append(compile_icon)

        compile_title = Gtk.Label(label="Compile from Source")
        compile_title.add_css_class("title-4")
        compile_title.set_halign(Gtk.Align.CENTER)
        compile_inner.append(compile_title)

        for text in [
            "Optimized for your specific hardware",
            "Full customization via CMake flags",
            "Supports CUDA, Vulkan, ROCm",
        ]:
            lbl = Gtk.Label(label="  +  " + text)
            lbl.set_halign(Gtk.Align.START)
            lbl.set_margin_start(8)
            lbl.add_css_class("success")
            compile_inner.append(lbl)

        for text in [
            "Takes 5-20 minutes to build",
            "Requires build tools (cmake, gcc)",
        ]:
            lbl = Gtk.Label(label="  -  " + text)
            lbl.set_halign(Gtk.Align.START)
            lbl.set_margin_start(8)
            lbl.add_css_class("dim-label")
            compile_inner.append(lbl)

        btn_compile = Gtk.Button(label="Compile")
        btn_compile.add_css_class("suggested-action")
        btn_compile.set_halign(Gtk.Align.CENTER)
        btn_compile.set_margin_top(8)
        btn_compile.connect("clicked", lambda x: content.scroll_to(content.get_nth_page(1), True))
        compile_inner.append(btn_compile)
        cards_box.append(compile_card)

        # Right card: Download
        download_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        download_card.add_css_class("card")
        download_card.set_margin_top(8)
        download_card.set_margin_bottom(8)
        download_card.set_margin_start(12)
        download_card.set_margin_end(12)

        download_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        download_inner.set_margin_top(16)
        download_inner.set_margin_bottom(16)
        download_inner.set_margin_start(16)
        download_inner.set_margin_end(16)
        download_card.append(download_inner)

        download_icon = Gtk.Image.new_from_icon_name("folder-download-symbolic")
        download_icon.set_pixel_size(48)
        download_icon.set_halign(Gtk.Align.CENTER)
        download_inner.append(download_icon)

        download_title = Gtk.Label(label="Download Pre-built")
        download_title.add_css_class("title-4")
        download_title.set_halign(Gtk.Align.CENTER)
        download_inner.append(download_title)

        for text in [
            "Ready in under a minute",
            "No build tools required",
            "Pre-tested official binaries",
        ]:
            lbl = Gtk.Label(label="  +  " + text)
            lbl.set_halign(Gtk.Align.START)
            lbl.set_margin_start(8)
            lbl.add_css_class("success")
            download_inner.append(lbl)

        for text in [
            "Generic CPU optimizations",
            "Limited to available releases",
            "No CUDA prebuilt for Linux",
        ]:
            lbl = Gtk.Label(label="  -  " + text)
            lbl.set_halign(Gtk.Align.START)
            lbl.set_margin_start(8)
            lbl.add_css_class("dim-label")
            download_inner.append(lbl)

        btn_download = Gtk.Button(label="Download")
        btn_download.add_css_class("suggested-action")
        btn_download.set_halign(Gtk.Align.CENTER)
        btn_download.set_margin_top(8)
        btn_download.connect("clicked", lambda x: self._on_prebuilt_selected(content))
        download_inner.append(btn_download)
        cards_box.append(download_card)

        page0_box.append(cards_box)

        btn_cancel0 = Gtk.Button(label="Cancel")
        btn_cancel0.add_css_class("destructive-action")
        btn_cancel0.set_halign(Gtk.Align.CENTER)
        btn_cancel0.connect("clicked", lambda x: win.close())
        page0_box.append(btn_cancel0)

        page0.set_child(page0_box)
        content.append(page0)

        # ── Page 1: Hardware Selection (Compile path) ──────────────────

        page1 = Adw.StatusPage(
            title="Select Hardware",
            description="Choose your acceleration backend",
            icon_name="brain-augemnted-symbolic",
        )
        main_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True, vexpand=True)
        main_container.set_halign(Gtk.Align.CENTER)

        if not can_escape_sandbox():
            warning_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            warning_box.set_margin_bottom(16)
            warning_box.add_css_class("warning")

            warning_label = Gtk.Label(label="Flatpak Sandbox Warning")
            warning_label.add_css_class("heading")
            warning_label.set_halign(Gtk.Align.CENTER)
            warning_box.append(warning_label)

            warning_text = Gtk.Label(
                label="To build stable-diffusion.cpp with hardware acceleration in Flatpak,\n"
                      "you need to grant sandbox escape permissions.\n"
                      "Run the following command in a terminal:"
            )
            warning_text.set_halign(Gtk.Align.CENTER)
            warning_text.set_wrap(True)
            warning_box.append(warning_text)

            command_entry = Gtk.Entry()
            command_entry.set_text(
                "flatpak --user override --talk-name=org.freedesktop.Flatpak --filesystem=home io.github.qwersyk.Newelle"
            )
            command_entry.set_editable(False)
            command_entry.set_halign(Gtk.Align.CENTER)
            command_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            command_box.set_halign(Gtk.Align.CENTER)
            command_box.append(command_entry)
            warning_box.append(command_box)

            copy_btn = Gtk.Button(label="Copy Command")
            copy_btn.set_halign(Gtk.Align.CENTER)
            copy_btn.connect(
                "clicked",
                lambda btn: self._copy_to_clipboard(
                    "flatpak --user override --talk-name=org.freedesktop.Flatpak --filesystem=home io.github.qwersyk.Newelle"
                ),
            )
            warning_box.append(copy_btn)

            main_container.append(warning_box)
            main_container.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24, hexpand=True)
        hbox.set_halign(Gtk.Align.CENTER)
        hbox.set_margin_start(24)
        hbox.set_margin_end(24)

        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.hw_options = {}
        dependency_warning = BuildDependencyWarning()
        main_container.append(dependency_warning)
        group = None
        backend_options = {
            "CPU": "cpu",
            "CPU (OpenBLAS)": "cpu_openblas",
            "Nvidia (CUDA)": "cuda",
            "AMD (ROCm)": "rocm",
            "Any GPU (Vulkan)": "vulkan",
        }
        for hw, backend in backend_options.items():
            btn = Gtk.CheckButton(label=hw, group=group)
            if group is None:
                group = btn
                btn.set_active(True)
            dependency_warning.watch_option(btn, backend)
            self.hw_options[hw] = btn
            left_box.append(btn)
        dependency_warning.refresh("cpu")

        right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER)
        lbl_flags = Gtk.Label(label="Custom CMake Flags (Optional)")
        lbl_flags.set_halign(Gtk.Align.START)
        right_box.append(lbl_flags)

        self.entry_cmake = Gtk.Entry()
        self.entry_cmake.set_placeholder_text("-DSD_WEBP=OFF ...")
        right_box.append(self.entry_cmake)

        hbox.append(left_box)
        hbox.append(right_box)
        main_container.append(hbox)

        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        button_box.set_halign(Gtk.Align.CENTER)
        button_box.set_margin_top(12)

        btn_cancel1 = Gtk.Button(label="Cancel")
        btn_cancel1.add_css_class("destructive-action")
        btn_cancel1.connect("clicked", lambda x: win.close())
        button_box.append(btn_cancel1)

        btn_next1 = Gtk.Button(label="Next")
        if not can_escape_sandbox():
            btn_next1.set_sensitive(False)
            btn_next1.set_tooltip_text("Please run the Flatpak override command first")
        else:
            btn_next1.connect("clicked", lambda x: content.scroll_to(content.get_nth_page(3), True))
        button_box.append(btn_next1)

        main_container.append(button_box)

        page1.set_child(main_container)
        content.append(page1)

        # ── Page 2: Pre-built Binary Selection ─────────────────────────

        page2 = Adw.StatusPage(
            title="Select Pre-built Binary",
            description="Choose the binary that matches your hardware",
            icon_name="folder-download-symbolic",
        )
        self.prebuilt_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True, vexpand=True)
        self.prebuilt_box.set_margin_start(24)
        self.prebuilt_box.set_margin_end(24)
        self.prebuilt_box.set_halign(Gtk.Align.CENTER)

        self.prebuilt_spinner = Gtk.Spinner()
        self.prebuilt_spinner.set_halign(Gtk.Align.CENTER)
        self.prebuilt_spinner.start()
        self.prebuilt_box.append(self.prebuilt_spinner)

        self.prebuilt_list_box = Gtk.ListBox()
        self.prebuilt_list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.prebuilt_list_box.add_css_class("boxed-list")
        self.prebuilt_list_box.set_hexpand(True)
        self.prebuilt_box.append(self.prebuilt_list_box)

        self.prebuilt_error_label = Gtk.Label(label="")
        self.prebuilt_error_label.set_halign(Gtk.Align.CENTER)
        self.prebuilt_error_label.set_wrap(True)
        self.prebuilt_box.append(self.prebuilt_error_label)

        prebuilt_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        prebuilt_buttons.set_halign(Gtk.Align.CENTER)
        prebuilt_buttons.set_margin_top(12)

        btn_back_prebuilt = Gtk.Button(label="Back")
        btn_back_prebuilt.connect("clicked", lambda x: content.scroll_to(content.get_nth_page(0), True))
        prebuilt_buttons.append(btn_back_prebuilt)

        self.btn_start_prebuilt = Gtk.Button(label="Download & Install")
        self.btn_start_prebuilt.add_css_class("suggested-action")
        self.btn_start_prebuilt.set_sensitive(False)
        self.btn_start_prebuilt.connect("clicked", lambda x: self._start_prebuilt_install(content))
        prebuilt_buttons.append(self.btn_start_prebuilt)

        self.prebuilt_box.append(prebuilt_buttons)
        page2.set_child(self.prebuilt_box)
        content.append(page2)

        # ── Page 3: Ready to Build ─────────────────────────────────────

        page3 = Adw.StatusPage(
            title="Ready to Build",
            description="Click start to begin compilation",
            icon_name="tools-symbolic",
        )
        box3 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True, vexpand=True)
        box3.set_halign(Gtk.Align.CENTER)

        btn_start = Gtk.Button(label="Start Build")
        btn_start.set_halign(Gtk.Align.CENTER)
        btn_start.connect("clicked", lambda x: self._start_build(content))
        box3.append(btn_start)

        btn_back3 = Gtk.Button(label="Back")
        btn_back3.set_halign(Gtk.Align.CENTER)
        btn_back3.connect("clicked", lambda x: content.scroll_to(content.get_nth_page(1), True))
        box3.append(btn_back3)

        page3.set_child(box3)
        content.append(page3)

        # ── Page 4: Progress ───────────────────────────────────────────

        page4 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page4.set_hexpand(True)
        page4.set_vexpand(True)
        page4.set_margin_top(24)
        page4.set_margin_bottom(24)
        page4.set_margin_start(24)
        page4.set_margin_end(24)

        icon4 = Gtk.Image.new_from_icon_name("magic-wand-symbolic")
        icon4.set_pixel_size(96)
        icon4.set_halign(Gtk.Align.CENTER)
        page4.append(icon4)

        title4 = Gtk.Label(label="Installing")
        title4.add_css_class("title-1")
        title4.set_halign(Gtk.Align.CENTER)
        page4.append(title4)

        desc4 = Gtk.Label(label="Please wait...")
        desc4.set_halign(Gtk.Align.CENTER)
        page4.append(desc4)

        self.progress_bar = Gtk.ProgressBar()
        page4.append(self.progress_bar)

        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        scroll = Gtk.ScrolledWindow()
        scroll.set_child(self.log_view)
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)
        page4.append(scroll)

        self.stop_build_button = Gtk.Button(label="Stop Build")
        self.stop_build_button.add_css_class("destructive-action")
        self.stop_build_button.set_halign(Gtk.Align.CENTER)
        self.stop_build_button.set_sensitive(False)
        self.stop_build_button.connect("clicked", self.stop_build)
        page4.append(self.stop_build_button)
        content.append(page4)

        # ── Page 5: Done ───────────────────────────────────────────────

        page5 = Adw.StatusPage(
            title="Completed",
            description="Installation finished successfully",
            icon_name="emblem-default-symbolic",
        )
        btn_close = Gtk.Button(label="Close")
        btn_close.set_halign(Gtk.Align.CENTER)
        btn_close.connect("clicked", lambda x: self._finish_install(win))
        page5.set_child(btn_close)
        content.append(page5)

        win.present()

    # ── Prebuilt binary download ───────────────────────────────────────

    def _on_prebuilt_selected(self, content):
        content.scroll_to(content.get_nth_page(2), True)
        if not hasattr(self, "_prebuilt_fetched") or not self._prebuilt_fetched:
            self._prebuilt_fetched = True
            threading.Thread(target=self._fetch_prebuilt_releases, args=(content,), daemon=True).start()

    @staticmethod
    def _detect_arch():
        machine = platform.machine().lower()
        if machine in ("x86_64", "amd64"):
            return "x86_64"
        elif machine in ("aarch64", "arm64"):
            return "arm64"
        return machine

    @staticmethod
    def _parse_asset_backend(name):
        name_lower = name.lower()
        if "rocm" in name_lower:
            return "rocm"
        elif "vulkan" in name_lower:
            return "vulkan"
        return "cpu"

    @staticmethod
    def _human_size(size_bytes):
        for unit in ("B", "KB", "MB", "GB"):
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} TB"

    @staticmethod
    def _backend_display_name(backend):
        return {
            "cpu": "CPU (Basic)",
            "vulkan": "Any GPU (Vulkan)",
            "rocm": "AMD ROCm",
        }.get(backend, backend)

    def _fetch_prebuilt_releases(self, carousel):
        arch = self._detect_arch()
        available = []

        try:
            # Use the specific release tagged in the repo
            resp = requests.get(self.RELEASE_API_URL + "/latest", timeout=15)
            resp.raise_for_status()
            release = resp.json()
            tag = release.get("tag_name", "unknown")

            for asset in release.get("assets", []):
                name = asset["name"]
                url = asset["browser_download_url"]
                size = asset.get("size", 0)

                # We only support Linux .zip archives
                if not name.endswith(".zip"):
                    continue
                if "Linux" not in name:
                    continue
                if "Darwin" in name or "macOS" in name:
                    continue

                # Check architecture match
                if arch == "x86_64" and ("arm64" in name.lower() or "aarch64" in name.lower()):
                    continue
                if arch == "arm64" and "x86_64" in name and "arm64" not in name.lower():
                    continue

                backend = self._parse_asset_backend(name)
                available.append({
                    "name": name,
                    "url": url,
                    "size": size,
                    "backend": backend,
                    "tag": tag,
                })

        except Exception as e:
            GLib.idle_add(self._show_prebuilt_error, f"Failed to fetch releases: {e}")
            return

        if not available:
            GLib.idle_add(
                self._show_prebuilt_error,
                f"No compatible pre-built binaries found for your architecture ({arch}).",
            )
            return

        # Sort: compatible backends first
        backend_checks = {}
        for b in ("vulkan", "rocm"):
            backend_checks[b] = has_backend(b)

        for item in available:
            item["compatible"] = item["backend"] == "cpu" or backend_checks.get(item["backend"], False)

        def _sort_key(x):
            is_compatible = 0 if x["compatible"] else 1
            backend_prio = {"vulkan": 0, "rocm": 1, "cpu": 2}.get(x["backend"], 99)
            return (is_compatible, backend_prio)

        available.sort(key=_sort_key)

        GLib.idle_add(self._populate_prebuilt_list, available)

    def _show_prebuilt_error(self, message):
        if hasattr(self, "prebuilt_spinner") and self.prebuilt_spinner:
            self.prebuilt_spinner.stop()
            self.prebuilt_spinner.set_visible(False)
        self.prebuilt_error_label.set_text(message)

    def _populate_prebuilt_list(self, available):
        if hasattr(self, "prebuilt_spinner") and self.prebuilt_spinner:
            self.prebuilt_spinner.stop()
            self.prebuilt_spinner.set_visible(False)

        child = self.prebuilt_list_box.get_first_child()
        while child:
            self.prebuilt_list_box.remove(child)
            child = self.prebuilt_list_box.get_first_child()

        self.prebuilt_assets = available
        group = None
        first_recommended = None
        first_overall = None

        for i, item in enumerate(available):
            row = Adw.ActionRow()
            row.set_title(self._backend_display_name(item["backend"]))

            if item["compatible"] and item["backend"] != "cpu":
                rec = Gtk.Label(label="Recommended")
                rec.add_css_class("success")
                rec.add_css_class("caption")
                rec.set_valign(Gtk.Align.CENTER)
                row.add_suffix(rec)

            subtitle_parts = [
                self._human_size(item["size"]),
                item["tag"],
            ]
            row.set_subtitle("  |  ".join(subtitle_parts))

            check = Gtk.CheckButton()
            if group is None:
                group = check
                check.set_active(True)
            else:
                check.set_group(group)
            row.add_prefix(check)
            row.set_activatable_widget(check)

            if item["compatible"] and first_recommended is None:
                first_recommended = i
                check.set_active(True)
            if first_overall is None:
                first_overall = i

            self.prebuilt_list_box.append(row)

        self._selected_prebuilt = first_recommended if first_recommended is not None else first_overall
        self.btn_start_prebuilt.set_sensitive(True)

        def on_row_activated(listbox, row):
            idx = 0
            child = listbox.get_first_child()
            while child:
                if child == row:
                    break
                child = child.get_next_sibling()
                idx += 1
            self._selected_prebuilt = idx

        self.prebuilt_list_box.connect("row-activated", on_row_activated)

    def _start_prebuilt_install(self, carousel):
        if not hasattr(self, "_selected_prebuilt") or self._selected_prebuilt is None:
            return
        if not hasattr(self, "prebuilt_assets") or self._selected_prebuilt >= len(self.prebuilt_assets):
            return

        asset = self.prebuilt_assets[self._selected_prebuilt]
        carousel.scroll_to(carousel.get_nth_page(4), True)
        threading.Thread(target=self._run_prebuilt_install, args=(asset, carousel), daemon=True).start()

    def _run_prebuilt_install(self, asset, carousel):
        def append_log(text):
            buf = self.log_view.get_buffer()
            buf.insert(buf.get_end_iter(), text)
            return False

        def set_progress(fraction):
            self.progress_bar.set_fraction(fraction)
            return False

        manager = get_download_manager()
        task = manager.create_task(
            _("Install {name}").format(name=asset["name"]),
            kind=DownloadKind.RUNTIME,
            source_id=f"runtime:{self.key}:{asset['name']}",
            phase=_("Downloading runtime"),
            cancellable=True,
        )
        tmp_dir = None
        try:
            task.check_cancelled()
            GLib.idle_add(append_log, f"Downloading {asset['name']}...\n")
            GLib.idle_add(set_progress, 0.0)

            resp = requests.get(asset["url"], stream=True, timeout=300)
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", asset["size"]))
            downloaded = 0

            tmp_dir = tempfile.mkdtemp()
            tmp_file = os.path.join(tmp_dir, asset["name"])

            with open(tmp_file, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    task.check_cancelled()
                    f.write(chunk)
                    downloaded += len(chunk)
                    task.update(
                        fraction=downloaded / total if total > 0 else None,
                        transferred_bytes=downloaded,
                        total_bytes=total if total > 0 else None,
                    )
                    if total > 0:
                        progress = (downloaded / total) * 0.7
                        GLib.idle_add(set_progress, progress)

            GLib.idle_add(set_progress, 0.7)
            GLib.idle_add(append_log, "Download complete. Extracting...\n")
            task.update(
                phase=_("Installing runtime"),
                reset_progress=True,
                cancellable=False,
            )

            abs_sd_path = os.path.abspath(self.sd_cpp_path)
            if os.path.exists(abs_sd_path):
                shutil.rmtree(abs_sd_path)

            with zipfile.ZipFile(tmp_file, "r") as zf:
                zf.extractall(tmp_dir)

            # Find the extracted directory or binary
            extracted_items = os.listdir(tmp_dir)
            extracted_items = [i for i in extracted_items if i != asset["name"]]

            build_bin = os.path.join(abs_sd_path, "build", "bin")
            os.makedirs(build_bin, exist_ok=True)

            # Look for sd-cli and sd-server binaries in extracted files
            sd_binary_found = False
            for root, _, files in os.walk(tmp_dir):
                for f in files:
                    if f == "sd-cli" or f == "sd":
                        src = os.path.join(root, f)
                        dst = os.path.join(build_bin, "sd-cli")
                        shutil.move(src, dst)
                        os.chmod(dst, 0o755)
                        sd_binary_found = True
                    elif f == "sd-server":
                        src = os.path.join(root, f)
                        dst = os.path.join(build_bin, "sd-server")
                        shutil.move(src, dst)
                        os.chmod(dst, 0o755)

            # Also copy any .so files
            for root, _, files in os.walk(tmp_dir):
                for f in files:
                    if f.endswith(".so") or f.endswith(".so.*"):
                        src = os.path.join(root, f)
                        dst = os.path.join(build_bin, f)
                        shutil.move(src, dst)

            if not sd_binary_found:
                raise Exception("sd-cli binary not found in the archive")

            shutil.rmtree(tmp_dir, ignore_errors=True)

            GLib.idle_add(set_progress, 1.0)
            GLib.idle_add(append_log, "Installation completed successfully!\n")
            GLib.idle_add(lambda: carousel.scroll_to(carousel.get_nth_page(5), True))
            GLib.idle_add(lambda: self.settings_update())
            self.set_setting("gpu_acceleration", asset.get("backend") != "cpu")
            task.complete(_("Installed"))

        except DownloadCancelled:
            task.cancelled(_("Cancelled"))
        except Exception as e:
            task.fail(str(e), _("Failed"))
            GLib.idle_add(append_log, f"\nError: {e}\n")
            import traceback
            GLib.idle_add(append_log, traceback.format_exc())
        finally:
            if tmp_dir is not None:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    # ── Build from source ──────────────────────────────────────────────

    def _start_build(self, carousel):
        backend = "cpu"
        if self.hw_options["Nvidia (CUDA)"].get_active():
            backend = "cuda"
        elif self.hw_options["AMD (ROCm)"].get_active():
            backend = "rocm"
        elif self.hw_options["Any GPU (Vulkan)"].get_active():
            backend = "vulkan"
        elif self.hw_options["CPU (OpenBLAS)"].get_active():
            backend = "cpu_openblas"

        custom_flags = self.entry_cmake.get_text()

        self._build_process.begin()
        self.stop_build_button.set_sensitive(True)
        carousel.scroll_to(carousel.get_nth_page(4), True)
        threading.Thread(target=self._run_build, args=(backend, carousel, custom_flags), daemon=True).start()

    def stop_build(self, button=None):
        """Stop the active stable-diffusion.cpp source build."""
        self._build_process.cancel()
        if button is None:
            button = getattr(self, "stop_build_button", None)
        if button is not None:
            button.set_sensitive(False)

    def _close_build_window(self):
        window = getattr(self, "_build_window", None)
        if window is not None:
            self._build_window = None
            window.close()
        return False

    def _run_build(self, backend, carousel, custom_flags=""):
        if not can_escape_sandbox():
            self.throw("You have to escape the sandbox to build stable-diffusion.cpp", ErrorSeverity.ERROR)
            return

        try:
            env = os.environ.copy()
            cmake_args = ["-DCMAKE_BUILD_TYPE=Release"]

            if backend == "cuda":
                cmake_args.append("-DSD_CUDA=ON")
            elif backend == "rocm":
                cmake_args.append("-DSD_HIPBLAS=ON")
            elif backend == "vulkan":
                cmake_args.append("-DSD_VULKAN=ON")
            elif backend == "cpu_openblas":
                cmake_args.append("-DGGML_OPENBLAS=ON")

            if custom_flags:
                custom_list = custom_flags.split() if isinstance(custom_flags, str) else custom_flags
                cmake_args.extend(custom_list)

            def append_log(text):
                buffer = self.log_view.get_buffer()
                buffer.insert(buffer.get_end_iter(), text)
                return False

            def set_progress(fraction):
                self.progress_bar.set_fraction(fraction)
                return False

            def run_cmd(cmd_list, extra_env=None, cwd=None):
                full_cmd = cmd_list
                if is_flatpak():
                    flatpak_cmd = get_spawn_command()
                    if extra_env:
                        for k, v in extra_env.items():
                            flatpak_cmd.extend([f"--env={k}={v}"])
                    full_cmd = flatpak_cmd + cmd_list
                else:
                    if extra_env:
                        env.update(extra_env)

                return self._build_process.run(
                    full_cmd,
                    env=env if not is_flatpak() else None,
                    cwd=cwd,
                    on_output=lambda line: GLib.idle_add(append_log, line),
                )

            GLib.idle_add(set_progress, 0.1)
            GLib.idle_add(append_log, "Cloning stable-diffusion.cpp repository...\n")

            abs_sd_path = os.path.abspath(self.sd_cpp_path)

            if os.path.exists(abs_sd_path):
                run_cmd(["rm", "-rf", abs_sd_path])

            clone_cmd = ["git", "clone", "--depth", "1", self.REPO_URL, abs_sd_path]
            if not run_cmd(clone_cmd):
                if self._build_process.cancel_requested:
                    raise BuildCancelled()
                raise Exception("Failed to clone stable-diffusion.cpp repository")

            # Initialize submodules (for webp/webm dependencies)
            GLib.idle_add(set_progress, 0.15)
            GLib.idle_add(append_log, "Initializing submodules...\n")
            submodule_cmd = ["git", "submodule", "update", "--init", "--recursive"]
            if not run_cmd(submodule_cmd, cwd=abs_sd_path):
                if self._build_process.cancel_requested:
                    raise BuildCancelled()
                GLib.idle_add(append_log, "Warning: submodule init failed, continuing anyway...\n")

            GLib.idle_add(set_progress, 0.25)
            GLib.idle_add(append_log, "Configuring CMake build...\n")

            cmake_configure = ["cmake", "-B", "build"] + cmake_args
            if not run_cmd(cmake_configure, cwd=abs_sd_path):
                if self._build_process.cancel_requested:
                    raise BuildCancelled()
                raise Exception("Failed to configure CMake build")

            GLib.idle_add(set_progress, 0.4)
            GLib.idle_add(append_log, f"Building stable-diffusion.cpp (Backend: {backend})...\n")
            GLib.idle_add(append_log, "This may take several minutes...\n")

            import multiprocessing
            num_jobs = multiprocessing.cpu_count()
            cmake_build = ["cmake", "--build", "build", "--config", "Release", "-j", str(num_jobs)]
            if not run_cmd(cmake_build, cwd=abs_sd_path):
                if self._build_process.cancel_requested:
                    raise BuildCancelled()
                raise Exception("Failed to build stable-diffusion.cpp")

            if self._build_process.cancel_requested:
                raise BuildCancelled()

            sd_binary = os.path.join(abs_sd_path, "build", "bin", "sd-cli")
            if not os.path.exists(sd_binary):
                # Check for sd binary name
                sd_alt = os.path.join(abs_sd_path, "build", "bin", "sd")
                if os.path.exists(sd_alt):
                    os.rename(sd_alt, sd_binary)

            sd_server = os.path.join(abs_sd_path, "build", "bin", "sd-server")
            if not os.path.exists(sd_binary) and not os.path.exists(sd_server):
                raise Exception("sd-cli and sd-server binaries not found after build")

            GLib.idle_add(set_progress, 1.0)
            GLib.idle_add(append_log, "Build completed successfully!\n")
            GLib.idle_add(lambda: carousel.scroll_to(carousel.get_nth_page(5), True))
            GLib.idle_add(lambda: self.settings_update())
            self.set_setting("gpu_acceleration", backend != "cpu")

        except BuildCancelled:
            GLib.idle_add(append_log, "\nBuild stopped by user.\n")
            GLib.idle_add(self._close_build_window)
        except Exception as e:
            GLib.idle_add(
                append_log,
                f"\nError: {e}\n",
            )
            import traceback
            GLib.idle_add(append_log, traceback.format_exc())
        finally:
            GLib.idle_add(
                lambda: self.stop_build_button.set_sensitive(False)
                if hasattr(self, "stop_build_button") else False
            )

    def _finish_install(self, win):
        win.close()
        self.settings_update()

    def _copy_to_clipboard(self, text):
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(text)
