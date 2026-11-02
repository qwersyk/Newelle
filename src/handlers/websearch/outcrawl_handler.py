from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from .websearch import WebSearchHandler

from ...handlers import ExtraSettings, ErrorSeverity


class OutcrawlSearchHandler(WebSearchHandler):
    API_URL = "https://api.outcrawl.ai/v1"
    key = "outcrawl"

    def supports_streaming_query(self) -> bool:
        return True

    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.EntrySetting("token", _("Token"), _("Outcrawl API key"), "", password=True),
            ExtraSettings.ScaleSetting("results", _("Max Results"), _("Number of results to consider"), 2, 1, 20, 0),
            ExtraSettings.ToggleSetting("read_pages", _("Read pages"), _("Fetch the full content of each result as Markdown instead of using only the search snippet. Each page uses extra API credits."), True),
        ]

    def query(self, keywords: str, max_results: int = None) -> tuple[str, list]:
        return self.query_streaming(keywords, lambda title, link, favicon: None, max_results=max_results)

    def query_streaming(self, keywords: str, add_website: Callable, max_results: int = None) -> tuple[str, list]:
        if not self.get_setting("token"):
            return "Outcrawl API token not provided. Please enter your token in the settings to continue.", []

        try:
            results = self.get_links(keywords, max_results)
        except Exception as e:
            self.throw("Failed to query Outcrawl: " + str(e), ErrorSeverity.WARNING)
            return "No results found", []

        if self.get_setting("read_pages"):
            results = self.read_pages(results)

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

    def _post(self, endpoint: str, data: dict, timeout: int) -> dict:
        import requests

        token = self.get_setting("token")
        if not token:
            raise ValueError("Outcrawl API token not provided")
        headers = {"Authorization": f"Bearer {token}", "User-Agent": "Newelle"}
        response = requests.post(f"{self.API_URL}/{endpoint}", headers=headers, json=data, timeout=timeout)
        if not response.ok:
            try:
                message = response.json()["error"]
            except Exception:
                message = response.text
            raise RuntimeError(f"Outcrawl {endpoint} failed ({response.status_code}): {message}")
        return response.json()

    def get_links(self, query: str, max_results: int = None) -> list:
        if max_results is None:
            max_results = self.get_setting("results")
        try:
            limit = max(1, min(int(max_results), 20))
        except (TypeError, ValueError):
            limit = 5

        hits = self._post("search", {"query": query, "limit": limit}, timeout=30).get("hits") or []
        results = [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": item.get("snippet") or "",
            }
            for item in hits
            if item.get("url")
        ]
        return results[:limit]

    def read_pages(self, results: list) -> list:
        """Replace each result's snippet with the page content as Markdown, keeping the snippet if the page fails"""
        def read_page(result: dict) -> dict:
            try:
                page = self._post("scrape", {"url": result["url"], "formats": ["markdown"], "onlyMainContent": True}, timeout=60)
            except Exception as e:
                print(f"Outcrawl could not read {result['url']}: {e}")
                return result
            return {**result, "content": page.get("markdown") or result["content"]}

        if not results:
            return results
        with ThreadPoolExecutor(max_workers=min(5, len(results))) as executor:
            return list(executor.map(read_page, results))
