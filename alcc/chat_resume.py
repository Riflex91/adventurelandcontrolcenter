"""Remember an existing ChatGPT conversation URL without inspecting chat content."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

FILE_NAME = 'last-chat.json'


def clean_chat_url(url: str) -> str | None:
    if not isinstance(url, str) or len(url) > 800:
        return None
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in ('chatgpt.com', 'www.chatgpt.com'):
        return None
    if parsed.username or parsed.password or parsed.port:
        return None
    path = parsed.path
    # Conversation deep links, including the ChatGPT project / GPT routes.
    if not (path.startswith('/c/') or (path.startswith('/g/') and '/c/' in path)):
        return None
    if not path.isascii() or any(ch not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_/.' for ch in path):
        return None
    if '..' in path.split('/') or len(path) > 600:
        return None
    return 'https://chatgpt.com' + path


def store_chat_url(data_dir: Path, url: str) -> bool:
    clean = clean_chat_url(url)
    if clean is None:
        return False
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / FILE_NAME
    temporary = data_dir / (FILE_NAME + '.tmp')
    temporary.write_text(json.dumps({'url': clean}) + '\n', encoding='utf-8')
    temporary.replace(target)
    return True


def read_chat_url(data_dir: Path) -> str | None:
    path = Path(data_dir) / FILE_NAME
    try:
        saved = json.loads(path.read_text(encoding='utf-8'))
        return clean_chat_url(saved['url']) if isinstance(saved, dict) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None
