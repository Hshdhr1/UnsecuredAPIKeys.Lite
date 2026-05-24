import asyncio
import logging
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from typing import List, Optional
from .database.models import RepoReference, SearchQuery


class SeleniumScraper:
    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger("SeleniumScraper")

    def get_driver(self):
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        # Add user agent to avoid bot detection
        chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

        service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=chrome_options)

    async def search_github_browser(self, query: str) -> List[dict]:
        """
        Performs a search on GitHub using Selenium and extracts potential file links.
        """
        results = []
        # Run selenium in a thread to not block the event loop
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_search, query)

    def _sync_search(self, query: str, search_type: str = "code") -> List[dict]:
        results = []
        driver = self.get_driver()
        try:
            url = f"https://github.com/search?q={query}&type={search_type}"
            self.logger.info(f"Selenium opening: {url}")
            driver.get(url)

            # Wait for search results to load
            wait = WebDriverWait(driver, 15)
            try:
                if search_type == "repositories":
                    # Repositories search results selector
                    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.repo-list-item, div.Box-row, a.v-align-middle")))
                    items = driver.find_elements(By.CSS_SELECTOR, "div.Box-row")
                    for item in items:
                        try:
                            # In repo search, find repo links
                            link_element = item.find_element(By.CSS_SELECTOR, "a.v-align-middle")
                            repo_url = link_element.get_attribute("href")

                            # For repo search, we might want to crawl common files
                            # For now, let's just add the repo itself as a reference
                            # (ScraperBot will need to handle this)
                            parts = repo_url.split('/')
                            if len(parts) >= 5:
                                owner = parts[3]
                                name = parts[4]
                                results.append({
                                    "repo_url": repo_url,
                                    "file_url": f"{repo_url}/blob/main/README.md",
                                    "api_content_url": f"https://raw.githubusercontent.com/{owner}/{name}/main/README.md",
                                    "repo_owner": owner,
                                    "repo_name": name,
                                    "file_path": "README.md",
                                    "provider": "GitHub"
                                })
                        except Exception: continue
                else:
                    # Default: code search
                    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.code-list-item, div.Box-row")))
                    items = driver.find_elements(By.CSS_SELECTOR, "div.Box-row")
                    for item in items:
                        try:
                            link_element = item.find_element(By.CSS_SELECTOR, "a[title]")
                            file_url = link_element.get_attribute("href")
                            file_path = link_element.get_attribute("title")

                        # Extract repo info from URL
                        # https://github.com/owner/repo/blob/branch/path
                        parts = file_url.split('/')
                        if len(parts) >= 5:
                            owner = parts[3]
                            repo_name = parts[4]

                            # Convert to raw URL for content fetching
                            raw_url = file_url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")

                            results.append({
                                "repo_url": f"https://github.com/{owner}/{repo_name}",
                                "file_url": file_url,
                                "api_content_url": raw_url,
                                "repo_owner": owner,
                                "repo_name": repo_name,
                                "file_path": file_path,
                                "provider": "GitHub"
                            })
                    except Exception as e:
                        self.logger.error(f"Error parsing item: {e}")
                        continue
            except Exception as e:
                self.logger.warning(f"Timeout or error waiting for results: {e}")

        finally:
            driver.quit()
        return results
