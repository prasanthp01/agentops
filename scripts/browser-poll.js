async (page) => {
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.addInitScript(() => { window.EventSource = undefined; });
  await page.reload();
  await page.getByRole('combobox',{name:'Scenario',exact:true}).selectOption('economical');
  await page.getByRole('spinbutton',{name:'Quality floor / 100 · synthetic'}).fill('90');
  await page.getByRole('button',{name:'Run simulation',exact:false}).click();
  await page.getByRole('heading',{name:'Selected harbor-eu',exact:true}).waitFor({state:'visible'});
  const list=await page.locator('#timeline li').count();
  if (list<5) throw new Error('Polling did not deliver trace');
  await page.setViewportSize({width:1440,height:1000});
  await page.locator('#try-it').evaluate(el=>el.scrollIntoView({block:'start',behavior:'instant'}));
  await page.screenshot({path:'docs/evidence/simulator-desktop.png'});
  await page.setViewportSize({width:320,height:900});
  await page.locator('#try-it').evaluate(el=>el.scrollIntoView({block:'start',behavior:'instant'}));
  if (await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth)) throw new Error('Narrow edited run overflow');
  await page.screenshot({path:'docs/evidence/simulator-mobile.png'});
  return {polling_fallback:true,meaningful_quality_edit:true,selected_model:'harbor-eu',timeline_events:list,reduced_motion:true,viewport_320_overflow:false,result:'PASS'};
}
