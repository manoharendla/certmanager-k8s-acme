import json
import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

def get_jobs_from_portal(companies_file):
    # Load the JSON file
    with open(companies_file, 'r') as f:
        companies = json.load(f)

    # Setup Chrome options (Headless mode for running without UI)
    options = webdriver.ChromeOptions()
    options.add_argument('--headless')  # Comment this out to see the browser open
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    
    extracted_data = []

    try:
        for company in companies:
            print(f"Scraping {company['company_name']}...")
            url = company['portal_url']
            driver.get(url)

            # --- Wells Fargo Specific Logic ---
            # Wait for the job list to load (looking for the specific job item class used by their portal)
            try:
                # The 'li.job-result' or '.jobs-list-item' is common for this platform
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "li.job-result, .jobs-list-item, .job-inner"))
                )
            except Exception as e:
                print(f"Timeout waiting for jobs to load for {company['company_name']}")
                continue

            # Identify the container for job cards
            # Wells Fargo typically uses 'li' elements with class 'job-result' or similar
            job_cards = driver.find_elements(By.CSS_SELECTOR, "li.job-result, .jobs-list-item")

            for card in job_cards:
                try:
                    # Extract Title
                    title_elem = card.find_element(By.CSS_SELECTOR, "h3, .job-title, a.job-title-link")
                    title = title_elem.text.strip()
                    
                    # Extract Link
                    # Sometimes the link is on the title element or a parent 'a' tag
                    try:
                        link = title_elem.get_attribute("href")
                        if not link: # If title element isn't the link, check parent or child
                            link = card.find_element(By.TAG_NAME, "a").get_attribute("href")
                    except:
                        link = url # Fallback

                    # Extract Location
                    try:
                        location_elem = card.find_element(By.CSS_SELECTOR, ".job-location, .location")
                        location = location_elem.text.strip()
                    except:
                        location = "Location not found"

                    job_info = {
                        "company": company['company_name'],
                        "title": title,
                        "location": location,
                        "apply_link": link
                    }
                    extracted_data.append(job_info)
                    print(f"Found: {title} ({location})")

                except Exception as e:
                    continue
            
    finally:
        driver.quit()

    return extracted_data

if __name__ == "__main__":
    results = get_jobs_from_portal('companies.json')
    
    # Save results to a file
    with open('job_results.json', 'w') as f:
        json.dump(results, f, indent=4)
    
    print(f"Scraping complete. Found {len(results)} jobs. Saved to job_results.json")
