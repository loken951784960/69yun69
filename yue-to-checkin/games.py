#!/usr/bin/env python3
"""悦通 WebApp 小游戏/活动自动游玩脚本。

覆盖: 签到 / 翻牌 / 免费抽 / 答题 / 竞猜 / 新手礼 / 挑战 / 抽奖 / 宝箱 /
      赛季手册 / 养鸡场(签到/蛋篮/收蛋/喂鸡王/许愿/任务/成就/通行证/开箱/兑换) /
      抽卡 / UNO(人机对局自动出牌)

流程:
    1. 从环境变量读取并解密 SESSION_B64 -> session/yue.session (本地已有则跳过)
    2. 用 Telethon 登录
    3. RequestWebView 获取最新 tgWebAppData (initData)
    4. 调用 /state 获取今日任务与各活动状态
    5. 逐项判断今日是否已完成，未完成则执行对应操作
    6. 汇总结果推送到 Telegram

环境变量 (GitHub Secrets):
    TG_API_ID / TG_API_HASH / SESSION_B64 / SESSION_PASS / NOTIFY_TO (可选) / BOT_TOKEN (可选)
本地调试: 设置 TG_PROXY=socks5://127.0.0.1:7897 走代理。
"""
import asyncio
import io
import os
import random
import re
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import requests
from telethon import TelegramClient

from checkin import (
    API_BASE,
    HEADERS,
    BOT,
    decrypt_session,
    fetch_init_data,
    get_proxy,
    get_requests_proxy,
    notify,
)

# ---------------- 幂等键 (对应前端 _gameApi 的 a()) ----------------
IDEM_CHARS = "0123456789abcdefghijklmnopqrstuvwxyz"


def _base36(n: int) -> str:
    if n == 0:
        return "0"
    s = ""
    while n:
        n, r = divmod(n, 36)
        s = IDEM_CHARS[r] + s
    return s


def idem_key(prefix: str = "mk") -> str:
    """生成幂等键: mk-<ts36>-<rand8>，截断 64 字符。"""
    ts36 = _base36(int(time.time() * 1000))
    rnd = _base36(random.getrandbits(48))[:8]
    key = f"{prefix}-{ts36}-{rnd}"
    key = re.sub(r"[^a-zA-Z0-9-_]", "", key)
    return key[:64]


# ---------------- API 调用 ----------------
def api_call(path: str, init_data: str, with_idem: bool = False,
             idem_prefix: str = "mk", **params) -> dict:
    """调用悦通 WebApp API，返回完整 JSON（不抛异常，把异常转成 dict）。"""
    body = {"initData": init_data, **params}
    headers = dict(HEADERS)
    if with_idem:
        key = idem_key(idem_prefix)
        body["idempotencyKey"] = key
        headers["X-Idempotency-Key"] = key
    try:
        r = requests.post(API_BASE + path, json=body, headers=headers,
                          proxies=get_requests_proxy(), timeout=60)
    except requests.RequestException as e:
        return {"ok": False, "_error": f"网络请求失败: {e}"}
    try:
        return r.json()
    except ValueError:
        return {"ok": False, "_error": f"响应非 JSON: HTTP {r.status_code} {r.text[:200]}"}


def ok_text(resp: dict, default: str = "") -> str:
    """从响应里取可展示的信息。"""
    if not resp.get("ok"):
        msg = resp.get("message") or resp.get("_error") or resp.get("detail") or "未知错误"
        return f"失败: {msg}"
    if resp.get("_error"):
        return f"失败: {resp['_error']}"
    return default


# ---------------- 答题智能 ---------------
# 已知题目池答案（questionId -> 正确选项文本），题目每日 5 题从池中抽取
QUIZ_ANSWERS = {
    "dq08": "切换节点或网络再试",
    "dq22": "今日分数和已完成状态",
    "dq19": "一题一屏",
    "dq32": "泰山",
    "dq43": "张骞",
}


def _overlap(text: str, opt: str) -> int:
    """统计选项文本中出现在题面/hint 中的字符数（去重加权）。"""
    score = 0
    seen = set()
    for ch in opt:
        if ch in seen:
            continue
        seen.add(ch)
        if ch in text:
            score += 1
    return score


