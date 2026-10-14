from .extensions import NewelleExtension
from .tools import create_io_tool

class VisionExtension(NewelleExtension):
    key = "vision"
    name = "Vision Extension"

    def __init__(self, pip_path: str, extension_path: str, settings):
        super().__init__(pip_path, extension_path, settings)

    def analyze_image(self, image_path: str, query: str):
        llm = None
        if self.get_setting("secondary_llm"):
            if self.secondary_llm.supports_vision():
                llm = self.secondary_llm 
            elif self.llm.supports_vision():
                llm = self.llm 
        else:
            if self.llm.supports_vision():
                llm = self.llm 
        if llm is None:
            return "No LLM supports vision"
        query = "```image```\n" + image_path + "\n```"
        query += "\n" + query

        return llm.generate_text(query, [], [self.get_setting("image_analysis_prompt")])
    
    def get_tools(self) -> list:
        return [
                create_io_tool("analyze_image", "Analyze an image and return the information in the image", self.analyze_image, title="Analyze Image"),

        ]
