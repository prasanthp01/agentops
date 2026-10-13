async (page) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/projects/agentops', async route => {
    const response = await route.fetch();
    const body = (await response.text()).replace(/<div id="error"[^>]*>.*?<\/div>/,
      '<div id="error" class="error" role="alert" hidden></div>');
    await route.fulfill({response,body});
  },{times:1});
  await page.reload();
  await page.locator('#run-status').filter({hasText:'READY'}).waitFor();
  if (await page.locator('#retry-connection').count() !== 1) throw new Error('Older markup did not gain recovery controls');
  await page.locator('#run').click();
  await page.locator('#result:not([hidden])').waitFor();
  await page.locator('#run:enabled').waitFor();
  if (errors.length) throw new Error(JSON.stringify(errors));
  await page.reload();
  await page.locator('#run-status').filter({hasText:'READY'}).waitFor();
  const response = await page.request.get(page.url());
  if (response.headers()['cache-control'] !== 'no-cache') throw new Error('HTML not revalidated');
  if (!(await page.locator('script[src]').getAttribute('src')).includes('?v=0.1.2')) throw new Error('Assets not versioned');
  return {legacy_alert_markup_compatible:true,simulation_completed:true,html_revalidation:true,versioned_assets:true,page_errors:errors,result:'PASS'};
}
