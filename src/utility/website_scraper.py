from os import wait
import requests
from newspaper import Article

class WebsiteScraper:
    def __init__(self, url, fallback_word_threshold=100) -> None:
        self.url = url
        self.html = None 
        self.article = None 
        self.fallback_word_threshold = fallback_word_threshold
    
    def get_page_source(self):
        self.html = requests.get(self.url).text 
        return self.html 
    
    def set_html(self, html):
        self.html = html

    def parse_article(self):
        if self.article:
            return
        self.article = Article(url=self.url)
        if self.html is not None:
            self.article.set_html(self.html)
        else:
            self.article.download()
            self.html = self.article.html 

        self.article.parse()

    def get_favicon(self):
        self.parse_article()
        if self.article is None:
            return ""
        favicon = self.article.meta_favicon
        if not favicon.startswith("http") and not favicon.startswith("https"):
            from urllib.parse import urlparse, urljoin
            base_url = urlparse(self.url).scheme + "://" + urlparse(self.url).netloc
            favicon = urljoin(base_url, favicon)
        return favicon

    def get_description(self):
        self.parse_article()
        if self.article is None:
            return ""
        return self.article.meta_description 

    def get_title(self):
        self.parse_article()
        if self.article is None:
            return ""
        return self.article.title

    def get_text(self):
        self.parse_article()
        if self.article is None:
            return ""
        text = self.article.text
        if len(text.split()) > self.fallback_word_threshold:
            return self.article.text 
        else:
            return self.clean_html_to_markdown(self.html)

    def clean_html_to_markdown(self, html_content, include_links=False):
        from bs4 import BeautifulSoup
        from markdownify import markdownify as md
        # Parse the HTML content
        soup = BeautifulSoup(html_content, 'html.parser')
        tags_whitelist = ['a', 'p', 'ul', 'ol', 'li', 'b', 'strong', 'i', 'em', 'table', 'tr', 'th', 'td', 'h1', 'h2', 'h3', 'h4', 'h5']
        if not include_links:
            tags_whitelist.remove("a")
        # Remove images
        for img in soup.find_all('img'):
            img.decompose()
        
        # Remove style and script tags
        for tag in soup(['style', 'script', 'iframe', 'meta', 'head']):
            tag.decompose()
        
        # Remove all tags except links, paragraphs, lists, bold, italic
        for tag in soup.find_all(True):
            if tag.name not in tags_whitelist:
                tag.unwrap()
        
        # Convert the cleaned HTML to Markdown
        markdown_content = md(str(soup))
        
        # Extract links and format them as a list
        links = []
        if include_links:
            for a_tag in soup.find_all('a', href=True):
                link_text = a_tag.get_text(strip=True)
                link_url = a_tag['href']
                links.append(f"- [{link_text}]({link_url})")
        
        # Join the links into a single string
        links_list = "\n".join(links)
        
        # Combine the Markdown content and the links list
        final_content = f"{markdown_content}"
        if len(links) > 0:
            final_content += "\n\n#### Link List: " + links_list
        
        return final_content