def guess_answer(q: dict) -> int:
    """根据题目 id 题库 + hint/题面关键词匹配，返回选项索引。"""
    opts = q.get("options") or []
    if not opts:
        return 0
    known = QUIZ_ANSWERS.get(q.get("id"))
    if known:
        for i, opt in enumerate(opts):
            if known in opt or opt in known:
                return i
    text = (q.get("hint") or "") + " " + (q.get("q") or "")
    best_i, best_score = 0, -1
    for i, opt in enumerate(opts):
        score = _overlap(text, opt)
        if score > best_score:
            best_i, best_score = i, score
    return best_i


def fmt_reward(r) -> str:
    if not r:
        return ""
    if isinstance(r, dict):
        return r.get("label") or r.get("description") or str(r)
    return str(r)


# ---------------- 主流程 ----------------
async def run_games(api_id: int, api_hash: str) -> str:
    client = None
    logs: list[str] = []
    try:
        client = TelegramClient("session/yue", api_id, api_hash, proxy=get_proxy())
        await client.start()
        logs.append("[OK] 已登录 Telegram")

        init_data = await fetch_init_data(client)
        logs.append(f"[OK] 已获取 WebApp 认证数据 (len={len(init_data)})")

        # ---- 0. 总体状态 ----
        state = api_call("/state", init_data)
        if not state.get("ok"):
            logs.append(f"[错误] /state 获取失败: {ok_text(state)}")
            return "\n".join(logs)
        data = state.get("data") or {}
        welfare = data.get("welfare") or {}
        today_loop = data.get("todayLoop") or {}
        dashboard = data.get("dashboard") or {}
        user = data.get("user") or {}
        points = data.get("points") or {}
        logs.append(f"[信息] 积分 {points.get('balance', 0)} · 剩余流量 "
                    f"{user.get('remainingGb', '?')}GB · 连签 {user.get('signStreak', 0)} 天")

        # ---- 1. 每日签到 ----
        if welfare.get("signToday"):
            logs.append("✅ 签到: 今日已签到")
        else:
            r = api_call("/sign/do", init_data)
            if r.get("ok"):
                reward = r.get("reward") or {}
                logs.append(f"✅ 签到成功: {fmt_reward(reward) or '未知奖励'}")
            else:
                logs.append(f"❌ 签到: {ok_text(r)}")

        # ---- 2. 翻牌 ----
        fs = api_call("/flip/state", init_data)
        if fs.get("ok") and fs.get("claimedToday"):
            logs.append("✅ 翻牌: 今日已翻" + (f"（{fmt_reward(fs.get('reward'))}）"
                                               if fs.get("reward") else ""))
        else:
            pick = random.randint(0, 2)
            r = api_call("/flip/claim", init_data, pick=pick)
            if r.get("ok"):
                logs.append(f"✅ 翻牌: {fmt_reward(r.get('reward')) or '已翻'}")
            else:
                logs.append(f"❌ 翻牌: {ok_text(r)}")

        # ---- 3. 免费抽 ----
        if welfare.get("freespinToday"):
            logs.append("✅ 免费抽: 今日已领")
        else:
            r = api_call("/freespin/claim", init_data)
            if r.get("ok"):
                reward = r.get("rewardGb") or r.get("reward")
                logs.append(f"✅ 免费抽: +{reward}GB 流量" if reward else "✅ 免费抽: 领取成功")
            else:
                logs.append(f"❌ 免费抽: {ok_text(r)}")

        # ---- 4. 答题 ----
        quiz = api_call("/quiz/today", init_data)
        if quiz.get("ok"):
            qdata = quiz.get("data") or quiz
            questions = qdata.get("questions") or []
            if qdata.get("answered") or qdata.get("done"):
                logs.append("✅ 答题: 今日已完成")
            elif questions:
                correct, total = 0, 0
                rule = qdata.get("rewardRule") or {}
                ppc = rule.get("pointsPerCorrect", 2)
                for q in questions:
                    total += 1
                    ans = guess_answer(q)
                    rr = api_call("/quiz/answer", init_data, questionId=q.get("id"), answer=ans)
                    if rr.get("ok") and (rr.get("correct") or rr.get("right")):
                        correct += 1
                bonus = rule.get("perfectBonus", 10)
                extra = f" · 全对额外 +{bonus}" if correct == total else ""
                logs.append(f"✅ 答题: 答对 {correct}/{total} 题 (+{correct * ppc} 积分){extra}")
            else:
                logs.append("⚠️ 答题: 今日无题目")
        else:
            logs.append(f"❌ 答题: {ok_text(quiz)}")

        # ---- 5. 竞猜 ----
        gs = api_call("/gamble/state", init_data)
        if gs.get("ok"):
            gdata = gs.get("data") or gs
            today_count = gdata.get("todayCount", 0)
            total_limit = gdata.get("totalLimit", 0)
            if today_count >= total_limit:
                logs.append("✅ 竞猜: 今日次数已用完")
            elif not gdata.get("playable"):
                logs.append(f"⚠️ 竞猜: 不可玩 - {gdata.get('reason') or gdata.get('message') or ''}")
            else:
                bet_opts = gdata.get("betOptions") or [10]
                bet = bet_opts[0]  # 最低下注
                games = gdata.get("games") or []
                game_type = "dice"
                if games:
                    game_type = games[0].get("type", "dice")
                r = api_call("/gamble/play", init_data, with_idem=True,
                             betGb=bet, gameType=game_type)
                if r.get("ok"):
                    won = r.get("won")
                    net = r.get("netGb")
                    logs.append(f"✅ 竞猜({game_type} 下注{bet}GB): "
                                f"{'赢' if won else '输'} "
                                f"{f'净 {net}GB' if net else ''}")
                else:
                    logs.append(f"❌ 竞猜: {ok_text(r)}")
        else:
            logs.append(f"❌ 竞猜: {ok_text(gs)}")

        # ---- 6. 新手礼 ----
        nb = api_call("/newbie/state", init_data)
        if nb.get("ok") and nb.get("canClaimToday"):
            r = api_call("/newbie/claim", init_data)
            if r.get("ok"):
                logs.append(f"✅ 新手礼: Day{nb.get('step', '?')} 领取成功")
            else:
                logs.append(f"❌ 新手礼: {ok_text(r)}")
        else:
            logs.append("✅ 新手礼: 今日不可领")

        # ---- 7. 挑战 ----
        cs = api_call("/challenges/state", init_data)
        if cs.get("ok"):
            claimed = 0
            for ch in cs.get("challenges") or []:
                if ch.get("completed") and not ch.get("rewarded"):
                    r = api_call("/challenges/claim", init_data, challengeKey=ch.get("key"))
                    if r.get("ok"):
                        claimed += 1
                        logs.append(f"✅ 挑战「{ch.get('desc')}」: +{ch.get('reward_points')} 积分")
                    else:
                        logs.append(f"❌ 挑战「{ch.get('desc')}」: {ok_text(r)}")
            if not claimed:
                logs.append("✅ 挑战: 无可领取项")
        else:
            logs.append(f"❌ 挑战: {ok_text(cs)}")

        # ---- 8. 抽奖 ----
        activity = (welfare or {}).get("activeLottery")
        lottery_id = None
        if isinstance(activity, dict):
            lottery_id = activity.get("activityId")
        elif isinstance(activity, str):
            lottery_id = activity
        if lottery_id and not (welfare or {}).get("lotteryDone"):
            r = api_call("/lottery/join", init_data, activityId=lottery_id)
            if r.get("ok"):
                logs.append("✅ 抽奖: 参与成功")
            else:
                logs.append(f"❌ 抽奖: {ok_text(r)}")
        else:
            logs.append("✅ 抽奖: 今日无活动")

        # ---- 9. 宝箱 (今日任务达标后解锁) ----
        chest = today_loop.get("chest") or {}
        if chest.get("state") == "unlocked":
            r = api_call("/treasure/claim", init_data)
            if r.get("ok"):
                reward = r.get("reward") or {}
                if isinstance(reward, dict):
                    if reward.get("kind") == "traffic":
                        txt = f"+{reward.get('value', '')}GB 流量"
                    elif reward.get("kind") == "points":
                        txt = f"+{reward.get('value', '')} 积分"
                    elif reward.get("label"):
                        txt = reward["label"]
                    else:
                        txt = str(reward)
                else:
                    txt = str(reward)
                logs.append(f"✅ 宝箱: {txt}")
            else:
                logs.append(f"❌ 宝箱: {ok_text(r)}")
        else:
            if chest.get("claimedAt"):
                logs.append("✅ 宝箱: 今日已领")
            else:
                prog = today_loop.get("progress", 0)
                thres = today_loop.get("threshold", 3)
                logs.append(f"✅ 宝箱: 未解锁（今日进度 {prog}/{thres}）")

        # ---- 10. 赛季手册 ----
        if (dashboard or {}).get("bpClaimable", 0) > 0:
            r = api_call("/battle-pass/claim-all", init_data)
            if r.get("ok"):
                logs.append("✅ 赛季手册: 已领取")
            else:
                logs.append(f"❌ 赛季手册: {ok_text(r)}")
        else:
            logs.append("✅ 赛季手册: 无可领取")

        # ---- 11. 养鸡场 ----
        await run_chicken(init_data, logs)

        # ---- 12. 抽卡 (5 积分抽普通) ----
        r = api_call("/redeem", init_data, with_idem=True, idem_prefix="gacha",
                     itemKey="gacha_basic")
        if r.get("ok"):
            if r.get("cached"):
                logs.append(f"✅ 抽卡: {r.get('message') or '今日已抽，未重复扣分'}")
            else:
                receipt = r.get("receipt") or []
                items = [str(x) for x in receipt if x]
                if not items and r.get("message"):
                    items.append(str(r["message"]))
                if not items:
                    items.append("已到账")
                bal = r.get("balance")
                tail = f"（余 {bal} 积分）" if isinstance(bal, (int, float)) else ""
                logs.append(f"✅ 抽卡: {'、'.join(items)}{tail}")
        else:
            logs.append(f"✅ 抽卡: 跳过（{ok_text(r)}）")

        # ---- 13. UNO 人机局 (今日任务 +25 积分) ----
        await run_uno(init_data, logs)

    except Exception as e:
        logs.append(f"[错误] 游玩流程异常: {type(e).__name__}: {e}")
    finally:
        if client:
            await client.disconnect()
    return "\n".join(logs)


