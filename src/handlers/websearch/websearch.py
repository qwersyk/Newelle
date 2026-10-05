from abc import abstractmethod
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from ...handlers import Handler
from ...utility.source_attribution import format_source_context
from ...utility.website_scraper import WebsiteScraper


class WebSearchHandler(Handler):
    schema_key = "websearch-settings"

    @staticmethod
    def format_source(title: str, url: str, content: str) -> str:
        """Return a web passage with its title and URL kept adjacent."""
        return format_source_context(
            content,
            source=url,
            title=title or None,
            source_type="Web",
        )
    
    @abstractmethod
    def query(self, keywords: str, max_results: int = None) -> tuple[str, list]:
        """Return the result for a query and the sources

        Args:
            keywords: the query 
            max_results: the max number of results to return

        Returns:
            - str: the text to send to the LLM 
            - list: the list of sources (URL)
        """
        return "", []

    def supports_streaming_query(self) -> bool:
        return False

    def scrape_websites(self, result_links, update, max_results=None):
        """Scrape the websites of the search results in parallel.

        Websites are downloaded concurrently to be faster. The update
        callback is called as soon as each website has been fetched (from
        a worker thread), so the UI can still show the results
        progressively. The returned content keeps the original search
        result order.

        Args:
            result_links: list of (url, title) tuples to scrape
            update: callback that takes (title, link, favicon), called when a website has been fetched
            max_results: the max number of results to consider

        Returns:
            - list: the scraped content in search result order, each item a dict with url, title and text
            - list: the list of scraped urls
        """
        if max_results is None:
            max_results = self.get_setting("results")
        if not result_links:
            print("No result links found.")
            return [], []

        def scrape_website(result):
            url, initial_title = result
            print(f"Processing URL: {url}")
            try:
                article = WebsiteScraper(url)
                article.parse_article()
                update(article.get_title(), url, article.get_favicon())
                return {"url": url, "title": article.get_title() or initial_title, "text": article.get_text()}
            except Exception as e:
                print(f"  An unexpected error occurred processing {url}: {e}")
                return None

        scraped = {}
        next_link = 0
        futures = {}
        with ThreadPoolExecutor(max_workers=max(1, min(max_results, len(result_links)))) as executor:
            def submit_next():
                nonlocal next_link
                futures[executor.submit(scrape_website, result_links[next_link])] = next_link
                next_link += 1

            while next_link < len(result_links) and len(futures) < max_results:
                submit_next()
            while futures:
                done, _ = wait(list(futures), return_when=FIRST_COMPLETED)
                for future in done:
                    index = futures.pop(future)
                    data = future.result()
                    if data is not None and data["text"]:
                        scraped[index] = data
                # Keep enough websites in flight to reach max_results extracted results
                while next_link < len(result_links) and len(scraped) + len(futures) < max_results:
                    submit_next()

        content = [scraped[index] for index in sorted(scraped)]
        print(f"Finished processing. Successfully extracted content from {len(content)} URLs.")
        return content, [data["url"] for data in content]


    @abstractmethod
    def query_streaming(self,keywords: str, add_website: Callable, max_results: int = None) -> tuple[str, list]:
        """Return the result for a query in streaming mode

        Args:
            keywords: the query 
            add_website: the function to add a website, takes (title, link, favicon_path) 
            max_results: the max number of results to return

        Returns:
            - str: the text to send to the LLM
            - list: the list of sources (URL)
        """
        return "", []
