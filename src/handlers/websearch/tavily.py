from .websearch import WebSearchHandler
from ...handlers import ExtraSettings, ErrorSeverity

class TavilyHandler(WebSearchHandler):
    key="tavily"

    @staticmethod
    def get_extra_requirements() -> list:
        return ["tavily_python"]

    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.EntrySetting("token", _("Token"), _("Tavily API key"), ""),
            ExtraSettings.ScaleSetting("results", _("Max Results"), _("Number of results to consider"), 2, 1, 20, 0),
            ExtraSettings.ComboSetting("search_depth", _("The depth of the search"), _("The depth of the search. advanced search is tailored to retrieve the most relevant sources and content snippets for your query, while basic search provides generic content snippets from each source. A basic search costs 1 API Credit, while an advanced search costs 2 API Credits."), {"Advanced":"advanced","Basic":"basic"}, "basic"),
            ExtraSettings.ComboSetting("topic", _("The category of the search"), _("The category of the search.news is useful for retrieving real-time updates, particularly about politics, sports, and major current events covered by mainstream media sources. general is for broader, more general-purpose searches that may include a wide range of sources."), {"General":"general","News":"news"}, "general"),
            ExtraSettings.ScaleSetting("chunks_per_source", _("Chunks per source"), _("The number of content chunks to retrieve from each source. Each chunk's length is maximum 500 characters. Available only when search_depth is advanced."), 3, 1, 3, 0),
            ExtraSettings.ScaleSetting("days", _("Number of days back from the current date to include"),_("Available only if topic is news."),7,1,365,0),
            ExtraSettings.ToggleSetting("include_answer", _("Include answer"),_("Include an LLM-generated answer to the provided query. basic or true returns a quick answer. advanced returns a more detailed answer."),True),
            ExtraSettings.ToggleSetting("include_raw_content", _("Include raw content"),_("Include the cleaned and parsed HTML content of each search result."),False),
            ExtraSettings.ToggleSetting("include_images", _("Include images"),_("Also perform an image search and include the results in the response."),False),
            ExtraSettings.ToggleSetting("include_image_descriptions", _("Include image descriptions"),_("When include_images is true, also add a descriptive text for each image."),False),
            ExtraSettings.EntrySetting("include_domains", _("Include domains"), _("A list of domains to specifically include in the search results."), ""),
            ExtraSettings.EntrySetting("exclude_domains", _("Exclude domains"), _("A list of domains to specifically exclude from the search results."), ""),

        ]

    def query(self, keywords: str) -> tuple[str, list]:
        return self.query_streaming(keywords, lambda title, link, favicon: None)

    def query_streaming(self, keywords: str, add_website) -> tuple[str, list]:
        from tavily import TavilyClient
        import re
        if not (token:=self.get_setting("token")):
            return "Tavily API token not provided. Please enter your token in the settings to continue.", []
        client = TavilyClient(api_key=token)
        try:
            results = client.search(
                    query= keywords,
                    search_depth= self.get_setting("search_depth"),
                    max_results= self.get_setting("results"),
                    topic = self.get_setting("topic"),
                    chunks_per_source = self.get_setting("chunks_per_source"),
                    days = self.get_setting("days"),
                    include_answer = self.get_setting("include_answer"),
                    include_raw_content = self.get_setting("include_raw_content"),
                    include_images = self.get_setting("include_images"),
                    include_image_descriptions = self.get_setting("include_image_descriptions"),
                    include_domains = [x for x in re.split(r'[,\s]+', self.get_setting("include_domains")) if x],
                    exclude_domains = [x for x in re.split(r'[,\s]+', self.get_setting("exclude_domains")) if x]
            )['results']
        except Exception as e:
            results = []
            if len(results) == 0:
                self.throw("Failed to query Tavily: " + str(e), ErrorSeverity.WARNING)
                return "No results found", []


        text = ""
        urls = []
        for result in results:
            add_website(result['title'],result['url'], None)
            text += f"## {result['title']}\n{result['content']}\n\n"
            urls.append(result['url'])
        text = text[:5000]
        return text, urls