async def run_chicken(init_data: str, logs: list[str]) -> None:
    """养鸡场全部日常操作。"""
    cs = api_call("/chicken/state", init_data)
    if not cs.get("ok"):
        logs.append(f"❌ 养鸡场: {ok_text(cs)}")
        return
    cdata = cs.get("data") or cs

    # 每日签到
    r = api_call("/chicken/checkin", init_data)
    if r.get("ok"):
        if r.get("alreadyToday"):
            logs.append(f"✅ 养鸡签到: 今日已签（连签 {cdata.get('streak', {}).get('days', 0)} 天）")
        else:
            logs.append("✅ 养鸡签到: 成功")
    else:
        logs.append(f"✅ 养鸡签到: 已签（{ok_text(r)}）")

    # 每日蛋篮
    if (cdata.get("dailyBasket") or {}).get("available"):
        r = api_call("/chicken/daily_basket", init_data)
        logs.append(f"✅ 蛋篮: 领取成功" if r.get("ok") else f"❌ 蛋篮: {ok_text(r)}")
    else:
        logs.append("✅ 蛋篮: 今日已领")

    # 收蛋
    pending = cdata.get("pendingEggs", 0)
    if pending > 0:
        r = api_call("/chicken/collect", init_data)
        if r.get("ok"):
            got = r.get("collected") or r.get("eggs") or pending
            logs.append(f"✅ 收蛋: +{got} 蛋")
        else:
            logs.append(f"❌ 收蛋: {ok_text(r)}")
    else:
        logs.append("✅ 收蛋: 无待收")

    # 喂鸡王
    star = cdata.get("star") or {}
    if star.get("canPet"):
        r = api_call("/chicken/star_feed", init_data)
        if r.get("ok"):
            logs.append(f"✅ 喂鸡王: 成功（{'进化' if r.get('evolved') else ''}"
                        f"{'升级' if r.get('leveledUp') else ''}）")
        else:
            logs.append(f"❌ 喂鸡王: {ok_text(r)}")
    else:
        logs.append("✅ 喂鸡王: 今日已喂")

    # 许愿
    wish = cdata.get("wish") or {}
    if wish.get("available"):
        r = api_call("/chicken/wish", init_data)
        logs.append("✅ 许愿: 成功" if r.get("ok") else f"❌ 许愿: {ok_text(r)}")
    else:
        logs.append("✅ 许愿: 今日已用")

    # 任务奖励
    tasks = cdata.get("tasks") or []
    t_claimed = 0
    for t in tasks:
        if t.get("done") and not t.get("claimed"):
            r = api_call("/chicken/task_claim", init_data, taskKey=t.get("key"))
            if r.get("ok"):
                t_claimed += 1
                logs.append(f"✅ 养鸡任务「{t.get('label')}」: {t.get('rewardText') or '已领'}")
            else:
                logs.append(f"❌ 养鸡任务「{t.get('label')}」: {ok_text(r)}")
    if not t_claimed:
        logs.append("✅ 养鸡任务: 无可领取")

    # 成就奖励
    album = api_call("/chicken/album", init_data)
    if album.get("ok"):
        a_claimed = 0
        for a in album.get("achievements") or []:
            if a.get("unlocked") and not a.get("claimed"):
                r = api_call("/chicken/ach_claim", init_data, achKey=a.get("key"))
                if r.get("ok"):
                    a_claimed += 1
                    logs.append(f"✅ 成就「{a.get('name')}」: "
                                f"{'+' + str(a.get('rewardFeed')) + ' 饲料' if a.get('rewardFeed') else ''}"
                                f"{'+' + str(a.get('rewardPoints')) + ' 积分' if a.get('rewardPoints') else ''}")
                else:
                    logs.append(f"❌ 成就「{a.get('name')}」: {ok_text(r)}")
        if not a_claimed:
            logs.append("✅ 养鸡成就: 无可领取")
    else:
        logs.append(f"✅ 养鸡成就: 查询失败（{ok_text(album)}）")

    # 赛季通行证
    sp = api_call("/chicken/season_pass", init_data)
    if sp.get("ok"):
        s_claimed = 0
        for tier in sp.get("tiers") or []:
            if tier.get("reached") and not tier.get("claimed"):
                r = api_call("/chicken/season_pass_claim", init_data, tierKey=tier.get("key"))
                if r.get("ok"):
                    s_claimed += 1
                    logs.append(f"✅ 通行证「{tier.get('label')}」: 已领")
                else:
                    logs.append(f"❌ 通行证「{tier.get('label')}」: {ok_text(r)}")
        if not s_claimed:
            logs.append("✅ 通行证: 无可领取")
    else:
        logs.append(f"✅ 通行证: 查询失败（{ok_text(sp)}）")

    # 开箱（每日首开铜箱 200 蛋）
    eggs = cdata.get("eggs", 0)
    box = api_call("/chicken/box/state", init_data)
    if box.get("ok"):
        daily_first = box.get("dailyFirst") or {}
        boxes = box.get("boxes") or []
        bronze = next((b for b in boxes if b.get("key") == "bronze"), None)
        if bronze and daily_first.get("bronze") and eggs >= bronze.get("cost", 200):
            r = api_call("/chicken/box/open", init_data, with_idem=True,
                         box="bronze", count=1)
            if r.get("ok"):
                results = r.get("results") or []
                parts = [str(x.get("receipt")) for x in results if x.get("receipt")]
                if not parts:
                    parts = [fmt_reward(results)]
                got_txt = "、".join(parts)
                if r.get("keyGranted"):
                    got_txt += f" +{r['keyGranted']} 钥匙"
                logs.append(f"✅ 开箱(铜): {got_txt}")
            else:
                logs.append(f"✅ 开箱: 跳过（{ok_text(r)}）")
        else:
            logs.append("✅ 开箱: 今日已开或蛋不足")
    else:
        logs.append(f"✅ 开箱: 查询失败（{ok_text(box)}）")

    # 蛋换流量（t1: 300蛋 → 10GB）
    for rw in cdata.get("rewards") or []:
        if rw.get("canRedeem"):
            r = api_call("/chicken/redeem_reward", init_data, with_idem=True,
                         tier=rw.get("key"))
            if r.get("ok"):
                logs.append(f"✅ 兑换「{rw.get('label')}」: 成功")
            else:
                logs.append(f"✅ 兑换: 跳过（{ok_text(r)}）")
            break
    else:
        logs.append("✅ 蛋兑流量: 蛋不够或今日额度用完")


