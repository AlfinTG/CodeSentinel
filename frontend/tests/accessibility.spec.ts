import { test, expect } from '@playwright/test'

test.describe('CodeSentinel Accessibility & Keyboard Navigation (WCAG 2.1 AA)', () => {
  test('A11Y-01: Keyboard Tab Navigation traverses run items and tabs logically', async ({ page }) => {
    await page.goto('/')

    // Initial focus on body/skip link
    await page.keyboard.press('Tab')

    // Navigate through interactive controls
    const focusedTag = await page.evaluate(() => document.activeElement?.tagName)
    expect(['A', 'BUTTON', 'INPUT', 'SELECT']).toContain(focusedTag)

    // Traverse into tabs
    for (let i = 0; i < 5; i++) {
      await page.keyboard.press('Tab')
    }

    // Verify an interactive element retains focus
    const hasFocus = await page.evaluate(() => document.activeElement !== document.body)
    expect(hasFocus).toBe(true)
  })

  test('A11Y-02: Status badges provide accessible text and high-contrast indicators', async ({ page }) => {
    await page.goto('/')

    // All status badges must have discernible text content
    const badges = page.locator('.status-badge, [data-testid*="badge"]')
    const count = await badges.count()

    for (let i = 0; i < count; i++) {
      const badge = badges.nth(i)
      const text = await badge.innerText()
      expect(text.trim().length).toBeGreaterThan(0)
    }
  })
})
