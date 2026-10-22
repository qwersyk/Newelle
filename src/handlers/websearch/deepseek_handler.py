from collections.abc import Callable

from .websearch import WebSearchHandler

from ...handlers import ExtraSettings, ErrorSeverity


class DeepseekSearchHandler(WebSearchHandler):
    SEARCH_URL = "https://api.deepseek.com/v1/tools/search"
    key = "deepseeksearch"

    def supports_streaming_query(self) -> bool:
        return True

    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.EntrySetting("token", _("Token"), _("DeepSeek API key"), "", password=True),
            ExtraSettings.ScaleSetting("results", _("Max Results"), _("Number of results to consider"), 2, 1, 10, 0),
            ExtraSettings.ComboSetting(
                "lang",
                _("Language"),
                _("Language for the search results"),
                {
                    _("English"): "en",
                    _("Chinese"): "zh",
                    _("Spanish"): "es",
                    _("French"): "fr",
                    _("German"): "de",
                    _("Italian"): "it",
                    _("Japanese"): "ja",
                    _("Portuguese"): "pt",
                    _("Russian"): "ru",
                },
                "en",
            ),
            ExtraSettings.EntrySetting("localization", _("Localization"), _("An optional IP address used to localize search results"), ""),
        ]

    def query(self, keywords: str, max_results: int = None) -> tuple[str, list]:
        return self.query_streaming(keywords, lambda title, link, favicon: None, max_results=max_results)

    def query_streaming(self, keywords: str, add_website: Callable, max_results: int = None) -> tuple[str, list]:
        if not self.get_setting("token"):
            return "DeepSeek API token not provided. Please enter your token in the settings to continue.", []

        try:
            results = self.get_links(keywords, max_results)
        except Exception as e:
            self.throw("Failed to query DeepSeek: " + str(e), ErrorSeverity.WARNING)
            return "No results found", []

        text_parts = []
        urls = []
        remaining_content = 30000
        for result in results:
            content = result["content"]
            if not content.strip():
                continue
            if text_parts and remaining_content <= 0:
                break
            content = content[:remaining_content]
            add_website(result["title"], result["url"], result["site_icon"])
            text_parts.append(self.format_source(result["title"], result["url"], content))
            urls.append(result["url"])
            remaining_content -= len(content)
        return "\n\n".join(text_parts), urls

    def get_links(self, query: str, max_results: int = None) -> list:
        import requests

        token = self.get_setting("token")
        if not token:
            raise ValueError("DeepSeek API token not provided")
        lang = self.get_setting("lang")
        localization = self.get_setting("localization")
        if max_results is None:
            max_results = self.get_setting("results")
        try:
            count = max(1, min(int(max_results), 50))
        except (TypeError, ValueError):
            count = 10

        # The query is limited to 128 characters, including appended operators
        suffix = f" lang:{lang}" if lang else ""
        search_query = f"{query[:max(0, 128 - len(suffix))]}{suffix}"
        data = {"query": search_query, "count": count, "need_content": True}
        if localization:
            data["localization"] = localization

        headers = {"Authorization": f"Bearer {token}"}
        response = requests.post(self.SEARCH_URL, headers=headers, json=data, timeout=30)
        if not response.ok:
            try:
                message = response.json()["error"]["message"]
            except Exception:
                message = response.text
            raise RuntimeError(f"DeepSeek search failed ({response.status_code}): {message}")
        return [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": item.get("content") or item.get("summary") or item.get("snippet") or "",
                "site_icon": item.get("site_icon"),
            }
            for item in response.json().get("results", [])
        ]