# ---------------- UNO 人机局 ----------------
UNO_COLORS = ("r", "g", "b", "y")


def _pick_uno_color(hand: list) -> str:
    """为万能牌选颜色: 手牌中数量最多的颜色，兜底红色。"""
    colors = [c.get("color") for c in hand
              if not c.get("isWild") and c.get("color") in UNO_COLORS]
    if not colors:
        return "r"
    return max(set(colors), key=colors.count)


async def run_uno(init_data: str, logs: list[str]) -> None:
    """UNO 人机局自动游玩（尽力而为，失败不影响主流程）。

    流程: /uno/quick_start 开局 -> 轮询 /uno/state -> 有可出牌则 /uno/play，
    否则 /uno/draw，剩 1 张喊 /uno/uno_call，结算后 /uno/leave 退出。
    """
    try:
        r = api_call("/uno/quick_start", init_data, with_idem=True,
                     idem_prefix="uno_qs")
        if not r.get("ok"):
            logs.append(f"⚠️ UNO: 开局失败（{ok_text(r)}）")
            return
        time.sleep(1.0)

        settled = False
        for _ in range(150):  # 最多约 3 分钟
            st = api_call("/uno/state", init_data)
            data = st.get("data") or st
            status = data.get("status")
            if status == "settled":
                settled = True
                break
            if status not in ("active", "playing") or not data.get("myTurn"):
                time.sleep(1.2)
                continue
            hand = data.get("myHand") or []
            playable = data.get("myPlayable") or []
            if playable:
                idx = playable[0]
                body = {"cardIndex": idx}
                if idx < len(hand) and hand[idx].get("isWild"):
                    body["color"] = _pick_uno_color(hand)
                pr = api_call("/uno/play", init_data, with_idem=True,
                              idem_prefix="uno", **body)
                if pr.get("ok"):
                    d = pr.get("data") or {}
                    if d.get("status") == "settled":
                        settled = True
                        break
            else:
                api_call("/uno/draw", init_data, with_idem=True,
                         idem_prefix="unodraw")
            if len(hand) == 1:
                api_call("/uno/uno_call", init_data, with_idem=True,
                         idem_prefix="unocall")
            time.sleep(1.2)

        api_call("/uno/leave", init_data, with_idem=True, idem_prefix="unoleave")
        if settled:
            logs.append("✅ UNO: 已打完 1 局（任务 +25 积分）")
        else:
            logs.append("⚠️ UNO: 对局超时未结算（不影响其他奖励）")
    except Exception as e:
        logs.append(f"⚠️ UNO: 异常（{type(e).__name__}: {e}）")


# ---------------- 入口 ----------------
def main() -> None:
    api_id = int(os.environ.get("TG_API_ID", "0"))
    api_hash = os.environ.get("TG_API_HASH", "")
    if not api_id or not api_hash:
        print("[错误] 缺少 TG_API_ID / TG_API_HASH")
        sys.exit(1)

    os.makedirs("session", exist_ok=True)
    if not os.path.exists("session/yue.session") or os.path.getsize("session/yue.session") < 100:
        with open("session/yue.session", "wb") as f:
            f.write(decrypt_session())
        print("[OK] session 解密完成")
    else:
        print("[OK] 使用本地已有 session")

    result = asyncio.run(run_games(api_id, api_hash))
    print("\n=== 游玩日志 ===")
    print(result)

    asyncio.run(notify(api_id, api_hash, result))
    sys.exit(0)


if __name__ == "__main__":
    main()
