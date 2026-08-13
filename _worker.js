
let domain = "Enter your domain here";
let username = "Enter your email here";
let password = "Enter your password here"; 
let token; 
let domain2;
let username2;
let password2;
let botToken = '';  
let chatId = '';  
let checkInResult;
 // 初始化变量
let fetch, Response; 

// 判断当前环境是否是 Node.js 环境
if (typeof globalThis.fetch === "undefined") {
  import('node-fetch').then(module => {
    fetch = module.default;
    Response = module.Response;
    console.log("在 Node.js 环境中，已导入 node-fetch");
    const env = {
        DOMAIN: process.env.DOMAIN,
        USERNAME: process.env.USERNAME,
        PASSWORD: process.env.PASSWORD,
        TOKEN: process.env.TOKEN,
        TG_TOKEN: process.env.TG_TOKEN,
        TG_ID: process.env.TG_ID,
        DOMAIN2: process.env.DOMAIN2,
        USERNAME2: process.env.USERNAME2,
        PASSWORD2: process.env.PASSWORD2
    };

    const handler = {
        async scheduled(controller, env) {
            console.log("定时任务开始");
            try {
                await initConfig(env);
                await handleCheckIn();
                console.log("定时任务成功完成");
            } catch (error) {
                console.error("定时任务失败:", error);
                await sendMessage(`定时任务失败: ${error.message}`);
            }
        }
    };
      
    handler.scheduled(null, env);
      }).catch(error => {
        console.error("导入 node-fetch 失败:", error);
      });
    
} else {
  fetch = globalThis.fetch;
  Response = globalThis.Response;
  console.log("在 Cloudflare Worker 环境中，已使用内置 fetch");
}


export default {
    async fetch(request, env, ctx) {
        await initConfig(env);
        const url = new URL(request.url);

        // Telegram webhook：收到 bot 消息时处理签到命令
        if (url.pathname === "/webhook" && request.method === "POST") {
            ctx.waitUntil(handleTelegramWebhook(request));
            return new Response("ok", { status: 200 });
        }

        if (url.pathname === "/tg") {
            return await handleTgMsg();
        } else if (url.pathname === `/${token}`) { 
            return await handleCheckIn();
        }

        return new Response(checkInResult, {
            headers: { 'Content-Type': 'text/plain;charset=UTF-8' },
            status: 200
        });
    },

    async scheduled(controller, env) {
        console.log("定时任务开始");
        try {
            await initConfig(env);
            await handleCheckIn();
            console.log("定时任务成功完成");
        } catch (error) {
            console.error("定时任务失败:", error);
            await sendMessage(`定时任务失败: ${error.message}`);
        }
    },
};

// 核心签到逻辑（定时任务、URL 触发、Telegram 命令共用）
async function doCheckIn() {
    validateConfig();

    const cookies = await loginAndGetCookies();
    let result = await performCheckIn(cookies);

    // 第二站（wjkc 网际快车面板），配置了 DOMAIN2 时自动签到
    if (domain2) {
        const r2 = await wjkcCheckIn(domain2, username2, password2);
        result = result + '\n\n' + r2;
    }

    checkInResult = result;
    return result;
}

async function handleCheckIn() {
    try {
        const result = await doCheckIn();
        await sendMessage(result);
        return new Response(result, { status: 200 });
    } catch (error) {
        console.error("签到失败:", error);
        const errorMsg = `${checkInResult}\n🎁${error.message}`;
        await sendMessage(errorMsg);
        return new Response(errorMsg, { status: 500 });
    }
}

