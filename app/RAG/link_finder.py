import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from fastapi import HTTPException


def get_internal_links(base_url: str, html_content: str):
    """Parse HTML and return all unique internal links from the given website."""
    
    soup = BeautifulSoup(html_content, "html.parser")
    base_domain = urlparse(base_url).netloc

    links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        try:
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)
            if parsed.netloc == base_domain:  # keep only internal links
                links.add(parsed.path)
        except Exception:
            continue

    return sorted(list(links))