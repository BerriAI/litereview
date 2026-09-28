"""Opt-in live UI acceptance test; requires a running app and .env."""

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
        await page.get_by_role("heading", name="PR agents", exact=True).wait_for()
        # Recover only this test's named resources after an interrupted earlier pass.
        for kind, names in [
            ("groups", {"ui-acceptance-one", "ui-acceptance-two"}),
            ("agents", {"UI acceptance reviewer"}),
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
        await dialog.get_by_label("Name", exact=True).fill("UI acceptance reviewer")
        await dialog.get_by_label("Model connection").select_option(label="Sandbox gateway")
        await dialog.get_by_label("Model", exact=True).fill("claude-haiku-4-5-20251001")
        await dialog.get_by_text("See and operate the desktop").click()
        await dialog.get_by_role("checkbox", name=re.compile("Evidence-based review")).check()
        await page.screenshot(path=".test-artifacts/agent-editor.png")
        await dialog.get_by_role("button", name="Save agent", exact=True).click()
        await dialog.wait_for(state="hidden")
        for name, count in [("ui-acceptance-one", 2), ("ui-acceptance-two", 1)]:
            await page.get_by_role("button", name="Agent groups", exact=True).click()
            await page.get_by_role("button", name="Create group", exact=True).click()
            dialog = page.get_by_role("dialog")
            await dialog.get_by_label("Command name").fill(name)
            await dialog.get_by_label("Add agent to group").select_option(label="UI acceptance reviewer")
            await dialog.get_by_label("Instances").fill(str(count))
            await dialog.get_by_text("BerriAI/litereview", exact=True).click()
            await dialog.get_by_role("button", name="Save group", exact=True).click()
            await dialog.wait_for(state="hidden")
        await page.screenshot(path=".test-artifacts/groups.png")
        await page.get_by_role("button", name="PR agents", exact=True).click()
        await page.get_by_role("button", name="UI acceptance reviewer", exact=True).click()
        dialog = page.get_by_role("dialog")
        assert await dialog.get_by_text("Changes apply to future runs in:").count() == 1
        await dialog.get_by_label("Review instructions").fill(
            "Inspect screenshots and report only reproducible UI defects."
        )
        await dialog.get_by_role("button", name="Save agent", exact=True).click()
        await dialog.wait_for(state="hidden")
        await page.screenshot(path=".test-artifacts/agents.png")
        for width in [768, 390, 320]:
            await page.set_viewport_size({"width": width, "height": 900})
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
                f"overflow at {width}"
            )
            if width == 390:
                await page.screenshot(path=".test-artifacts/mobile.png")
        await page.set_viewport_size({"width": 1440, "height": 1000})
        await page.emulate_media(color_scheme="dark")
        await page.screenshot(path=".test-artifacts/dark.png")
        await page.emulate_media(color_scheme="light")
        assert not errors, errors
        # Delete only resources created by this test, through authenticated API calls.
        for kind, names in [
            ("groups", {"ui-acceptance-one", "ui-acceptance-two"}),
            ("agents", {"UI acceptance reviewer"}),
        ]:
            rows = await (await page.request.get(os.environ["PUBLIC_URL"] + "/api/resources/" + kind)).json()
            for row in rows:
                if row["name"] in names:
                    result = await page.request.delete(
                        os.environ["PUBLIC_URL"] + "/api/resources/" + kind + "/" + row["id"]
                    )
                    assert result.ok
        print(
            "UI PASS: create/edit/reuse agents, instance counts, skills, desktop option, responsive widths, and no JavaScript errors."
        )
        await browser.close()


asyncio.run(main())