// Telegram webhook 处理：收到 bot 消息时执行签到命令
async function handleTelegramWebhook(request) {
    try {
        const update = await request.json();
        const msg = update.message || update.edited_message;
        if (!msg || !msg.text) {
            return;
        }

        const chat = String(msg.chat.id);
        const text = String(msg.text).trim();

        // 安全校验：仅允许配置的 TG_ID（或群组 -100 前缀）触发签到
        if (chatId && chat !== String(chatId)) {
            console.log(`拒绝来自 chat=${chat} 的签到请求（未授权）`);
            return;
        }

        // 支持命令：/checkin、/qd、/签到 等
        const cmd = text.toLowerCase().split(' ')[0].split('@')[0];
        if (['/checkin', '/qd', '/sign', '/签到', '/check', '/打卡'].includes(cmd)) {
            await sendMessage("⏳ 正在签到，请稍候...", chat);
            try {
                const result = await doCheckIn();
                await sendMessage(result, chat);
            } catch (error) {
                console.error("Telegram 触发签到失败:", error);
                await sendMessage(`❌ 签到失败: ${error.message}`, chat);
            }
        } else {
            await sendMessage(
                "🤖 签到机器人可用命令：\n/checkin - 立即签到\n/help - 帮助信息",
                chat
            );
        }
    } catch (error) {
        console.error("处理 Telegram 消息失败:", error);
    }
}

function validateConfig() {
    if (!domain || !username  || !password) {  
        throw new Error("缺少必要的配置参数");
    }
}

async function loginAndGetCookies() {
    const loginUrl = `${domain}/auth/login`;
    const response = await fetch(loginUrl, {
        method: "POST",
        headers: { 
            "Content-Type": "application/json", 
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36", 
            "Accept": "application/json, text/plain, */*", 
            "Origin": domain, 
            "Referer": `${domain}/auth/login`
        },
        body: JSON.stringify({ email: username , passwd: password, remember_me: "on", code: "" }),  
    });

    if (!response.ok) {
        throw new Error(`登录失败: ${await response.text()}`);
    }

    const jsonResponse = await response.json();
    if (jsonResponse.ret !== 1) {
        throw new Error(`登录失败: ${jsonResponse.msg || "未知错误"}`);
    }

    const cookieHeader = response.headers.get("set-cookie");
    if (!cookieHeader) {
        throw new Error("登录成功但未收到 Cookies");
    }

    return cookieHeader.split(',').map(cookie => cookie.split(';')[0]).join("; ");
}

async function performCheckIn(cookies) {
    const checkInUrl = `${domain}/user/checkin`;
    const response = await fetch(checkInUrl, {
        method: "POST",
        headers: {
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Origin': domain,
            'Referer': `${domain}/user/panel`,
            'Cookie': cookies,
            'X-Requested-With': 'XMLHttpRequest'
        },
    });

    if (!response.ok) {
        throw new Error(`签到请求失败: ${await response.text()}`);
    }

    const jsonResponse = await response.json();
    if (!jsonResponse.ret) {
        const msg = String(jsonResponse.msg || "未知错误");
        // 「已签到」类提示视为成功（当天已经签到过）
        if (/已经签到|已签到|已签过|重复签到|SIGN_USE_MULTY_TIMES|HAS_SIGNED|ALREADY_SIGN/.test(msg)) {
            return `🎉 签到结果 🎉\n${msg}`;
        }
        throw new Error(`签到失败: ${msg}`);
    }

    return `🎉 签到结果 🎉\n${jsonResponse.msg || "签到完成"}`;
}

// ===================== 第二站支持：wjkc（网际快车）面板 =====================

const B64_CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';

function b64encode(str) {
    const bin = new TextEncoder().encode(str);
    let out = '';
    for (let i = 0; i < bin.length; i += 3) {
        const b0 = bin[i];
        const b1 = bin[i + 1];
        const b2 = bin[i + 2];
        out += B64_CHARS[b0 >> 2];
        out += B64_CHARS[((b0 & 3) << 4) | (b1 !== undefined ? b1 >> 4 : 0)];
        out += b1 !== undefined ? B64_CHARS[((b1 & 15) << 2) | (b2 !== undefined ? b2 >> 6 : 0)] : '=';
        out += b2 !== undefined ? B64_CHARS[b2 & 63] : '=';
    }
    return out;
}

function b64decode(str) {
    let t = String(str || '').replace(/-/g, '+').replace(/_/g, '/');
    t += '='.repeat((4 - (t.length % 4)) % 4);
    const bytes = [];
    for (let i = 0; i < t.length; i += 4) {
        const e0 = B64_CHARS.indexOf(t[i]);
        const e1 = B64_CHARS.indexOf(t[i + 1]);
        const e2 = t[i + 2] === '=' ? 0 : B64_CHARS.indexOf(t[i + 2]);
        const e3 = t[i + 3] === '=' ? 0 : B64_CHARS.indexOf(t[i + 3]);
        bytes.push((e0 << 2) | (e1 >> 4));
        if (t[i + 2] !== '=') bytes.push(((e1 & 15) << 4) | (e2 >> 2));
        if (t[i + 3] !== '=') bytes.push(((e2 & 3) << 6) | e3);
    }
    return new TextDecoder().decode(new Uint8Array(bytes));
}

