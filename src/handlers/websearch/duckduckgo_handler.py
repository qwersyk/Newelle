from .websearch import WebSearchHandler
from ...handlers import ExtraSettings, ErrorSeverity

class DDGSeachHandler(WebSearchHandler):
    key="ddgsearch"
    search_engines = [
        "auto",
        "bing",
        "brave",
        "duckduckgo",
        "google",
        "grokipedia",
        "mojeek",
        "yandex",
        "yahoo",
        "wikipedia"
    ]
    @staticmethod
    def get_extra_requirements() -> list:
        return ["ddgs"]

    def supports_streaming_query(self) -> bool:
        return True

    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.ScaleSetting("results", _("Max Results"), _("Number of results to consider"), 2, 1, 10, 0),
            ExtraSettings.EntrySetting("region", _("Region"), _("Region for the search results"), "us-en"),
            ExtraSettings.ComboSetting("search_engine", _("Search Engine"), _("Search engine to use"), self.search_engines,"auto"),
        ]
    
    def query(self, keywords: str, max_results: int = None) -> tuple[str, list]:
        return self.query_streaming(keywords, lambda title, link, favicon: None, max_results=max_results)
    
    def query_streaming(self, keywords: str, add_website, max_results: int = None) -> tuple[str, list]:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        ddg = DDGS()
        if max_results is None:
            max_results = int(self.get_setting("results"))
        try:
            results = ddg.text(keywords, max_results=max_results, region=self.get_setting("region"), backend=self.get_setting("search_engine"))
        except Exception as e:
            results = []
            if len(results) == 0:
                self.throw("Failed to query DDG: " + str(e), ErrorSeverity.WARNING)
                return "No results found", []
        results = [(result['href'], result['title']) for result in results]
        print(results)
        content, urls = self.scrape_websites(results, add_website, max_results=max_results)
        text = "\n\n".join(
            self.format_source(result["title"], result["url"], result["text"][:3000])
            for result in content
        )
        return text, urls

