// 测试 harness v2：用 new Function 包装，每次独立作用域
const fs = require('fs');

let script = fs.readFileSync('/tmp/jd-remove-ads-v2/JD_remove_ads_v2.js', 'utf8');
// 脚本里有 'use strict' 吗？没有。new Function 体内 $request/$response/$done 是参数
function run(url, body) {
  let result = null;
  const done = (o) => { result = o || {}; };
  try {
    new Function('$request', '$response', '$done', script)({ url }, { body }, done);
  } catch (e) {
    return { __error: String(e) };
  }
  return result;
}

function b64(s) { return Buffer.from(s, 'utf8').toString('base64'); }
const BASE = 'https://api.m.jd.com/client.action?';

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log('  ✅', name); }
  else { fail++; console.log('  ❌', name, '—', detail || ''); }
}

console.log('== 用例 1: HAR 真实 basicConfig (800KB base64，应原样放行) ==');
const bc = fs.readFileSync('/tmp/basicConfig.json', 'utf8').trim();
const r1 = run(BASE, bc);
check('basicConfig 未改写', r1.body === undefined || r1.body === bc, 'body: ' + (r1.__error ? r1.__error : 'len=' + String((r1.body || '').length)));

console.log('\n== 用例 2: HAR 真实 startup (解码后仅 deviceLevel，应原样放行) ==');
const startup = fs.readFileSync('/tmp/resp_42.json', 'utf8');
const r2 = run(BASE, b64(startup));
check('startup 未改写', r2.body === undefined || r2.body === b64(startup), r2.__error || String((r2.body || '')).slice(0, 100));

console.log('\n== 用例 3: 开屏广告 (start, base64 新协议，images 应清空) ==');
const splash = { code: 0, data: { images: [{ url: 'https://ad.jd.com/a.jpg' }], showTimesDaily: 3, deviceLevel: 'L1' } };
const r3 = run(BASE, b64(JSON.stringify(splash)));
const d3 = JSON.parse(Buffer.from(r3.body, 'base64').toString('utf8'));
check('images 清空', Array.isArray(d3.data.images) && d3.data.images.length === 0, JSON.stringify(d3.data.images));
check('showTimesDaily=0', d3.data.showTimesDaily === 0, String(d3.data.showTimesDaily));
check('其他字段保留', d3.data.deviceLevel === 'L1', JSON.stringify(d3.data));

console.log('\n== 用例 4: 首页 welcomeHome (明文老协议, floorList 过滤) ==');
const home = { floorList: [{ type: 'recommend' }, { type: 'bottomXview' }, { type: 'float' }, { type: 'searchIcon' }], webViewFloorList: [1, 2], topBgImgBig: 'x' };
const r4 = run(BASE + 'functionId=welcomeHome', JSON.stringify(home));
const d4 = JSON.parse(r4.body);
check('bottomXview 删除', !d4.floorList.some(i => i.type === 'bottomXview'), JSON.stringify(d4.floorList.map(i => i.type)));
check('recommend 保留', d4.floorList.some(i => i.type === 'recommend'));
check('webViewFloorList 清空', d4.webViewFloorList.length === 0);
check('响应保持明文(非base64)', r4.body.startsWith('{'));

