"""Cancellable streaming downloads from the Hugging Face Hub."""

from __future__ import annotations

import os
from collections.abc import Callable
from gettext import gettext as _

import requests


def download_huggingface_file(
    repo_id: str,
    filename: str,
    destination: str,
    *,
    task=None,
    session=requests,
    url: str | None = None,
    headers: dict[str, str] | None = None,
    progress_callback: Callable[[int, int | None], None] | None = None,
) -> None:
    """Stream one Hub file to a temporary path and atomically install it.

    Using the HTTP stream directly keeps cancellation under the application's
    control. Some optional Hugging Face transfer backends execute outside the
    Python progress callback and therefore cannot reliably stop when requested.
    """
    if url is None or headers is None:
        from huggingface_hub import hf_hub_url
        from huggingface_hub.utils import build_hf_headers

        if url is None:
            url = hf_hub_url(repo_id, filename)
        if headers is None:
            headers = build_hf_headers(library_name="newelle")

    partial_path = destination + ".part"
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    try:
        if task is not None:
            task.check_cancelled()
            task.update(
                phase=_("Connecting to Hugging Face"),
                reset_progress=True,
                cancellable=False,
            )
        with session.get(
            url,
            headers=headers,
            stream=True,
            allow_redirects=True,
            timeout=(10, 30),
        ) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0) or 0)
            transferred = 0
            if task is not None:
                task.check_cancelled()
                task.update(
                    phase=_("Downloading {name}").format(name=filename),
                    cancellable=True,
                )
            with open(partial_path, "wb") as output:
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    if not chunk:
                        continue
                    if task is not None:
                        task.check_cancelled()
                    output.write(chunk)
                    transferred += len(chunk)
                    if progress_callback is not None:
                        progress_callback(transferred, total if total > 0 else None)
                    if task is not None:
                        task.update(
                            fraction=transferred / total if total > 0 else None,
                            transferred_bytes=transferred,
                            total_bytes=total if total > 0 else None,
                        )
        if task is not None:
            task.check_cancelled()
            task.update(
                phase=_("Finalizing model"),
                reset_progress=True,
                cancellable=False,
            )
        os.replace(partial_path, destination)
    except Exception:
        try:
            os.remove(partial_path)
        except FileNotFoundError:
            pass
        raise
