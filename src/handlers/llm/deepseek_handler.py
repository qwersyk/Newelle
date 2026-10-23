from .openai_handler import OpenAIHandler


class DeepseekHandler(OpenAIHandler):
    key = "deepseek"
    default_send_reasoning = True
    default_models = (("deepseek-flash", "deepseek-flash"), ("deepseek-v4-pro", "deepseek-v4-pro"))

    # Models that accept image input. The legacy names are still accepted by
    # the API and are now served by the vision-capable V4.1 Flash model.
    VISION_MODELS = (
        "deepseek-flash",
        "deepseek-v4-flash",
        "deepseek-v4-flash-vision-exp",
    )

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.set_setting("endpoint", "https://api.deepseek.com")
        self.set_setting("advanced_params", False)

    def supports_vision(self) -> bool:
        return str(self.get_selected_model() or "") in self.VISION_MODELS

    def supports_video_vision(self) -> bool:
        # DeepSeek accepts images only; video_url parts are not supported.
        return False

    def get_supported_files(self) -> list[str]:
        # DeepSeek has no document input; images are sent via image_url.
        return []

    def get_extra_settings(self) -> list:
        return self.build_extra_settings("Deepseek", True, True, False, True, True, None, "https://api-docs.deepseek.com/quick_start/pricing", False, True)