console.log('\n== 用例 5: 我的页 personinfoBusiness (floors 过滤+弹窗清理) ==');
const myPage = {
  code: 0, data: {
    floors: [
      { mId: 'userinfo', data: { newPlusBlackCard: { t: 1 }, name: 'zbs' } },
      { mId: 'basefloorinfo', data: { commonPopup: { t: 1 }, commonTips: [1], floatLayer: { t: 1 }, keep: 1 } },
      { mId: 'noticeFloor', data: {} },
      { mId: 'orderIdFloor', data: { commentRemindInfo: { infos: [1, 2] } } },
      { mId: 'orderIdFloor2', data: { list: [1] } },
      { mId: 'iconToolFloor', data: { nodes: [[{ functionId: 'xianzhiguanjia' }, { functionId: 'bad1' }, { functionId: 'kehufuwu' }], [{ functionId: 'bad2' }]] } }
    ]
  }
};
const r5 = run(BASE, b64(JSON.stringify(myPage)));
const d5 = JSON.parse(Buffer.from(r5.body, 'base64').toString('utf8'));
const floors5 = d5.data.floors;
check('noticeFloor 删除', !floors5.some(f => f.mId === 'noticeFloor'), JSON.stringify(floors5.map(f => f.mId)));
check('plus 卡片删除', !floors5.find(f => f.mId === 'userinfo').data.newPlusBlackCard);
check('弹窗删除', !floors5.find(f => f.mId === 'basefloorinfo').data.commonPopup);
check('floatLayer 删除', !floors5.find(f => f.mId === 'basefloorinfo').data.floatLayer);
check('保留字段未误删', floors5.find(f => f.mId === 'basefloorinfo').data.keep === 1);
check('评论提醒清空', floors5.find(f => f.mId === 'orderIdFloor').data.commentRemindInfo.infos.length === 0);
check('工具栏过滤排序', JSON.stringify(floors5.find(f => f.mId === 'iconToolFloor').data.nodes[0].map(i => i.functionId)) === JSON.stringify(['kehufuwu', 'xianzhiguanjia']));
check('响应保持 base64', !r5.body.startsWith('{'));

console.log('\n== 用例 6: 物流页 deliverLayer (bannerInfo 删除) ==');
const dl = { code: 0, data: { bannerInfo: { t: 1 }, floors: [{ mId: 'banner' }, { mId: 'jdDeliveryBanner' }, { mId: 'normal' }] } };
const r6 = run(BASE, b64(JSON.stringify(dl)));
const d6 = JSON.parse(Buffer.from(r6.body, 'base64').toString('utf8'));
check('bannerInfo 删除', d6.data.bannerInfo === undefined);
check('banner 楼层删除', !d6.data.floors.some(f => f.mId === 'banner'));
check('normal 楼层保留', d6.data.floors.some(f => f.mId === 'normal'));

console.log('\n== 用例 7: 新品页 getTabHomeInfo (iconInfo/roofTop 删除) ==');
const gti = { code: 0, data: { result: { iconInfo: { t: 1 }, roofTop: { t: 1 }, title: 'x' } } };
const r7 = run(BASE, b64(JSON.stringify(gti)));
const d7 = JSON.parse(Buffer.from(r7.body, 'base64').toString('utf8'));
check('iconInfo 删除', d7.data.result.iconInfo === undefined);
check('roofTop 删除', d7.data.result.roofTop === undefined);
check('title 保留', d7.data.result.title === 'x');

console.log('\n== 用例 8: 无效响应 (HTML 错误页，应原样放行) ==');
const r8 = run(BASE, '<html><body>error</body></html>');
check('HTML 放行且不崩溃', r8.__error === undefined && (r8.body === undefined || r8.body === '<html><body>error</body></html>'), r8.__error || String(r8.body).slice(0, 60));

console.log('\n== 用例 9: 无广告的普通响应 (应原样放行，不改写) ==');
const plain = { code: 0, data: { deviceLevel: 'L1', refresh: true } };
const r9 = run(BASE, b64(JSON.stringify(plain)));
check('无广告放行', r9.body === undefined || r9.body === b64(JSON.stringify(plain)), String(r9.body || '').slice(0, 60));

console.log('\n== 用例 10: 含中文的响应（base64 往返 UTF-8 无损） ==');
const zh = { code: 0, data: { images: [{ name: '开屏广告图' }], message: '成功' } };
const r10 = run(BASE, b64(JSON.stringify(zh)));
const d10 = JSON.parse(Buffer.from(r10.body, 'base64').toString('utf8'));
check('中文无损', d10.data.images.length === 0 && d10.data.message === '成功', JSON.stringify(d10));

console.log('\n== 用例 11: 巨大响应 (2MB+，直接放行) ==');
const big = b64(JSON.stringify({ data: { images: [{ url: 'x'.repeat(3000000) }] } }));
const r11 = run(BASE, big);
check('2MB+ 放行', r11.body === undefined, String((r11.body || '')).length);

console.log(`\n结果: ${pass} 通过, ${fail} 失败`);
process.exit(fail ? 1 : 0);
