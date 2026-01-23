"""Spider.cloud API client for link discovery and scraping."""
import os
import requests
from typing import List, Dict, Optional


SPIDER_API_KEY = os.getenv("SPIDER_API_KEY")
SPIDER_BASE_URL = "https://api.spider.cloud"


def discover_links_spider(url: str, limit: int = 0) -> List[str]:
    """
    Discover links using Spider.cloud Links API.
    
    Args:
        url: Base URL to discover links from
        limit: Maximum number of links to return (0 = unlimited)
        
    Returns:
        List of discovered URLs
        
    Raises:
        Exception: If API call fails
    """
    if not SPIDER_API_KEY:
        raise ValueError("SPIDER_API_KEY not found in environment variables")
    
    endpoint = f"{SPIDER_BASE_URL}/links"
    headers = {
        "Authorization": f"Bearer {SPIDER_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "url": url,
        "limit": limit
    }
    
    try:
        response = requests.post(endpoint, json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        
        data = response.json()
        
        # Spider.cloud returns links in different formats depending on the plan
        # Handle both array and object responses
        if isinstance(data, list):
            links = [item.get('url') for item in data if item.get('url')]
        elif isinstance(data, dict):
            links = data.get('links', [])
            if isinstance(links, list) and len(links) > 0:
                if isinstance(links[0], dict):
                    links = [item.get('url') for item in links if item.get('url')]
        else:
            links = []
        
        return links
        
    except requests.exceptions.Timeout:
        raise Exception("Spider.cloud API timeout")
    except requests.exceptions.RequestException as e:
        raise Exception(f"Spider.cloud API error: {str(e)}")


def scrape_url_spider(url: str, return_format: str = "markdown") -> Dict:
    """
    Scrape a URL using Spider.cloud Scrape API.
    
    Args:
        url: URL to scrape
        return_format: Format to return content in (markdown, html, text)
        
    Returns:
        Dict with 'content', 'title', and 'url'
        
    Raises:
        Exception: If API call fails
    """
    if not SPIDER_API_KEY:
        raise ValueError("SPIDER_API_KEY not found in environment variables")
    
    endpoint = f"{SPIDER_BASE_URL}/scrape"
    headers = {
        "Authorization": f"Bearer {SPIDER_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "url": url,
        "return_format": return_format
    }
    
    try:
        response = requests.post(endpoint, json=payload, headers=headers, timeout=90)
        response.raise_for_status()
        
        data = response.json()
        
        # Extract content from response
        # Spider.cloud returns array with single object
        if isinstance(data, list) and len(data) > 0:
            result = data[0]
        elif isinstance(data, dict):
            result = data
        else:
            raise Exception("Unexpected response format from Spider.cloud")
        
        return {
            'content': result.get('content', ''),
            'title': result.get('title', ''),
            'url': result.get('url', url)
        }
        
    except requests.exceptions.Timeout:
        raise Exception("Spider.cloud scrape timeout")
    except requests.exceptions.RequestException as e:
        raise Exception(f"Spider.cloud scrape error: {str(e)}")


def test_spider_connection() -> bool:
    """
    Test Spider.cloud API connection.
    
    Returns:
        True if connection successful, False otherwise
    """
    if not SPIDER_API_KEY:
        return False
    
    try:
        # Try a simple scrape request
        scrape_url_spider("https://example.com")
        return True
    except Exception:
        return False
