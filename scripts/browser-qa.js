async (page) => {
  const checks = [];
  const errors = [], failedRequests = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('requestfailed', request => { if (!request.url().includes('/events')) failedRequests.push(request.url().split('?')[0]); });
  const scenarios = [
    ['outage', 'Selected harbor-eu'], ['rate-limit', 'Selected cedar-eu'],
    ['region', 'POLICY BLOCKED'], ['budget', 'BUDGET EXHAUSTED'],
    ['malformed', 'Selected harbor-eu'], ['all-down', 'AI UNAVAILABLE'],
    ['agentops-down', 'AI UNAVAILABLE'], ['expired', 'POLICY BLOCKED'], ['economical', 'Selected cedar-eu']
  ];
  for (const [preset, expected] of scenarios) {
    await page.getByRole('combobox', {name:'Scenario', exact:true}).selectOption(preset);
    await page.getByRole('button', {name:'Run simulation', exact:false}).click();
    await page.getByRole('heading', {name:expected, exact:true}).waitFor({state:'visible'});
    await page.getByRole('button', {name:'Run simulation', exact:false}).waitFor({state:'visible'});
    checks.push({scenario:preset, expected, passed:true});
  }
  await page.getByRole('button', {name:'Eligibility checked SUCCEEDED'}).click();
  await page.getByRole('button', {name:'Technical', exact:true}).click();
  const technical = await page.locator('#step-json').textContent();
  if (!technical.includes('REGION_FORBIDDEN')) throw new Error('Technical filter evidence missing');
  await page.getByRole('button', {name:'Evidence', exact:true}).click();
  if (!(await page.locator('#step-json').textContent()).includes('trace_id')) throw new Error('Trace evidence missing');
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', {name:'Download run JSON ↓'}).click();
  const download = await downloadPromise;
  if (download.suggestedFilename() !== 'agentops-synthetic-run.json') throw new Error('Export filename mismatch');
  checks.push({check:'technical inspector, evidence, JSON export and comparison', passed:await page.getByRole('heading',{name:'Compare the last two runs'}).isVisible()});
  await page.getByRole('combobox', {name:'Scenario', exact:true}).selectOption('outage');
  await page.getByRole('button', {name:'Run simulation', exact:false}).click();
  await page.getByRole('heading', {name:'Selected harbor-eu', exact:true}).waitFor({state:'visible'});
  for (const width of [320,375,390,430,768,1024,1280,1440,1920]) {
    await page.setViewportSize({width, height:1000});
    await page.locator('#try-it').scrollIntoViewIfNeeded();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    if (overflow) throw new Error('Page overflow at '+width);
    await page.screenshot({path:`docs/evidence/simulator-${width}.png`, fullPage:false});
    checks.push({viewport:width, horizontal_overflow:false, passed:true});
  }
  await page.setViewportSize({width:390,height:844});
  await page.evaluate(() => window.scrollTo(0,0));
  await page.getByRole('button',{name:'Menu',exact:false}).click();
  await page.getByRole('button',{name:'Close navigation'}).waitFor({state:'visible'});
  await page.keyboard.press('Tab');
  const trapped = await page.evaluate(() => document.getElementById('sidebar').contains(document.activeElement));
  await page.keyboard.press('Escape');
  const returned = await page.getByRole('button',{name:'Menu',exact:false}).evaluate(el => el === document.activeElement);
  if (!trapped || !returned) throw new Error('Drawer keyboard focus failed');
  checks.push({check:'mobile drawer focus, Escape, return',passed:true});
  await page.screenshot({path:'docs/evidence/mobile-hero.png'});
  await page.setViewportSize({width:1440,height:1000});
  await page.evaluate(() => window.scrollTo(0,0));
  await page.screenshot({path:'docs/evidence/desktop-full.png',fullPage:true});
  if (errors.length || failedRequests.length) throw new Error(JSON.stringify({errors,failedRequests}));
  return {checks, page_errors:errors, failed_requests:failedRequests, browser:'cached Chromium 151', result:'PASS'};
}
