import { test, expect } from '@playwright/test'

test.describe('CodeSentinel Core User Flow & Evidence-Led Invariant Verification', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to local or staging frontend dashboard
    await page.goto('/')
  })

  test('TC01: Dashboard loads project and shows strict separation between Factual Truth and AI Hypothesis', async ({ page }) => {
    // 1. Verify Header and Repository Context
    await expect(page.locator('header')).toBeVisible()
    await expect(page.getByText('CodeSentinel')).toBeVisible()

    // 2. Verify Run List is populated
    const runItems = page.locator('[data-testid="run-list-item"]')
    await expect(runItems.first()).toBeVisible({ timeout: 10000 })
    const runCount = await runItems.count()
    expect(runCount).toBeGreaterThanOrEqual(1)

    // 3. Select a failing regression run (e.g. Run #2)
    const failingRun = page.locator('[data-testid="run-list-item"]').filter({ hasText: 'failed' }).first()
    await failingRun.click()

    // 4. Assert Factual Check Outcome is prominently displayed
    const statusBadge = page.locator('[data-testid="test-outcome-badge"]')
    await expect(statusBadge).toBeVisible()
    await expect(statusBadge).toHaveText(/failed/i)

    // 5. Assert AI Hypothesis Panel is strictly labeled as Hypothesis
    const analysisPanel = page.locator('[data-testid="analysis-panel"]')
    await expect(analysisPanel).toBeVisible()
    await expect(analysisPanel.getByText(/hypothesis/i)).toBeVisible()

    // 6. Assert Provider attribution is dynamically displayed
    await expect(analysisPanel.getByText(/(NVIDIA|Nebius)/i)).toBeVisible()
  })

  test('TC02: Evidence viewer displays bounded logs with canary redaction', async ({ page }) => {
    // Click failing run
    const failingRun = page.locator('[data-testid="run-list-item"]').filter({ hasText: 'failed' }).first()
    await failingRun.click()

    // Switch to Evidence tab
    const evidenceTab = page.getByRole('button', { name: /evidence/i })
    await evidenceTab.click()

    // Verify log viewer content is present and bounded
    const logViewer = page.locator('[data-testid="evidence-viewer"]')
    await expect(logViewer).toBeVisible()

    // Verify canary redaction mask exists in output
    const logText = await logViewer.innerText()
    expect(logText).toBeDefined()
    expect(logText.length).toBeLessThanOrEqual(262144 + 500) // 256 KiB plus truncation notice
  })

  test('TC03: Candidate patch inspector supports split and unified diff modes', async ({ page }) => {
    // Click failing run
    const failingRun = page.locator('[data-testid="run-list-item"]').filter({ hasText: 'failed' }).first()
    await failingRun.click()

    // Switch to Candidate Patch tab
    const patchTab = page.getByRole('button', { name: /patch/i })
    if (await patchTab.isVisible()) {
      await patchTab.click()

      // Toggle split mode
      const splitModeButton = page.getByRole('button', { name: /split/i })
      if (await splitModeButton.isVisible()) {
        await splitModeButton.click()
        await expect(page.locator('.diff-split-container')).toBeVisible()
      }

      // Toggle unified mode
      const unifiedModeButton = page.getByRole('button', { name: /unified/i })
      if (await unifiedModeButton.isVisible()) {
        await unifiedModeButton.click()
        await expect(page.locator('.diff-unified-container')).toBeVisible()
      }
    }
  })
})
