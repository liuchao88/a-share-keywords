#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""取语料用的小抓取库（从 hudong-rss 的 fetch_qna.py 抽出来的，逻辑一字未改）。

只做一件事：把深交所互动易 + 上证e互动的"全市场最新问答"抓回来，并提供词库匹配器，
给 update_keywords.py 选语料用（只留命中现有词库的那批问答当语料）。
不写 RSS、不碰 state.json、不依赖任何本仓库之外的文件。
"""

import html
import json
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
TZ = timezone(timedelta(hours=8))  # 北京时间
MAX_PAGES = 2      # 每个平台翻几页（取"全市场最新"流，够取材就行）
PAGE_SIZE = 50     # 每页条数
_ASCII_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .+\-/]*$")   # 纯 ASCII 词 → 按词边界匹配


def log(msg):
    print(f"[{datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://sns.sseinfo.com/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def http_post(url, form_data, timeout=30):
    body = urllib.parse.urlencode(form_data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "User-Agent": UA,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://irm.cninfo.com.cn/ircs/index",
        "Content-Type": "application/x-www-form-urlencoded",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_szse():
    """全市场最新回答流（JSON），翻 MAX_PAGES 页"""
    items = []
    for page in range(1, MAX_PAGES + 1):
        try:
            raw = http_post("https://irm.cninfo.com.cn/newircs/index/search",
                            {"keyWord": "", "pageNo": page, "pageSize": PAGE_SIZE})
            data = json.loads(raw)
        except Exception as e:
            log(f"互动易第{page}页失败: {e}")
            break
        for r in data.get("results", []):
            q = (r.get("mainContent") or "").strip()
            a = (r.get("attachedContent") or "").strip()
            if not q or not a:
                continue
            ts_ms = r.get("attachedPubDate") or 0
            items.append({
                "guid": "szse_" + str(r.get("indexId", "")),
                "platform": "深交所互动易",
                "company": (r.get("companyShortName") or "").strip(),
                "code": (r.get("stockCode") or "").strip(),
                "question": q,
                "answer": a,
                "ts": int(ts_ms) / 1000 if ts_ms else 0,
                "link": f"https://irm.cninfo.com.cn/newircs/question/questionDetail?questionId={r.get('indexId', '')}",
            })
        time.sleep(0.5)
    return items


def parse_relative_time(s, now_ts):
    """解析'刚刚/N分钟前/N小时前/昨天 HH:MM/MM-DD HH:MM' → 时间戳，失败返回 0"""
    s = s.strip()
    if not s:
        return 0
    if s == "刚刚":
        return int(now_ts)
    m = re.match(r"(\d+)\s*分钟前", s)
    if m:
        return int(now_ts) - int(m.group(1)) * 60
    m = re.match(r"(\d+)\s*小时前", s)
    if m:
        return int(now_ts) - int(m.group(1)) * 3600
    m = re.match(r"昨天\s*(\d{1,2}):(\d{2})", s)
    if m:
        t = datetime.now(TZ) - timedelta(days=1)
        return int(t.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0).timestamp())
    m = re.match(r"(\d{2})-(\d{2})\s*(\d{1,2}):(\d{2})", s)
    if m:
        y = datetime.now(TZ).year
        return int(datetime(y, int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))).timestamp())
    return 0


def clean_sse_tail(s):
    """清理互动平台页面残留的操作按钮文字"""
    s = re.sub(r"\s*\|?\s*收藏\s*\|?\s*评论.*$", "", s)
    s = re.sub(r"--+>?\s*$", "", s)
    s = re.sub(r"[◆●]+", "", s)
    s = re.sub(r"请登录后再(点赞|收藏)!?", "", s)
    return s.strip()


def parse_sse_html(raw_html):
    """解析 feeds.do 返回的 HTML，提取问答条目"""
    items = []
    now_ts = time.time()
    blocks = re.findall(r'<div class="m_feed_item[^"]*" id="item-(\d+)">(.*?)(?=<div class="m_feed_item|$)', raw_html, re.S)
    for iid, body in blocks:
        text = re.sub(r"<[^>]+>", " ", body)
        text = html.unescape(re.sub(r"\s+", " ", text)).strip()
        # 拆出公司名和代码：某某公司(600166)
        m = re.search(r"([\u4e00-\u9fa5A-Za-z0-9]+)\((\d{6})\)", text)
        company, code = (m.group(1), m.group(2)) if m else ("", "")
        # 提取回答时间（相对时间），提问时间在问题文本里保留
        tm = re.search(r"(刚刚|\d+\s*分钟前|\d+\s*小时前|昨天\s*\d{1,2}:\d{2}|\d{2}-\d{2}\s*\d{1,2}:\d{2})", text)
        ts = parse_relative_time(tm.group(1), now_ts) if tm else 0
        # 问题部分：第一个"来自"之前的内容，去掉"投资者_xxx :"前缀
        q_part = text.split("来自")[0]
        q_part = re.sub(r"^投资者_\d+\s*[:：]?\s*", "", q_part).strip()
        # 回答部分：从第二个公司名出现处开始（第一个是问题里的引用）
        a_part = text
        if company:
            first = a_part.find(company)
            second = a_part.find(company, first + 1)
            if second > 0:
                a_part = a_part[second + len(company):]
        # 去掉开头的 ◆ 标记和相对时间/来源
        a_part = re.sub(r"^\s*[◆●]+\s*", "", a_part)
        a_part = re.sub(r"^(刚刚|\d+\s*分钟前|\d+\s*小时前|昨天\s*\d{1,2}:\d{2}|\d{2}-\d{2}\s*\d{1,2}:\d{2})\s*来自\s*\S+", "", a_part)
        a_part = clean_sse_tail(a_part)
        if not q_part or not a_part:
            continue
        items.append({
            "guid": "sse_" + iid,
            "platform": "上证e互动",
            "company": company,
            "code": code,
            "question": q_part,
            "answer": a_part,
            "ts": ts,
            "link": f"https://sns.sseinfo.com/company.do?stockcode={code}" if code else "",
        })
    return items


def fetch_sse():
    items = []
    for page in range(1, MAX_PAGES + 1):
        try:
            url = f"https://sns.sseinfo.com/ajax/feeds.do?page={page}&type=11&pageSize={PAGE_SIZE}&lastid=-1&show=1"
            raw = http_get(url)
            page_items = parse_sse_html(raw)
            items.extend(page_items)
        except Exception as e:
            log(f"上证e互动第{page}页失败: {e}")
        time.sleep(0.5)
    return items


def build_matcher(kws):
    """返回 match(text) -> 命中的词列表"""
    plain, regexes = [], []
    for k in kws:
        if _ASCII_RE.match(k) and len(k) <= 30:
            regexes.append((k, re.compile(r"(?<![A-Za-z0-9])" + re.escape(k) + r"(?![A-Za-z0-9])", re.I)))
        else:
            plain.append(k)

    def match(text):
        low = text.lower()
        got = [k for k in plain if k.lower() in low]
        got += [k for k, rgx in regexes if rgx.search(text)]
        return got

    return match
