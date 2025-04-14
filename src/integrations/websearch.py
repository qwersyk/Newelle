from ..extensions import NewelleExtension

class WebsearchIntegration(NewelleExtension):
    id = "websearch"
    name = "Websearch"

    def get_replace_codeblocks_langs(self) -> list:
        return ["search"]

    def get_answer(self, codeblock: str, lang: str) -> str | None:
        print(self.websearch.query(codeblock))
        return self.websearch.query(codeblock)

    
