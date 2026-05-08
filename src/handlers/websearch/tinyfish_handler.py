from .websearch import WebSearchHandler
from ...handlers import ExtraSettings, ErrorSeverity

class TinyFishHandler(WebSearchHandler):
    key = "tinyfish"

    @staticmethod
    def get_extra_requirements() -> list:
        return ["tinyfish"]

    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.EntrySetting("token", _("API Key"), _("TinyFish API key"), "", password=True),
            ExtraSettings.ScaleSetting("results", _("Max Results"), _("Number of results to consider"), 5, 1, 20, 0),
            ExtraSettings.ComboSetting("location", _("Location"), _("Location for search results"), {"United States": "US", "United Kingdom": "GB", "Germany": "DE", "France": "FR", "Italy": "IT", "Spain": "ES", "Japan": "JP", "China": "CN"}, "US"),
            ExtraSettings.ComboSetting("language", _("Language"), _("Language for search results"), {"English": "en", "Spanish": "es", "French": "fr", "German": "de", "Italian": "it", "Japanese": "ja", "Chinese": "zh"}, "en"),
        ]

    def query(self, keywords: str, max_results: int = None) -> tuple[str, list]:
        return self.query_streaming(keywords, lambda title, link, favicon: None, max_results=max_results)

    def query_streaming(self, keywords: str, add_website, max_results: int = None) -> tuple[str, list]:
        from tinyfish import TinyFish
        import os

        token = self.get_setting("token") or os.environ.get("TINYFISH_API_KEY", "")
        kwargs = {}
        if token:
            kwargs["api_key"] = token

        client = TinyFish(**kwargs)

        if max_results is None:
            max_results = self.get_setting("results")

        try:
            response = client.search.query(
                keywords,
                location=self.get_setting("location"),
                language=self.get_setting("language"),
            )
        except Exception as e:
            self.throw("Failed to query TinyFish: " + str(e), ErrorSeverity.WARNING)
            return "No results found", []

        text = ""
        urls = []
        results = response.results[:max_results]
        for result in results:
            url = getattr(result, "url", getattr(result, "link", ""))
            title = getattr(result, "title", "")
            snippet = getattr(result, "snippet", getattr(result, "content", ""))
            add_website(title, url, None)
            text += f"## {title}\n{snippet}\n\n"
            urls.append(url)
        text = text[:30000]
        return text, urls
