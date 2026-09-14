import os
import json
import base64
import threading
import gettext

_ = gettext.gettext

from .image_generator import ImageGeneratorHandler
from ...handlers import ExtraSettings


class OpenAIImageHandler(ImageGeneratorHandler):
    key = "openai-image"

    default_models = (("dall-e-3", "dall-e-3"), ("gpt-image-1", "gpt-image-1"))

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.models = self.default_models
        if self.get_setting("models", False) is not None:
            try:
                self.models = json.loads(self.get_setting("models", False))
            except Exception:
                pass
        else:
            threading.Thread(target=self._fetch_models, daemon=True).start()

    def _fetch_models(self):
        try:
            import openai
            api = self.get_setting("api")
            if not api:
                return
            client = openai.Client(api_key=api, base_url=self._get_endpoint())
            models_resp = client.models.list()
            image_models = []
            for m in models_resp:
                mid = m.id if hasattr(m, "id") else str(m)
                if any(kw in mid.lower() for kw in ("dall", "image", "gpt-image", "flux", "sd3", "stable")):
                    image_models.append((mid, mid))
            if image_models:
                self.models = tuple(image_models)
                print(self.models)
                self.set_setting("models", json.dumps(self.models))
                self.settings_update()
        except Exception as e:
            print(f"Error fetching OpenAI image models: {e}")

    def _get_endpoint(self):
        endpoint = self.get_setting("endpoint")
        if endpoint and endpoint.strip():
            return endpoint.strip()
        return "https://api.openai.com/v1"

    @staticmethod
    def get_extra_requirements() -> list:
        return ["openai"]

    def get_extra_settings(self) -> list:
        custom = self.get_setting("custom_model", False, False)
        default_model = self.models[0][0] if self.models else self.default_models[0][0]
        if custom:
            model_settings = [
                ExtraSettings.EntrySetting("model", _("Model"), _("Name of the image generation model to use"), default_model),
            ]
        else:
            model_settings = [
                ExtraSettings.ComboSetting("model", "Model", "Image generation model", self.models, default_model, refresh=lambda x: self._refresh_models()),
            ]
        return [
            ExtraSettings.EntrySetting("api", "API Key", "OpenAI API key", "", password=True),
            ExtraSettings.EntrySetting("endpoint", "API Endpoint", "Custom endpoint for OpenAI-compatible services. Leave empty for default OpenAI endpoint.", "", website="https://platform.openai.com/docs/api-reference/images"),
            ExtraSettings.ToggleSetting("custom_model", _("Use Custom Model"), _("Use a custom model name instead of selecting from the list"), False, update_settings=True),
            *model_settings,
            ExtraSettings.SpinSetting("height", "Height", "Height of the generated image", 512, 256, 2048, 0),
            ExtraSettings.SpinSetting("width", "Width", "Width of the generated image", 512, 256, 2048, 0),
            ExtraSettings.ComboSetting("quality", "Quality", "Image quality (dall-e-3 only)", (
                ("standard", "Standard"),
                ("hd", "HD"),
                ), "standard"),
            ExtraSettings.MultilineEntrySetting("positive-prompt", "Positive Prompt Template", "Prompt template, [input] will be replaced with the user prompt", "[input]"),
            ExtraSettings.EntrySetting("style", "Style", "Generation style: vivid or natural (dall-e-3 only)", "vivid"),
        ]

    def _refresh_models(self):
        threading.Thread(target=self._fetch_models, daemon=True).start()

    def generate_image(self, prompt: str, msg_uuid: str, output_file: str = None) -> str:
        import openai

        prompt = self.get_setting("positive-prompt").replace("[input]", prompt)
        output = output_file if output_file else os.path.join(self.cache_dir, f"{msg_uuid}.png")

        client = openai.Client(api_key=self.get_setting("api"), base_url=self._get_endpoint())

        model = self.get_setting("model")
        width = self.get_setting("width")
        height = self.get_setting("height")
        try:
            size = f"{int(width)}x{int(height)}"
        except (TypeError, ValueError):
            size = "1024x1024"
        quality = self.get_setting("quality")
        response_format = "url"
        style = self.get_setting("style")

        kwargs = {
            "model": model,
            "prompt": prompt,
            "size": size,
            "n": 1,
        }

        if model == "dall-e-3":
            kwargs["quality"] = quality
            if style and style in ("vivid", "natural"):
                kwargs["style"] = style

        if model != "gpt-image-1":
            kwargs["response_format"] = response_format

        response = client.images.generate(**kwargs)

        image_data = response.data[0]

        if hasattr(image_data, "url") and image_data.url:
            return image_data.url
        elif hasattr(image_data, "b64_json") and image_data.b64_json:
            img_bytes = base64.b64decode(image_data.b64_json)
            from PIL import Image
            from io import BytesIO
            img = Image.open(BytesIO(img_bytes))
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA")
            img.save(output, "PNG")
            return output
        else:
            raise RuntimeError("No image data returned from API")
