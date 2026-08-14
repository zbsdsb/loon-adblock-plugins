/*
 * 网易云去广告 v2 —— 适配 9.5.67+ 投放通道
 * ==================================================
 * 开屏 /x?e?api/ad 仍由插件 Rewrite reject-dict 拦截。
 * 9.5.x 把可见广告迁到 AES+gzip 加密的 link / delivery / popup：
 *   - 首页推荐 PAGE_RECOMMEND_BANNER_*
 *   - 首页二楼 / Banner 反馈位
 *   - 运营商免流弹窗、会员投放卡（由 lpx Rewrite 整段拦）
 *
 * 本脚本只清洗「广告和功能混在一起」的加密响应：
 *   /link/page/rcmd/resource/show
 *   /link/position/show/resource
 * 解密 → 按内容特征清洗 → gzip(store)+AES 原格式回写
 * 无广告特征时原样放行。
 */

const url = $request.url;
const body = $response.body;
if (!body) return $done({});
if (!/^https?:\/\/(ipv4|interface\d?)\.music\.163\.com\/x?e?api\//.test(url)) return $done({});

const AES_KEY = strToBytes('e82ckenh8dichen8');

const AD_POSITIONS = {
  homepage_second_floor_entrance: 1,
  PAGE_RECOMMAND_BANNER_AD_FEEDBACK: 1
};

function strToBytes(s) {
  const out = new Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i) & 0xff;
  return out;
}

function bytesToStr(bytes) {
  let s = '';
  for (let i = 0; i < bytes.length; i += 8192) {
    s += String.fromCharCode.apply(null, bytes.slice(i, i + 8192));
  }
  return s;
}

function toBytes(input) {
  if (!input) return [];
  if (typeof input === 'string') return strToBytes(input);
  if (input instanceof ArrayBuffer) return Array.from(new Uint8Array(input));
  if (ArrayBuffer.isView(input)) return Array.from(new Uint8Array(input.buffer, input.byteOffset, input.byteLength));
  if (Array.isArray(input)) return input.slice();
  return [];
}

function toU8(bytes) {
  return bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
}

