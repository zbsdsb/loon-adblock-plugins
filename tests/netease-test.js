// 网易云去广告 v2 harness：new Function 独立作用域，模拟 Loon binary-body
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const zlib = require('zlib');

const ROOT = path.resolve(__dirname, '..');
const script = fs.readFileSync(path.join(ROOT, 'NeteaseCloudMusic_ads_v2.js'), 'utf8');
const KEY = Buffer.from('e82ckenh8dichen8');
const HAR = '/Users/zbs/Downloads/AirDrop/115_1786691259698.har';

function run(url, body, utils) {
  let result = null;
  const done = (o) => { result = o || {}; };
  const $utils = utils || {
    ungzip(buf) {
      const input = Buffer.isBuffer(buf) ? buf : Buffer.from(buf);
      return zlib.gunzipSync(input);
    }
  };
  try {
    new Function('$request', '$response', '$done', '$utils', script)({ url }, { body }, done, $utils);
  } catch (e) {
    return { __error: String(e && e.stack || e) };
  }
  return result;
}

function aesDecrypt(buf) {
  const dec = crypto.createDecipheriv('aes-128-ecb', KEY, null);
  dec.setAutoPadding(true);
  return Buffer.concat([dec.update(buf), dec.final()]);
}

function aesEncrypt(buf) {
  const enc = crypto.createCipheriv('aes-128-ecb', KEY, null);
  enc.setAutoPadding(true);
  return Buffer.concat([enc.update(buf), enc.final()]);
}

function decodeNet(buf) {
  const raw = Buffer.isBuffer(buf) ? buf : Buffer.from(buf);
  const dec = aesDecrypt(raw);
  const plain = dec[0] === 0x1f && dec[1] === 0x8b ? zlib.gunzipSync(dec) : dec;
  return JSON.parse(plain.toString('utf8'));
}

function encodeNet(obj) {
  const gz = zlib.gzipSync(Buffer.from(JSON.stringify(obj)), { mtime: 0 });
  return aesEncrypt(gz);
}

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log('  ✅', name); }
  else { fail++; console.log('  ❌', name, '—', detail || ''); }
}

const RCMD = 'https://interface3.music.163.com/eapi/link/page/rcmd/resource/show';
const POS = 'https://interface3.music.163.com/eapi/link/position/show/resource';
const OTHER = 'https://interface3.music.163.com/eapi/link/home/framework/tab';

console.log('== 用例 1: 首页推荐 Banner 应剔除 ==');
const rcmdObj = {
  code: 200,
  data: {
    blocks: [
      { positionCode: 'PAGE_RECOMMEND_DAILY_RECOMMEND', dslData: { ok: 1 } },
      {
        positionCode: 'PAGE_RECOMMEND_BANNER_1',
        nativeConfig: { showType: 'RECOMMEND_BANNER' },
        nativeData: { banners: [{ adLocation: '10088', bannerType: 'big_card_banner' }] }
      }
    ],
    blockCodeOrderList: ['PAGE_RECOMMEND_DAILY_RECOMMEND', 'PAGE_RECOMMEND_BANNER_1']
  }
};
const r1 = run(RCMD, encodeNet(rcmdObj));
check('无报错', !r1.__error, r1.__error);
const d1 = decodeNet(r1.body);
check('日常推荐保留', d1.data.blocks.length === 1 && d1.data.blocks[0].positionCode === 'PAGE_RECOMMEND_DAILY_RECOMMEND', JSON.stringify(d1.data.blocks.map(b => b.positionCode)));
check('Banner 删除', !d1.data.blocks.some(b => /BANNER/i.test(b.positionCode)));
check('orderList 同步删 Banner', JSON.stringify(d1.data.blockCodeOrderList) === JSON.stringify(['PAGE_RECOMMEND_DAILY_RECOMMEND']));
check('gzip 回写可解', d1.code === 200);

console.log('\n== 用例 2: 无广告推荐应原样放行 ==');
const cleanObj = { code: 200, data: { blocks: [{ positionCode: 'PAGE_RECOMMEND_RANK' }], blockCodeOrderList: ['PAGE_RECOMMEND_RANK'] } };
const cleanBody = encodeNet(cleanObj);
const r2 = run(RCMD, cleanBody);
check('未改写', r2.body === undefined, r2.body ? 'rewritten' : '');

