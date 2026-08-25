from __future__ import annotations

import re
from collections.abc import Iterable
from difflib import SequenceMatcher

# The icon names correspond to monochrome SVGs from lobehub/lobe-icons.
# Keep more specific derivative families before their base model families.
_MODEL_ICON_RULES = (
    (r"deepseek|deep-seek|deepscaler", "lobehub-deepseek-symbolic", "#4D6BFE"),
    (r"deep-cogito|(?:^|-)cogito(?:-|$)", "lobehub-deepcogito-symbolic", "#4E81EE"),
    (r"muse-glimmer", "lobehub-meta-symbolic", "#1D65C1"),
    (r"(?:^|-)laguna(?:-|$)", "lobehub-poolside-symbolic", "#4137FF"),
    (r"seed-oss", "lobehub-bytedance-symbolic", "#325AB4"),
    (r"(?:^|-)ernie(?:[0-9.-]|$)", "lobehub-wenxin-symbolic", "#167ADF"),
    (r"qwen|(?:^|-)qwq(?:-|$)", "lobehub-qwen-symbolic", "#615CED"),
    (r"gemma", "lobehub-gemma-symbolic", "#2E96FF"),
    (
        r"mistral|mixtral|codestral|devstral|ministral|magistral|mathstral",
        "lobehub-mistral-symbolic",
        "#FA520F",
    ),
    (r"granite", "lobehub-ibm-symbolic", "#0F62FE"),
    (r"nemotron", "lobehub-nvidia-symbolic", "#74B71B"),
    (r"(?:^|-)phi(?:[0-9.-]|$)", "lobehub-microsoft-symbolic", "#00A4EF"),
    (r"gpt-oss", "lobehub-openai-symbolic", "#000000"),
    (r"command-(?:a|r)|commandr", "lobehub-commanda-symbolic", "#39594D"),
    (r"(?:^|-)aya(?:-|$)", "lobehub-aya-symbolic", "#416FDC"),
    (r"(?:^|-)yi(?:-|$)|yi-coder", "lobehub-yi-symbolic", "#003425"),
    (r"minimax", "lobehub-minimax-symbolic", "#F23F5D"),
    (r"chatglm|(?:^|-)glm(?:[0-9.-]|$)|codegeex", "lobehub-zai-symbolic", "#000000"),
    (r"(?:^|-)claude(?:[0-9.-]|$)", "lobehub-claude-symbolic", "#D97757"),
    (r"kimi|moonshot", "lobehub-moonshot-symbolic", "#16191E"),
    (
        r"(?:^|-)olmo(?:[0-9.-]|$)|(?:^|-)tulu(?:[0-9.-]|$)",
        "lobehub-ai2-symbolic",
        "#F0529C",
    ),
    (
        r"stable-diffusion|(?:^|-)sdxl(?:[0-9.-]|$)|(?:^|-)sd(?:[0-9.-]|$)",
        "lobehub-stability-symbolic",
        "#330066",
    ),
    (r"(?:^|-)flux(?:[0-9.-]|$)", "lobehub-flux-symbolic", "#FFFFFF"),
    (r"(?:^|-)z-image(?:-|$)", "lobehub-zai-symbolic", "#000000"),
    (r"stablelm|stable-code", "lobehub-stability-symbolic", "#330066"),
    (r"snowflake", "lobehub-snowflake-symbolic", "#249EDC"),
    (r"(?:^|-)falcon(?:[0-9.-]|$)", "lobehub-tii-symbolic", "#6400FF"),
    (r"exaone", "lobehub-lg-symbolic", "#C00C3F"),
    (r"(?:^|-)lfm(?:[0-9.-]|$)", "lobehub-liquid-symbolic", "#FFFFFF"),
    (r"openchat", "lobehub-openchat-symbolic", "#4A7FE3"),
    (r"internlm", "lobehub-internlm-symbolic", "#1B3882"),
    (r"baichuan", "lobehub-baichuan-symbolic", "#FF6933"),
    (r"hunyuan", "lobehub-hunyuan-symbolic", "#0053E0"),
    (
        r"codellama|tinyllama|(?:^|-)llama(?:[0-9.-]|$)|llava|bakllava|medllama",
        "lobehub-meta-symbolic",
        "#1D65C1",
    ),
    (r"(?:^|-)whisper(?:[0-9.-]|$)", "lobehub-openai-symbolic", "#000000"),
)


