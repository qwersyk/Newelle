from .websearch import WebSearchHandler
from ...handlers import ExtraSettings, ErrorSeverity


class SearXNGHandler(WebSearchHandler):
    key = "searxng"

    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.EntrySetting("endpoint", "SearXNG Instance", "URL of the instance of SearXNG to query.\nIt is strongly suggested to selfhost your own instance with json mode enabled", "https://search.hbubli.cc"),
            ExtraSettings.EntrySetting("lang", "Language", "Language for the search results", "en"),
            ExtraSettings.ScaleSetting("results", "Results", "Number of results to consider", 2, 1, 10, 0),
            ExtraSettings.ToggleSetting("scrape", "Instance scraping", "Scrape SearXNG instance if JSON format is not enabled", True)
        ]

    def query(self, keywords: str) -> str:
        try:
           results = self.get_links(keywords)
        except Exception as e:
            results = []
            if self.get_setting("scrape"):
                results = self.scrape_searxng_results(keywords)
            if len(results) == 0:
                self.throw("Failed to query SearXNG: " + str(e), ErrorSeverity.WARNING)
                return "No results found"
        content = self.scrape_websites(results)
        text = ""
        for result in content:
            text += f"## {result['title']}\n{result['text']}\n\n"
        text = text[:5000]
        return text


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

        # Extract links from the HTML response
        print(response.text)
        result_links = self.extract_links_from_html(response.text)
        return result_links

    def scrape_websites(self, result_links):
        from newspaper import Article, ArticleException
        max_results = self.get_setting("results")
        lang = self.get_setting("lang")
        if not result_links:
            print("No result links found on the SearXNG page.")
            return []

        extracted_content = []
        processed_count = 0

        for url, initial_title in result_links:
            if processed_count >= max_results:
                print(f"Reached maximum results limit ({max_results}).")
                break

            print(f"\nProcessing URL ({processed_count + 1}/{min(len(result_links), max_results)}): {url}")
            article_data = {'url': url, 'title': initial_title, 'text': ''} # Pre-populate with URL and initial title

            try:
                # Configure Article object
                article = Article(url, language=lang, request_timeout=4, fetch_images=False)

                # Download and parse
                article.download()
                article.parse()

                # Check if parsing was successful and text was extracted
                if article.text:
                    article_data['title'] = article.title or initial_title # Prefer newspaper's title if available
                    article_data['text'] = article.text
                    extracted_content.append(article_data)
                    print(f"  Successfully extracted content. Title: '{article_data['title']}'")
                    processed_count += 1
                else:
                    print("  Could not extract main text content from the page.")

            except ArticleException as e:
                print(f"  Newspaper3k failed for {url}: {e}")
            except Exception as e:
                # Catch other potential errors during download/parse
                print(f"  An unexpected error occurred processing {url}: {e}")

        print(f"\nFinished processing. Successfully extracted content from {len(extracted_content)} URLs.")
        return extracted_content
