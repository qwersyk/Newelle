from .websearch import WebSearchHandler
from ...handlers import ExtraSettings, ErrorSeverity

class SearXNGHandler(WebSearchHandler):
    key = "searxng"

    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.EntrySetting("endpoint", _("SearXNG Instance"), _("URL of the instance of SearXNG to query.\nIt is strongly suggested to selfhost your own instance with json mode enabled"), "https://search.nyarchlinux.moe"),
            ExtraSettings.EntrySetting("lang", _("Language"), _("Language for the search results"), "en"),
            ExtraSettings.ScaleSetting("results", _("Results"), _("Number of results to consider"), 2, 1, 10, 0),
            ExtraSettings.ToggleSetting("scrape", _("Instance scraping"), _("Scrape SearXNG instance if JSON format is not enabled"), True),
            ExtraSettings.ToggleSetting("streaming", _("Show search progress"), _("Show search progress"), True)
        ]

    def supports_streaming_query(self) -> bool:
        return self.get_setting("streaming")

    def query(self, keywords: str, max_results: int = None) -> tuple[str, list]:
        return self.query_streaming(keywords, lambda title, link, favicon: None, max_results=max_results)

    def query_streaming(self, keywords: str, add_website, max_results: int = None) -> tuple[str, list]:
        try:
           results = self.get_links(keywords)
        except Exception as e:
            results = []
            if self.get_setting("scrape"):
                results = self.scrape_searxng_results(keywords)
            if len(results) == 0:
                self.throw("Failed to query SearXNG: " + str(e), ErrorSeverity.WARNING)
                return "No results found", []
        content, urls = self.scrape_websites(results, add_website, max_results=max_results)
        text = "\n\n".join(
            self.format_source(result["title"], result["url"], result["text"][:3000])
            for result in content
        )
        return text, urls

    def extract_links_from_html(self,response):
        from bs4 import BeautifulSoup
        from urllib.parse import urljoin
        soup = BeautifulSoup(response, 'html.parser')
        links = soup.find_all('a', {'class': 'url_header'})
        return [(urljoin(self.get_setting("endpoint"), link.get('href')), link.text) for link in links]


    def get_links(self, query):
        import requests 
        lang = self.get_setting("lang")
        try:
            r = requests.get(self.get_setting("endpoint") + "/search", params={'q': query, 'language': lang, 'format': 'json'})
            r.raise_for_status()
            results = r.json()
            res = []
            for result in results["results"]:
                res.append((result["url"], result["title"]))
            return res
        except Exception as e:
            raise e 

    def scrape_searxng_results(
        self,
        query: str,
    ):
        """
        Scrapes SearXNG HTML results

        Args:
            query: The search term.

        Returns:
            A list of dictionaries, each containing 'url', 'title', and 'text'
            for successfully processed articles. Returns empty list on failure.
        """
        from urllib.parse import urlencode
        import requests

        searxng_instance = self.get_setting("endpoint")
        lang = self.get_setting("lang")
        max_results = self.get_setting("results")

        search_url = f"{searxng_instance.rstrip('/')}/search"
        params = {
            'q': query,
            'language': lang,
            'categories': 'general',
            'time-range': '',
            'safesearch': 0, # 0:None, 1:Moderate, 2: Strict
            'theme': 'simple'

        }
        HEADERS = {
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br, zstd',
            'Accept-Language': 'it-IT,it;q=0.8,en-US;q=0.5,en;q=0.3',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Origin': 'null',
            'Sec-GPC': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'same-origin',
            'Sec-Fetch-User': '?1',
            'Priority': 'u=0, i',
        }
        print(f"Searching on: {searxng_instance} for query: '{query}'")
        print(f"Request URL: {search_url}?{urlencode(params)}")

        try:
            response = requests.get(search_url, params=params, headers=HEADERS, timeout=3)
            response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)
            print(f"SearXNG request successful (Status: {response.status_code})")
        except requests.exceptions.RequestException as e:
            print(f"Error fetching search results from {searxng_instance}: {e}")
            return []
        except Exception as e:
            print(f"An unexpected error occurred during the search request: {e}")
            return []


        result_links = self.extract_links_from_html(response.text)
        return result_links