// ---------- CRC32 + gzip stored（不依赖 $utils.gzip） ----------
const CRC_TABLE = (function () {
  const t = new Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

function crc32(bytes) {
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function gzipStore(bytes) {
  const chunks = [];
  chunks.push(0x1f, 0x8b, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xff);
  let offset = 0;
  while (offset < bytes.length) {
    const remain = bytes.length - offset;
    const size = remain > 65535 ? 65535 : remain;
    const last = offset + size >= bytes.length ? 1 : 0;
    chunks.push(last); // BFINAL + BTYPE=00
    chunks.push(size & 0xff, (size >> 8) & 0xff);
    const nlen = (~size) & 0xffff;
    chunks.push(nlen & 0xff, (nlen >> 8) & 0xff);
    for (let i = 0; i < size; i++) chunks.push(bytes[offset + i]);
    offset += size;
  }
  const crc = crc32(bytes);
  const len = bytes.length >>> 0;
  chunks.push(crc & 0xff, (crc >> 8) & 0xff, (crc >> 16) & 0xff, (crc >> 24) & 0xff);
  chunks.push(len & 0xff, (len >> 8) & 0xff, (len >> 16) & 0xff, (len >> 24) & 0xff);
  return chunks;
}

function ungzip(bytes) {
  if (typeof $utils !== 'undefined' && typeof $utils.ungzip === 'function') {
    const out = $utils.ungzip(toU8(bytes));
    return toBytes(out);
  }
  throw new Error('no-ungzip');
}

// ---------- AES-128-ECB + PKCS7 ----------
const SBOX = [
  99,124,119,123,242,107,111,197,48,1,103,43,254,215,171,118,202,130,201,125,250,89,71,240,173,212,162,175,156,164,114,192,183,253,147,38,54,63,247,204,52,165,229,241,113,216,49,21,4,199,35,195,24,150,5,154,7,18,128,226,235,39,178,117,9,131,44,26,27,110,90,160,82,59,214,179,41,227,47,132,83,209,0,237,32,252,177,91,106,203,190,57,74,76,88,207,208,239,170,251,67,77,51,133,69,249,2,127,80,60,159,168,81,163,64,143,146,157,56,245,188,182,218,33,16,255,243,210,205,12,19,236,95,151,68,23,196,167,126,61,100,93,25,115,96,129,79,220,34,42,144,136,70,238,184,20,222,94,11,219,224,50,58,10,73,6,36,92,194,211,172,98,145,149,228,121,231,200,55,109,141,213,78,169,108,86,244,234,101,122,174,8,186,120,37,46,28,166,180,198,232,221,116,31,75,189,139,138,112,62,181,102,72,3,246,14,97,53,87,185,134,193,29,158,225,248,152,17,105,217,142,148,155,30,135,233,206,85,40,223,140,161,137,13,191,230,66,104,65,153,45,15,176,84,187,22
];
const INV_SBOX = new Array(256);
for (let i = 0; i < 256; i++) INV_SBOX[SBOX[i]] = i;
const RCON = [0x00,0x01,0x02,0x04,0x08,0x10,0x20,0x40,0x80,0x1b,0x36];

function xtime(a) { return ((a << 1) ^ ((a & 0x80) ? 0x1b : 0)) & 0xff; }
function mul(a, b) {
  let p = 0;
  for (let i = 0; i < 8; i++) {
    if (b & 1) p ^= a;
    a = xtime(a);
    b >>= 1;
  }
  return p;
}

function expandKey(key) {
  const w = key.slice();
  for (let i = 16; i < 176; i += 4) {
    let t0 = w[i - 4], t1 = w[i - 3], t2 = w[i - 2], t3 = w[i - 1];
    if (i % 16 === 0) {
      const old0 = t0;
      t0 = SBOX[t1] ^ RCON[i / 16];
      t1 = SBOX[t2];
      t2 = SBOX[t3];
      t3 = SBOX[old0];
    }
    w[i] = w[i - 16] ^ t0;
    w[i + 1] = w[i - 15] ^ t1;
    w[i + 2] = w[i - 14] ^ t2;
    w[i + 3] = w[i - 13] ^ t3;
  }
  return w;
}

function addRoundKey(s, rk, off) {
  for (let i = 0; i < 16; i++) s[i] ^= rk[off + i];
}
function subBytes(s, box) {
  for (let i = 0; i < 16; i++) s[i] = box[s[i]];
}
function shiftRows(s) {
  let t = s[1]; s[1] = s[5]; s[5] = s[9]; s[9] = s[13]; s[13] = t;
  t = s[2]; s[2] = s[10]; s[10] = t; t = s[6]; s[6] = s[14]; s[14] = t;
  t = s[15]; s[15] = s[11]; s[11] = s[7]; s[7] = s[3]; s[3] = t;
}
function invShiftRows(s) {
  let t = s[13]; s[13] = s[9]; s[9] = s[5]; s[5] = s[1]; s[1] = t;
  t = s[2]; s[2] = s[10]; s[10] = t; t = s[6]; s[6] = s[14]; s[14] = t;
  t = s[3]; s[3] = s[7]; s[7] = s[11]; s[11] = s[15]; s[15] = t;
}
function mixColumns(s) {
  for (let c = 0; c < 4; c++) {
    const i = c * 4;
    const a0 = s[i], a1 = s[i + 1], a2 = s[i + 2], a3 = s[i + 3];
    s[i] = xtime(a0) ^ xtime(a1) ^ a1 ^ a2 ^ a3;
    s[i + 1] = a0 ^ xtime(a1) ^ xtime(a2) ^ a2 ^ a3;
    s[i + 2] = a0 ^ a1 ^ xtime(a2) ^ xtime(a3) ^ a3;
    s[i + 3] = xtime(a0) ^ a0 ^ a1 ^ a2 ^ xtime(a3);
  }
}
function invMixColumns(s) {
  for (let c = 0; c < 4; c++) {
    const i = c * 4;
    const a0 = s[i], a1 = s[i + 1], a2 = s[i + 2], a3 = s[i + 3];
    s[i] = mul(a0, 14) ^ mul(a1, 11) ^ mul(a2, 13) ^ mul(a3, 9);
    s[i + 1] = mul(a0, 9) ^ mul(a1, 14) ^ mul(a2, 11) ^ mul(a3, 13);
    s[i + 2] = mul(a0, 13) ^ mul(a1, 9) ^ mul(a2, 14) ^ mul(a3, 11);
    s[i + 3] = mul(a0, 11) ^ mul(a1, 13) ^ mul(a2, 9) ^ mul(a3, 14);
  }
}

function cryptBlock(input, rk, encrypt) {
  const s = input.slice();
  if (encrypt) {
    addRoundKey(s, rk, 0);
    for (let r = 1; r < 10; r++) {
      subBytes(s, SBOX); shiftRows(s); mixColumns(s); addRoundKey(s, rk, r * 16);
    }
    subBytes(s, SBOX); shiftRows(s); addRoundKey(s, rk, 160);
  } else {
    addRoundKey(s, rk, 160);
    for (let r = 9; r >= 1; r--) {
      invShiftRows(s); subBytes(s, INV_SBOX); addRoundKey(s, rk, r * 16); invMixColumns(s);
    }
    invShiftRows(s); subBytes(s, INV_SBOX); addRoundKey(s, rk, 0);
  }
  return s;
}

function pkcs7Pad(bytes) {
  const n = 16 - (bytes.length % 16 || 16);
  const out = bytes.slice();
  for (let i = 0; i < n; i++) out.push(n);
  return out;
}

function pkcs7Unpad(bytes) {
  if (!bytes.length) return bytes;
  const n = bytes[bytes.length - 1];
  if (n < 1 || n > 16) return bytes;
  for (let i = 0; i < n; i++) if (bytes[bytes.length - 1 - i] !== n) return bytes;
  return bytes.slice(0, bytes.length - n);
}

function aesEcb(bytes, key, encrypt) {
  const rk = expandKey(key);
  const src = encrypt ? pkcs7Pad(bytes) : bytes.slice();
  if (src.length % 16 !== 0) return null;
  const out = [];
  for (let i = 0; i < src.length; i += 16) {
    const block = cryptBlock(src.slice(i, i + 16), rk, encrypt);
    for (let j = 0; j < 16; j++) out.push(block[j]);
  }
  return encrypt ? out : pkcs7Unpad(out);
}

function decodeBody(raw) {
  let bytes = toBytes(raw);
  if (!bytes.length || bytes.length % 16 !== 0) return null;
  const dec = aesEcb(bytes, AES_KEY, false);
  if (!dec) return null;
  let plain = dec;
  if (plain[0] === 0x1f && plain[1] === 0x8b) {
    try { plain = ungzip(plain); } catch (e) { return null; }
  }
  try {
    return JSON.parse(bytesToStr(plain));
  } catch (e) {
    return null;
  }
}

function encodeBody(obj) {
  const json = strToBytes(JSON.stringify(obj));
  const gz = gzipStore(json);
  return toU8(aesEcb(gz, AES_KEY, true));
}

function isAdBlock(block) {
  if (!block || typeof block !== 'object') return false;
  const code = String(block.positionCode || block.bizCode || '');
  if (/BANNER/i.test(code)) return true;
  if (block.nativeConfig && block.nativeConfig.showType === 'RECOMMEND_BANNER') return true;
  const banners = block.nativeData && block.nativeData.banners;
  if (Array.isArray(banners) && banners.some((b) => b && (b.adLocation || b.bannerType || (b.extMonitor && b.extMonitor.length)))) return true;
  return false;
}

function dropBannerCodes(list) {
  return list.filter((c) => !/BANNER/i.test(String(c)));
}

function filterOrderList(list) {
  if (Array.isArray(list)) {
    const next = dropBannerCodes(list);
    return next.length === list.length ? list : next;
  }
  if (typeof list === 'string') {
    try {
      const arr = JSON.parse(list);
      if (Array.isArray(arr)) {
        const next = dropBannerCodes(arr);
        return next.length === arr.length ? list : JSON.stringify(next);
      }
    } catch (e) {}
  }
  return list;
}

function cleanRcmd(obj) {
  const data = obj && obj.data;
  if (!data || typeof data !== 'object') return false;
  let changed = false;
  if (Array.isArray(data.blocks)) {
    const next = data.blocks.filter((b) => !isAdBlock(b));
    if (next.length !== data.blocks.length) {
      data.blocks = next;
      changed = true;
    }
  }
  if (data.blockCodeOrderList) {
    const next = filterOrderList(data.blockCodeOrderList);
    if (next !== data.blockCodeOrderList) {
      data.blockCodeOrderList = next;
      changed = true;
    }
  }
  return changed;
}

function positionCodeOf(data) {
  if (!data || typeof data !== 'object') return '';
  if (data.positionCode) return String(data.positionCode);
  const first = (data.commonResourceList && data.commonResourceList[0])
    || data.commonResource
    || (data.dataGroupResourceList && data.dataGroupResourceList[0]);
  return first && first.positionCode ? String(first.positionCode) : '';
}

function cleanPosition(obj) {
  const data = obj && obj.data;
  if (!data || typeof data !== 'object') return false;
  const code = positionCodeOf(data);
  if (!AD_POSITIONS[code]) return false;
  let changed = false;
  if (Array.isArray(data.commonResourceList) && data.commonResourceList.length) {
    data.commonResourceList = [];
    changed = true;
  }
  if (Array.isArray(data.dataGroupResourceList) && data.dataGroupResourceList.length) {
    data.dataGroupResourceList = [];
    changed = true;
  }
  if (data.commonResource && typeof data.commonResource === 'object') {
    data.commonResource = {};
    changed = true;
  }
  if (data.crossPlatformResource && typeof data.crossPlatformResource === 'object') {
    data.crossPlatformResource = {};
    changed = true;
  }
  return changed;
}

function clean(obj, href) {
  if (!obj || typeof obj !== 'object') return false;
  if (/\/link\/page\/rcmd\/resource\/show/.test(href)) return cleanRcmd(obj);
  if (/\/link\/position\/show\/resource/.test(href)) return cleanPosition(obj);
  return false;
}

const obj = decodeBody(body);
if (!obj) return $done({});
if (!clean(obj, url)) return $done({});
return $done({ body: encodeBody(obj) });
