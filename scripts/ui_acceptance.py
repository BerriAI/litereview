"""Opt-in legacy local UI test; requires seeded fixtures and SSO disabled.

Google SSO/onboarding acceptance is documented in docs/VALIDATION.md.
"""

import asyncio
import os
import re

from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv(".env")


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(os.environ["PUBLIC_URL"])
        await page.get_by_label("Workspace access key").fill(os.environ["ADMIN_TOKEN"])
        await page.get_by_role("button", name="Sign in", exact=True).click()
        await page.goto(os.environ["PUBLIC_URL"] + "/agents")
        await page.get_by_role("heading", name="PR agents", exact=True).wait_for()
        # Recover only this test's named resources after an interrupted earlier pass.
        for kind, names in [
            ("groups", {"ui-acceptance-one", "ui-acceptance-two"}),
            ("agents", {"ui-acceptance-reviewer"}),
        ]:
            rows = await (await page.request.get(os.environ["PUBLIC_URL"] + "/api/resources/" + kind)).json()
            for row in rows:
                if row["name"] in names:
                    response = await page.request.delete(
                        os.environ["PUBLIC_URL"] + "/api/resources/" + kind + "/" + row["id"]
                    )
                    assert response.ok
        await page.reload()
        await page.get_by_role("heading", name="PR agents", exact=True).wait_for()
        await page.get_by_role("button", name="Create agent", exact=True).click()
        dialog = page.get_by_role("dialog")
        await dialog.get_by_label("Command name", exact=True).fill("ui-acceptance-reviewer")
        await dialog.get_by_label("Model connection", exact=True).click()
        await dialog.get_by_role("combobox", name="Model connection", exact=True).fill("Sandbox")
        await dialog.get_by_role("option", name="Sandbox gateway", exact=True).click()
        await dialog.get_by_label("Model", exact=True).click()
        await dialog.get_by_role("combobox", name="Model", exact=True).fill("claude-haiku-4-5-20251001")
        await dialog.get_by_role("combobox", name="Model", exact=True).press("Enter")
        await dialog.get_by_text("See and operate the desktop").click()
        await dialog.get_by_role("checkbox", name=re.compile("Evidence-based review")).check()
        await page.screenshot(path=".test-artifacts/agent-editor.png")
        await dialog.get_by_role("button", name="Save agent", exact=True).click()
        await dialog.wait_for(state="hidden")
        for name, count in [("ui-acceptance-one", 2), ("ui-acceptance-two", 1)]:
            await page.get_by_role("link", name="Agent groups", exact=True).click()
            await page.get_by_role("button", name="Create group", exact=True).click()
            dialog = page.get_by_role("dialog")
            await dialog.get_by_label("Command name").fill(name)
            await dialog.get_by_label("Add agent to group").select_option(label="ui-acceptance-reviewer")
            await dialog.get_by_label("Instances").fill(str(count))
            await dialog.get_by_role("button", name="Save group", exact=True).click()
            await dialog.wait_for(state="hidden")
        await page.screenshot(path=".test-artifacts/groups.png")
        await page.get_by_role("link", name="PR agents", exact=True).click()
        await page.get_by_role("button", name="ui-acceptance-reviewer", exact=True).click()
        dialog = page.get_by_role("dialog")
        assert await dialog.get_by_text("Changes apply to future runs in:").count() == 1
        await dialog.get_by_label("Review instructions").fill(
            "Inspect screenshots and report only reproducible UI defects."
        )
        await dialog.get_by_role("button", name="Save agent", exact=True).click()
        await dialog.wait_for(state="hidden")
        await page.screenshot(path=".test-artifacts/agents.png")
        # Capability filters and group memberships must preserve reusable identities.
        await page.get_by_role("button", name="Computer use", exact=True).click()
        assert await page.get_by_role("button", name="ui-acceptance-reviewer", exact=True).count() == 1
        await page.get_by_role("button", name="Code only", exact=True).click()
        assert await page.get_by_role("button", name="ui-acceptance-reviewer", exact=True).count() == 0
        await page.get_by_role("button", name=re.compile("^All agents")).click()
        await page.get_by_role("link", name="Agent groups", exact=True).click()
        await page.get_by_role("button", name="ui-acceptance-one", exact=True).click()
        dialog = page.get_by_role("dialog")
        assert await dialog.get_by_label("Instances").input_value() == "2"
        assert await dialog.get_by_text(re.compile("Unique across agents and groups.*@litereview ui-acceptance-one")).count() == 1
        await page.keyboard.press("Escape")
        await dialog.wait_for(state="hidden")
        for width in [768, 390, 320]:
            await page.set_viewport_size({"width": width, "height": 900})
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
                f"overflow at {width}"
            )
            if width == 390:
                await page.screenshot(path=".test-artifacts/mobile.png")
        await page.get_by_role("button", name="Open navigation").click()
        assert await page.get_by_role("button", name="Close navigation").evaluate(
            "el => el === document.activeElement"
        )
        await page.keyboard.press("Escape")
        assert (
            await page.get_by_role("button", name="Open navigation").get_attribute("aria-expanded") == "false"
        )
        await page.get_by_role("button", name="Open navigation").click()
        await page.get_by_role("link", name="Skills", exact=True).click()
        await page.get_by_role("heading", name="Skills", exact=True).wait_for()
        await page.get_by_role("button", name="Create skill", exact=True).click()
        dialog = page.get_by_role("dialog")
        assert await dialog.evaluate("el => el.scrollWidth <= el.clientWidth"), "mobile editor overflow"
        await dialog.get_by_role("button", name="Cancel", exact=True).click()
        await page.set_viewport_size({"width": 1440, "height": 1000})
        await page.get_by_role("link", name="PR agents", exact=True).click()
        await page.emulate_media(color_scheme="dark")
        assert await page.evaluate("getComputedStyle(document.documentElement).colorScheme") == "dark"
        assert (
            await page.evaluate("getComputedStyle(document.documentElement).backgroundColor")
            == "rgb(22, 23, 27)"
        )
        await page.screenshot(path=".test-artifacts/system-dark-ui.png")
        await page.get_by_role("button", name="System theme. Change theme", exact=True).click()
        assert await page.evaluate("document.documentElement.dataset.theme") == "light"
        assert await page.evaluate("getComputedStyle(document.documentElement).colorScheme") == "light"
        await page.reload()
        await page.get_by_role("heading", name="PR agents", exact=True).wait_for()
        assert await page.evaluate("document.documentElement.dataset.theme") == "light"
        await page.get_by_role("button", name="Light theme. Change theme", exact=True).click()
        await page.get_by_role("button", name="Dark theme. Change theme", exact=True).click()
        assert await page.evaluate("localStorage.getItem('litereview-theme')") is None
        await page.emulate_media(color_scheme="light")
        # Inspect every data surface and editor without launching models or jobs.
        for title, action in [("Skills", "Create skill")]:
            await page.get_by_role("button", name=title, exact=True).click()
            await page.get_by_role("heading", name=title, exact=True).wait_for()
            await page.get_by_role("button", name=action, exact=True).click()
            dialog = page.get_by_role("dialog")
            await dialog.get_by_role("button", name="Cancel", exact=True).click()
        await page.get_by_role("link", name="Settings", exact=True).click()
        for tab in ["Models", "MCP servers"]:
            await page.get_by_role("button", name=tab, exact=True).click()
            await (
                page.get_by_role("button", name="Add connection", exact=True).click()
            )
            dialog = page.get_by_role("dialog")
            await dialog.get_by_role("button", name="Cancel", exact=True).click()
        await page.get_by_role("link", name="Runs", exact=True).click()
        await page.get_by_role("heading", name="Runs", exact=True).wait_for()
        await page.get_by_role("button", name="Open run", exact=True).first.click()
        await page.get_by_role("heading", name="Review results", exact=True).wait_for()
        await page.locator(".job").first.wait_for()
        assert await page.locator(".summary-caller").inner_text(), "Missing run attribution"
        await page.screenshot(path=".test-artifacts/redesign-run-results.png")
        assert not errors, errors
        # Delete only resources created by this test, through authenticated API calls.
        for kind, names in [
            ("groups", {"ui-acceptance-one", "ui-acceptance-two"}),
            ("agents", {"ui-acceptance-reviewer"}),
        ]:
            rows = await (await page.request.get(os.environ["PUBLIC_URL"] + "/api/resources/" + kind)).json()
            for row in rows:
                if row["name"] in names:
                    result = await page.request.delete(
                        os.environ["PUBLIC_URL"] + "/api/resources/" + kind + "/" + row["id"]
                    )
                    assert result.ok
        print(
            "UI PASS: reusable agents/groups, capability filters, saved instance counts, skills, all editors, mobile navigation/keyboard, responsive widths, run results, and no JavaScript errors."
        )
        await browser.close()


asyncio.run(main())
