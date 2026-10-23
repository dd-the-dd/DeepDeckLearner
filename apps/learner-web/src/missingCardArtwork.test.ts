import {afterEach, expect, test, vi} from 'vitest';
import {missingArtworkRequests, resolveMissingArtwork} from './missingCardArtwork';
afterEach(() => vi.unstubAllGlobals());

test('only requests public missing artwork, never masked hands, facedown permanents or library backs', () => {
  const visible = {name:'Eldrazi Spawn Token', raw:{definitionId:'token:spawn', isToken:true, power:'0', toughness:'1'}};
  expect(missingArtworkRequests({players:[{
    battlefield:[visible,{name:'Ponder',imageUrl:'selected-printing.jpg'},{name:'Secret morph',faceDown:true}],
    hand:[{name:'Hidden card',faceDown:true,raw:{name:'Private answer'}},{name:'Unknown card'}],
    zones:[{cards:[{name:'Library',faceDown:true}]}],
  }],stack:[{card:visible}]})).toEqual([{key:'token:spawn', name:'Eldrazi Spawn Token', token:true, power:'0', toughness:'1'}]);
});

test('known unusual cards get stable name keys rather than a request per instance or frame', () => {
  expect(missingArtworkRequests({players:[{hand:[{name:'Life // Death',id:'instance-1'},{name:'Life // Death',id:'instance-2'}]}]})).toHaveLength(1);
});

test('missing artwork requests are deduplicated across frames and failures never crash the viewer', async () => {
  const fetch = vi.fn(async () => ({ok:true,json:async()=>({artwork:{imageUrl:'token.jpg'}})}));
  vi.stubGlobal('fetch',fetch);
  const request = {key:'token:cached',name:'Cache test Token',token:true,power:'1',toughness:'1'};
  const first = resolveMissingArtwork(request);
  expect(resolveMissingArtwork({...request,key:'another-instance'})).toBe(first);
  expect(await first).toEqual({imageUrl:'token.jpg'});
  expect(fetch).toHaveBeenCalledOnce();
  fetch.mockRejectedValueOnce(new Error('offline'));
  expect(await resolveMissingArtwork({...request,name:'Offline token'})).toBeNull();
});
