from .websearch import WebSearchHandler
from ...handlers import ExtraSettings, ErrorSeverity

class DDGSeachHandler(WebSearchHandler):
    key="ddgsearch"

    @staticmethod
    def get_extra_requirements() -> list:
        return ["duckduckgo_search"]

    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.ScaleSetting("results", _("Max Results"), _("Number of results to consider"), 2, 1, 10, 0),
            ExtraSettings.EntrySetting("region", _("Region"), _("Region for the search results"), "us-en"),
        ]
    
    def query(self, keywords: str) -> tuple[str, list]:
        return self.query_streaming(keywords, lambda title, link, favicon: None)
    
    def query_streaming(self, keywords: str, add_website) -> tuple[str, list]:
        from duckduckgo_search import DDGS
        ddg = DDGS()
        try:
            results = ddg.text(keywords, max_results=self.get_setting("results"), region=self.get_setting("region"))
        except Exception as e:
            results = []
            if len(results) == 0:
                self.throw("Failed to query DDG: " + str(e), ErrorSeverity.WARNING)
                return "No results found", []
        results = [(result['href'], result['title']) for result in results]
        print(results)
        content, urls = self.scrape_websites(results, add_website)
        text = ""
        for result in content:
            text += f"## {result['title']}\n{result['text']}\n\n"
        text = text[:5000]
        return text, urls
    
    def scrape_websites(self, result_links, update):
        from newspaper import Article, ArticleException
        max_results = self.get_setting("results")
        if not result_links:
            print("No result links found on the DDG page.")
            return [],[]
        urls = []
        extracted_content = []
        processed_count = 0

        for url, initial_title in result_links:
            urls.append(url)
            if processed_count >= max_results:
                print(f"Reached maximum results limit ({max_results}).")
                break

            print(f"\nProcessing URL ({processed_count + 1}/{min(len(result_links), max_results)}): {url}")
            article_data = {'url': url, 'title': initial_title, 'text': ''} # Pre-populate with URL and initial title

            try:
                # Configure Article object
                article = Article(url, request_timeout=4, fetch_images=False)

                # Download and parse
                article.download()
                article.parse()
                update(article.title, url, article.meta_favicon)
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
        return extracted_content, urls