function md5hex(string) {
    function RotateLeft(lValue, iShiftBits) {
        return (lValue << iShiftBits) | (lValue >>> (32 - iShiftBits));
    }
    function AddUnsigned(lX, lY) {
        const lX8 = lX & 0x80000000;
        const lY8 = lY & 0x80000000;
        const lX4 = lX & 0x40000000;
        const lY4 = lY & 0x40000000;
        const lResult = (lX & 0x3FFFFFFF) + (lY & 0x3FFFFFFF);
        if (lX4 & lY4) return (lResult ^ 0x80000000 ^ lX8 ^ lY8);
        if (lX4 | lY4) {
            if (lResult & 0x40000000) return (lResult ^ 0xC0000000 ^ lX8 ^ lY8);
            return (lResult ^ 0x40000000 ^ lX8 ^ lY8);
        }
        return (lResult ^ lX8 ^ lY8);
    }
    function F(x, y, z) { return (x & y) | ((~x) & z); }
    function G(x, y, z) { return (x & z) | (y & (~z)); }
    function H(x, y, z) { return (x ^ y ^ z); }
    function I(x, y, z) { return (y ^ (x | (~z))); }
    function FF(a, b, c, d, x, s, ac) {
        a = AddUnsigned(a, AddUnsigned(AddUnsigned(F(b, c, d), x), ac));
        return AddUnsigned(RotateLeft(a, s), b);
    }
    function GG(a, b, c, d, x, s, ac) {
        a = AddUnsigned(a, AddUnsigned(AddUnsigned(G(b, c, d), x), ac));
        return AddUnsigned(RotateLeft(a, s), b);
    }
    function HH(a, b, c, d, x, s, ac) {
        a = AddUnsigned(a, AddUnsigned(AddUnsigned(H(b, c, d), x), ac));
        return AddUnsigned(RotateLeft(a, s), b);
    }
    function II(a, b, c, d, x, s, ac) {
        a = AddUnsigned(a, AddUnsigned(AddUnsigned(I(b, c, d), x), ac));
        return AddUnsigned(RotateLeft(a, s), b);
    }
    function ConvertToWordArray(string) {
        let lWordCount;
        const lMessageLength = string.length;
        const lNumberOfWords_temp1 = lMessageLength + 8;
        const lNumberOfWords_temp2 = (lNumberOfWords_temp1 - (lNumberOfWords_temp1 % 64)) / 64;
        const lNumberOfWords = (lNumberOfWords_temp2 + 1) * 16;
        const lWordArray = Array(lNumberOfWords - 1);
        let lBytePosition = 0;
        let lByteCount = 0;
        while (lByteCount < lMessageLength) {
            lWordCount = (lByteCount - (lByteCount % 4)) / 4;
            lBytePosition = (lByteCount % 4) * 8;
            lWordArray[lWordCount] = (lWordArray[lWordCount] | (string.charCodeAt(lByteCount) << lBytePosition));
            lByteCount++;
        }
        lWordCount = (lByteCount - (lByteCount % 4)) / 4;
        lBytePosition = (lByteCount % 4) * 8;
        lWordArray[lWordCount] = lWordArray[lWordCount] | (0x80 << lBytePosition);
        lWordArray[lNumberOfWords - 2] = lMessageLength << 3;
        lWordArray[lNumberOfWords - 1] = lMessageLength >>> 29;
        return lWordArray;
    }
    function WordToHex(lValue) {
        let WordToHexValue = "";
        let WordToHexValue_temp = "";
        let lByte, lCount;
        for (lCount = 0; lCount <= 3; lCount++) {
            lByte = (lValue >>> (lCount * 8)) & 255;
            WordToHexValue_temp = "0" + lByte.toString(16);
            WordToHexValue = WordToHexValue + WordToHexValue_temp.substr(WordToHexValue_temp.length - 2, 2);
        }
        return WordToHexValue;
    }
    const S11 = 7, S12 = 12, S13 = 17, S14 = 22;
    const S21 = 5, S22 = 9, S23 = 14, S24 = 20;
    const S31 = 4, S32 = 11, S33 = 16, S34 = 23;
    const S41 = 6, S42 = 10, S43 = 15, S44 = 21;
    string = decodeURIComponent(encodeURIComponent(string));
    const x = ConvertToWordArray(string);
    let a = 0x67452301, b = 0xEFCDAB89, c = 0x98BADCFE, d = 0x10325476;
    for (let k = 0; k < x.length; k += 16) {
        const AA = a, BB = b, CC = c, DD = d;
        a = FF(a, b, c, d, x[k + 0], S11, 0xD76AA478);
        d = FF(d, a, b, c, x[k + 1], S12, 0xE8C7B756);
        c = FF(c, d, a, b, x[k + 2], S13, 0x242070DB);
        b = FF(b, c, d, a, x[k + 3], S14, 0xC1BDCEEE);
        a = FF(a, b, c, d, x[k + 4], S11, 0xF57C0FAF);
        d = FF(d, a, b, c, x[k + 5], S12, 0x4787C62A);
        c = FF(c, d, a, b, x[k + 6], S13, 0xA8304613);
        b = FF(b, c, d, a, x[k + 7], S14, 0xFD469501);
        a = FF(a, b, c, d, x[k + 8], S11, 0x698098D8);
        d = FF(d, a, b, c, x[k + 9], S12, 0x8B44F7AF);
        c = FF(c, d, a, b, x[k + 10], S13, 0xFFFF5BB1);
        b = FF(b, c, d, a, x[k + 11], S14, 0x895CD7BE);
        a = FF(a, b, c, d, x[k + 12], S11, 0x6B901122);
        d = FF(d, a, b, c, x[k + 13], S12, 0xFD987193);
        c = FF(c, d, a, b, x[k + 14], S13, 0xA679438E);
        b = FF(b, c, d, a, x[k + 15], S14, 0x49B40821);
        a = GG(a, b, c, d, x[k + 1], S21, 0xF61E2562);
        d = GG(d, a, b, c, x[k + 6], S22, 0xC040B340);
        c = GG(c, d, a, b, x[k + 11], S23, 0x265E5A51);
        b = GG(b, c, d, a, x[k + 0], S24, 0xE9B6C7AA);
        a = GG(a, b, c, d, x[k + 5], S21, 0xD62F105D);
        d = GG(d, a, b, c, x[k + 10], S22, 0x02441453);
        c = GG(c, d, a, b, x[k + 15], S23, 0xD8A1E681);
        b = GG(b, c, d, a, x[k + 4], S24, 0xE7D3FBC8);
        a = GG(a, b, c, d, x[k + 9], S21, 0x21E1CDE6);
        d = GG(d, a, b, c, x[k + 14], S22, 0xC33707D6);
        c = GG(c, d, a, b, x[k + 3], S23, 0xF4D50D87);
        b = GG(b, c, d, a, x[k + 8], S24, 0x455A14ED);
        a = GG(a, b, c, d, x[k + 13], S21, 0xA9E3E905);
        d = GG(d, a, b, c, x[k + 2], S22, 0xFCEFA3F8);
        c = GG(c, d, a, b, x[k + 7], S23, 0x676F02D9);
        b = GG(b, c, d, a, x[k + 12], S24, 0x8D2A4C8A);
        a = HH(a, b, c, d, x[k + 5], S31, 0xFFFA3942);
        d = HH(d, a, b, c, x[k + 8], S32, 0x8771F681);
        c = HH(c, d, a, b, x[k + 11], S33, 0x6D9D6122);
        b = HH(b, c, d, a, x[k + 14], S34, 0xFDE5380C);
        a = HH(a, b, c, d, x[k + 1], S31, 0xA4BEEA44);
        d = HH(d, a, b, c, x[k + 4], S32, 0x4BDECFA9);
        c = HH(c, d, a, b, x[k + 7], S33, 0xF6BB4B60);
        b = HH(b, c, d, a, x[k + 10], S34, 0xBEBFBC70);
        a = HH(a, b, c, d, x[k + 13], S31, 0x289B7EC6);
        d = HH(d, a, b, c, x[k + 0], S32, 0xEAA127FA);
        c = HH(c, d, a, b, x[k + 3], S33, 0xD4EF3085);
        b = HH(b, c, d, a, x[k + 6], S34, 0x04881D05);
        a = HH(a, b, c, d, x[k + 9], S31, 0xD9D4D039);
        d = HH(d, a, b, c, x[k + 12], S32, 0xE6DB99E5);
        c = HH(c, d, a, b, x[k + 15], S33, 0x1FA27CF8);
        b = HH(b, c, d, a, x[k + 2], S34, 0xC4AC5665);
        a = II(a, b, c, d, x[k + 0], S41, 0xF4292244);
        d = II(d, a, b, c, x[k + 7], S42, 0x432AFF97);
        c = II(c, d, a, b, x[k + 14], S43, 0xAB9423A7);
        b = II(b, c, d, a, x[k + 5], S44, 0xFC93A039);
        a = II(a, b, c, d, x[k + 12], S41, 0x655B59C3);
        d = II(d, a, b, c, x[k + 3], S42, 0x8F0CCC92);
        c = II(c, d, a, b, x[k + 10], S43, 0xFFEFF47D);
        b = II(b, c, d, a, x[k + 1], S44, 0x85845DD1);
        a = II(a, b, c, d, x[k + 8], S41, 0x6FA87E4F);
        d = II(d, a, b, c, x[k + 15], S42, 0xFE2CE6E0);
        c = II(c, d, a, b, x[k + 6], S43, 0xA3014314);
        b = II(b, c, d, a, x[k + 13], S44, 0x4E0811A1);
        a = II(a, b, c, d, x[k + 4], S41, 0xF7537E82);
        d = II(d, a, b, c, x[k + 11], S42, 0xBD3AF235);
        c = II(c, d, a, b, x[k + 2], S43, 0x2AD7D2BB);
        b = II(b, c, d, a, x[k + 9], S44, 0xEB86D391);
        a = AddUnsigned(a, AA);
        b = AddUnsigned(b, BB);
        c = AddUnsigned(c, CC);
        d = AddUnsigned(d, DD);
    }
    return (WordToHex(a) + WordToHex(b) + WordToHex(c) + WordToHex(d)).toLowerCase();
}

