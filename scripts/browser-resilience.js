async (page) => {
  // Fault responses are intercepted only in this isolated browser, never on the server.
  const checks = [], errors = [];
  // Respect the public proxy's 10 session requests/minute while exercising reloads.
  if (page.url().startsWith('https:')) await page.route('**/api/projects/agentops/session', async route => {
    await page.waitForTimeout(6500);
    await route.fallback();
  });
  page.on('pageerror', error => errors.push(error.message));
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const done = async () => { await page.locator('#result:not([hidden])').waitFor(); await page.locator('#run:enabled').waitFor(); };
  const run = async () => { await page.locator('#run:enabled').click(); await done(); };
  await page.reload();
  await page.locator('#run:enabled').waitFor();
  await page.locator('#preset').selectOption('economical');
  await run();
  const cookiesBefore = await page.context().cookies();
  const second = await page.context().newPage();
  await second.goto(page.url());
  await second.locator('#run:enabled').waitFor();
  const cookiesAfter = await page.context().cookies();
  assert(JSON.stringify(cookiesBefore) === JSON.stringify(cookiesAfter), 'Second tab rotated session');
  await run();
  await second.locator('#run').click();
  await second.locator('#result:not([hidden])').waitFor();
  assert(await page.locator('#error').isHidden(), 'First tab lost session');
  checks.push({check:'both tabs can run with the same unrotated session',passed:true});
  await second.close();

  await page.route('**/api/projects/agentops/simulations', route => route.fulfill({status:429,headers:{'Retry-After':'7'},contentType:'text/html',body:'<html>Too Many Requests</html>'}),{times:1});
  await page.locator('#run').click();
  await page.locator('#error:not([hidden])').waitFor();
  assert((await page.locator('#error-message').textContent()).includes('wait 7 seconds'), 'HTML 429 lacks friendly retry delay');
  assert(await page.locator('#error').evaluate(el => el === document.activeElement), 'Error was not focused');
  await run();
  checks.push({check:'HTML rate limit is readable, focused, and recoverable',passed:true,injected:true});

  const cancellations = [];
  page.on('request', request => { if (request.url().endsWith('/cancel')) cancellations.push(request.url()); });
  let release, admitted;
  const gate = new Promise(resolve => { release = resolve; });
  const pending = new Promise(resolve => { admitted = resolve; });
  await page.route('**/api/projects/agentops/simulations', async route => {
    const response = await route.fetch(); admitted(); await gate; await route.fulfill({response});
  },{times:1});
  await page.locator('#preset').selectOption('outage');
  await page.locator('#run').click(); await pending;
  try {
    assert(await page.locator('#cancel').isDisabled(), 'Cancel targets previous run during admission');
    assert(await page.locator('#result').isHidden(), 'Previous result appears during new admission');
    assert(await page.locator('#run-status').textContent() === 'SUBMITTING', 'Wrong admission state');
    assert(cancellations.length === 0, 'Unexpected cancellation');
  } finally { release(); }
  await done();
  assert(await page.locator('#result-title').textContent() === 'Selected harbor-eu', 'Wrong result after delayed admission');
  checks.push({check:'delayed admission cannot cancel or display the preceding run',passed:true,injected:true});

  await page.route('**/api/projects/agentops/session', route => route.fulfill({status:503,contentType:'text/html',body:'<html>Unavailable</html>'}),{times:1});
  await page.reload(); await page.locator('#retry-connection:not([hidden])').waitFor();
  assert((await page.locator('#error-message').textContent()).includes('temporarily unavailable'), 'Startup error unreadable');
  assert(await page.locator('#run').isDisabled(), 'Run admitted without session');
  await page.locator('#quality_floor').fill('91');
  await page.locator('#retry-connection').click(); await page.locator('#run:enabled').waitFor();
  assert(await page.locator('#quality_floor').inputValue() === '91', 'Retry erased edited input');
  assert(await page.locator('.provider-row').count() === 4, 'Retry duplicated provider controls');
  checks.push({check:'startup retry recovers without reload or lost input',passed:true,injected:true});

  await page.route('**/api/projects/agentops/simulation/presets', route => route.abort('failed'),{times:1});
  await page.reload(); await page.locator('#retry-connection:not([hidden])').waitFor();
  assert((await page.locator('#error-message').textContent()).includes('Connection lost'), 'Network failure unreadable');
  await page.locator('#retry-connection').click(); await page.locator('#run:enabled').waitFor();
  assert(await page.locator('#preset option').count() === 9, 'Presets did not recover');
  checks.push({check:'initial presets network failure can be retried',passed:true,injected:true});

  // Browser-only removal of this test visitor's cookie exercises expired/missing session recovery.
  await page.context().clearCookies();
  await run();
  assert(await page.locator('#error').isHidden(), 'Session renewal failed');
  checks.push({check:'missing session renews automatically on submission',passed:true});

  await page.addInitScript(() => { window.EventSource = undefined; });
  await page.reload(); await page.locator('#run:enabled').waitFor();
  let starts = 0;
  page.on('request', request => { if (request.method() === 'POST' && request.url().endsWith('/simulations')) starts++; });
  await page.route('**/events?transport=poll*', route => route.fulfill({status:502,contentType:'text/html',body:'<html>Bad gateway</html>'}),{times:1});
  await page.locator('#run').click(); await page.locator('#retry-connection:not([hidden])').waitFor();
  assert(await page.locator('#run-status').textContent() === 'DISCONNECTED', 'Lost stream has wrong status');
  await page.locator('#retry-connection').click(); await done();
  assert(starts === 1, 'Reconnect started a duplicate run');
  assert(await page.locator('#timeline li').count() >= 5, 'Reconnect lost trace');
  checks.push({check:'polling reconnect retrieves the same run and trace without duplicate submission',passed:true,injected:true});

  await page.locator('#advanced').evaluate(el => { el.open = true; });
  for (const checkbox of await page.locator('[name=capability]').all()) await checkbox.uncheck();
  await page.locator('#run').click(); await page.locator('#error:not([hidden])').waitFor();
  assert((await page.locator('#error-message').textContent()).includes('at least one'), 'Missing capability not explained');
  const priorStarts = starts;
  await page.locator('#reset').click();
  await run();
  assert(starts === priorStarts + 1, 'Invalid input was submitted');
  checks.push({check:'empty capabilities rejected locally and reset restores runnable input',passed:true});

  await page.setViewportSize({width:667,height:320});
  await page.locator('#menu-open').click();
  const lastLink = page.locator('#sidebar a').last();
  await lastLink.focus();
  const box = await lastLink.boundingBox();
  assert(box && box.y >= 0 && box.y + box.height <= 320, 'Short mobile drawer link unreachable');
  await page.keyboard.press('Escape');
  assert(await page.locator('#menu-open').evaluate(el => el === document.activeElement), 'Drawer did not return focus');
  checks.push({check:'short landscape drawer scroll and keyboard escape',passed:true});
  await page.setViewportSize({width:390,height:844});
  await page.locator('#try-it').scrollIntoViewIfNeeded();
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Mobile page overflow');
  await page.screenshot({path:'output/playwright/live-e2e/resilience-after.png'});
  assert(errors.length === 0, JSON.stringify(errors));
  return {url:page.url(),checks,page_errors:errors,result:'PASS'};
}
