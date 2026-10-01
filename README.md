# a-share-keywords · A股行业关键词库（每周自动补词）

这个仓库只干一件事：**存放各行业的关键词库，并每周自动往里补新词**。
所有抓取/监控项目都从这里读词库 —— 词库只有这一份真源，不再往各个项目里拷副本。

现在只有 **AI 产业链**一个行业词库（`keywords/ai.json`，18 个分类、767 个词）。
将来加行业（机器人、医药、军工…）：放一个新文件进 `keywords/`，再在 `keywords/index.json` 的 `industries` 里加一条即可。

## 一、数据结构

```
keywords/
  index.json      ← 汇总处：列出所有行业词库 + 每个行业的 enabled 开关（只改这里）
  ai.json         ← AI 产业链词库（只放词汇：categories / signal_weights / changelog…）
scripts/
  update_keywords.py   ← 每周自动补词（自带抓取，不依赖别的仓库）
  qa_source.py         ← 取语料用的小抓取库（互动易 + 上证e互动）
.github/workflows/
  update-keywords.yml  ← 每周一 10:00（北京）补词
  purge-jsdelivr.yml   ← keywords/ 一有改动就刷新 CDN 缓存
```

`keywords/index.json`（**汇总 + 开关都在这里**）：
```json
{
  "industries": [
    { "file": "ai.json", "name": "AI 产业链", "enabled": true }
  ]
}
```
- `file` = 文件名（行业文件放在同一个 `keywords/` 目录下）
- `name` = 给人看的行业名，只会出现在日志里
- `enabled` = **这个行业是否生效**：`false` → 消费方跳过它（文件不删、词不丢，随时再打开）
- 加一个行业：把文件放进 `keywords/`，再在 `industries` 里加一条

行业文件本身**只放词汇**，没有开关；其中的 `updated_at` / `changelog` / `heat` / `weekly_note` 由每周任务维护，别手改。

`signal_weights` 分四档（critical / high / medium / low），消费方按权重排序决定标题里挂哪些命中词。

## 二、消费方怎么读（约定）

按这个顺序读（国内一律走 jsdelivr，别用 raw）：

1. `https://cdn.jsdelivr.net/gh/liuchao88/a-share-keywords@main/keywords/index.json` → 拿到 `industries` 清单
2. 逐个读 `.../keywords/<file>`，**跳过 `enabled: false` 的**
3. 把各文件的 `categories[].keywords` + `categories[].subcategories[].keywords` + `entities` 合并成词表，
   `signal_weights` 合并成权重表（同名冲突时后面的文件覆盖前面的）

匹配规则（两个消费方都是这么做的，换新项目请沿用）：
- 中文词走**子串**匹配（中文没有词边界）
- 纯 ASCII 词走**词边界**匹配（否则 `PD` 会命中 `update`、`IB` 会命中 `subscribe`）
- 命中词按权重排序，标题里最多挂 4 个

当前消费方：
- [hudong-rss](https://github.com/liuchao88/hudong-rss) —— 互动问答监控（每 10 分钟）
- [cninfo-ann-rss](https://github.com/liuchao88/cninfo-ann-rss) —— 巨潮公告 + 调研记录表监控（每小时）

## 三、每周自动补词（update-keywords.yml）

每周一 10:00（北京）跑一次，流程：

1. 抓一轮互动易 + 上证e互动的全市场问答
2. 只留"**当前生效词库已经命中**"的那批问答当语料
3. 再抓两条新闻语料：华尔街见闻早餐「要闻」段、虎嗅 RSS 标题
   （原因：董秘问答是企业被动回复、比行情慢半拍，新主题通常先在新闻里冒头）
4. 连同"近几周各关键词的命中数"（从 hudong-rss 的 `state.json` 远程取）一起交给大模型，
   让它输出：`add`（新词）/ `directions`（本周变热的方向 + 升|平|降 + 依据）/ `note`（一句整体观察）
5. **四道闸门**：只加不删；分类必须已存在；新词必须原样出现在本次语料里（防编造）；每次 ≤20 个、总量 ≤1200
6. 结果写回 `keywords/ai.json`（`updated_at` / `changelog` / `heat` / `weekly_note`），并推一条周报到企微群

任何一步失败（抓取失败、模型超时、返回乱码）→ **一律不动文件、退出码 0**，不让仓库变红。
例外：**没配 `DEEPSEEK_API_KEY` 会故意退出码 1**（配置缺失不是偶发失败，要让你看见，而不是静默不再补词）。

需要的 Secrets（仓库 Settings → Secrets and variables → Actions）：

| Secret | 必填 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 是 | 不配 → 任务报红（不会静默跳过） |
| `WECOM_WEBHOOK_URL` | 否 | 配了才把周报推到企微群 |

## 四、常用操作

- **开关某个行业**：改 `keywords/index.json` 里那条的 `"enabled"`（true/false）→ 提交后 `purge-jsdelivr` 自动刷 CDN
- **加一个行业**：新建 `keywords/<行业>.json`（照 `ai.json` 的结构：`name` / `categories` / `signal_weights`），
  再在 `keywords/index.json` 的 `industries` 里加一条 `{"file": "...", "name": "...", "enabled": true}`
- **手动加/删词**：直接改 `keywords/ai.json` 里的 `categories[].keywords`
- **立刻补一次词**：Actions → update-keywords → Run workflow
- **本地试跑**（只看模型想加什么、不写文件）：
  ```bash
  DEEPSEEK_API_KEY=sk-xxx python scripts/update_keywords.py --dry-run
  ```

## 五、注意

- **不要手改 `changelog` / `heat` / `weekly_note` / `updated_at`**：每周任务会覆盖它们
- **别把词库拷进别的项目**：要加词就加在这个仓库里（这正是本仓库存在的意义 —— 消灭副本漂移）
- 词库变更的历史都在这一个仓库的提交记录里，翻 changelog 或看 commit 都行