function fmtTraffic(n) {
    n = Number(n || 0);
    const gb = 1024 * 1024 * 1024;
    const mb = 1024 * 1024;
    const kb = 1024;
    if (n >= gb) return (n / gb).toFixed(2) + ' GB';
    if (n >= mb) return (n / mb).toFixed(2) + ' MB';
    return (n / kb).toFixed(0) + ' KB';
}

function extractCookies(res) {
    const setCookies = typeof res.headers.getSetCookie === 'function'
        ? res.headers.getSetCookie()
        : (res.headers.get('set-cookie') ? [res.headers.get('set-cookie')] : []);
    return setCookies.map((c) => c.split(';')[0]).join('; ');
}

async function wjkcCheckIn(siteDomain, siteUsername, sitePassword) {
    const headers = {
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    };

    // 1. 登录：密码 MD5 + payload base64 包裹
    const loginPayload = b64encode(JSON.stringify({ email: siteUsername, password: md5hex(sitePassword) }));
    const loginRes = await fetch(siteDomain + '/api/user/login', {
        method: 'POST',
        headers: headers,
        body: JSON.stringify({ data: loginPayload }),
    });
    if (!loginRes.ok) {
        throw new Error('wjkc 登录请求失败: HTTP ' + loginRes.status);
    }
    const loginJson = await loginRes.json();
    const loginData = JSON.parse(b64decode(loginJson.data || ''));
    if (loginData.code !== 0) {
        throw new Error('wjkc 登录失败: ' + (loginData.msg || '未知错误'));
    }

    // 2. 携带 session cookie 签到
    const cookie = extractCookies(loginRes);
    const signRes = await fetch(siteDomain + '/api/user/sign_use', {
        method: 'POST',
        headers: Object.assign({}, headers, { Cookie: cookie }),
        body: JSON.stringify({ data: b64encode('{}') }),
    });
    if (!signRes.ok) {
        throw new Error('wjkc 签到请求失败: HTTP ' + signRes.status);
    }
    const signJson = await signRes.json();
    const signData = JSON.parse(b64decode(signJson.data || ''));
    if (signData.code !== 0) {
        const msg = String(signData.msg || '未知错误');
        // 「已签到」类提示视为成功（当天已经签到过）
        if (/SIGN_USE_MULTY_TIMES|HAS_SIGNED|ALREADY_SIGN|已签到|已经签到/.test(msg)) {
            return '🎉 wjkc 签到结果 🎉\n今日已签到（' + msg + '）';
        }
        throw new Error('wjkc 签到失败: ' + msg);
    }
    const add = signData.data && signData.data.addTraffic;
    return '🎉 wjkc 签到结果 🎉\n签到成功' + (add ? '，获得 ' + fmtTraffic(add) : '');
}

