from .image_generator import ImageGeneratorHandler
from ...handlers import ExtraSettings
import urllib.parse
import os

class PollinationsHandler(ImageGeneratorHandler):
    """Pollinations AI image generation handler."""
    key = "pollinations"

    models = ["flux", "turbo", "gptimage", "kontext", "seedream", "nanobanana", "nanobanana-pro",
              "seedream-pro", "gptimage-large", "zimage", "klein", "klein-large", "imagen-4", "grok-imagine"]

    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.EntrySetting("api-key", _("API Key"), _("Pollinations API key from enter.pollinations.ai (required)"), "", password=True),
            ExtraSettings.ComboSetting("model", _("Model"), _("Choose the model to use for image generation"), self.models, "zimage"),
            ExtraSettings.MultilineEntrySetting("positive-prompt", _("Positive Prompt Template"), _("Prompt template for positive prompt, [input] will be replaced with the AI input"), "[input]"),
            ExtraSettings.MultilineEntrySetting("negative-prompt", _("Negative Prompt"), _("Negative prompt to exclude from the image"), ""),
            ExtraSettings.NestedSetting("advanced_settings", _("Advanced Settings"), _("Advanced image generation settings"), [
                ExtraSettings.ScaleSetting("width", _("Width"), _("Width of the generated image"), 400, 256, 2048, 0),
                ExtraSettings.ScaleSetting("height", _("Height"), _("Height of the generated image"), 400, 256, 2048, 0),
                ExtraSettings.EntrySetting("seed", _("Seed"), _("Seed for reproducible results (-1 for random)"), "-1"),
                ExtraSettings.EntrySetting("enhance", _("Enhance"), _("Enhance the prompt with more detail (true/false)"), "false"),
                ExtraSettings.EntrySetting("quality", _("Quality"), _("Image quality (standard/hd)"), "standard"),
                ExtraSettings.EntrySetting("transparent", _("Transparent"), _("Transparent background if supported (true/false)"), "false"),
            ])
        ]

    def generate_image(self, prompt: str, msg_uuid: str, output_file: str = None) -> str:
        """Generate an image URL from a prompt using Pollinations AI.

        Args:
            prompt: The text prompt for image generation
            msg_uuid: Unique message identifier (unused, cache handled by base class)
            output_file: Optional local path; if provided and the result is a URL,
                         the base class downloads it for you.

        Returns:
            str: URL to the generated Pollinations image
        """
        prompt = self.get_setting("positive-prompt").replace("[input]", prompt)
        output = os.path.join(self.cache_dir, f"{msg_uuid}.png") if output_file is None else output_file

        # Build query parameters for the Pollinations API
        params = {}

        api_key = self.get_setting("api-key")
        model = self.get_setting("model")
        if model:
            params["model"] = model
        if api_key:
            params["key"] = api_key
        negative_prompt = self.get_setting("negative-prompt")
        if negative_prompt:
            params["negative_prompt"] = negative_prompt

        width = self.get_setting("width")
        if width and int(width) > 0:
            params["width"] = int(width)

        height = self.get_setting("height")
        if height and int(height) > 0:
            params["height"] = int(height)

        seed = self.get_setting("seed")
        if seed and str(seed) != "-1":
            params["seed"] = int(seed)

        enhance = self.get_setting("enhance")
        if enhance and enhance.lower() == "true":
            params["enhance"] = "true"

        quality = self.get_setting("quality")
        if quality and quality != "standard":
            params["quality"] = quality

        transparent = self.get_setting("transparent")
        if transparent and transparent.lower() == "true":
            params["transparent"] = "true"

        url = "https://gen.pollinations.ai/image/" + urllib.parse.quote(prompt) + "?" + urllib.parse.urlencode(params)
        return url 
