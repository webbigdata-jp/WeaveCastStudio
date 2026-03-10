from DrissionPage import ChromiumPage, ChromiumOptions
import time

co = ChromiumOptions()
co.headless()
co.set_argument('--no-sandbox')
co.set_argument('--disable-dev-shm-usage')

page = ChromiumPage(co)
page.get('https://news.un.org/en/')
time.sleep(3)

print(f"page.url   = {page.url}")
print(f"page.title = {page.title}")
print(f"HTML先頭300文字:")
print(page.html[:300])
page.quit()