// ===================== 第二站支持结束 =====================

async function sendMessage(msg, targetChatId) {
    const to = targetChatId || chatId;
    if (!botToken || !to) {  
        console.log("Telegram 推送未启用. 消息内容:", msg);
        return;
    }

    const now = new Date();
    const formattedTime = new Date(now.getTime() + 8 * 60 * 60 * 1000)
        .toISOString()
        .slice(0, 19)
        .replace("T", " ");
    
    const message = `执行时间: ${formattedTime}\n${msg}`;
    const tgUrl = `https://api.telegram.org/bot${botToken}/sendMessage?chat_id=${to}&parse_mode=HTML&text=${encodeURIComponent(message)}`;

    try {
        const response = await fetch(tgUrl, { method: "GET", headers: { "User-Agent": "Mozilla/5.0", "Accept": "application/json" } });
        
        if (!response.ok) {
             return "Telegram 消息发送失败: "  + await response.text(); 
        }
        const jsonResponse = await response.text(); 
        console.log("Telegram 消息发送成功:", jsonResponse);
        return message;
    } catch (error) {
        console.error("发送 Telegram 消息失败:", error);
        return `发送 Telegram 消息失败: ${error.message}`; 
    }
}


function formatDomain(domain) {
    return domain.includes("//") ? domain : `https://${domain}`;
}

