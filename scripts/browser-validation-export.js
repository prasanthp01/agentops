async (page) => {
 const errors=[]; page.on('pageerror', e=>errors.push(e.message));
 await page.locator('#advanced').evaluate(e=>e.open=true);
 await page.locator('#input_tokens').fill('0');
 await page.locator('#advanced').evaluate(e=>e.open=false);
 await page.locator('#run').click();
 const validation=await page.locator('#input_tokens').evaluate(e=>({valid:e.validity.valid,focused:document.activeElement===e,details_open:e.closest('details').open}));
 if(validation.valid||!validation.focused||!validation.details_open) throw new Error(JSON.stringify(validation));
 await page.locator('#reset').click();
 await page.locator('#run').click(); await page.locator('#result:not([hidden])').waitFor(); await page.locator('#run:enabled').waitFor();
 const downloadPromise=page.waitForEvent('download'); await page.locator('#export').click(); const download=await downloadPromise;
 const stream=await download.createReadStream(); stream.setEncoding('utf8'); let text=''; for await (const chunk of stream) text+=chunk;
 const data=JSON.parse(text);
 if(data.result.status!=='SUCCEEDED'||!data.events.length||data.events.some(e=>e.run_id!==data.result.run_id)) throw new Error('Export content inconsistent');
 const links=await page.locator('a[href^="#"]').evaluateAll(elements=>elements.map(e=>({href:e.getAttribute('href'),exists:!!document.querySelector(e.getAttribute('href'))})));
 if(links.some(l=>!l.exists)) throw new Error('Broken anchor');
 if(errors.length) throw new Error(JSON.stringify(errors));
 return {hidden_input_native_validation:validation,export_parsed:true,export_event_count:data.events.length,internal_anchors_valid:true,page_errors:errors,result:'PASS'};
}
