from collections.abc import Callable

from .websearch import WebSearchHandler

from ...handlers import ExtraSettings, ErrorSeverity


class SpecStreamSearchHandler(WebSearchHandler):
    API_URL = "https://api.specstream.ai/v1/web-search"
    key = "specstream"

    def supports_streaming_query(self) -> bool:
        return True

    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.EntrySetting("token", _("Token"), _("SpecStream API key"), "", password=True),
            ExtraSettings.ScaleSetting("results", _("Max Results"), _("Number of results to consider"), 2, 1, 10, 0),
            ExtraSettings.ToggleSetting("passages", _("Include page text"), _("Also return the text of the pages closest to the query instead of using only the search snippet. It is part of the same search request."), True),
        ]

    def query(self, keywords: str, max_results: int = None) -> tuple[str, list]:
        return self.query_streaming(keywords, lambda title, link, favicon: None, max_results=max_results)

    def query_streaming(self, keywords: str, add_website: Callable, max_results: int = None) -> tuple[str, list]:
        if not self.get_setting("token"):
            return "SpecStream API token not provided. Please enter your token in the settings to continue.", []

        try:
            results = self.get_links(keywords, max_results)
        except Exception as e:
            self.throw("Failed to query SpecStream: " + str(e), ErrorSeverity.WARNING)
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
            add_website(result["title"], result["url"], None)
            text_parts.append(self.format_source(result["title"], result["url"], content))
            urls.append(result["url"])
            remaining_content -= len(content)
        return "\n\n".join(text_parts), urls

    def _get(self, params: dict, timeout: int) -> dict:
        import requests

        token = self.get_setting("token")
        if not token:
            raise ValueError("SpecStream API token not provided")
        headers = {"Authorization": f"Bearer {token}", "User-Agent": "Newelle"}
        response = requests.get(self.API_URL, headers=headers, params=params, timeout=timeout)
        if not response.ok:
            try:
                error = response.json()["error"]
                message = f"{error['message']} ({error['code']})"
            except Exception:
                message = response.text
            raise RuntimeError(f"SpecStream search failed ({response.status_code}): {message}")
        return response.json()

    def get_links(self, query: str, max_results: int = None) -> list:
        if max_results is None:
            max_results = self.get_setting("results")
        try:
            # Up to 10 results is one search at the base price
            num = max(1, min(int(max_results), 10))
        except (TypeError, ValueError):
            num = 5

        params = {"q": query, "num": num}
        if self.get_setting("passages"):
            params["passages"] = "true"
        page = self._get(params, timeout=60).get("data") or {}

        passages = {}
        for passage in page.get("passages") or []:
            passages.setdefault(passage.get("url"), []).append(passage.get("text") or "")

        results = []
        for item in page.get("sources") or []:
            url = item.get("url")
            if not url:
                continue
            content = "\n\n".join(passages.get(url, [])) or item.get("snippet") or ""
            if item.get("published") and content:
                content = f"Published: {item['published']}\n{content}"
            results.append({"title": item.get("title", ""), "url": url, "content": content})
        return results[:num]