async function handleTgMsg() {
    const message = `${checkInResult}`;
    const sendResult = await sendMessage(message);
    return new Response(sendResult, { status: 200 });
}


function maskSensitiveData(str, type = 'default') {
    if (!str) return "N/A";

   const urlPattern = /^(https?:\/\/)([^\/]+)(.*)$/;
    if (type === 'url' && urlPattern.test(str)) {
        return str.replace(/(https:\/\/)(\w)(\w+)(\w)(\.\w+)/, '$1$2****$4$5');;
    }

    const emailPattern = /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/;
    if (type === 'email' && emailPattern.test(str)) {
        return str.replace(/^(\w)(\w+)(\@)(\w)(\w+)(\.\w+)$/, '$1****$3$4****$6');
    }

    return `${str[0]}****${str[str.length - 1]}`;
}

async function initConfig(env) {
    domain = formatDomain(env.DOMAIN || domain);
    username  = env.USERNAME || username ;
    password = env.PASSWORD || password;  
    domain2 = env.DOMAIN2 ? formatDomain(env.DOMAIN2) : '';
    username2 = env.USERNAME2 || username;
    password2 = env.PASSWORD2 || password;
    token = env.TOKEN || token;  
    botToken = env.TG_TOKEN || botToken;  
    chatId = env.TG_ID || chatId; 

    checkInResult = `配置信息: 
    登录地址: ${maskSensitiveData(domain, 'url')} 
    登录账号: ${maskSensitiveData(username, 'email')} 
    登录密码: ${maskSensitiveData(password)} 
    第二站: ${domain2 ? `${maskSensitiveData(domain2, 'url')} / ${maskSensitiveData(username2, 'email')}` : '未配置'} 
    TG 推送:  ${botToken && chatId ? "已启用" : "未启用"} `;
}
