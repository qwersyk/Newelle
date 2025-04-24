from .openai_handler import OpenAIHandler
from ...handlers import ExtraSettings

class OpenRouterHandler(OpenAIHandler):
    key = "openrouter"
    default_models = (("meta-llama/llama-3.1-70b-instruct:free", "meta-llama/llama-3.1-70b-instruct:free"), )
    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.set_setting("endpoint", "https://openrouter.ai/api/v1/")

    def get_extra_settings(self) -> list:
        r =  self.build_extra_settings("OpenRouter", True, True, False, False, True, "https://openrouter.ai/privacy", "https://openrouter.ai/docs/models", False, True)
        r += [
            ExtraSettings.ComboSetting("sorting", _("Provider Sorting"), _("Choose providers based on pricing/throughput or latency"), ((_("Price"), "price"), (_("Throughput"), "throughput"),(_("Latency"), "latency")), "price"),
            ExtraSettings.EntrySetting("order", _("Providers Order"), _("Add order of providers to use, names separated by a comma.\nEmpty to not specify"), ""),
            ExtraSettings.ToggleSetting("fallback", _("Allow Fallbacks"), _("Allow fallbacks to other providers"), True),
        ]
        return r
    
    def get_extra_headers(self):
        return {
            "HTTP-Referer": "https://github.com/qwersyk/Newelle",
            "X-Title": "Newelle"
        }

    def get_extra_body(self):
        r = {}
        p = {}
        p["sort"] = self.get_setting("sorting")
        if self.get_setting("order") and self.get_setting("order") != "":
            p["order"] = self.get_setting("order").split(",")
            p["allow_fallbacks"] = self.get_setting("fallback")
        r["provider"] = p
        return r