_FUZZY_MODEL_ALIASES = (
    ("deepseek", "lobehub-deepseek-symbolic", "#4D6BFE"),
    ("deep cogito", "lobehub-deepcogito-symbolic", "#4E81EE"),
    ("muse glimmer", "lobehub-meta-symbolic", "#1D65C1"),
    ("laguna", "lobehub-poolside-symbolic", "#4137FF"),
    ("seed oss", "lobehub-bytedance-symbolic", "#325AB4"),
    ("ernie", "lobehub-wenxin-symbolic", "#167ADF"),
    ("qwen", "lobehub-qwen-symbolic", "#615CED"),
    ("gemma", "lobehub-gemma-symbolic", "#2E96FF"),
    ("mistral", "lobehub-mistral-symbolic", "#FA520F"),
    ("mixtral", "lobehub-mistral-symbolic", "#FA520F"),
    ("granite", "lobehub-ibm-symbolic", "#0F62FE"),
    ("nemotron", "lobehub-nvidia-symbolic", "#74B71B"),
    ("gpt oss", "lobehub-openai-symbolic", "#000000"),
    ("command r", "lobehub-commanda-symbolic", "#39594D"),
    ("minimax", "lobehub-minimax-symbolic", "#F23F5D"),
    ("chat glm", "lobehub-zai-symbolic", "#000000"),
    ("claude", "lobehub-claude-symbolic", "#D97757"),
    ("moonshot", "lobehub-moonshot-symbolic", "#16191E"),
    ("stable diffusion", "lobehub-stability-symbolic", "#330066"),
    ("flux", "lobehub-flux-symbolic", "#FFFFFF"),
    ("z image", "lobehub-zai-symbolic", "#000000"),
    ("stablelm", "lobehub-stability-symbolic", "#330066"),
    ("snowflake", "lobehub-snowflake-symbolic", "#249EDC"),
    ("falcon", "lobehub-tii-symbolic", "#6400FF"),
    ("exaone", "lobehub-lg-symbolic", "#C00C3F"),
    ("openchat", "lobehub-openchat-symbolic", "#4A7FE3"),
    ("internlm", "lobehub-internlm-symbolic", "#1B3882"),
    ("baichuan", "lobehub-baichuan-symbolic", "#FF6933"),
    ("hunyuan", "lobehub-hunyuan-symbolic", "#0053E0"),
    ("codellama", "lobehub-meta-symbolic", "#1D65C1"),
    ("tinyllama", "lobehub-meta-symbolic", "#1D65C1"),
    ("llama", "lobehub-meta-symbolic", "#1D65C1"),
    ("whisper", "lobehub-openai-symbolic", "#000000"),
)


def _normalize_model_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _fuzzy_candidates(value: str) -> set[str]:
    words = re.findall(r"[a-z]+", value)
    candidates = {"".join(words)}
    candidates.update(word for word in words if len(word) >= 4)
    for size in (2, 3):
        candidates.update(
            "".join(words[index : index + size])
            for index in range(len(words) - size + 1)
        )
    return {candidate for candidate in candidates if len(candidate) >= 4}


def get_model_icon(
    model_name: str,
    aliases: Iterable[str] = (),
) -> tuple[str | None, str | None]:
    """Fuzzily map a model name to its symbolic LobeHub icon and brand color.

    Aliases can provide catalog tags or alternate names. Exact family rules
    are evaluated first; fuzzy matching is intentionally conservative and
    only wins when the best family is both similar and unambiguous.
    """
    if isinstance(aliases, str):
        aliases = (aliases,)
    searchable = _normalize_model_name(
        " ".join((str(model_name), *(str(alias) for alias in aliases)))
    )
    if not searchable:
        return None, None

    for pattern, icon_name, color in _MODEL_ICON_RULES:
        if re.search(pattern, searchable):
            return icon_name, color

    candidates = _fuzzy_candidates(searchable)
    scores = {}
    for alias, icon_name, color in _FUZZY_MODEL_ALIASES:
        compact_alias = _normalize_model_name(alias).replace("-", "")
        score = max(
            (
                SequenceMatcher(None, candidate, compact_alias).ratio()
                for candidate in candidates
            ),
            default=0.0,
        )
        icon = (icon_name, color)
        scores[icon] = max(score, scores.get(icon, 0.0))

    ranked = sorted(
        ((score, *icon) for icon, score in scores.items()),
        reverse=True,
    )
    best_score, icon_name, color = ranked[0]
    next_score = ranked[1][0]
    if best_score >= 0.84 and best_score - next_score >= 0.08:
        return icon_name, color
    return None, None
