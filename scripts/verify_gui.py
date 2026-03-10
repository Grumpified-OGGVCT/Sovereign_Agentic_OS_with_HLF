import time

from playwright.sync_api import expect, sync_playwright


def test_gui_changes():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        # Wait for Streamlit to start
        print("Waiting for Streamlit server...")
        time.sleep(5)

        try:
            page.goto("http://localhost:8501")

            # Wait for the app to load
            print("Waiting for app to load...")
            page.wait_for_selector(".stApp", timeout=15000)
            time.sleep(2)  # Give it a bit more time to render everything

            # 1. Verify Empty Chat State
            print("Checking empty chat state...")
            # Switch to Chat tab
            chat_tab = page.get_by_role("tab", name="Agent Chat")
            chat_tab.click()
            time.sleep(1)

            # Look for the empty state message
            welcome_msg = page.get_by_text("Welcome to the Agent Chat!")
            expect(welcome_msg).to_be_visible()

            # Take screenshot of chat tab
            page.screenshot(path="assets/chat_empty_state.png")

            # 2. Verify Intent Dispatch Button State
            print("Checking dispatch button state...")
            # Switch to Dispatch tab
            dispatch_tab = page.get_by_role("tab", name="Intent Dispatch")
            dispatch_tab.click()
            time.sleep(1)

            # Look for the Dispatch button and check if it's disabled
            dispatch_btn = page.get_by_role("button", name="Dispatch")
            expect(dispatch_btn).to_be_disabled()

            # Take screenshot of dispatch tab
            page.screenshot(path="assets/dispatch_disabled_btn.png")

            print("Verification successful!")

        except Exception as e:
            print(f"Error during verification: {e}")
            page.screenshot(path="error_state.png")
            raise e
        finally:
            browser.close()


if __name__ == "__main__":
    test_gui_changes()
