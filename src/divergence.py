from .extensions import NewelleExtension
from gi.repository import Gtk, WebKit
from .handlers import PromptDescription
from .tools import create_io_tool, Tool, ToolResult
import json
import threading

DIVERGENCE_PROMPT = """
- You can get the current worldline divergence number using 

```divergence
current 
```

"""

class DivergenceExtension(NewelleExtension):
    name = "Divergence Meter"
    id="divergence"

    API_BASE = "https://divergence.nyarchlinux.moe/api"

    def get_additional_prompts(self) -> list:
        return [
            PromptDescription("divergence", "Divergence", "Get current worldline divergence number", DIVERGENCE_PROMPT)
        ]

    def provides_both_widget_and_answer(self, codeblock: str, lang: str) -> bool:
        return True 

    def get_gtk_widget(self, codeblock: str, lang: str, msg_uuid=None) -> Gtk.Widget | None:
        webview = WebKit.WebView()
        webview.set_size_request(400, 150)
        webview.load_uri("https://divergence.nyarchlinux.moe/lite.html")
        webview.set_sensitive(False)
        return webview

    def get_answer(self, codeblock: str, lang: str) -> str | None:
        import requests
        content = requests.get(f"{self.API_BASE}/divergence")
        j = content.json()
        result = j["divergence"]
        return result

    def fetch_divergence(self) -> str:
        """Fetch the current worldline divergence number."""
        import requests
        result = ToolResult()
        result.set_widget(self.get_gtk_widget("", ""))
        def th():
            result.set_output(self.get_answer("", ""))
        t = threading.Thread(target=th)
        t.start()
        return result

    def fetch_divergence_news(self, page: int = 1, per_page: int = 10, min_impact: float = None, max_impact: float = None) -> str:
        """Fetch news articles from the Divergence Meter API.

        Args:
            page: Page number (starts at 1)
            per_page: Number of articles per page
            min_impact: Filter by minimum impact value
            max_impact: Filter by maximum impact value

        Returns:
            JSON string with articles, pagination and filters
        """
        import requests
        params = {
            "page": page,
            "per_page": per_page,
        }
        if min_impact is not None:
            params["min_impact"] = min_impact
        if max_impact is not None:
            params["max_impact"] = max_impact

        content = requests.get(f"{self.API_BASE}/news", params=params)
        j = content.json()
        return json.dumps(j, indent=2)

    def get_tools(self) -> list:
        return [
            Tool(
                "get_divergence",
                "Get the current worldline divergence number from the Divergence Meter.",
                self.fetch_divergence,
                title="Get Divergence",
                tools_group="Divergence Meter",
            ),
            create_io_tool(
                "get_divergence_news",
                "Get news articles from the Divergence Meter. Supports pagination and filtering by impact value. Generally, important news have >0.1 impact",
                self.fetch_divergence_news,
                title="Get Divergence News",
                tools_group="Divergence Meter",
            ),
        ]
