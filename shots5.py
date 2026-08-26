"""Screenshots for the "playing more than one team" help section.

Two shots, shared by all three How to Play pages and the Help hub:
  switch-accounts.png -- the Hi dropdown open, showing the other teams on the
                         same email plus the Add Another Account item
  add-account.png     -- the Add Another Account form

Run with the frozen clock rolled back before the Week 1 deadline: past it the
Add Another Account item is hidden, because a new account could not join
anything.
"""
import sys
from playwright.sync_api import sync_playwright
sys.argv = ["shots5", "multi"]
exec(open("/root/gp/shots.py").read().split("with sync_playwright()")[0])

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=2)
    page = ctx.new_page()
    for pat in ("https://cdn.jsdelivr.net/**", "https://fonts.googleapis.com/**",
                "https://fonts.gstatic.com/**"):
        page.route(pat, offline_cdn)

    login(page, "Foxglove")
    page.goto(f"{BASE}/"); page.wait_for_load_state("networkidle")
    dismiss_modal(page); page.evaluate(STRIP)

    # The dropdown, open. Clip the top-right corner of the page so the shot is
    # the navbar and the open menu rather than a mostly-empty 1280px strip.
    toggle = page.query_selector('.navbar .dropdown-toggle:has-text("Hi,")') \
             or page.query_selector('#accountDropdown') \
             or page.query_selector('.navbar .nav-item.dropdown .dropdown-toggle')
    print("toggle:", toggle and toggle.inner_text())
    toggle.click()
    page.wait_for_timeout(400)
    menu = page.query_selector('.dropdown-menu.show')
    box = menu.bounding_box()
    page.screenshot(path="/root/gp/shots/M-switch-accounts.png",
                    clip={"x": max(0, box["x"] - 340), "y": 0,
                          "width": min(1280 - max(0, box["x"] - 340), box["width"] + 380),
                          "height": box["y"] + box["height"] + 16})
    print("  ok switch-accounts")

    page.goto(f"{BASE}/auth/add-account"); page.wait_for_load_state("networkidle")
    dismiss_modal(page); page.evaluate(STRIP)
    print("  add-account url:", page.url)
    card = page.query_selector('main .card') or page.query_selector('main') or page.query_selector('.container')
    card.screenshot(path="/root/gp/shots/M-add-account.png")
    print("  ok add-account")

    b.close()
print("done")