console.log('\n== 用例 3: 二楼入口应清空 ==');
const floorObj = {
  code: 200,
  data: {
    commonResourceList: [{ positionCode: 'homepage_second_floor_entrance', title: 'true' }]
  }
};
const r3 = run(POS, encodeNet(floorObj));
const d3 = decodeNet(r3.body);
check('二楼列表清空', Array.isArray(d3.data.commonResourceList) && d3.data.commonResourceList.length === 0);

console.log('\n== 用例 4: 播放策略缓存位应放行 ==');
const cacheObj = {
  code: 200,
  data: {
    dataGroupResourceList: [{ positionCode: 'audioPlayerStrategy_cache', data: { keep: 1 } }]
  }
};
const cacheBody = encodeNet(cacheObj);
const r4 = run(POS, cacheBody);
check('策略位未改写', r4.body === undefined);

console.log('\n== 用例 5: Banner 反馈位应清空 ==');
const fbObj = {
  code: 200,
  data: {
    dataGroupResourceList: [{ positionCode: 'PAGE_RECOMMAND_BANNER_AD_FEEDBACK', data: { title: '对这条内容的反馈' } }]
  }
};
const r5 = run(POS, encodeNet(fbObj));
const d5 = decodeNet(r5.body);
check('反馈位清空', Array.isArray(d5.data.dataGroupResourceList) && d5.data.dataGroupResourceList.length === 0);

console.log('\n== 用例 6: 非目标接口放行 ==');
const r6 = run(OTHER, encodeNet({ code: 200, data: { commonResourceList: [{ title: '首页' }] } }));
check('framework/tab 未改写', r6.body === undefined);

console.log('\n== 用例 7: 明文 / 坏包不抛错 ==');
const r7a = run(RCMD, '{"code":200}');
const r7b = run(RCMD, Buffer.from('not-aes-at-all'));
check('明文放行', !r7a.__error && r7a.body === undefined, r7a.__error);
check('坏包放行', !r7b.__error && r7b.body === undefined, r7b.__error);

if (fs.existsSync(HAR)) {
  console.log('\n== HAR 回归 ==');
  const har = JSON.parse(fs.readFileSync(HAR, 'utf8'));
  let seen = 0, rewritten = 0, errors = 0, injured = 0;
  for (const e of har.log.entries) {
    const u = e.request.url;
    if (!/music\.163\.com\/x?e?api\/link\/(page\/rcmd\/resource\/show|position\/show\/resource)/.test(u)) continue;
    const c = e.response.content || {};
    if (!c.text) continue;
    const raw = Buffer.from(c.text, c.encoding === 'base64' ? 'base64' : 'utf8');
    seen++;
    const before = decodeNet(raw);
    const out = run(u, raw);
    if (out.__error) { errors++; console.log('  error', u, out.__error); continue; }
    if (!out.body) continue;
    rewritten++;
    const after = decodeNet(out.body);
    if (/rcmd\/resource\/show/.test(u)) {
      const left = (after.data && after.data.blocks || []).map(b => b.positionCode || b.bizCode);
      if (left.some(c => /BANNER/i.test(String(c)))) injured++;
      const keepDaily = (before.data.blocks || []).some(b => (b.positionCode || '') === 'PAGE_RECOMMEND_DAILY_RECOMMEND');
      const stillDaily = left.includes('PAGE_RECOMMEND_DAILY_RECOMMEND');
      if (keepDaily && !stillDaily) injured++;
    } else {
      const code = ((before.data.commonResourceList || [])[0] || (before.data.dataGroupResourceList || [])[0] || {}).positionCode;
      if (code === 'audioPlayerStrategy' || code === 'audioPlayerStrategy_cache' || code === 'vinyl_share' || code === 'attachResource') injured++;
    }
  }
  check('HAR 无报错', errors === 0, 'errors=' + errors);
  check('HAR 无误伤功能位', injured === 0, 'injured=' + injured);
  check('HAR 至少改写首页推荐', rewritten >= 1, 'seen=' + seen + ' rewritten=' + rewritten);
  console.log('   (seen=' + seen + ', rewritten=' + rewritten + ')');
}

console.log('\n结果: ' + pass + ' 通过 / ' + fail + ' 失败');
process.exit(fail ? 1 : 0);
