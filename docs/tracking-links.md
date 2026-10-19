<!--
背景（给以后的对话看）：
- hiwin-partners.com 是 HIWIN / 住一 Apartment Hotel 11 的旅行社（B2B）合作网站，GA4 property「HIWN Partners Website」。
- 本文件是发给旅行社的所有带 UTM 追踪链接的唯一主档。新增活动请改这里，并在本仓库提交。
- 邮件工具（lightweight_tool）里用「🔗 插入链接」（⌘K）把这些长链接藏在文字后面，或在「网页链接」框写 `名称 | 网址`。
- 每周一的 SEO 周报会按下面的 utm_campaign 统计访问和 contact_click（点邮箱/电话/WhatsApp/LINE）。
- 2026-10-15 开始使用；截至 2026-10-18 还没有任何追踪链接被点开。
-->

# HIWIN Partners 追踪链接（旅行社开发用）· 2026-10

用途：发给旅行社的邮件、LINE、WhatsApp、展位二维码，都改用下面的链接。GA4 会自动按「来源／媒介／活动」分开统计，每周一报告里就能看到“从开发邮件点进来几个人、从 ITF 二维码来几个人”。
链接已在 2026-10-15 测试，可以正常打开。不需要改网站。

## 怎么选
- 给台湾旅行社：用繁中页面（/tw/）。给东南亚或英文市场：用英文页面（/en/），或对应语言页面（/my/ /th/ /id/ /ph/）。
- 邮件正文第一个链接建议用「合作方式」页（价格、取消政策、团体住宿都在这页）。
- 链接里的参数不要删，也不要改大小写。

## 台湾开发邮件（第 4 季）
| 页面 | 链接 |
|---|---|
| 首页 | https://hiwin-partners.com/tw/?utm_source=email&utm_medium=outreach&utm_campaign=tw_agency_2026q4 |
| 合作方式（推荐） | https://hiwin-partners.com/tw/travel-agency-partnership/?utm_source=email&utm_medium=outreach&utm_campaign=tw_agency_2026q4 |
| 大阪精选住宿 | https://hiwin-partners.com/tw/osaka-accommodation/?utm_source=email&utm_medium=outreach&utm_campaign=tw_agency_2026q4 |
| 联系我们 | https://hiwin-partners.com/tw/contact/?utm_source=email&utm_medium=outreach&utm_campaign=tw_agency_2026q4 |

## ITF 台北旅展（展位 J3302）
用于展板、问卷页、现场发的资料上的二维码。现有二维码如果直接指向 LINE / WhatsApp，不用改；只有指向网站的才换成下面的链接。
| 页面 | 链接 |
|---|---|
| 首页 | https://hiwin-partners.com/tw/?utm_source=itf2026&utm_medium=event&utm_campaign=itf_taipei_2026 |
| 合作方式 | https://hiwin-partners.com/tw/travel-agency-partnership/?utm_source=itf2026&utm_medium=event&utm_campaign=itf_taipei_2026 |
| 展后跟进邮件 | https://hiwin-partners.com/tw/travel-agency-partnership/?utm_source=email&utm_medium=outreach&utm_campaign=itf_followup_2026 |

## 新加坡／东南亚开发邮件
| 页面 | 链接 |
|---|---|
| 英文首页 | https://hiwin-partners.com/en/?utm_source=email&utm_medium=outreach&utm_campaign=sg_agency_2026q4 |
| 英文合作方式 | https://hiwin-partners.com/en/travel-agency-partnership/?utm_source=email&utm_medium=outreach&utm_campaign=sg_agency_2026q4 |
| 马来西亚（马来文合作方式） | https://hiwin-partners.com/my/travel-agency-partnership/?utm_source=email&utm_medium=outreach&utm_campaign=my_agency_2026q4 |
| 泰国（泰文合作方式） | https://hiwin-partners.com/th/travel-agency-partnership/?utm_source=email&utm_medium=outreach&utm_campaign=th_agency_2026q4 |

## LINE／WhatsApp 跟进
| 用途 | 链接 |
|---|---|
| LINE 发给台湾同业 | https://hiwin-partners.com/tw/travel-agency-partnership/?utm_source=line&utm_medium=messaging&utm_campaign=agency_followup |
| WhatsApp 发给东南亚同业 | https://hiwin-partners.com/en/travel-agency-partnership/?utm_source=whatsapp&utm_medium=messaging&utm_campaign=agency_followup |

## 新开一个活动时怎么写
照这个格式，只改 campaign 那一段：`utm_source=来源&utm_medium=媒介&utm_campaign=活动名`
- 来源：email、line、whatsapp、itf2026、brochure（印刷品）
- 媒介：outreach（开发信）、messaging（聊天软件）、event（展会）、print（印刷品）
- 活动名：市场_对象_时间，全部小写英文、用底线连接，例如 `kr_agency_2027q1`

## 在 GA4 哪里看
Reports → Acquisition → Traffic acquisition，把维度改成「Session source / medium」或「Session campaign」。也会出现在每周一的 SEO 周报里。
